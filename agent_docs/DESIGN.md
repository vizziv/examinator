# Examinator: design

Read [`README.md`](README.md) first for what is decided, recommended and open.
Unless a section says **Decided**, what follows is **Recommended**: a design the
user has seen and broadly accepted, with implementation detail left to you. All
names are placeholders.

## 1. Vocabulary

The model (fields, readers, checks, questions; values, results, decisions;
the status of a grade) is [`MODEL.md`](MODEL.md). This document is the rest.

| Term | Meaning |
| --- | --- |
| Exam | One printed exam: its pages, its questions, its grader. One Python package. |
| Workspace | A folder of one exam's data: scanned PDFs, a roster, the SQLite file, exports. |
| Submission | One student's scanned PDF, identified by its file stem. |
| Field | A named slot for one value (text) read from paper, with a label (`'1.2'`) and usually a region. |
| Region | A rectangle on a printed exam page, in per-mille of that page. |
| Reader | What reads fields: the question's handwriting reader, or a field's own (`Printed`). |
| Printing | One of several printed versions of an exam or a question. Not a library concept: it is read into printed fields. |
| Check | A function from field values to one proposed grade and evidence. |
| Result | A check's stored output: the proposal, evidence, what it read. |
| Decision | A person's grade for a check. |
| Evidence | Per-field judgements (beside the field) and display blocks (below). |
| Verified | Someone stands behind a value: a person, or the reader that vouched for it. |

## 2. Shape

```text
src/examinator/           the library and app (this repository)
  core/                   definitions, store, status rules, runner, batch, overrides, focus;
                          asking about one region (ask: `Ask`, `Printed`); intake (split, intake, identity); reading helpers (reading)
  pdf/  model/            rendering, scan layout, orientation, writing PDFs; loopback model client
  std/                    the standard library: readers, prompts, fields, math, pseudocode, blocks
  web/                    server and browser interface
src/examinator_kits/
  stochastic/             optional kit for sampler questions (primitives, fit tests, checks)

my_exam/                  an exam package (lives wherever the user keeps exams)
  exam.py                 builds and exposes `exam`
  ...                     any helper modules the exam needs

workspace/                one exam's data (never in a repository)
  sources/  scanned stacks as added (intake)
  pdfs/  ID.pdf per submission
  exam.sqlite
  cache/  exports/
```

```sh
examinator serve path/to/my_exam --workspace path/to/workspace
```

One process serves one exam (**Decided**, for now). Keep no module-level state in
the library: everything hangs off the `Exam` object and an open workspace. That
is all multi-exam serving will need later.

## 3. Defining an exam

An exam is declared in Python. Plain data where data suffices, functions where it
does not. The author's guide is [`WRITING_AN_EXAM.md`](WRITING_AN_EXAM.md).

```python
from examinator import Exam, Field, Printed, Region
from examinator.std import math, readers

exam = Exam('midterm', title="Midterm", pages=12)

exam.question(
    'q1', title="Rectangles", pages=[3],             # where the answer is; what the reader sees
    fields=[Field('width', Region(2, 80, 120, 920, 300),        # printed: tells the printing
                  read=Printed("the printed width of the rectangle, as a number",
                               reads={'3': 'original', '4': 'alternate'})),
            Field('1.1', Region(3, 500, 96, 885, 196)),
            Field('1.2', Region(3, 500, 207, 885, 307))],
    read=readers.answers(symbols=['a']),             # reads the handwritten fields
    check=math.keyed(symbols={'a': 'positive'}, by='width', grade=math.at_least(1), key={
        'original': {'1.1': '3*a', '1.2': '1/2'},
        'alternate': {'1.1': '4*a', '1.2': '1/2'}}))
```

Design rules for this layer:

- **Small pieces that compose.** A reader, a check function, a grade rule
  over outcomes, a field's chooser of a key: each is a value. No bundles.
- **Prompts are strings, and every reader accepts them.** A built-in's default
  prompt is an importable value; `notes=` adds a sentence.
- **Anything the standard library cannot express is a function** the exam passes
  in: a custom check, a custom key chooser, a custom grade rule.
- **Definitions are data once built.** After `exam.py` runs, each question is a
  frozen structure that can be hashed, displayed and overridden (`core/overrides.py`).

## 4. Readers and checks

