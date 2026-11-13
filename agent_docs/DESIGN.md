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
| Verified | A person stands behind a value (in the model: settled). |

## 2. Shape

```text
src/examinator/           the library and app (this repository)
  core/                   definitions, store, status rules, runner, batch, overrides, focus;
                          printed fields (printed); intake (split, intake, identity); reading a region (reading)
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
regions (with this submission's regions), the note a grader left about the
pages, and the loopback model (`ctx.model(prompt, images=, text=, expect=,
step=)`; `step` tags the call). A reader may vouch for a value
(`vouches(field, text)`), need a region (`needs_region`), offer facets, and
script its own replies for a synthetic workspace (`script`).

- `std.readers.answers()`: transcribe the pages (or crops), then parse the
  transcript into one value per field. Two model calls; the transcript is not
  stored, shown or verified.
- `std.readers.code(entries)`: transcribe, then one parse per code field into
  the pseudocode language; `then=` rewrites the source deterministically.
- `core.printed.Printed`: a field's own reader of printed text (§5).

A **check** is `fn(ctx) -> Result` (or a bare grade), wrapped as
`Check(fn, points, timeout=, isolate=)`. `ctx.read(label)` reads its own
question's field, `ctx.read(label, question=...)` another's; each read is
recorded with the field's revision. A field not read, or with a review marker,
raises `Undecided`. `ctx.params` is the one generic hook for "run this check
once with a different setting"; the library gives it no meaning. Checks run in
a warm worker process with a wall-clock limit (default 60 s; a worker's
start-up is not counted).

A model output may contain `[REVIEW: reason]` wherever it is unsure. Such a
value is never settled, so every grade that read it waits for a person.

## 5. Printings: printed fields, and checks that read other questions

**Decided:** two layers, no graph. Paper is read into fields; checks compute
from fields. Which printing a submission has is not a library concept: it is
read into fields like anything else, and checks use it.

- **A printed field** (`Field(label, region, read=Printed(ask, reads=...))`)
  is a field whose value the model reads from the *printed* text in its region
  (`core/printed.py`), asked `ask`. With `reads`, a reading is normalised (case,
  spaces, one layer of brackets or quotes, a trailing `.` or `%`) and mapped to a
  value; the prompt lists the expected readings and tells the model to answer
  with a review marker when it sees none of them. A reading that is absent, or
  not one of `reads`, becomes a value with a review marker. Never guess.
- **A reader may vouch for its value**: `Printed` does when the reading was one
  of a closed set of expected ones, unless `verify=True`. Such a value is
  settled by the reader; reading again replaces it, never a value a person
  settled.
- A question with only printed fields and no checks holds the exam's printing.
  Its grid column shows how its fields stand (not read, unverified, verified).
- **A check reads other questions' fields** with `ctx.read(label,
  question=...)`. Nothing is declared: the library **records** what it read.
  A grade counts only once every field it read is settled; one unreadable
  field holds back only the checks that read it.
- Keys that differ by printing are chosen by a field's value:
  `math.keyed(key={value: {...}}, by=...)`, where `by` is a label of the
  question's own field, `std.fields.value(label, question=...)`,
  `std.fields.agree(...)` (fields that must agree, else undecided), or any
  `fn(ctx) -> value`.
- Reading a printed field is `core/reading.py`, shared with intake's landmarks
  and identity boxes. Regions are instructor-defined template rectangles. Do
  **not** ask the model to locate regions (see `LESSONS.md`).

**Open, worth a spike:** read a printed field with no model call by comparing
the printed region against each printing's blank template.

## 6. Evidence and grades

A check returns a `Result`: one grade and its evidence.

```python
Result(grade, fields={'1.1': 'correct', '1.2': Judgement('incorrect', expected='3/2', note="sign")},
       blocks=[Plot.cdf(samples, cdf, title="weibull(0.5, 2)"), Details("Samples", Table(rows))])
