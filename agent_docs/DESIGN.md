# Examinator: design

Read [`README.md`](README.md) first for what is decided, recommended and open.
Unless a section says **Decided**, what follows is **Recommended**: a design the
user has seen and broadly accepted, with implementation detail left to you. All
names are placeholders.

## 1. Vocabulary

| Term | Meaning |
| --- | --- |
| Exam | One printed exam: its pages, its questions, its grader. One Python package. |
| Workspace | A folder of one exam's data: scanned PDFs, a roster, the SQLite file, exports. |
| Submission | One student's scanned PDF, identified by its file stem. |
| Question | A unit with fields and stages, usually a typed result. One with no result (the exam's printing, say) has only a parse to verify. |
| Field | One slot of a question's parse, with a label (`'1.2'`) and usually a region. The student writes it, or a reader reads it (a printed field). |
| Region | A rectangle on a printed exam page, in per-mille of that page. |
| Printing | One of several printed versions of an exam or a question. Not a library concept: it is read into printed fields. |
| Printed field | A field whose value the model reads from the printed text in its region (`Printed`), not from the student's answer. |
| Stage | One step for one submission and question, named by the artifact it writes: `transcript`, `parse`, `evidence` (the check), `grade`. |
| Artifact | The stored output of a stage. Editable by graders where the stage allows. |
| Evidence | What a check found: data plus an ordered list of display blocks. |
| Result | The typed value a question produces: a bool, a number, or a small named group. |
| Verified | A human confirmed that a parsed answer matches the scan. |

## 2. Shape

```text
src/examinator/           the library and app (this repository)
  core/                   definitions, store, runner, workflow rules, overrides, focus;
                          intake (split, intake, identity) and reading a region (reading)
  pdf/  model/            rendering, scan layout, orientation, writing PDFs; loopback model client
  std/                    the standard library: stages, prompts, math, pseudocode, blocks
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
does not. See [`EXAM3.md`](EXAM3.md) for a complete example.

```python
from examinator import Exam, Field, Printed, Region, Bool, Number
from examinator.std import stages, math

exam = Exam('midterm', title="Midterm", pages=12)

exam.question(
    'q1', title="Rectangles",
    pages=[3],                       # where the answer is; sent to the model
    result=Bool(),
    fields=[Field('width', region=Region(2, 80, 120, 920, 300),        # printed: tells the printing
                  read=Printed("the printed width of the rectangle, as a number",
                               reads={'3': 'original', '4': 'alternate'})),
            Field('1.1', region=Region(3, 500, 96, 885, 196)),
            Field('1.2', region=Region(3, 500, 207, 885, 307))],
    stages=stages.math(
        symbols={'a': 'positive'},
        by='width',                  # one key per value of the field
        key={'original': {'1.1': '3*a', '1.2': '1/2'},
             'alternate': {'1.1': '4*a', '1.2': '1/2'}},
    ),
)
```

Design rules for this layer:

- **A question's `stages` is an explicit list.** `stages.math(...)` returns such a
  list. The same pieces are public, so an exam can assemble its own:

  ```python
  [stages.transcribe(),
   stages.parse_math(symbols=['a']),
   stages.check_math(key=..., symbols={'a': 'positive'}),
   stages.grade(math.all_correct)]
  ```

- **Prompts are strings, and every model stage accepts one.** A built-in's
  default prompt is an importable value, and helpers that generate prompt text
  (for example, notation hints from a list of symbols and functions) are plain
  functions returning strings. Adding a sentence is concatenation, never a
  reimplementation.
- **Anything the standard library cannot express is a function** the exam passes
  in: a custom check, a custom key chooser, a custom grade rule. Exams build their own
  abstractions with ordinary Python (loops, helpers, shared lists).
- **Definitions are data once built.** After `exam.py` runs, each question is a
  frozen structure that can be hashed, displayed and overridden (`core/overrides.py`).

## 4. Stages and the stage context

A stage is a named function plus a little metadata:

| Property | Purpose |
| --- | --- |
| `name` | Also the artifact kind: `'transcript'`, `'parse'`, `'evidence'`, `'grade'`. |
| `inputs` | Names of earlier stages in the same question whose artifacts it reads. |
| `uses_model` | True for stages that call the model. Drives permissions and batch planning. |
| `editable` | Whether graders may edit the artifact directly (parse: yes; evidence: no). |
| `isolate`, `timeout` | Run in a subprocess with a wall-clock limit. Default on for checks. |

The function receives a context and returns the artifact body (text or
JSON-serializable data):

```python
ctx.submission      # id
ctx.question        # the frozen definition
ctx.input('parse')  # body of an upstream artifact
ctx.read('info')    # a check only: another question's parse (recorded, see §5)
ctx.pages()         # rendered images of the question's selected pages
ctx.crop(region)    # one rendered region
ctx.model(prompt, images=..., text=...)   # loopback model call; returns text
ctx.params          # optional per-run parameters supplied by the grader
```

`ctx.params` is the one generic hook for "run this check once with a different
setting". The library attaches no meaning to it.

The standard pipeline, and why each stage is separate:

1. **`transcript`** (`stages.transcribe`) — model, images in, text out. Records
   what is on the page, mistakes included. Never solves or corrects.
2. **`parse`** — model, text in, structured answer out (a JSON object of field
   values, or source code in the pseudocode language). A parse of fields also
   holds the question's printed fields, read beside it (§5). This is what
   graders edit and verify against the scan.
3. **`evidence`** (the check: `stages.check`, `stages.check_math`) — local code,
   no model. Produces evidence. It may read other questions' parses.
4. **`grade`** — a pure, fast function from evidence to the question's result.
   Runs in-process immediately after the check, one artifact per result part
   (`grade`, or `grade:<part>`). A grader's manual decision writes this same artifact.

A model output may contain `[REVIEW: reason]` wherever it is unsure. A stage
refuses to consume an upstream artifact that still contains a marker; a check
treats a field containing one as undecided. Graders clear markers by editing.

## 5. Printings: printed fields, and checks that read other questions

**Decided:** two layers, no graph. The whole exam is parsed into fields; checks
run on parsed fields. Which printing a submission has is not a library concept:
it is read into fields like anything else, and checks use it.

- **A printed field** (`Field(label, region, read=Printed(ask, reads=...))`) is
  an ordinary field of a question's parse whose value the model reads from the
  *printed* text in its region (`core/printed.py`), asked `ask`. With `reads`, a
  reading is normalised (case, spaces, one layer of brackets or quotes, a
  trailing `.` or `%`) and mapped to a value; the prompt lists the expected
  readings and tells the model to answer with a review marker when it sees none
  of them. A reading that is absent, or not one of `reads`, becomes a value with
  a review marker, which holds the parse back as any marker does. Never guess.
- The value sits in the parse beside the student's fields, is shown with its
  crop, is edited like them, and is verified with them: one verify covers the
  whole parse. `Field.read` is a value (a reader, `reader(ctx, field) -> str`)
  so another kind of reader (a barcode) can be added without a new concept.
- A question with only printed fields and no `result` holds the exam's printing
  (`stages.read_fields()`): it has a parse to verify and no grade. Nothing in the
  library names or special-cases it.
- **A check reads other questions' parses** with `ctx.read(question)`. There is
  no declaration: the library **records** what a check read, the parse's
  revision for each question (`None` if it did not exist), with the evidence's
  recorded inputs (`inputs['reads']`). A grade is outdated when any parse it
  read has changed, and counts only when its own parse and every parse it read
  are verified; until then it waits, naming the question (`Status.waiting_on`).
  A read of a parse that does not exist leaves the check undecided, and the
  evidence outdated once the parse appears. A check that returns before reading
  a question does not depend on it. Checks never read other checks' results.
- Batches run every question's parse before any check (§9), so a missing parse
  is rare. Saving or verifying a parse rechecks the questions whose stored
  evidence read it.
- Keys that differ by printing are chosen by a field's value: `stages.math(key=
  {value: {...}}, by=...)`, where `by` is a label of the question's own field,
  `std.fields.value(label, question=...)`, `std.fields.agree(...)` (fields that
  must agree, else undecided), or any `fn(ctx) -> value`. Agreement and flags
  need no other library concept.
- Reading a printed field is `core/reading.py`, shared with intake's landmarks
  and identity boxes: the prompt (with "expected to be one of" from `reads`),
  the memo key. A field's region may be on any page. Regions are
  instructor-defined template rectangles. Do **not** ask the model to locate
  regions (see `LESSONS.md`).

**Open, worth a spike:** read a printed field with no model call by comparing
the printed region against each printing's blank template. Scan registration is
the risk. Untested.

## 6. Evidence, results and grades

**Evidence** is what a check returns: machine-readable data for the grade rule,
and an ordered list of blocks for people.

```python
Evidence(
    data={'fields': {'1.1': {'answer': '3*a', 'outcome': 'correct'}, ...}},
    blocks=[Plot.cdf(samples, cdf, title="weibull(0.5, 2)"),
            Note("KS p = 3e-9"),
            Details("Samples", Table(rows))],        # collapsed by default
)
```

- Blocks come from a small vocabulary: note, table, plot (from data: CDF overlay,
  histogram, scatter), code excerpt, collapsed details, and one escape hatch
  that takes raw SVG or HTML. The app renders them; exams never write layout.
- Plots are stored as data and drawn in the browser, not as embedded images.
- A block may be marked grader-only. The later student view renders the rest.
- Standard checks use a per-field `outcome` of `'correct'`, `'incorrect'`,
  `'blank'` or `None` (could not decide). Custom checks may put anything in
  `data`; only the question's own grade rule reads it.

**Result types** (**Decided** in spirit, shape yours): `Bool()`,
`Number(int | float, min=None, max=None)`, or a dict of names to those. Each
produces one grid column. A result declares whether it counts toward the total.

**Grade** is the result value, or `None` for undecided, plus optional display
flags (for example `'blank'`, so blank answers can be filtered separately from
wrong ones). The grade rule is a function `evidence -> grade`. As a convenience,
`Bool` accepts the standard outcomes directly: `'correct'` is true,
`'incorrect'` is false, `'blank'` is false with the blank flag, and `None` is
undecided.

## 7. Workflow rules

This section decides whether a grade counts. Implement it once, in the library,
as pure functions over stored rows, and test every row. Get it reviewed.

Definitions, for one submission and question:

- **Started:** any artifact exists, other than per-submission settings and
  intake records (`workflow.NOT_WORK`: the page selection, the scan layout, the
  identity reading and the pick). Intake uses the same list to decide whether
  a submission has grading work.
- **Verified parse:** a human confirmed the parse's current content.
- **Current evidence:** computed from the current parse, the current parse of
  every other question the check read (§5), and the current question definition.
- **Automatic grade:** written by the grade stage. **Manual grade:** written by a
  grader, recording which parse content they were looking at.

| Situation | Shown | Counts toward totals |
| --- | --- | --- |
| Nothing exists | Not started | No |
| Artifacts exist, parse not verified | Proposed grade, marked unverified | No |
| Parse verified, a parse the check read not verified (or missing) | Proposed grade, waiting on that question | No |
| Parse verified, every parse read verified, evidence current, grade decided | The grade | Yes |
| Parse verified, every parse read verified, evidence current, grade undecided | Needs review, with the reason | No |
| Evidence computed from an older parse (its own or one it read) or definition | Outdated; old evidence still visible | No |
| Manual grade, parse unchanged since | The grade, marked manual | Yes |
| Manual grade, parse changed since | The old decision, marked outdated | No |

Further rules:

- Verification approves content; it does not change who wrote it. Editing a
  parse clears its verification. Rerunning a check does not.
- A manual grade is authoritative even if earlier artifacts still carry review
  markers, and may be recorded when no evidence exists at all.
- With a multi-part result, each part is decided independently. Deciding one
  part does not approve another.
- Supporting work on the page is never transcribed or checked. Graders judge it.
- Status always comes from these rules, never from sniffing evidence contents.
- A check that read nothing of another question does not depend on it.
- A manual grade is recorded against the question's own parse only, not the
  parses its check read.

**Protection.** An artifact is protected if a grader wrote it, edited it or
verified it, and a manual grade is always protected. Batch runs skip protected
artifacts by default. A deliberate single "run this check" replaces even a
manual grade, and the replaced row goes to history.

**Automatic recheck.** Saving or verifying a parse runs the check if the existing
grade is automatic or missing. It never replaces a manual grade.

## 8. Storage

**Decided:** SQLite, one file per workspace, keyed rows.

```sql
create table artifact (
  submission   text not null,      -- or a source id, for its split plan
  question     text not null,      -- '' for per-submission rows
  kind         text not null,      -- a stage name; 'pages', 'layout', 'identity', 'who', 'split'
  body         text,
  origin       text not null,      -- 'auto' | 'manual'
  author       text,
  updated_at   text not null,
  rev          integer not null,   -- from one workspace-wide counter; never reused
  inputs       text,               -- JSON: upstream {kind: rev}, parses read {question: rev}, definition hash, code hash
  verified_rev integer,            -- equals rev when a human approved this content
  verified_by  text,
  primary key (submission, question, kind)
);
create table history (...);        -- same columns plus replaced_at; written on overwrite
create table submission (id text primary key, name text, email text, pdf text, student text);
create table setting (key text primary key, value text);        -- workspace defaults, roster, counters
create table override (path, value, author, updated_at, rev);   -- live edits of exam data (§15)
create table override_history (...);                            -- plus replaced_at
create table source (id, file, name, sha256, pages, added_at);  -- intake: scanned stacks
create table made (submission, source, pages, orient, made_at); -- intake: what each PDF holds
create table memo (submission, key, value);                     -- model readings (printed fields, landmarks, boxes)
```

- Every read on the normal path is a keyed lookup on `artifact`. History is
  written but only read for undo; it can be purged at any time.
- "Outdated" and "verified" are integer comparisons.
- All writes carry the `rev` the writer last saw. A mismatch is rejected and the
  browser keeps its draft. This replaces file locks and content hashing.
- A check computes outside any transaction, then publishes in one transaction
  only if the revs it read are still current; otherwise the result is discarded.
- Record `author` on every manual write (from the authenticating proxy when
  deployed, otherwise a local name).
- The implementer may instead keep an append-only log with the `artifact` table
  as its cache. It is the same design; choose whichever is simpler to get right.

**Definition and code changes.** `inputs` records a hash of the question's
definition as the stage saw it, so editing a key outdates exactly the checks
that used it. It also records a hash of the exam package's source. Recommended:
a code change is surfaced as a filterable "checked with older code" flag and a
one-click rerun, but does not by itself stop a grade counting. (An earlier
grader invalidated everything on any source edit and it was a constant
nuisance.) **Open** — confirm with the user once they have used it.

Scanned PDFs stay on disk. Rendered page images are cached on disk, keyed by
the PDF's identity, the page and the resolution.

## 9. Running work

- **Single run:** one stage, or one stage and everything after it, for one
  submission and question.
- **Batch:** a selection of submissions, questions and stages. Always previewed
  first: the preview lists what will run, what is skipped as protected or
  already present, and what is blocked by a missing input. The run is refused
  if anything relevant changed since the preview.
- **Order:** each stage finishes across the whole batch before the next begins,
  with a configurable number of parallel tasks inside a stage. A failure stops
  later stages for that submission and question only.
- **Missing-only mode** fills gaps without touching anything that exists.
- **Cancellation** stops queued work and lets running tasks finish.
- **Isolation:** checks run in a subprocess with a timeout (default 60 s,
  per-question override — **Decided**). A timeout or resource limit yields an
  undecided result, never "incorrect". Model stages and rendering do not need a
  fresh interpreter per task; avoid process-per-stage overhead.
- A stage that fails leaves the previous artifact intact.

## 10. Model client

- OpenAI-compatible chat completions over loopback HTTP only. Reject any other
  host, credentials in the URL, proxies and redirects.
- A fixed system preamble says the supplied images and text are data, not
  instructions, and that the model must not solve, grade or complete answers.
  The stage's prompt follows it.
- Temperature 0. Defaults that worked: 4,096 output tokens, 120 s timeout,
  reasoning off, with an explicit per-run "retry with reasoning" for one stage.
- Retry HTTP 500 and 503 twice, after 10 s and 20 s. Fail immediately on
  anything else. A response that did not stop normally, or is empty, is a
  failure, not an artifact.
- When the expected output is JSON or code, strip exactly one whole-response
  Markdown fence. Never extract a block from surrounding commentary.
- Diagnostics on failure are structural only (status, finish reason, lengths,
  token counts). Never log or display prompt or response text.
- Probe the endpoint at startup. If it is absent, model features are hidden and
  refused server-side; everything else (editing, verifying, checking, grading)
  still works.

## 11. Pages, regions and scan layout

- Page numbers in an exam definition are **printed exam pages**. A workspace has
  a default number of prepended cover pages (scanning services add them), and
  each submission can override it. The exam definition may supply the initial
  defaults for cover pages and the scan window; the workspace's saved settings
  take precedence once they exist.
- A question names the `pages` that hold its answer (what the model sees).
- The printed page is usually reduced and inset within the scanned page. A
  **scan window** (left, top, right, bottom, per-mille of the scanned page) maps
  printed-page coordinates onto the scan. There is a workspace default and a
  per-submission override. All regions in an exam definition are in
  printed-page coordinates; the library applies the window and adds padding.
- Each question's selected pages can be overridden per submission, with a note,
  for answers that continue onto extra-space pages. The note is passed to the
  model.
- Render with PDFium at about 160 DPI. PDFium must not be used from several
  threads at once; confine it to a worker. Cache rendered pages, and cut crops
  from the cache.
- The same regions serve three purposes: crops shown beside the grader's input,
  crops sent to the model for printed fields, and (optionally) cropped transcription.
  Whether transcription sees whole pages or crops is a per-stage option;
  whole pages is the proven default.

## 12. The standard library

The inclusion test (**Decided** in spirit): something belongs in `std` if several
unrelated exams would use it unchanged, and it must be built from public pieces
so that a near-miss can be reassembled instead of rewritten. When in doubt,
leave it out and let the exam package write it.

| Module | Contents |
| --- | --- |
| `std.stages` | `transcribe`, `parse_math`, `parse_pseudocode`, `read_fields`, `check_math` (with `by=`, a key per value of a field), `check(fn)`, `grade(fn)`; bundles `math(...)` and `pseudocode(...)` that return lists of these. |
| `std.fields` | A field's value in a check (`value(label, question=)`, recorded through `ctx.read`) and fields that must agree (`agree`), else `Undecided`. |
| `std.prompts` | Default prompt texts as values, and small functions that generate prompt fragments. |
| `std.math` | A restricted expression parser feeding SymPy (expressions are data, never `eval`); equality under declared symbol assumptions; choice and text normalization; a key entry may be an expression string, a choice set, or a function `answer -> outcome`. |
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
- The app owns the frame: grid, navigation, scan and crop views, stage cards,
  edit/verify/decide controls, runs, settings. Exams own only evidence blocks.

Screens for the first version:

- **Grid:** one row per submission, one column per result (a question with no
  result has none; its readers show that they wait for it). Shows
  the focused set (below); text search; sort; totals. Previous/next
  navigation follows the focus, search and sort.
- **Filter page:** the focused set (`core/focus.py`). One card per question with
  its facets: grade value and workflow state (built in, from the workflow
  rules), the value of each printed field (built in), plus facets declared by stages (`Stage.facets`) and questions
  (`Exam.question(..., facets=)`), e.g. the standard check's normalized answer
  per field and the comments in parsed pseudocode. OR within a facet's values,
  AND across facets; counts are over the other facets' selection. The focus is
  per browser (localStorage) and shareable as `?focus=` in a link.
- **Question page:** the parsed answer as a form (one input per field, with its
  scan crop beside it; a printed field is marked "printed") or a code editor
  (with the answer areas beside it); verify; evidence blocks; the proposed grade
  with a control to decide it; "waits on" with a link to each question whose
  parse the check read and that is not verified; run controls; the answer key
  the printing picks, collapsed.
- **Runs:** scope, stage checkboxes, options, preview, progress, stop.
- **Settings:** per-browser keys and appearance first, then workspace defaults (cover pages, print area)
  last.

Details that graders relied on and should survive: keyboard navigation between
students that keeps the current question; keys for verify, edit and save; a
separate PDF window that follows the current student and question; drafts that
are never silently discarded; an adjustable crop size.

### Words and colours on screen

One word per idea, everywhere a person can see it (labels, titles, messages,
docs). Internal names (`needs_review`, `core/focus.py`) may differ.

- **submission**: one student's paper. Never "exam" for that; the exam is the exam itself.
- **verify**: a person agrees with a proposal; before, **unverified**; after, **verified**.
- **manual**: decided or entered by a person (never "by hand"); one plain look everywhere ("manual · who").
- **undecided**: no grade yet, the glyph `?` (never "not determined", "needs review", "unresolved", "open").
- **filter**: the narrowed set of submissions (never "focus", "like this").
- **print area**: where the printed page sits on the scanned page (Settings, a question's pages panel).
- **read pages** / **read names**: what the model does on Scans; **pages read** after; **N failed** when a call fails.
- **not read yet** (nothing asked), **empty** (asked, the box had nothing), **N lines unclear** (a roster).
- **put back** (a dropped page), **unverify**, **guess again**, **join with next**, **type name**.

Buttons: only the one primary button of a panel is capitalised; the rest are
lowercase. Key hints come from the live key map, never from text.

Colour: a person is asked for exactly one thing, and **one yellow** (`--unv-bg`)
means it: unverified, wherever it appears (a grid cell, the state pill, a tag,
a needs-review cell, which shows as unverified with `?`). Tags: **red** = a
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
| Live editing of exam data (keys, regions, prompt notes) | **Built** (`core/overrides.py`, the Exam page and the question page's editors). Plain data addressed by path, stored in the `override` table with history; an edit outdates exactly the checks whose stage hash it changes and rechecks them. |
| Live-editable logic (small rules) | Grade rules are already just functions of evidence. A restricted interpreter (the pseudocode interpreter with a rules profile, or an embedded hermetic language) can supply such a function from stored text. |
| Filter and search facets (group by normalized answer; search comments in parsed code) | **Built** (`core/focus.py`). A facet is a small function from stored rows to values, computed on read (one query per question and kind, JSON paths so plot blocks are not loaded) and cached by the store's version. |
| Static student view | Evidence blocks carry an audience mark; a question may supply `feedback(evidence, grade)`. The view is a second renderer over stored rows. |
| Several exams in one process | No module-level state; exam packages loaded under distinct names. |
| "Review me later" flag (a TA marks an answer for the professor to look over) | Not built. The glyph `!` is reserved for it in the grid and on the question page; `?` means only "a person must decide". The flag would be a small manual artifact per submission and question, with author and an optional note, and a built-in facet so the filter page can gather flagged answers. It must not affect whether a grade counts. |
| A pluggable interpreter back end (vectorized, masked execution) | Not built; the closure interpreter is fast enough so far. The route: keep one front end (parse, whitelist, validate), expose the validated tree, and route operators through an overridable table; a masked executor is then a second back end. |

## 16. Intake: scans in, submissions out

Examinator takes stacks of scans (many students per PDF) and writes one PDF per
submission into `pdfs/`; everything downstream is unchanged.

- **Sources.** Each scanned PDF is kept unchanged as `sources/ID.pdf`, ID the
  start of its SHA-256 (so adding it twice is noticed); a `source` table lists
  them. CLI `examinator intake`, or a streamed upload on the Scans page.
- **Split plan.** One artifact per source, `(source id, '', 'split')`, so it has
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
  The plan holds one for the source and optionally one per submission (the
  submission's wins); per-page orientation would be one more optional field.
  The page shows sheets turned in the web layer (`/sheet/...?o=`), landmark
  crops are cut from the turned sheet, and accepting writes pages upright.
- **Accepting** writes changed submissions (pages or orientation) with
  `pdf/pages.py`: an upright sheet is copied untouched by PDFium; a turned one
  is baked as a new page holding one full-page JPEG (quality 95) shaped like
  the image, so every written page is a plain scan for the renderer's fast
  path. A `made` table records each submission's source sheets and their
  orientation. Ids are opaque (`s0001`), allocated once, never reused.
  Accepting marks every start confirmed. An entry left with no pages removes
  its submission. A submission that would change or disappear while it has
  grading work (any artifact outside `workflow.NOT_WORK`) is refused with the
  list (`Busy`); a forced accept keeps the work and clears that submission's
  model memo. PDFs are written to temporary files and moved into place only
  after the store has committed, so a plan edit in between changes nothing.
- **Who** (`core/identity.py`). A roster is optional. It is stored as a
  setting, `[{'name', 'id', 'email', 'key'}]`, read from a CSV/TSV with a
  header or from plain lines (name and ID, comma or tab, either order; the ID
  is the part with no spaces and a digit). A preview (count, first rows,
  unreadable lines) comes before saving.
  - **Declared boxes**: `Exam(identity=[Identity(name, region, ask, roster)])`,
    shaped like a printed field, but about handwriting (`IDENTITY_PROMPT`); `roster`
    is the roster field the reading is compared with (`'name'`, `'id'`,
    `'email'`, or None to show only); boxes sharing a field are joined (first
    and last name). The library names no field beyond those three.
  - **Reading**: per written submission and box, through the model, memoised
    per box (region and question), scan window and cover pages. Stored as the
    artifact `(submission, '', 'identity')`, `{'fields', 'basis'}` (history like
    any artifact; unchanged readings are not rewritten); a reading under an old
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
    dropped. Computed on read, cached by reading revision and roster.
  - **Picking**: every submission is named by its pick `(submission, '', 'who')`
    (`{'name', 'id', 'email', 'key'}`), written by a person against the
    revision they saw. One programmatic way in, for PDFs handed in already
    split: a file name carrying exactly one roster ID as a whole run of
    letters and digits (`identity.by_file`, on `sync` and when the roster is
    saved) becomes the pick (origin `'auto'`) of a submission that never had
    one; it shows on the Names tab as verified and is changed there like any
    other. The
    submission row carries the name, email and the student ID (`student`).
    The Names tab steps through unnamed exams, most doubtful first (V take,
    A/D other proposals, W/S back/skip, E type), and lists roster entries
    with no exam.
- A pre-split PDF that came in sideways could be fixed by adding it as a
  source of one submission (`length` = its pages), at the cost of a new id.

## 17. Testing

- Unit tests for the workflow rules, one per table row and further rule in §7
  (and §5: reading another question's parse).
- Store tests for concurrent edits, discarded stale checks and history.
- Toolkit tests on synthetic inputs only.
- A scripted fake model that returns canned text per (stage, question), so the
  whole pipeline runs deterministically offline.
- A synthetic-workspace generator and a real-browser smoke test over it.
- The two overfitting guards described in the brief: no exam identifiers in the
  library, and the toy exam.
- A test that no answer-key text appears in any prompt the pipeline builds.
- Opt-in live tests against the loopback model, on blank or synthetic material
  only, writing outside the repository.