A **reader** reads some fields of one submission: `reader.read(ctx, fields) ->
{label: text}`. Its context gives the question's selected pages, crops of
regions (with this submission's regions), and the loopback model (`ctx.model(prompt, images=, text=, expect=,
step=)`; `step` tags the call). A reader may vouch for a value
(`vouches(field, text)`), read each field's region rather than the question's
pages (`needs_region`), offer facets, and script its own replies for a
synthetic workspace (`script`).

**What a read reads is one computation**, `runner.to_read`: a pure function
of the question as it is for the submission, its stored values, the pages its
PDF lacks and the read's options (only some fields, missing only, include
protected, whether there is a model). It returns, per reader, the fields to
read and, for every other field with a reader, why not: 'not asked',
'present', 'protected', or blocked ('no region', 'page missing', 'model
unavailable'). `Runner.read` executes exactly that, the batch plan counts
exactly that, and the question page shows its blocked reasons.

**A read needs its pages.** A region reader looks at the page of the field's
region ("no region" without one); any other reader at every one of the
question's selected pages. If one of them is missing, the field is not read
("page missing"), and a whole-page reader with only some of its pages waits
too: its transcript would be of part of the answer. So no reader is called
without its images.

- `std.readers.answers()`: transcribe the pages (or crops), then parse the
  transcript into one value per field. Two model calls; the transcript is not
  stored, shown or verified.
- `std.readers.code(entries)`: transcribe, then one parse per code field into
  the pseudocode language; `then=` rewrites the source deterministically.
- `core.ask.Printed`: a field's own reader of printed text (§5).

A **check** is `fn(ctx) -> Result` (or a bare grade), wrapped as
`Check(fn, points, timeout=, isolate=)`. `ctx.read(label)` reads its own
question's field, `ctx.read(label, question=...)` another's; each read is
recorded with the field's revision. A field not read, or with a review marker,
raises `Undecided`. A setting a grader may change (a constant the check uses)
is a field with a default (`Field(label, default='10')`): it reads as the
default, verified, until a person sets a value, and a change recomputes the
check like any other (MODEL.md). Checks run in
a warm worker process with a wall-clock limit (default 60 s; a worker's
start-up is not counted).

A model output may contain `[REVIEW: reason]` wherever it is unsure. Such a
value is never verified, so every grade that read it waits for a person.

## 5. Printings: printed fields, and checks that read other questions

**Decided:** two layers, no graph. Paper is read into fields; checks compute
from fields. Which printing a submission has is not a library concept: it is
read into fields like anything else, and checks use it.

- **A printed field** (`Field(label, region, read=Printed(ask, reads=...))`)
  is a field whose value the model reads from the *printed* text in its region
  (`core/ask.py`: `Printed` is `Ask` with a prompt about printed text; `Ask`
  alone asks about handwriting, as the identity fields do), asked `ask`. With `reads`, a reading is normalised (case,
  spaces, one layer of brackets or quotes, a trailing `.` or `%`) and mapped to a
  value; the prompt lists the expected readings and tells the model to answer
  with a review marker when it sees none of them. A reading that is absent, or
  not one of `reads`, becomes a value with a review marker. Never guess.
- **A reader may vouch for its value**: `Printed` does when the reading was one
  of a closed set of expected ones, unless `verify=True`. Such a value is
  verified by the reader; reading again replaces it, never a value a person
  verified.
- A question with only printed fields and no checks holds the exam's printing.
  Its grid column shows how its fields stand (not read, unverified, verified).
- **A check reads other questions' fields** with `ctx.read(label,
  question=...)`. Nothing is declared: the library **records** what it read.
  A grade counts only once every field it read is verified; one unreadable
  field holds back only the checks that read it.
- Keys that differ by printing are chosen by a field's value:
  `math.keyed(key={value: {...}}, by=...)`, where `by` is a label of the
  question's own field, `std.fields.value(label, question=...)`,
  `std.fields.agree(...)` (fields that must agree, else undecided), or any
  `fn(ctx) -> value`.
- Asking the model one thing about one region is `Ask.call`, the one call
  path for printed fields, identity fields and intake's landmarks. Regions are instructor-defined template rectangles. Do
  **not** ask the model to locate regions (see `LESSONS.md`).

**Open, worth a spike:** read a printed field with no model call by comparing
the printed region against each printing's blank template.

## 6. Evidence and grades

A check returns a `Result`: one grade and its evidence.

```python
Result(grade, fields={'1.1': 'correct', '1.2': Judgement('incorrect', expected='3/2', note="sign")},
       blocks=[plots.cdf(samples, cdf, title="weibull(0.5, 2)"), Details("Samples", Table(rows))])
```

- The grade is anything the check's points accept: `Bool()` takes
  `True`/`False` and the outcomes `'correct'`, `'incorrect'`, `'blank'` (false,
  flagged blank) and None (undecided); `Number(int | float, min=, max=)` takes a
  number. `Grade(value, flags, reason)` carries flags and the reason an
  undecided grade is undecided. Points declare whether they count toward the
  total (`counts=`); `Bool(worth=)` sets what a true grade is worth.
- Per-field judgements are shown beside the field: an outcome, optionally the
  expected answer and a short note. Blocks are shown below, top-level images
  first in a row of figures.
- Blocks come from a small vocabulary (`std.blocks`): note, table, code
  excerpt, collapsed details, and image (a PNG, JPEG, GIF, WebP or SVG file's
  bytes; shown with `<img>`, so an SVG runs nothing; inverted in dark mode as
  scans are). A block is an object or the plain dict it stands for.
- **One stored shape, enforced at one boundary.** `result.settle` turns what a
  check returned into what is stored (`Record`), in the check's process (the
  worker sends the record across its pipe): the grade through the check's
  points, every block, however written, against the block grammar and limits
  (image type, 2 MB per image, 8 MB per result, details 8 deep). What does not
  fit is a check error ("the check returned ..."), stored as an undecided
  result, never a bad row. There each image gets its digest and its bytes
  leave the result: they are stored once per result in the `image` table,
  written and dropped with it; the block keeps `{digest, media_type, alt,
  title}`. Everything after `settle` trusts the shape: the page renders the
  stored blocks as they are, and `/image/{sub}/{q}/{check}/{digest}` serves an
  image only if that result shows it (immutable: the digest names the
  content; its type, `nosniff`, a sandboxing CSP).
- **A kit figure is a pure function of its arguments.** The stochastic kit's
  plots (matplotlib) draw under a fixed style that ignores ambient settings,
  bound every size by one rule, treat text as literal unless asked for math,
  and show a figure that cannot be drawn as a short note. A test plot is drawn
  only when shown.
- **Nothing computes from evidence.** The grade is the check's own.

## 7. Whether a grade counts

The status table is in [`MODEL.md`](MODEL.md) and implemented once, as pure
functions over stored rows, in `core/status.py`, with one test per row
(`tests/test_status.py`) and one per row of "changing the exam"
(`tests/test_changes.py`). Further rules:

- Verification approves a value; it does not change who wrote it, nor the
  value's revision. A person who sets a value verifies it.
- A decision is authoritative even if a field it was made on carries a review
  marker, and may be made when the check never ran.
- A check that read no field never counts by itself.
- Supporting work on the page is never read or checked. Graders judge it.
- Status always comes from these rules, never from evidence contents.

**Protection.** A value a person verified is never replaced by a read unless
asked (`include_protected`); the replaced value goes to history. Nothing
automatic touches a decision.

**Automatic recomputation.** A result outdated by a changed value, a changed
check (an override) or changed exam code is recomputed in the background: after
a change in the app, when the app starts, and before a batch run from the CLI.

## 8. Storage

**Decided:** SQLite, one file per workspace, keyed rows.

```sql
create table row (
  submission text not null,      -- or a source id, for its split plan
  kind       text not null,      -- 'value' 'result' 'decision' 'who' 'split'
  name       text not null,      -- 'question/label', 'question/check', a question id, or ''
  body       text,               -- JSON
  origin     text not null,      -- 'auto' | 'manual'
  author     text,
  updated_at text not null,
  rev        integer not null,   -- from one workspace-wide counter; never reused
  primary key (submission, kind, name)
);
create table history (...);       -- the same plus replaced_at; a replaced value, decision or plan (not a result)
create table submission (id text primary key, pdf text);         -- who it is: its pick
create table setting (key text primary key, value text);        -- roster, counters
create table override (scope, path, value, author, updated_at, rev,   -- live edits of exam data:
  primary key (scope, path));    -- scope '' (the exam), 'source:<id>', 'submission:<id>'; no history