```

- The grade is anything the check's points accept: `Bool()` takes
  `True`/`False` and the outcomes `'correct'`, `'incorrect'`, `'blank'` (false,
  flagged blank) and None (undecided); `Number(int | float, min=, max=)` takes a
  number. `Grade(value, flags, reason)` carries flags and the reason an
  undecided grade is undecided. Points declare whether they count toward the
  total (`counts=`); `Bool(worth=)` sets what a true grade is worth.
- Per-field judgements are shown beside the field: an outcome, optionally the
  expected answer and a short note. Blocks are shown below, plots first.
- Blocks come from a small vocabulary (`std.blocks`): note, table, plot (from
  data), code excerpt, collapsed details, and raw SVG or HTML. A block may be
  marked grader-only.
- **Nothing computes from evidence.** The grade is the check's own.

## 7. Whether a grade counts

The status table is in [`MODEL.md`](MODEL.md) and implemented once, as pure
functions over stored rows, in `core/status.py`, with one test per row
(`tests/test_status.py`) and one per row of "changing the exam"
(`tests/test_changes.py`). Further rules:

- Verification approves a value; it does not change who wrote it, nor the
  value's revision. A person who sets a value settles it.
- A decision is authoritative even if a field it was made on carries a review
  marker, and may be made when the check never ran.
- A check that read no field never counts by itself.
- Supporting work on the page is never read or checked. Graders judge it.
- Status always comes from these rules, never from evidence contents.

**Protection.** A value a person settled is never replaced by a read unless
asked (`include_protected`); the replaced value goes to history. Nothing
automatic touches a decision.

**Automatic recomputation.** A result outdated by a changed value, a changed
check (an override) or changed exam code is recomputed in the background: after
a change in the app, when the app starts, and before a batch run from the CLI.

## 8. Storage

**Decided:** SQLite, one file per workspace, keyed rows.

```sql
create table row (
  submission text not null,      -- or a source id, for its split plan and print area
  kind       text not null,      -- 'value' 'result' 'decision' 'pages' 'region' 'layout' 'identity' 'who' 'split' 'print_area'
  name       text not null,      -- 'question/label', 'question/check', a question id, or ''
  body       text,               -- JSON
  origin     text not null,      -- 'auto' | 'manual'
  author     text,
  updated_at text not null,
  rev        integer not null,   -- from one workspace-wide counter; never reused
  primary key (submission, kind, name)
);
create table history (...);       -- the same plus replaced_at; written on every change
create table submission (id text primary key, name text, email text, pdf text, student text);
create table setting (key text primary key, value text);        -- defaults, roster, counters
create table override (path, value, author, updated_at, rev);   -- live edits of exam data
create table override_history (...);
create table source (...);  create table made (...);             -- intake
create table memo (submission, key, value);                      -- model readings (printed fields, landmarks, boxes)
```

Bodies: a value `{'text', 'settled': None | 'reader' | 'person', 'by'}`; a
result `{'grade', 'fields', 'blocks', 'reads': {'question/label': rev | None},
'defn', 'code', 'params'?}`; a decision `{'grade', 'seen': {'question/label':
rev | None}}`.

- Every read on the normal path is a keyed lookup. The grid and the filter
  page load every row in one query, leaving result blocks out.
- A revision changes only when content changes (`keep_rev` for a value
  settled or unsettled). Writing the body a row already has is a no-op.
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
  settled unless asked), run a check (with optional parameters).
- **Batch:** reading only, for a selection of submissions and questions.
  Always previewed first: what will be read, what is skipped as protected or
  present, what is blocked (no model, no region). Refused if anything relevant
  changed since the preview. Checks have no batch: they recompute by
  themselves, and the batch refreshes each submission once its reads finish.
- **Missing only** reads only fields with no value, runs only checks that are
  not current. **Include protected** reads again what a person settled.
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
  reasoning off, with an explicit per-run "retry with reasoning" for one read.
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

- Page numbers in an exam definition are **printed exam pages**. A workspace has
  a default number of prepended cover pages (scanning services add them), and
  each submission can override it. The exam definition may supply the initial
  defaults for cover pages and the scan window; the workspace's saved settings
  take precedence once they exist.
- A question names the `pages` that hold its answer (what its reader sees).
- The printed page may be reduced and inset within the scanned page. The
  **print area** (`scan_window` in code: left, top, right, bottom, per-mille of
  the scanned page) maps printed-page coordinates onto the scan. Most specific
  wins: the submission's own, its source scan's (every submission intake split
  from that file; none for PDFs handed in split), the workspace's, the
  package's (default: the whole sheet). All regions in an exam definition are in
  printed-page coordinates; the library applies the print area and adds padding.
  The question page's regions mode draws it on every sheet, to drag and save at
  any of the first three levels.
- Each question's selected pages can be overridden per submission, with a note,
  for answers that continue onto extra-space pages. The note is passed to the
  model.
- Render with PDFium at about 160 DPI. PDFium must not be used from several
  threads at once; confine it to a worker. Cache rendered pages, and cut crops
  from the cache.
- A field's region may be replaced for one submission (`Workspace.set_region`),
  as the page selection can. Changing where a field is read changes no value:
  the app offers to read again.
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
verdict and a plot, and check scaffolding. It uses the core; the core never
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
  edit/verify/decide controls, runs, settings. Exams own only evidence blocks.

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
- **Read:** scope, options, preview, progress, stop.
- **Settings:** per-browser keys and appearance first, then workspace defaults (cover pages, print area)
  last.

Details that graders relied on and should survive: keyboard navigation between
students that keeps the current question; keys for verify, edit and save; a
separate PDF window that follows the current student and question; drafts that
are never silently discarded; an adjustable crop scale (header slider, Ctrl+scroll).

### Words and colours on screen

One word per idea, everywhere a person can see it (labels, titles, messages,
docs). Internal names (`needs_review`, `core/focus.py`) may differ.

- **submission**: one student's paper. Never "exam" for that; the exam is the exam itself.
- **verify**: a person stands behind a value; before, **unverified**; after, **verified** (the model says settled; the screen never does).
- **decided**: a grade a person gave (never "by hand", "manual"); one plain look everywhere ("decided · who"). **to decide**: a person must give the grade.
- **undecided**: no grade yet, the glyph `?` (never "not determined", "needs review", "unresolved", "open").
- **filter**: the narrowed set of submissions (never "focus", "like this").
- **print area**: where the printed page sits on the scanned page (Settings, a question page's regions mode).
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
| Live editing of exam data (keys, regions, prompt notes) | **Built** (`core/overrides.py`, the Exam page and the question page's editors). Plain data addressed by path, stored in the `override` table with history; an edit outdates exactly the checks whose definition it changes, which are recomputed. |
| Live-editable logic (small rules) | Grade rules are functions of outcomes. A restricted interpreter (the pseudocode interpreter with a rules profile, or an embedded hermetic language) could supply such a function from stored text. |
| Filter and search facets (group by normalized answer; search comments in code) | **Built** (`core/focus.py`). A facet is a small function of one submission's view (values, statuses), computed on read in one query and cached by the store's version. |
| Listing which grades changed after a check changed | Not built. Old results stay in history, so the app can compare. |
| Static student view | Evidence blocks carry an audience mark. The view is a second renderer over stored rows. |
| Several exams in one process | No module-level state; exam packages loaded under distinct names. |
| "Review me later" flag (a TA marks an answer for the professor to look over) | Not built. The glyph `!` is reserved for it in the grid and on the question page; `?` means only "a person must decide". The flag would be a small manual row per submission and question, with author and an optional note, and a built-in facet so the filter page can gather flagged answers. It must not affect whether a grade counts. |
| A pluggable interpreter back end (vectorized, masked execution) | Not built; the closure interpreter is fast enough so far. The route: keep one front end (parse, whitelist, validate), expose the validated tree, and route operators through an overridable table; a masked executor is then a second back end. |

## 16. Intake: scans in, submissions out

Examinator takes stacks of scans (many students per PDF) and writes one PDF per
submission into `pdfs/`; everything downstream is unchanged.

- **Sources.** Each scanned PDF is kept unchanged as `sources/ID.pdf`, ID the
  start of its SHA-256 (so adding it twice is noticed); a `source` table lists
  them. CLI `examinator intake`, or a streamed upload on the Scans page.
- **Split plan.** One row per source, `(source id, 'split', '')`, so it has
  revisions and history like any grader edit. It is a list of *starts* (the
  sheet where each submission begins; its pages run to the next start, less
  dropped sheets), each marked confirmed or a guess, plus dropped sheets,
  landmark readings, the expected length and orientation (`core/split.py`).
  Boundary logic is pure: `split.propose(n, length, read)` takes a reader
  `sheet -> position | None` and is tested with plain dicts.
  `intake.reguess` guesses a source's plan again and writes it.
- **Landmarks** (`Exam(landmarks=[Landmark(name, region, ask, reads)])`): like
  a printed field, a region and a question for the model about printed text, reading
  normalised; `reads` maps a reading to a printed page (a title on page 1),
  or, absent, the reading is the page number. Readings are taken only where
  needed: at the expected boundary, then neighbours back and forth (a page
  number jumps straight to the right place), and memoised per source, sheet,
  landmark, orientation and scan window (`core/reading.py`, as for printed fields). A
  failed model call leaves its sheet unread, not read as nothing; the plan
  counts such sheets (`failed`) and is not marked checked when every call failed.
- **Doubts** are computed, never stored: too few or too many pages, a first
  page whose landmark did not read as one, a page number out of place. A sheet
  never read is no doubt.
- **People decide.** The Scans page steps through boundaries (V confirm, A/D
  move a page, W/S back/forward). A confirmed start is never moved by a guess;
  guesses after it are recomputed from it (every N kept sheets, adjusted by
  stored readings). A length mismatch after confirmation is a flag, not a block.
- **Orientation**: one of the 8 square symmetries (`pdf/orient.py`: mirror,
  then quarter turns clockwise; `then` composes, `apply` transposes exactly).
  The plan holds three levels, each added to the one above: the source's
  (`orient`), a submission's own (entries' `orient`) and a sheet's own
  (`sheet_orient`); `intake.sheet_orient` is the one place that composes them.
  Every turn is a quarter turn clockwise per press (four return to the start):
  R the sheet shown, Shift+R its submission, the whole scan by button only
  (mirroring too). The page shows sheets turned in the web layer
  (`/sheet/...?o=`), landmark crops are cut from the turned sheet, and
  accepting writes pages upright (so name-box crops and question pages are).
- **Accepting** writes changed submissions (pages or orientation) with
  `pdf/pages.py`: an upright sheet is copied untouched by PDFium; a turned one
  is baked as a new page holding one full-page JPEG (quality 95) shaped like
  the image, so every written page is a plain scan for the renderer's fast
  path. A `made` table records each submission's source sheets and their
  orientation. Ids are opaque (`s0001`), allocated once, never reused.
  Accepting marks every start confirmed. An entry left with no pages removes
  its submission. A submission that would change or disappear while it has
  grading work (a value, a result or a decision) is refused with the
  list (`Busy`); a forced accept keeps the work and clears that submission's
  model memo. PDFs are written to temporary files and moved into place only
  after the store has committed, so a plan edit in between changes nothing.
- **Who** (`core/identity.py`). A roster is optional. It is stored as a
  setting, `[{'name', 'id', 'email', 'key'}]`, read from a CSV/TSV with a
  header or from plain lines (name and ID, comma or tab, either order; the ID
  is the part with no spaces and a digit). A preview (count, first rows,
  unreadable lines) comes before saving. The Roster tab lists everyone on it
  (sent once with the page, filtered in the browser) with the submission each
  is picked for, a link to it on Names, or "unmatched".
  - **Declared boxes**: `Exam(identity=[Identity(name, region, ask, roster)])`,
    shaped like a printed field, but about handwriting (`IDENTITY_PROMPT`); `roster`
    is the roster field the reading is compared with (`'name'`, `'id'`,
    `'email'`, or None to show only); boxes sharing a field are joined (first
    and last name). The library names no field beyond those three.
  - **Drawn boxes**: the Names tab's "boxes" (B) draws them on a submission's
    page, inside its print area, with the question page's rectangle editor
    (`static/rects.js`, shared with `regions.js`). They are exam data, saved
    for every submission through `/exam/set` and `/exam/clear` like a field's
    region (`core/overrides.py`, the exam-level list `@identity`, see
    `Exam.LISTS`): a declared box's region is `@identity/<name>/region` (none:
    the box is not used; also its `ask` and `roster`), a box the package does
    not declare is one override `@identity/<name>` holding all of it (clearing
    it takes it away). They show on the Exam data page and its change list,
    with revisions, conflicts and revert. A box added in the app asks by its
    roster field (`identity.ASKS`). `identity.boxes(ws)` (the live boxes with a
    region) is what reading, proposals and the pages use.
  - **Reading**: per written submission and box, through the model, memoised
    per box (region and question), scan window and cover pages. Stored as the
    row `(submission, 'identity', '')`, `{'fields', 'basis'}` (history like
    any row; unchanged readings are not rewritten); a reading under an old
    box definition is ignored. A background job on the Scans page (and
    `intake --names`) reads only what `identity.unread` lists; hidden
    without a model or boxes. Accepting a rewritten submission drops its
    reading (and memo), not its pick. No roster text ever reaches a prompt.
  - **Proposing** is pure: `propose(readings by roster field, roster)` ranks
    candidates (an exact ID first; then IDs that differ only in look-alike
    characters such as `l`/`1` and `o`/`0`, compared both ways round so IDs
    may mix letters and digits; then IDs within 2 edits for 8+ characters,
    1 for 4-7, none for shorter; names by
    order-insensitive token similarity, accents, case and punctuation folded)
    with a reason, and gives a doubt: `agree`, `id`, `name`, `disagree`,
    `nobody`, `unread`. A person already picked elsewhere is flagged, not
    dropped, and comes after the others among equal scores. Computed on read, cached by reading revision and roster.
  - **Picking**: every submission is named by its pick `(submission, 'who', '')`
    (`{'name', 'id', 'email', 'key'}`), written by a person against the
    revision they saw. One programmatic way in, for PDFs handed in already
    split: a file name carrying exactly one roster ID as a whole run of
    letters and digits (`identity.by_file`, on `sync` and when the roster is
    saved) becomes the pick (origin `'auto'`) of a submission that never had
    one; it shows on the Names tab as verified and is changed there like any
    other. The
    submission row carries the name, email and the student ID (`student`).
    The Names tab steps through unnamed exams, most doubtful first (V take,
    W/S other proposals, A/D back/skip, E the picker), showing the boxes'
    crops, or without boxes the first page's top (or whole). The picker
    filters the roster as you type (a word starting a name part, ID or email
    first, then anywhere); Enter picks the highlighted person and moves on;
    while empty it lists the proposals. A person picked elsewhere is tagged
    "already s0003" (a problem pill), listed after the others among equal
    matches, and may still be picked (both are then flagged "name twice"). It also lists roster entries with no exam.
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
