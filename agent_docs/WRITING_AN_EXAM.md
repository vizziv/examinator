# Writing an exam package

For people and agents who write the grader for one exam. Read this, then
`exams/toy_exam/` (two files, about 70 lines). You should not need library source;
where this guide names a library file, read only its docstring.

**Student data boundary.** Write and test an exam package with the instructor's
blank PDFs and synthetic fixtures only. Never open real scans, transcriptions,
parses, feedback, model logs or caches, or a real workspace (`pdfs/`,
`exam.sqlite`, `cache/`, `exports/`). Tests use the scripted fake model.

## 1. Where a thing belongs

| Layer | Who changes it | What goes there |
| --- | --- | --- |
| The app | graders, by hand | a misread answer, a variant, a grade, a region nudged on the scan, a key edited live |
| The exam package | you | pages, questions, regions, cues, keys, prompt notes, custom checks and grade rules, a course's pseudocode builtins |
| The library (`src/examinator`) | rarely, generically | mechanism only; knows nothing about any exam |

If only one exam needs something, it goes in that exam's package; if several
do, prefer a small generic hook over an option named after a course.

## 2. The smallest package that passes the self-test

```text
my_exam/            directory name = import name (a valid identifier, not clashing with any module)
  exam.py           builds and exposes `exam`
  fixtures.py       cases() -> list[Case], for `synth` and `selftest`
```

```python
# my_exam/exam.py
from examinator import Bool, Exam, Field, Region
from examinator.std import stages

exam = Exam('my_exam', title="My exam", pages=2)

exam.question(
    'slope', title="Slope", pages=[2], result=Bool(),
    fields=[Field('1', region=Region(2, 500, 100, 900, 200))],
    stages=stages.math(key={'1': '3/4'}))
```

```python
# my_exam/fixtures.py
from examinator.synth import Case


def cases():
    return [Case('right', answers={'slope': {'1': '0.75'}}, expect={'slope': True}),
            Case('wrong', answers={'slope': {'1': '4/3'}}, expect={'slope': False}),
            Case('blank', answers={'slope': {'1': None}}, expect={'slope': 'blank'})]
```

```sh
uv run --project /path/to/examinator examinator selftest /path/to/my_exam    # prints "ok"
```