create table source (...);  create table made (...);             -- intake
create table memo (submission, key, value);                      -- Split's landmark readings, by source
```

In code and prose a row is written as the store's methods take it,
`(submission, name, kind)`: `(sub, 'a/x', 'value')`, `(source id, '', 'split')`.

Bodies: a value `{'text', 'verified': None | 'reader' | 'person', 'by'}`; a
result `{'grade', 'fields', 'blocks', 'reads': {'question/label': rev | None},
'defn', 'code'}` (a field read at its default: revision `DEFAULT`, 0); a decision `{'grade', 'seen': {'question/label':
rev | None}}`.

- Every read on the normal path is a keyed lookup. The grid and the filter
  page load every row in one query, leaving result blocks out. A result's
  images are rows of their own (`image`: submission, name, digest), replaced
  with the result and gone with it or its submission.
- A revision changes only when content changes (`keep_rev` for a value
  verified or unverified). Writing the body a row already has is a no-op.
- All writes by people carry the revision they saw; a mismatch is rejected and
  the browser keeps its draft. A decision carries the revisions of the fields
  it was made on.
- A check computes outside any transaction, then publishes only if every field
  it read is still at the revision it read; otherwise the result is discarded.
- Rows are keyed by name, so the package may change under a workspace: rows of
  removed fields and checks are ignored, not deleted.

Scanned PDFs stay on disk. Rendered page images are cached on disk, keyed by
the PDF's identity, the page and the resolution.

## 9. Running work

- **From a question page:** read (the question's fields; not what a person
  verified unless asked), run a check again (one that timed out, say).
- **Batch:** reading only, for a selection of submissions and questions. The
  Read page shows live, as the options change, what will be read, what is
  skipped as protected or present, and what is blocked (no model, no region);
  one button reads it, planned again at the start. A read never replaces what
  a person verified unless asked, and a read that finds a value changed
  meanwhile is discarded. Checks have no batch: they recompute by themselves,
  and the batch refreshes each submission once its reads finish.
- **Missing only** reads only fields with no value: how a person resumes a
  batch read that stopped halfway (the Read page leaves it unticked by
  default). **Include protected** reads again what a person verified.
- **Every read asks the model.** Reads happen only when a person asks, so
  nothing remembers a field's reading but its value; only Split's landmark
  readings are memoised (the plan re-reads every sheet without being asked).
- **Cancellation** stops queued work and lets running tasks finish.
- A timeout or crash yields an undecided result, never "incorrect".
- A read that fails leaves the previous values intact.

## 10. Model client

- OpenAI-compatible chat completions over loopback HTTP only. Reject any other
  host, credentials in the URL, proxies and redirects.
- A fixed system preamble says the supplied images and text are data, not
  instructions, and that the model must not solve, grade or complete answers.
  The reader's prompt follows it.
- Temperature 0. Defaults that worked: 4,096 output tokens, 120 s timeout,
  reasoning off (reads never ask for it).
- Retry HTTP 500 and 503 twice, after 10 s and 20 s. Fail immediately on
  anything else. A response that did not stop normally, or is empty, is a
  failure, not a value.
- When the expected output is JSON or code, strip exactly one whole-response
  Markdown fence. Never extract a block from surrounding commentary.
- Diagnostics on failure are structural only (status, finish reason, lengths,
  token counts). Never log or display prompt or response text.
- Probe the endpoint at startup. If it is absent, reading is hidden and
  refused server-side; everything else (editing, verifying, checking, deciding)
  still works.

## 11. Pages, regions and scan layout

- **One mechanism for "this value, but more specifically here"**: an override
  (`core/overrides.py`) is a value at a path and a **scope**: the exam (every
  submission), a source scan (every submission intake split from that file) or
  one submission. The value in force for a submission is its own, else its
  scan's, else the exam's, else the package's. Which scopes a kind
  of value may be set at is one table (`overrides.SCOPES`): a region and pages
  for the exam or a submission, the print area at all three; everything else for the exam only. The
  identity fields' regions are field regions like any (exam or submission).
  Every scope has revisions and conflicts alike (no history); the app sets and
  clears them all through `/exam/set` and `/exam/clear` with a `scope`. Three
  principles keep this simple:
  1. **An override is a change to the definition**, valid exactly when the
     definition with it applied is valid. The definition is a set of roots
     (`Exam.roots`: each question, and the print area as '@print_area'); there
     is one validation, `Exam.validate` (regions, pages, defaults, each reader's
     and check's own `validate`, such as a key that must parse), which the
     package's questions and every override go through, on a copy with the
     change. A scope enters only as context: one submission's extra sheets are
     pages it may name. One scope's overrides of one root go in together when
     the definition with all of them validates, else all but the one that
     breaks them, else one by one in path order. A stored override that does
     not validate is skipped and reported, at every scope alike
     (`Workspace.skipped_overrides`; the Exam data page shows the exam's).
  2. **A workspace's definition is its own.** `Workspace.exam` is the
     package's definition with the exam's overrides (`overrides.apply`, at
     opening and after every exam-wide edit); the package's `Exam` is never
     changed, and the runner, the CLI and the app read the definition through
     the workspace. What holds for one submission is `Layers`
     (`Workspace.layers(sub)`: `question(id)`, `layout`), the one resolver,
     each root resolved once.
  3. **A group that stands apart is reached only by its name.**
     `exam.questions` are the questions graders work through; the identity
     group is `exam.identity` (or `exam.group('@identity')`), so the grading
     routes, the grid, batch reads and `run -q` cannot reach it by accident;
     the Names step saves its values through its own route.
- Page numbers in an exam definition are **printed exam pages**, and a
  submission's PDF has printed page p at index p - 1, then its extra sheets
  (intake writes it so: §16; a blank placeholder stands for a page not
  scanned). Nothing downstream offsets a page. `Workspace.missing(sub)` is the
  printed pages a submission does not have (a placeholder, or the PDF ends
  first): no field whose reader would look at them is read (§4, "A read
  needs its pages").
- A question names the `pages` that hold its answer (what its reader sees).
- The printed page may be reduced and inset within the scanned page. The
  **print area** (`print_area`: left, top, right, bottom, per-mille of the
  scanned page; `Exam(print_area=)`, an override `@print_area`; default the
  whole sheet) maps printed-page coordinates onto the scan. All regions in an
  exam definition are in printed-page coordinates; the library applies the
  print area and adds padding. The question page's print-area mode draws it on
  every sheet, to drag and save for this submission, its scan, or all.
- Each question's selected pages can be overridden per submission, for answers
  that continue onto extra-space pages; pages beyond the exam's name that
  submission's extra sheets in order (exam pages + 1 is its first), checked
  against how many it has; so may its own regions.
- Render with PDFium at about 160 DPI. PDFium must not be used from several
  threads at once; confine it to a worker. Cache rendered pages, and cut crops
  from the cache.
- A field's region may be replaced for one submission, as the page selection
  can. Changing where a field is read (region, print area, pages)
  changes no value; reading again is a person's explicit choice.
- The same regions serve three purposes: crops shown beside the grader's input,
  crops sent to the model for printed fields, and (optionally) cropped reading
  (`readers.answers(crops=True)`). Whole pages is the proven default.

## 12. The standard library

The inclusion test (**Decided** in spirit): something belongs in `std` if several
unrelated exams would use it unchanged, and it must be built from public pieces
so that a near-miss can be reassembled instead of rewritten. When in doubt,
leave it out and let the exam package write it.

| Module | Contents |
| --- | --- |
| `std.readers` | Handwriting readers: `answers(...)` (short answers) and `code(entries, ...)` (pseudocode). |
| `std.fields` | A field's value in a check (`value(label, question=)`, recorded through `ctx.read`) and fields that must agree (`agree`), else `Undecided`. |
| `std.prompts` | Default prompt texts as values, and small functions that generate prompt fragments. |
| `std.math` | A restricted expression parser feeding SymPy (expressions are data, never `eval`); equality under declared symbol assumptions; choice and text normalization; `keyed(key, by=, grade=)`, the standard check (a key entry may be an expression string, a choice set, an approximate number, or a function `answer -> outcome`); grade rules over outcomes (`all_correct`, `at_least`). |
| `std.pseudocode` | A bounded interpreter for a Python-syntax teaching language: whitelisted syntax, no imports or attributes, step and randomness budgets; seeded sampling of a named function; instructor-supplied trusted primitives and constants registered through an API; selection of one entry point and its helpers; blank detection; error reports that name the failing expression and its operand values. The language description for prompts is a value. |
| `std.blocks` | The evidence block vocabulary. |

Sampler questions use an optional kit outside the standard library,
`examinator_kits.stochastic`: random primitives for the pseudocode language,
goodness-of-fit tests (support, KS, chi-square, projected KS) that return a
verdict and a plot, the plots (`plots`: matplotlib figures of samples against
a target, as PNG images), and check scaffolding. It uses the core; the core never
imports it.

Out of the standard library: anything about a particular distribution, problem
or rubric; multi-trial "try several interpretations" policies; numeric-constant
normalization. Those are exam code.

## 13. Server and interface

- Binds to loopback. Optionally told its public URL when it sits behind an
  authenticating reverse proxy; then it validates Host and Origin against that.
- State-changing requests need a per-process token delivered in the page.
- Strict content security policy. No path from a request reaches the filesystem
  unvalidated; no symlinks.
- **Server-rendered HTML with a small amount of plain JavaScript.** No Node
  toolchain (**Decided** preference). The grid is rendered once and filtered and
  sorted in the browser; requests are for state changes.
- The app owns the frame: grid, navigation, scan and crop views, fields,
  edit/verify/decide controls, batch reads, settings. Exams own only evidence blocks.

### Changes

**A page has one subject, drawn from one server response. Every change names
the revision of what it changes as the page drew it, and the server refuses a
stale one.** Moving to another subject is a navigation (the question page: one
submission's question; next and previous are page loads), so a change can only
be about what is on screen, and what was queued dies with the page. Pages that
edit one subject in place (Split: one scan's plan; the region and print-area
editors; Exam data; Read: the batch run; Names, while it shows one submission at
a time in place) send every change the same way:

- **Server.** A POST route is a *change* or a *query* (the route table says
  which; `change(...)`, `query(...)` in `web/app.py`). A change requires the
  revision drawn (`rev`, or each item's in a multi-value save) and answers the
  new one; a stale one is refused with 409 and `current`, what is there now. A
  run (a read, a check, a batch, reading the name boxes) is a change to its
  slot: its revision is the last run's id, and a second while one runs is
  refused. The roster's revision is a digest of it. Only the scan upload (a
  file, kept once by its digest) is neither.
- **Browser.** `Changes.send(subject, path, body, {done, redraw, line, ends})` in
  `common.js` is the only way to POST a change (`request` only reads; a test
  scans the scripts). The body is finished when it is sent: built by the
  press, from what is drawn, and copied. *Changes to one subject go one at a
  time in press order, each expecting the revision the one before it left;
  a refusal redraws from `current` and drops what was queued behind it.* So a
  quick A A D on Split is three edits in order. A change that `ends` its
  subject (naming, verify-and-next: the page moves on) drops the presses queued
  behind it, which were about a state the person never saw answered: V V names
  once.
- **When a change does not go through, the person gets one message, where they
  were working, that is true:** why, what the page now shows, and whether what
  they typed was kept. It goes to the caller's `line` (`Line` in `common.js`),
  else the header; a line belongs to the last change sent there ('…' while slow,
  then its outcome), so a message goes when the state it was about does. A
  refusal never throws a draft away: the draft stays, takes the revision now
  saved, and the line says "changed elsewhere to X · yours kept: save again to
  overwrite" (`Changes.kept`).
- **One press, one change.** In `actions.js`, for every control: a key's
  auto-repeat never triggers an action, a held Enter or Space never clicks a
  button or takes a picker's choice again, and the second click of a double
  click never reaches a button, an action or a list choice.
- **Moving is a page load, rendered ahead.** The pages a key reaches (on a
  question page: next and previous submission in the grid's order, next and
  previous question; on the grid: the cursor's cell, and a hovered one) are
  listed for the browser to prerender (`ahead()` in `common.js`, speculation
  rules; the CSP admits `'inline-speculation-rules'`, JSON, and still no inline
  code). Where the browser does not prerender, the move is an ordinary load.
  A prerendered page runs before it is shown and may never be: every GET
  changes nothing (a test), what a page writes to state shared with other pages
  waits for `whenShown()`, and a page shown later than drawn (prerendered, or
  restored from the back/forward cache) redraws from the server at once, as
  after any change. HTML is `no-store`, so Back without that cache loads what is
  there now.

Screens for the first version:

- **Grid:** one row per submission, one column per check (a question with no
  checks has one column: how its fields stand). Shows
  the focused set (below); text search; sort; totals. Previous/next
  navigation follows the focus, search and sort.
- **Filter page:** the focused set (`core/focus.py`). One card per question with
  its facets: each check's grade and state (built in, from the status rules),
  the value of each printed field (built in), plus facets offered by readers
  and check functions (`facets(question)`) and questions
  (`Exam.question(..., facets=)`), e.g. the keyed check's normalized answer per
  field and the comments in pseudocode. OR within a facet's values,
  AND across facets; counts are over the other facets' selection. The focus is
  per browser (localStorage) and shareable as `?focus=` in a link.
- **Question page:** each check's proposed grade and status with a control to
  decide it; the fields on the page (the question's own and those its checks
  read from other questions, linked), each with its scan crop, a badge only when
  unverified, and each check's judgement beside it; blocks below, plots first,
  details collapsed; read and run controls; the answer key the printing picks,
  collapsed; per-field history. The main actions are combinations: **Verify**
  (save edits and verify everything here), **save** (a person who sets a value
  verifies it), **Verify, next** (V).
- **Read:** scope, options, what they would read (live counts, the breakdown
  collapsed), one read button, progress, stop.
- **Settings:** per-browser keys and appearance. Exam data (the print area
  for all, too) is on the Exam data page, which also lists what a
  scan or a submission has of its own, each revertible.

Details that graders relied on and should survive: keyboard navigation between
students that keeps the current question; keys for verify, edit and save; a
separate PDF window that follows the current student and question; drafts that
are never silently discarded; an adjustable crop scale (header slider, Ctrl+scroll).

### Words and colours on screen

One word per idea, everywhere a person can see it (labels, titles, messages,
docs). Internal names (`needs_review`, `core/focus.py`) may differ.

- **submission**: one student's paper. Never "exam" for that; the exam is the exam itself.
- **verify**: a person stands behind a value; before, **unverified**; after, **verified**.
- **decided**: a grade a person gave (never "by hand", "manual"); one plain look everywhere ("decided · who"). **to decide**: a person must give the grade.
- **undecided**: no grade yet, the glyph `?` (never "not determined", "needs review", "unresolved", "open").
- **filter**: the narrowed set of submissions (never "focus", "like this").
- **print area**: where the printed page sits on the scanned page (a question page's print-area mode, Exam data).
- **read pages** / **read names**: what the model does on Scans; **pages read** after; **N failed** when a call fails.
- **not read yet** (nothing asked), **empty** (asked, the box had nothing), **N lines unclear** (a roster).
- **put back** (a dropped page), **unverify**, **guess again**, **join with next**, **type name**.

Buttons: only the one primary button of a panel is capitalised; the rest are
lowercase. Key hints come from the live key map, never from text.

Colour: a person is asked for exactly one thing, and **one yellow** (`--unv-bg`)
means it: unverified, wherever it appears (a grid cell, the state pill, a tag,
a to-decide cell, which shows `?`). Tags: **red** = a
problem; **yellow** = unverified or otherwise waiting on a person (Split's "to
write"); **plain** = information and finished states (verified, written,
manual, turned, pages read). No green tags. A grid cell that needs nobody is its
glyph on the plain background. The second accent is only "here / selected" and
key hints; the blue `--ask` is only the `?` glyph. Success messages are not red.

## 14. Security posture

- Exam packages are trusted code: they are written by the instructor (or an
  agent the instructor directs) and reviewed like any code.
- Student-derived content is untrusted. It is never executed as Python. Parsed
  pseudocode runs only in the bounded interpreter; parsed math only through the
  restricted expression parser. Both have size and work limits, and checks run
  in a time-limited subprocess.
- The model sees student handwriting and is told it is data. Its output is
  validated structurally before being stored and is always reviewable.

## 15. Later layers, and what each needs from the core

Do not build these in M1–M3. Do not block them either.

| Later feature | What the core must already provide |
| --- | --- |
| Live editing of exam data (keys, regions, prompt notes) | **Built** (`core/overrides.py`, the Exam page and the question page's editors). Plain data addressed by path, stored in the `override` table (no history); an edit outdates exactly the checks whose definition it changes, which are recomputed. |
| Live-editable logic (small rules) | Grade rules are functions of outcomes. A restricted interpreter (the pseudocode interpreter with a rules profile, or an embedded hermetic language) could supply such a function from stored text. |
| Filter and search facets (group by normalized answer; search comments in code) | **Built** (`core/focus.py`). A facet is a small function of one submission's view (values, statuses), computed on read in one query and cached by the store's version. |
| Listing which grades changed after a check changed | Not built. Results keep no history; it would compare old and new results at recompute time (MODEL.md). |
| Static student view | A second renderer over stored rows; which blocks it shows is its own decision (no mark on blocks yet). |
| Several exams in one process | No module-level state; exam packages loaded under distinct names. |
| "Review me later" flag (a TA marks an answer for the professor to look over) | Not built. The glyph `!` is reserved for it in the grid and on the question page; `?` means only "a person must decide". The flag would be a small manual row per submission and question, with author and an optional note, and a built-in facet so the filter page can gather flagged answers. It must not affect whether a grade counts. |
| A pluggable interpreter back end (vectorized, masked execution) | Not built; the closure interpreter is fast enough so far. The route: keep one front end (parse, whitelist, validate), expose the validated tree, and route operators through an overridable table; a masked executor is then a second back end. |

## 16. Intake: scans in, submissions out

Examinator takes stacks of scans (many students per PDF) and writes one PDF per
submission into `pdfs/`; everything downstream is unchanged.

- **Sources.** Each scanned PDF is kept unchanged as `sources/ID.pdf`, ID the
  start of its SHA-256 (so adding it twice is noticed); a `source` table lists
  them. CLI `examinator intake`, or a streamed upload on the Scans page.
- **Split plan.** One row per source, `(source id, '', 'split')`, so it has
  revisions and history like any grader edit (`core/split.py`). It is only
  the pile sorted: one ordered list of the source's sheets (`sheets`), each a
  record with its marks, plus landmark readings, `anchor` and the expected
  `length`. It says nothing about ids, about what was written, or about the
  exam's page count (`exam.pages`, passed where needed). Every edit is an edit
  of that list, and undo steps back through its history.

  | mark | on a sheet |
  | --- | --- |
  | `start` | it begins a submission |
  | `said` | a person said whether it starts one and whether it is dropped |
  | `dropped` | in no submission (blank, stray, a leading junk sheet) |
  | `extra` | kept with its submission, not a printed page (False: a person said not) |
  | `missing` | how many printed pages not scanned come before it (0: a person said none) |
  | `turn` | its own quarter turns clockwise, 1-3 |

  Four rules hold it together:
  1. **A submission is its kept sheets.** It is the run of kept sheets from a
     start up to the next. Every operation ends by normalising: starts sit
     only on kept sheets (dropping a first page hands the start to the next
     kept sheet of its run, or the run is gone), the first kept sheet starts
     one; so there is no empty submission, and the server tells the page each
     sheet's submission and printed page (`owner`, `slot`) rather than the page
     working it out.
  2. **A mark is a guess or a person's statement.** Each person's edit sets
     `said` on the sheets whose start or dropped mark it speaks about (V: the
     first page; A/D, cut, join: the sheets it moves or joins; drop, put back;
     Shift+A/D across a boundary); Accept sets it on every sheet. A guess
     (the first, "guess again", reading pages, and the re-guess that ends every
     edit, from stored readings) clears the start and dropped marks of the
     sheets nobody spoke for and guesses them again; it never changes a `said`
     sheet. So a join is not split again, a put-back sheet is not dropped
     again, and after Accept nothing moves unless a person moves it. A
     submission is *verified* when its first sheet is `said`. A turn changes
     only turns (readings stay as last read until pages are read again).
  3. **What was written is `made`, and only `made`.** Which run is which
     written submission is derived (`split.link`, used by `intake.outline` for
     the page, its counts and Accept alike): a written submission and a run
     that share a sheet are ranked by where the first shared sheet comes in
     the run, then where it came in what was written; links are made best
     first, each at most once. A run linked to none is new; a written
     submission linked to none is to be removed. So undoing past an Accept
     keeps every id, and a join's removed id never comes back.
  4. **The store says written only about files in place** (see Accepting).
- **A submission maps the exam's printed pages to sheets** (`split.lay_out`):
  its sheets in list order, less extra ones, with the missing pages, are
  printed pages 1, 2, .... Sheets beyond the exam's pages are extra
  by default; pages after the last sheet are missing by default (still
  flagged "too few pages"). `length` (sheets per submission, default the
  exam's pages) is only the splitter's grouping size: more means trailing
  extras are expected. A leading junk sheet is simply dropped.
  Boundary logic is pure: `split.propose(n, length, read)` takes a reader
  `sheet -> position | None` and is tested with plain dicts.
  `intake.reguess` guesses a source's plan again and writes it (`split.guess`).
- **Landmarks** (`Exam(landmarks=[Landmark(name, region, ask, reads)])`): like
  a printed field, a region and a question for the model about printed text, reading
  normalised; `reads` maps a reading to a printed page (a title on page 1),
  or, absent, the reading is the page number. Readings are taken only where
  needed: at the expected boundary, then neighbours back and forth (a page
  number jumps straight to the right place), and memoised per source, sheet,
  landmark, orientation and print area (`core/reading.py`'s `region_key`, the key
  `Ask` uses too). A reading is a printed page, 0-based. A landmark is asked as a `Printed`
  would be (`Ask.call`). A
  failed model call leaves its sheet unread, not read as nothing; the plan
  counts such sheets (`failed`) and is not marked checked when every call failed.
- **Doubts** are computed, never stored: too few or too many pages, a first
  page whose landmark did not read as one, a page number out of place, a
  proposed mark. A sheet never read is no doubt.
- **Proposed marks** are computed, never stored (`split.proposals`). For a
  submission one sheet short of `length` (or long) with no extra or missing
  mark of a person's and a sheet nobody spoke for, the page missing (or the
  sheet extra) is searched by halves (log N readings, memoised in the plan's
  readings) and proposed when the readings either side agree; at the end it
  is the default already. Verifying the submission, a person's mark on it, or
  accepting makes proposals a person's (`split.settle`).
- **People decide.** The Scans page steps through boundaries (V confirm, A/D
  move a first page, W/S back/forward). The first submission always starts the
  list: its first page moves later by dropping it, earlier by putting back the
  dropped sheet before it. On the page shown: E extra or counted, G a missing
  page before it, Shift+G not missing, Shift+A/Shift+D move it one place in
  the list, stepping over dropped sheets. Within a submission it swaps with
  its neighbour (the start stays at the first place); at a boundary it crosses
  into the neighbouring submission instead, and a submission it leaves with no
  sheet is gone. The overview labels each sheet with its printed page ("p3",
  "extra") and shows "missing p3" placeholders. Guesses after a person's start
  are recomputed from it (every N kept sheets, adjusted by stored readings). A
  length mismatch after verifying is a flag, not a block.
- **Orientation**: quarter turns clockwise, 0-3 (`pdf/orient.py`: `turn`
  transposes exactly), a sheet's own `turn` and nothing else. Every press is a
  quarter turn clockwise more (four return to the start): R the sheet shown;
  Shift+R and the whole-scan button are shortcuts that turn each sheet of the
  submission's run, or of the source. A sheet later moved into a turned
  submission keeps its own turn. The page shows sheets turned in the web layer
  (`/sheet/...?o=`), landmark crops are cut from the turned sheet, and
  accepting writes pages upright (so name-box crops and question pages are).
- **Accepting** writes changed submissions (pages or orientation) with
  `pdf/pages.py`: an upright sheet is copied untouched by PDFium; a turned one
  is baked as a new page holding one full-page JPEG (quality 95) shaped like
  the image, so every written page is a plain scan for the renderer's fast
  path. A submission's PDF is its printed pages in order, a blank placeholder
  ("missing page" in small grey letters) where one is missing, then its extra
  sheets (`split.written`). A `made` table records each submission's source
  sheets in that order (None for a placeholder) and each page's turn
  (`Made.orient`). Ids are opaque (`s0001`), allocated once, never reused.
  Accepting makes every sheet a person's. A written submission no run is
  any more is removed. A submission that would change or disappear while it has
  grading work (a value, a result or a decision; identity fields' values are
  not grading work) is refused with the
  list (`Busy`); a forced accept keeps what people typed, verified or decided. Every rewritten submission
  loses the values a reader wrote that no person typed or
  verified (`intake.forget_reads`): those fields are not read again until asked.
  Order: PDFs are written to temporary files; one commit checks
  the plan is unchanged and notes the ids about to be placed (setting
  `intake_pending`) with the id counter; files move into place; one commit
  records the plan, `made` and the submissions; removed files are deleted; the
  note is cleared. A crash anywhere is repaired by the next Accept: a noted
  file with no `made` row is deleted (and `Workspace.sync` never takes it for
  a submission), a noted or missing file counts as not written, and stray
  temporary files are swept.
- **Who** (`core/identity.py`). Three rules hold throughout:
  1. *A naming is a change like any other* (§13, Changes): `{sub, rev, key |
     text | restore}` (take a proposal, pick from the list, type a name, unname,
     undo), about the submission drawn. The server refuses a stale `rev` (409,
     with the pick as it is now) and an unknown key (400), and never
     reinterprets one as another.
  2. *Automatic matching acts only on unique evidence.* Each identifying
     column (of a reading, a file name or a pick) supports some roster people;
     a match needs them to single out one person. Ties and conflicts between
     columns are doubts shown to a person, never resolved silently.
  3. *Moving a box changes no value.* Reading again is asked for ("read
     names", with "again" for values no person verified).

  A roster is optional. It is stored as a
  setting, `[{'name', 'id', 'email', 'key'}]`, read from a CSV/TSV with a
  header (generic column names only) or from plain lines (name and ID, comma
  or tab, either order; the ID is the part with no spaces and a digit). A
  roster is valid exactly when every person on it is found as themselves
  (`Matcher.find`), so a pick of anyone on it links to them: a line whose
  person cannot be told apart from an earlier one's (the same ID with no
  email to tell them apart, or for people with neither, the same name) is a
  problem; `key` (email, else ID, else name) only addresses a person from
  the page. A preview (count, first rows, problems) comes before saving. The Roster tab lists everyone on it
  (sent once with the page, filtered in the browser) with the submission each
  is picked for, a link to it on Names, or "unmatched".
  - **Identity fields**: `Exam(identity={'name': Field('name', Region(...)),
    'id': Field('id', ...)})` maps roster columns to ordinary fields of the
    built-in group `@identity` (`defs.identity_group`): one field per roster
    column (`name`, `id`, `email`), those not mapped with no region. A field
    without a reader of its own is read by `Ask(defs.ASKS[column])`, about
    the handwriting. The group is `exam.identity` (`apart=True`), not one of
    `exam.questions` (the grid, totals, filter facets, the Read page, batch
    reads, prev/next question). Everything else is
    a field's: values with revisions, history and `verified`; `Runner.read`,
    `set`, `verify`; re-read protection; regions at exam and submission scope
    (`@identity/fields/<column>/region`, the Exam data page); scripted
    fake-model replies.
  - **Boxes** on the Names tab ("boxes", B) are those regions, drawn on a
    submission's page with the question page's rectangle editor
    (`static/rects.js`): "+ <column>" for a column with none, "save for this
    submission" or "save for all", "drop own", "revert all", as on the
    question page.
  - **Reading**: `identity.read(runner, sub)` is `Runner.read` of the group's
    placed fields with no value (`again`: also those no person verified;
    `intake --names`, the Scans page's background job). `identity.unread`
    lists what "read names" reads, and the page counts as "to read": the
    submissions nobody has named with such a field. A named submission is never
    read (its pick stands; the boxes only propose who). With nothing to read no
    run starts, and the page says why. "Are there boxes" is one
    predicate, `identity.boxed` (any submission's, its own included). No
    roster text ever reaches a prompt. Each value shows under its crop on
    the Names tab, where it can be corrected (Enter) or verified (✓), through
    the question page's save endpoint.
  - **Proposing** is pure: `propose(readings by roster field, roster)` ranks
    candidates (an exact ID first; then IDs that differ only in look-alike
    characters such as `l`/`1` and `o`/`0`, compared both ways round so IDs
    may mix letters and digits; then IDs within 2 edits for 8+ characters,
    1 for 4-7, none for shorter; names by
    order-insensitive token similarity, accents, case and punctuation folded)
    with a reason, and gives who the evidence singles out (`match`) and a
    doubt (rule 2), all from one computation: each column read (that the
    roster has) supports the people it fits best (an ID or email the nearest
    within tolerance; a name those within a margin of the best, if close
    enough), possibly nobody, which is evidence too; the people every column
    read supports decide: one is the match, `agree` (or `id`, `email`,
    `name` when one column was read), several `tie`, none `disagree`, or
    `nobody` when no column fits anyone. Candidates come in that order: the
    people every column supports first, then by how many columns support
    them, then by how alike. With nothing usable: `unread` (still
    to read), `unreadable` (a review marker), `empty` (every box blank). A
    person already picked elsewhere is flagged,
    never dropped or ranked lower: taken-ness never breaks a tie (one
    confident misreading must not cascade into a second wrong match). Computed
    on read, cached by value revisions and roster.
  - **Picking**: every submission is named by its pick `(submission, '', 'who')`:
    the person (`{'name', 'id', 'email'}`), written by a person against the
    revision they saw. Who a
    pick is on the current roster (and so "taken", "twice", "not on the
    roster") is derived by unique exact evidence (`Matcher.find`: ID and
    email where both have them, else the whole name), so a replaced roster
    relinks every pick. A rewritten submission keeps its pick.
    One programmatic way in, for PDFs handed in already
    split: a file name carrying exactly one roster ID, that one person has, as a whole run of
    letters and digits (`identity.by_file`, on `sync` and when the roster is
    saved) becomes the pick (origin `'auto'`) of a submission that never had
    one; it shows on the Names tab as verified and is changed there like any
    other. A
    submission's name, email and student ID (`Submission.name`, `email`,
    `student`) are read from its pick; the submission table holds only its id and file.
    The Names tab steps through unnamed exams, most doubtful first (V take,
    W/S other proposals, A/D back/skip, E the picker), showing the boxes'
    crops, or without boxes the first page's top (or whole). The picker
    filters the roster as you type (a word starting a name part, ID or email
    first, then anywhere); Enter picks the highlighted person and moves on;
    while empty it lists the proposals. A person picked elsewhere is tagged
    "already s0003" (a problem pill), ranked by the match alone (ties in
    roster order), and may still be picked (both are then flagged "name twice"). It also lists roster entries with no exam.
- A pre-split PDF that came in sideways could be fixed by adding it as a
  source of one submission (`length` = its pages), at the cost of a new id.

## 17. Testing

- Unit tests for the status rules, one per row of MODEL.md's status table, and
  one per row of its "changing the exam" table; the small changes.
- Store tests for concurrent edits, discarded stale results and history.
- Toolkit tests on synthetic inputs only.
- A scripted fake model that returns canned text per (submission, question,
  step), written by each reader (`script`), so the whole pipeline runs
  deterministically offline.
- A synthetic-workspace generator and a real-browser smoke test over it.
- The two overfitting guards described in the brief: no exam identifiers in the
  library, and the toy exam.
- A test that no answer-key text appears in any prompt the pipeline builds.
- Opt-in live tests against the loopback model, on blank or synthetic material
  only, writing outside the repository.