`load_exam(path)` imports the directory as a package named after it and takes
`exam` from `__init__.py` or else `exam.py`. Helpers sit beside it and are
imported relatively (`from . import keys`). The exam id need not match the
directory name (the toy exam's is `'toy'`).

**`fixtures.py`** must define `cases()` returning `examinator.synth.Case`s:

| `Case` field | Meaning |
| --- | --- |
| `name` | the submission id |
| `answers` | `{question: parse body}`: `{label: text or None}` for math, a source string for pseudocode. `None` means blank |
| `variants` | `{question: variant}` that this fake submission was printed with |
| `readings` | `{cue name: reading or None}` to override what the scripted model reads (e.g. `None`: unreadable) |
| `expect` | `{grid column: value}`. The column is `'q'` for a single result and `'q.part'` for a part. A value, `'blank'`, or `None` (undecided, so the grade must not count) |
| `blank` | optional path of a blank PDF to draw on; otherwise placeholder pages are drawn |

The self-test draws each case, scripts the model to reply with exactly your
answers, runs every stage, verifies each variant and parse as a grader would,
and compares the grid. A parse with a `[REVIEW: ...]` marker cannot be verified,
so every part of that question expects `None`. Cover each variant, a wrong
answer, a blank answer and an unread cue (`exams/toy_exam/fixtures.py`).

## 3. Declaring the exam

### Exam and questions

```python
exam = Exam('toy', title="Toy exam", pages=4, scan_window=[50, 40, 950, 900], cover_pages=1,
            landmarks=[...], identity=[...])
exam.question('areas', title="Areas", pages=[2], result=Number(int, min=0, max=3),
              variants=[...], cues=[...], trust=1, fields=[...], stages=[...])
```

| Argument | Meaning |
| --- | --- |
| `Exam(pages=)` | printed pages in one copy of the exam |
| `scan_window` | where the printed page sits in a scanned sheet: left, top, right, bottom in per-mille of the scan. An initial default; the workspace's setting and per-submission overrides win |
| `cover_pages` | sheets the scanning service adds before page 1 (also an initial default) |
| `crop_pad` | per-mille padding around every crop (default 12) |
| `question(pages=)` | the printed pages holding the answer: what the model sees. A grader can change them per submission (an extra-space page, with a note that reaches the prompts) |

**Result specs.** `result=Bool()` or `Number(int | float, min=, max=)` for one
grid column, or a dict for several parts: `{'check': Bool(), 'shiny': Bool(counts=False)}`
gives the columns `q2.check` and `q2.shiny`. `counts=False` keeps a part out of
the total. Keep parts few: two outputs are usually two questions.

### Fields and regions

`Field(label, region=Region(page, left, top, right, bottom))` in **printed-page**
coordinates: per-mille of the printed page, origin top left, pages from 1. The
library maps regions onto each scan (scan window, cover pages) and pads them by
`crop_pad`. They feed the crops beside the grader's input, the cue crops sent to
the model, and `transcribe(crops=True)`. A field's page should be in the
question's `pages`; labels are the printed labels (the prompts use them).

Finding coordinates: render a blank page, read each box's pixels, divide by the
image size × 1000. Helpers keep rows of boxes short (`boxes()`, `band()` in
`exams/exam_03/exam.py`; `inset=` in `exams/exam_01/exam.py` for boxes closer
than the padding). To check, crop them from a blank and look:

```python
import numpy as np
from examinator.pdf.layout import Layout
from examinator.pdf.render import Renderer

renderer, layout = Renderer('/tmp/somewhere/cache', dpi=100), Layout()   # a blank: no cover, full window
crop = layout.crop(renderer.page(blank_pdf, region.page - 1), region)
assert np.asarray(crop.convert('L')).min() < 128      # ink: a box frame or printed text
```

(From `tests/test_exam_01.py`. For a synthetic scan use
`Layout(exam.cover_pages, exam.scan_window)` and `layout.scan_index(page)`.) In
the app, **regions** on a question page drags rectangles on a scan; the **Exam
data** page lists the changes as Python lines to copy into the package.

### Variants and cues

A question with several printings lists `variants` and the `cues` that tell
them apart. Variants are usually covert: a cue reads **printed** text that
differs between printings.

```python
cues=[Cue('areas_side', region=Region(2, 0, 60, 1000, 240),
          ask="the printed side length of the square, as a number",
          reads={'2': 'red', '3': 'green', '5': 'blue'})]
```

- `ask` finishes the sentence "Report: ..." in a fixed prompt about printed
  text. The `reads` keys are listed to the model as expected values. They
  identify a printing, so they are not keys.
- Readings are normalised before lookup (case, spaces, one layer of brackets or
  quotes, a trailing `.` or `%`). Give several readings for one variant when
  models write it differently (`{'r+1': 'original', '1+r': 'original', '-r': 'alternate'}`).
- Every cue that reads removes the variants it does not match. The variant
  resolves when one remains and at least `require` cues agree (default: all of
  them). Nothing is guessed: unresolved goes to a person, and a check that
  `uses_variant` stays undecided until then.
- `trust` (default 2) is how many agreeing cues let a resolved variant count
  without a person verifying it. With one cue, a resolved variant waits for a
  person unless you set `trust=1` (the toy exam). Real exams use two cues per
  question, often one from a neighbouring question's pages (Exams 1 and 3).
- A cue used by several questions (same name) is read once per submission.
- `Cue(name, fn=callable)` computes a variant (or `None`) from `ctx` when a map
  cannot, e.g. by combining other cues' votes with
  `examinator.core.variants.vote(ctx, cue)` (`Combine` and `printing()` in
  `exams/exam_02/versions.py`).

Keep the table of printed versions (alt index → question → variant) in
`versions.py`, with an environment variable naming the blanks' directory, so
tests can compare cues with the blanks' text and skip without them.

### Landmarks and identity boxes (intake)

Needed only when scans arrive as stacks of many students. Both are declared on
the `Exam`, like a cue (a region and a question for the model):

```python
landmarks=[Landmark('title', region=Region(1, 300, 90, 700, 150),
                    ask="the title printed in this box", reads={'TOY EXAM': 1}),
           Landmark('page_number', region=Region(1, 900, 940, 990, 990),
                    ask="the printed page number, as a number")],
identity=[Identity('name', Region(1, 100, 200, 800, 280), roster='name', ask="the student's full name"),
          Identity('id', Region(1, 100, 330, 600, 410), roster='id', ask="the student ID number")]
```

A landmark's `reads` maps a reading to a printed page; without `reads` the
reading is the page number (from `region.page` on). An `Identity` box is read
for handwriting and compared with the roster field `roster` (`'name'`, `'id'`,
`'email'`, or `None` to only show it); boxes sharing a field are joined. People
confirm every split and name on the Scans page.

## 4. Stages

A question's `stages` is a list. Each stage writes one artifact whose kind is
the stage's name:

| Stage (factory) | Reads | Writes | Notes |
| --- | --- | --- | --- |
| `variant` (added automatically when `variants` is set) | the cue crops | `{'cues', 'candidates', 'agree', 'resolved', ...}` | model; editable |
| `stages.transcribe(prompt=, notes=, crops=False)` | the page images | `str` | model; editable |
| `stages.parse_math(symbols=, functions=, notes=)` | `transcript` | `{label: str or None}` | model; editable; this is what graders verify |
| `stages.parse_pseudocode(entries, language=, notes=, then=)` | `transcript` | source `str` | model; editable; `then(source)` rewrites deterministically |
| `stages.check_math(key, symbols=, functions=, timeout=)` | `parse` | evidence | local and isolated |
| `stages.check(fn, inputs=('parse',), uses_variant=, timeout=, isolate=True)` | `inputs` | evidence | your check |
| `stages.grade(rule)` | `evidence` | one grade per result part | pure and in-process |

Bundles return such lists:

```python
stages.math(key, symbols=, functions=, notes=, grade_rule=math.all_correct, timeout=)
#   == [transcribe(notes), parse_math(...), check_math(...), grade(grade_rule)]
stages.pseudocode(entries, check_fn, language=, notes=, then=, grade_rule=, timeout=, uses_variant=)
#   == [transcribe(TRANSCRIBE_PSEUDOCODE), parse_pseudocode(...), check(check_fn), grade(grade_rule)]
```

Assemble the list by hand when a bundle does not fit (`sampler()` and question 3
in `exams/exam_01/exam.py`). `stages=[]` makes a question graded by hand only.

What the `Stage` flags mean in practice:

- `editable`: graders may edit it; editing clears verification, and saving or
  verifying a parse reruns the check. `uses_model`: blocked without a model.
- `isolate`, `timeout`: a worker process with a wall-clock limit (default 60 s).
  A timeout or any exception from a check makes every part undecided, with the
  error kept for graders. There `ctx.pages()`, `crop()`, `model()` raise `Unavailable`.
- `uses_variant`: the stage gets `ctx.variant`, is undecided while it is
  unresolved and outdated when it changes. `check_math` sets it for a per-variant
  key (`stages.per_variant(key)`); a custom check must ask for it.
- `accepts_markers`: stages refuse inputs carrying `[REVIEW: ...]` unless set;
  `check_math` sets it and leaves the marked field undecided.
- `facets`: filters for the app's filter bar. `check_math` adds one per answer
  label, `parse_pseudocode` makes comments searchable; question-level ones go in
  `exam.question(..., facets=[...])` (`examinator.core.focus.Facet`).

**Prompts.** The defaults are values in `examinator.std.prompts`. Add to them
with `notes="..."` (or concatenation); do not rewrite them. Keep notes in
`notes.py` and about notation and faithfulness only. A course's own wording
belongs there, not in the library's defaults: Exams 1-3 add
`FORMULAS = "Do not repair missing probability formulas."` to their pseudocode notes.

## 5. Keys and grade rules

`check_math` compares each parsed field with its key entry and records an
**outcome**: `'correct'`, `'incorrect'`, `'blank'`, or `None` (undecided).
Undecided is never used for a demonstrated mismatch.

| Key entry | Meaning |
| --- | --- |
| `'2*x**2'` | an expression, equal for all values the symbols allow (a symbolic proof for correct, a clear numeric difference for incorrect, otherwise undecided) |
| `math.Choice('even', 'E', 'option 1')` | any of these texts, after folding case, spaces, quotes and brackets |
| `math.Approx(0.135, tol=0.001)` or `Approx(0.05, rel=0.02)` | a number within an absolute or relative tolerance |
| `fn(answer) -> outcome or (outcome, note)` | anything else. Give it a `label` attribute so graders see the key (`Number` in `exams/exam_02/keys.py`) |
| `('3', '4'): fn(answers)` | a group: `fn` gets a tuple of the fields' answers (blank is `None`) and returns one result or a list of results, one per field |

```python
def roots(answers):
    """Both roots, in either order: one result for the pair."""
    a, b = answers                     # a blank field arrives as None
    if a is None or b is None:
        return 'incorrect', "one root missing"
    got = {math.parse_expr(a), math.parse_expr(b)}      # math.Unsupported -> undecided
    return 'correct' if got == {math.parse_expr('-2'), math.parse_expr('3')} else 'incorrect'

stages.math(symbols={'t': 'positive'}, key={
    'a': {'1': '2*t', '2': math.Approx(0.135, tol=0.001), ('3', '4'): roots},
    'b': {'1': '3*t', '2': math.Approx(0.05, rel=0.02), ('3', '4'): roots}})
```

- **Per-variant keys:** if every value of `key` is a dict, the key is chosen
  by variant. The variant names must match the question's `variants`.
- **Symbols:** `symbols=['x']`, or a dict of assumptions
  (`{'x': 'positive'}`, also `negative`, `nonnegative`, `nonpositive`, `nonzero`,
  `real`, `integer`, `rational`). `'positive indexed'` declares `s[i]`, where
  each distinct index is its own unknown (`exams/exam_01/blanks.py`). An
  undeclared name is notation the parser does not accept, so the field is
  undecided.
- **Functions:** `None` allows `sqrt exp log ln sin cos tan abs min max`. A
  list allows only those. `[]` allows none, so `a(1-b)` is a product (and the
  parse prompt says so).
- Juxtaposition multiplies where it has only one reading (`2x`, `ab`).
  `a/2b` and `2^ab` are refused as ambiguous. Decimals are exact.
- A key that does not parse raises when checked. A custom entry should parse its
  key at definition time (`number()` in `exams/exam_02/keys.py`).

**Grade rules** map evidence to grades: `rule(evidence) -> value`, or a dict of
part → value for a multi-part result. `evidence` is an `EvidenceView`
(`.data`, `.fields`, `.outcome(label)`, `.outcomes()`). A value is anything the
part's spec accepts: `True`/`False` or a number, `'correct'`/`'incorrect'`/`'blank'`/`None`
for `Bool`, or `Grade(value, flags=('blank',), reason="...")`. `None` is
undecided (shown as `?`), and the `reason` tells graders why. A part missing
from the dict, or a rule that raises, is undecided.

```python
def count_correct(evidence):                     # exams/toy_exam/exam.py
    outcomes = evidence.outcomes()
    if None in outcomes:
        return None
    if all(o == 'blank' for o in outcomes):
        return Grade(0, ('blank',))
    return outcomes.count('correct')
```

The default is `math.all_correct` (a mistake outranks an undecided field, which
outranks a partly blank answer). To copy: k-of-n credit (`at_least` in
`exams/exam_01/rules.py`), marks over chosen fields (`tiers` in
`exams/exam_02/rules.py`, `marks` in `exams/exam_03/exam.py`), credit a person
judges from the work (`with_work`). Keep rules pure and fast.

## 6. Custom checks

```python
def check(ctx) -> Evidence: ...
stages.check(check, uses_variant=True, timeout=60)
```

| `ctx.` | |
| --- | --- |
| `input('parse')` | the body of an upstream artifact named in `inputs` |
| `variant` | the resolved variant (only with `uses_variant=True`) |
| `params` | `{key: str}` typed by a grader for one deliberate rerun ("run with `huge_constant=10`"). The library gives it no meaning (`custom_value` in `exams/exam_03/weibullish.py`) |
| `question`, `submission`, `note`, `rerun` | the definition, the id, the page note, and whether the artifact already existed |
| `pages()`, `crop(region)`, `model(...)`, `remember(...)` | for model stages only. Not available inside an isolated check |

Return `Evidence(data, blocks)` from `examinator`. Put
`{'fields': {label: {'outcome': ..., 'note': ...}}}` in `data` if you want
`EvidenceView.outcome()` and the standard rules. Otherwise put anything
JSON-serializable there (plain Python numbers, not NumPy scalars), and your
own grade rule reads it. Blocks come from `examinator.std.blocks`:

| Block | |
| --- | --- |
| `Plot.cdf(samples, cdf)`, `Plot.hist(samples, pmf= or density=)`, `Plot.scatter(x, y)`, `Plot(title, [Series(...)])` | stored as data and drawn in the browser. Put plots first |
| `Table(columns, rows)`, `Note(text, tone='info' or 'good' or 'bad' or 'warn')`, `Code(text, highlight=[lines])` | |
| `Details(summary, *blocks)` | collapsed. Use it for sample dumps, sources and tracebacks |
| `Raw(html)` | escape hatch for SVG or HTML. Scripts do not run |

Every block takes `audience='grader'` (hidden from the future student view): use it for keys and sources.

What to raise: `Blocked("no parse")` when the stage cannot run yet (nothing is
written); `BadOutput` from a model stage whose reply has the wrong structure
(the message never quotes the reply). Anything else a check raises becomes
undecided, with the error shown to graders: decide outcomes instead of crashing.

**Run-time policy for checks that run student programs:**

- It ran and failed (an exception, a wrong type, a bad value): **incorrect**.
- It ran out of budget (steps, depth, size, time): **undecided**. A grader may
  want it run longer.
- It could not be run or parsed (source outside the language): **undecided**.

**Safety rules (not negotiable):**

- Student-derived text is never executed as Python. Math goes through
  `math.parse_expr`, and pseudocode goes through `pseudocode.parse` and
  `Program.sample`. No `eval`, `exec`, `sympify` or `import` of an answer.
- Answer keys never appear in a prompt. Copy `test_no_answer_key_in_any_prompt`
  from an example exam: it greps every prompt the fake model received for the keys.
- Errors and logs never contain prompt or response text.

## 7. Pseudocode and the stochastic kit

The pseudocode language (`examinator.std.pseudocode`) is a bounded
Python-syntax subset with its own interpreter. Start from `BASE` (scalars,
`MATH`) or `WITH_COLLECTIONS` (lists, sets, comprehensions, `len`, `sum`) and
add a course's names with `with_builtins`; the parse prompt's description of the
language is generated from the builtins' docs.

```python
def spin(rng, n):
    return int(rng.integers(1, n + 1))

LANGUAGE = pseudocode.BASE.with_builtins(
    spin=pseudocode.Primitive(spin, "`spin(n)` is uniform on 1..n", group='Randomness', random=True))
```

Other options are `Language(collections=True, vectors=True, extra="...")`
(lists act as points), and `dataclasses.replace(...)` to change flags; see
`exams/exam_02/samplers.py`. Primitives use `numeric=True` for math functions,
and `Constant(value, doc)` for named numbers. A primitive raises
`pseudocode.Limitation` for "not the student's fault".

In a check: `program = pseudocode.parse(source, LANGUAGE)` (raises `Unsupported`),
then `program.is_blank('f')`, `program.has('g')`, `program.references(...)`,
`program.has_loop(...)`, and
`program.sample('f', args, n=, seed=, deadline=, primitives=, constants=)`.
Sampling raises `Failure` (the run failed: incorrect) or `Budget` (undecided).
Pass `primitives=` to substitute a trusted reference for a helper the student
also wrote (`reference_weibull` in `exams/exam_03/weibullish.py`).

**`examinator_kits.stochastic`** (optional) is for questions that ask for a
**sampler**: random builtins (`primitives.UNIF`, `COIN`, `die(start=)`),
goodness-of-fit tests with plots (`fit.support`, `ks`, `chi_square`,
`projected_ks`), and scaffolding that applies the run-time policy above and
builds the evidence (`checks.draw`, `Part`, `check_source`; pattern in the
`checks.py` docstring). Examples: `exams/exam_02/normal.py` (short),
`exams/exam_03/weibullish.py`, `exams/exam_03/point_process.py`. Seeds, sample
sizes and targets belong to the exam.

## 8. Iterating

Workspaces always live **outside** the repository. For a package outside the
repository, run the commands as `uv run --project /path/to/examinator examinator ...`.

```sh
uv run examinator selftest my_exam [WS]                       # fixtures end to end; "ok" or the mismatches
uv run examinator synth my_exam /tmp/x/ws                     # synthetic workspace with a scripted model
uv run examinator run my_exam -w /tmp/x/ws --fake             # run stages (-s SUB, -q Q, --stage, --dry-run)
uv run examinator status my_exam -w /tmp/x/ws --fake          # the grade grid
uv run examinator serve my_exam -w /tmp/x/ws --fake           # the app: verify, edit, decide, regions
uv run examinator synth my_exam /tmp/x/st --stacks            # stacks of scans for intake (landmarks, identity)
uv run examinator intake my_exam -w /tmp/x/st --fake --roster /tmp/x/st/roster.csv /tmp/x/st/incoming/*.pdf
```

Add `tests/test_<exam>.py` like `tests/test_exam_03.py`: the self-test, the
definition's shape, checks called directly on a parse body (`outcomes()` in
`tests/test_exam_01.py` builds a `Context` by hand), one test per tricky key or
rule, and the no-key-in-prompt test. Tests that need the blanks skip without them.
Live-model runs are opt-in (`pytest -m live`), on blank or synthetic material
only, writing to a new directory outside the repository.

**Done when:**

- [ ] `selftest` prints `ok`, with cases for every variant, a wrong answer, a blank answer and an unread cue
- [ ] every region lands on its box or printed cue on every blank (crops checked by eye or by the ink test)
- [ ] the cues' readings, checked against each blank's text, give the right variant for every printed version
- [ ] the no-key-in-prompt test passes
- [ ] every custom key entry and grade rule has a unit test, including its undecided case
- [ ] the library is unchanged, or changed only generically, with `uv run pytest` passing

## 9. Common mistakes

- **Exam words in the library.** A question id, topic or course name in
  `src/examinator` fails `test_library_contains_no_exam_identifiers`. Put it in
  the package.
- **Keys in prompts:** a note that hints at the answer, or a key pasted into `ask`.
- **Regions off the printed page.** Coordinates are per-mille of the printed
  page, not of the scan and not pixels. A field on a page outside `pages` is
  never sent to the model. Boxes closer than `crop_pad` show their neighbours.
- **A cue that cannot tell variants apart:** the same text on every printing, a
  band that also covers answer boxes with the same letters (comment above `BIT2`
  in `exams/exam_03/exam.py`), or a variant with no reading in some map cue's
  `reads` (it can never resolve, and the self-test cannot draw it).
- **One cue with default `trust`**, or **a custom check without
  `uses_variant=True`** (it sees `ctx.variant is None` and is not outdated when
  the variant changes).
- **Crashing instead of deciding.** An uncaught exception is undecided, so a
  failing student program shows `?`. Catch `Failure` and return `'incorrect'`.
  Conversely, notation the parser rejects or an inconclusive comparison is
  `None`, never `'incorrect'`.
- **Callables where data would do.** Text keys, `Choice` entries, cue `reads`,
  regions and notes can be edited live in the app; a key inside a function cannot.
- **Fixtures derived from the keys**, so a wrong key passes its own self-test.
  Write answers out independently, in other notations too (`OTHER_FORMS` in
  `exams/exam_03/fixtures.py`).
