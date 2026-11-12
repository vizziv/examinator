# Writing an exam package

For people and agents who write the grader for one exam. Read this and
[`MODEL.md`](MODEL.md), then `exams/toy_exam/` (two files, about 60 lines). You
should not need library source; where this guide names a library file, read
only its docstring.

**Student data boundary.** Write and test an exam package with the instructor's
blank PDFs and synthetic fixtures only. Never open real scans, transcriptions,
parsed answers, feedback, model logs or caches, or a real workspace (`pdfs/`,
`exam.sqlite`, `cache/`, `exports/`). Tests use the scripted fake model.

## 1. Where a thing belongs

- **The app** (graders): a misread value, a grade, a region nudged on the scan
  (for everyone, or for one submission), a key edited live.
- **The exam package** (you): pages, questions, fields and regions, printed
  fields, readers' notes, keys, custom checks and grade rules, a course's
  pseudocode builtins.
- **The library** (`src/examinator`; rarely, generically): mechanism only. If
  several exams need something, prefer a small hook over an option named after a course.

## 2. The smallest package that passes the self-test

```text
my_exam/            directory name = import name (a valid identifier)
  exam.py           builds and exposes `exam`
  fixtures.py       cases() -> list[Case], for `synth` and `selftest`
```

```python
# my_exam/exam.py
from examinator import Exam, Field, Region
from examinator.std import math, readers

exam = Exam('my_exam', title="My exam", pages=2)

exam.question('slope', title="Slope", pages=[2],
              fields=[Field('1', Region(2, 500, 100, 900, 200))],
              read=readers.answers(),
              check=math.keyed({'1': '3/4'}))
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
imported relatively (`from . import keys`).

**`fixtures.py`** defines `cases()` returning `examinator.synth.Case`s:

| `Case` field | Meaning |
| --- | --- |
| `name` | the submission id |
| `answers` | `{question: {label: text or None}}` for the fields its reader reads (`None` is blank); for a question with one such field (pseudocode), the text itself |
| `printed` | `{question: {printed field label: what is printed}}`: which printing this case has. A reading of the field's `reads`, or the value it reads to; `None`: the model cannot read it |
| `expect` | `{grid column: value}`. The column is the question id when it has one check, else `question.check`. A value, `'blank'`, or `None` (must not count: undecided, or waiting for a person) |
| `blank` | optional path of the blank PDF of this printing to draw on; otherwise placeholder pages |

The self-test draws each case, scripts the model to read exactly your answers
and printed fields, reads every question, verifies every value as a grader
would (a value with a `[REVIEW: ...]` marker cannot be verified), runs every
check, and compares the grid. Cover each printing, a wrong answer, a blank
answer and an unread printed field (`exams/toy_exam/fixtures.py`). A case that
does not fit the exam is one line naming the case and the key.

**Imports** (everything an exam package needs):

```python
from examinator import Exam, Field, Printed, Region, Landmark, Identity, Check, Bool, Number, Grade
from examinator import Result, Judgement, Undecided, Facet
from examinator.synth import Case
from examinator.std import readers, math, fields, pseudocode, prompts
from examinator.std.blocks import Plot, Series, Table, Note, Code, Details, Raw
from examinator_kits.stochastic import primitives, fit, checks     # samplers (optional)
from examinator.core.runner import try_check                       # in your tests
```

## 3. Declaring the exam

```python
exam = Exam('toy', title="Toy exam", pages=4, scan_window=[50, 40, 950, 900], cover_pages=1,
            landmarks=[...], identity=[...])
exam.question('areas', title="Areas", pages=[2], fields=[...], read=..., check=... or checks={...})
```

| Argument | Meaning |
| --- | --- |
| `Exam(pages=)` | printed pages in one copy of the exam |
| `scan_window`, `cover_pages` | where the printed page sits in a scan (per-mille), and the sheets a scanning service adds before page 1. Initial defaults; the workspace's settings win |
| `crop_pad` | per-mille padding around every crop (default 12) |
| `question(pages=)` | the printed pages holding the answer: what the reader sees. A grader can change them per submission (an extra-space page, with a note that reaches the prompts) |
| `fields=` | the question's fields (§4) |
| `read=` | the reader of the fields that have no reader of their own (§5). Without one, a person types them |
| `check=` / `checks=` | one check (named `'check'`), or several by name: `checks={'answer': ..., 'work': ...}`. None: the question only holds fields (the exam's printing, which other questions read) |

## 4. Fields and regions

`Field(label, Region(page, left, top, right, bottom))` in **printed-page**
coordinates: per-mille of the printed page, origin top left, pages from 1. The
region feeds the crop beside the grader's input and the crop a printed field's
reader sees. It may be left out (`Field(label)`) and drawn in the app; a
printed field without a region is simply not read. Labels are the printed
labels (the prompts use them). An off-page region or a repeated label is a
`ValueError` naming the question.

Finding coordinates: render a blank page and divide each box's pixels by the
image size × 1000. Helpers keep rows of boxes short (`boxes()` in
`exams/exam_03/exam.py`; `inset=` in `exams/exam_01/exam.py` for boxes closer
than the padding). To check, crop each region from a blank
(`Layout().crop(Renderer(cache, dpi=100).page(blank_pdf, region.page - 1), region)`)
or test for ink as `tests/test_exam_01.py` does. In the app, **regions** on a
question page drags rectangles on a scan; the **Exam data** page lists the
changes as Python lines to copy into the package.

### Printed fields: which printing a submission has

```python
Field('side', Region(2, 0, 60, 1000, 240),
      read=Printed("the printed side length of the square, as a number",
                   reads={'2': 'red', '3': 'green', '5': 'blue'}))
```

- `ask` finishes the sentence "Report: ..." in a fixed prompt about printed
  text. The `reads` keys are listed to the model as expected readings: printed
  text, never keys. Readings are normalised (case, spaces, one layer of
  brackets or quotes, a trailing `.` or `%`); give several readings for one
  value when models write it differently. Without `reads`, the value is the
  reading itself.
- A reading that is absent, or not one of `reads`, becomes a value with a
  review marker. Nothing is guessed.
- With `reads`, an expected reading is **vouched for**: nobody need verify it.
  `Printed(..., verify=True)` makes a person verify it anyway.
- **Two shapes.** A question that needs its own printing holds the printed field
  itself (the toy exam's `side`). When printings switch groups of questions,
  put the printed fields in one question of their own with no checks,
  `exam.question('info', pages=[...], fields=[...printed...])`, declared first,
  and let the other questions' checks read it (Exams 1-3). Two printed fields
  that must agree catch a misreading (`fields.agree(...)`).

Keep the facts about the printed versions in `versions.py`, with an
environment variable naming the blanks' directory, so fixtures draw each case
on its blank and tests compare the printed fields with the blanks' text and
skip without them.

### Landmarks and identity boxes (intake)

Needed only when scans arrive as stacks of many students. Both are declared on
the `Exam`: `Landmark(name, region, ask, reads)` tells a scanned sheet's printed
page (`reads` maps a reading to a page; without it the reading is the page
number); `Identity(name, region, ask, roster)` is a box read for handwriting and
compared with the roster field `roster` (`'name'`, `'id'`, `'email'`, or
`None`). See `exams/toy_exam/exam.py`.

## 5. Readers

```python
read=readers.answers(symbols=['x'], functions=['sqrt'], notes="Angles are in degrees.")
read=readers.code(['tri'], language=LANGUAGE, notes=..., then=rewrite)
read=readers.code({'2.1': ['weibull'], '2.2': ['weibullish']}, language=LANGUAGE)   # one source per field
```

A reader reads all its fields of one submission in one go: transcribe the
question's pages, then turn the transcript into one value per field (text;
`''` is blank). The transcript is not stored. `symbols` and `functions` are
notation hints for the prompt. `then` rewrites parsed source deterministically
(`exams/exam_03/huge_constants.py`); the rewritten source is the value.

**Prompts.** The defaults are values in `examinator.std.prompts`. Add to them
with `notes=`; do not rewrite them. Keep notes in `notes.py` and about notation
and faithfulness only. A course's own wording belongs there, not in the
library's defaults.

## 6. Checks

A check is a function from field values to one grade and its evidence:

```python
def check(ctx):
    source = ctx.read('code')                       # this question's field; recorded
    side = ctx.read('side', question='info')        # another question's
    ...
    return Result('correct', fields={'code': Judgement('correct', note="3 tests")}, blocks=[Table(...)])
```

- `ctx.read(label, question=None)` returns the value (text). A field not read,
  or carrying a review marker, raises `Undecided`; the grade is undecided and
  waits for that field. Read only what you use: a field never read is no
  dependency, and the grade counts once every field read is verified.
- Return a `Result(grade, fields=, blocks=)`, or a bare grade. The grade is
  what the points accept: `True`/`False`, `'correct'`/`'incorrect'`/`'blank'`/
  `None` for `Bool`; a number for `Number`; or `Grade(value, flags, reason)`.
  `fields` are judgements shown beside the field: an outcome
  (`'correct'`, `'incorrect'`, `'blank'`, `None`), optionally `expected=` and
  `note=`. Blocks are shown below.
- Raise `Undecided("a few words")` when nothing can be decided. Anything else
  a check raises, and a timeout, is undecided, never incorrect (the error is
  shown to graders): decide outcomes instead of crashing.
- `Check(fn, Number(int, min=0, max=3), timeout=90)` sets the points and the
  time limit (default 60 s). `Bool(counts=False)` keeps a grade out of the
  total. `Check(None)` is decided by a person only.
- `ctx.params` is `{key: value}` typed by a grader for one deliberate run
  ("run with `huge_constant=10`"; `custom_value` in `exams/exam_03/weibullish.py`).
- A question with two grades has two checks. Each reads what it needs
  (Exam 2's `answer` and `check` for "answer + work", Exam 3's two-function
  questions); nothing reads a check.

### The keyed check: one line for short answers

```python
check=math.keyed({'1': '2*t', '2': math.Approx(0.135, tol=0.001), ('3', '4'): roots},
                 symbols={'t': 'positive'}, grade=math.at_least(2))
```

`math.keyed` compares each field with its key entry, judges each field, and
grades the outcomes with `grade` (default `math.all_correct`; `math.at_least(k,
required=())`; or any `fn({label: outcome}) -> grade`, as the toy exam's
`count_correct`).

| Key entry | Meaning |
| --- | --- |
| `'2*x**2'` | an expression, equal for all values the symbols allow (a symbolic proof for correct, a clear numeric difference for incorrect, otherwise undecided) |
| `math.Choice('even', 'E', 'option 1')` | any of these texts, after folding case, spaces, quotes and brackets |
| `math.Approx(0.135, tol=0.001)` or `Approx(0.05, rel=0.02)` | a number within a tolerance |
| `fn(answer) -> outcome or (outcome, note)` | anything else; give it a `label` attribute so graders see the key (`Number` in `exams/exam_02/keys.py`) |
| `('3', '4'): fn(answers)` | a group: `fn` gets the fields' answers (blank is `None`) and returns one result or one per field |

- **Keys that differ by printing:** with `by=`, every value of `key` is a key,
  and `by` picks one: a label of the question's own field (`by='side'`), another
  question's field (`fields.value('q5_conf', question='info')`), fields that
  must agree (`fields.agree(...)`), or any `fn(ctx) -> value` that raises
  `Undecided` when it cannot tell (`Printing` in `exams/exam_02/versions.py`).
  When the fields `by` names have `reads`, the key's names must be exactly their
  values (checked at definition).
- **Symbols:** `symbols=['x']`, or assumptions (`{'x': 'positive'}`; also
  `negative`, `nonnegative`, `nonpositive`, `nonzero`, `real`, `integer`,
  `rational`; `'positive indexed'` declares `s[i]`).
- **Functions:** `None` allows `sqrt exp log ln sin cos tan abs min max`; a list
  allows only those; `[]` none, so `a(1-b)` is a product.
- A check compares exactly the fields its key names, so a check about some of a
  question's fields has a key with only those (question 8 in
  `exams/exam_02/exam.py`).
- Credit that needs a person (work on the page) is a check with no function:
  `Check(None, Bool())`.

## 7. Pseudocode and the stochastic kit

The pseudocode language (`examinator.std.pseudocode`) is a bounded
Python-syntax subset with its own interpreter. Start from `BASE` or
`WITH_COLLECTIONS` and add a course's names with `with_builtins`; the prompt's
description of the language is generated from the builtins' docs.

```python
LANGUAGE = pseudocode.BASE.with_builtins(
    spin=pseudocode.Primitive(spin, "`spin(n)` is uniform on 1..n", group='Randomness', random=True))
```

In a check: `program = pseudocode.parse(source, LANGUAGE)` (raises
`Unsupported`, never runs anything), then `program.is_blank('f')`,
`has('g')`, `references(...)`, `has_loop(...)`, and
`program.sample('f', args, n=, seed=0, deadline=, max_steps=, primitives=, constants=)`,
which returns a list of `n` results and raises `Failure` or `Budget`.
`primitives=` substitutes a trusted reference for a helper the student wrote.

**Run-time policy** (the kit's `checks.draw` applies it): a run that failed
(`Failure` of any kind, a primitive's `Limitation`) is **incorrect**; out of
budget (`Budget`) is **undecided**; source outside the language
(`Unsupported`) is **undecided**. A person may decide otherwise.

**A complete pseudocode question:**

```python
TESTS = [(0, 0), (3, 6), (10, 55)]       # tri(n) = 0 + 1 + ... + n

def check_tri(ctx):
    try:
        program = pseudocode.parse(ctx.read('code'), pseudocode.BASE)
    except pseudocode.Unsupported as e:          # outside the language: a grader fixes the value
        return Result(None, reason=str(e))
    if program.is_blank('tri'):
        return 'blank'
    rows = []
    for n, want in TESTS:
        try:
            got = program.sample('tri', (n,), n=1, seed=0)[0]
        except pseudocode.Budget as e:           # out of budget: undecided
            return Result(None, blocks=[Table(['n', "expected", "got"], rows)], reason=f"out of {e.kind}")
        except pseudocode.Failure as e:          # ran and failed: incorrect
            got = f"failed: {e.detail}"
        rows.append([n, want, got])
    right = all(got == want for _, want, got in rows)
    return Result(right, {'code': 'correct' if right else 'incorrect'}, [Table(['n', "expected", "got"], rows)])

exam.question('tri', title="Triangular numbers", pages=[2], fields=[Field('code', Region(2, 100, 300, 900, 900))],
              read=readers.code(['tri']), check=Check(check_tri, timeout=30))
```

In fixtures, a pseudocode answer is the source as one string; `None` is blank.

**`examinator_kits.stochastic`** (optional) is for questions that ask for a
sampler: random builtins (`primitives`), goodness-of-fit tests with plots
(`fit`), and `checks.check_source(source, language, label, judge)`, which
applies the run-time policy and returns a `Result` from one `checks.Part`.
Examples: `exams/exam_02/normal.py` (short), `exams/exam_03/weibullish.py`.

**Safety rules (not negotiable):** student-derived text is never executed as
Python (math through `math.parse_expr`, pseudocode through `pseudocode.parse`;
no `eval`, `exec`, `sympify` or `import` of an answer). Answer keys never appear
in a prompt: copy `test_no_answer_key_in_any_prompt` from an example exam.
Errors and logs never contain prompt or response text.

## 8. Iterating

Workspaces always live **outside** the repository.

```sh
uv run examinator selftest my_exam [WS]                       # fixtures end to end; "ok" or the mismatches
uv run examinator synth my_exam /tmp/x/ws                     # synthetic workspace with a scripted model
uv run examinator run my_exam -w /tmp/x/ws --fake             # read, then check (-s SUB, -q Q, --step, --dry-run)
uv run examinator status my_exam -w /tmp/x/ws --fake          # the grade grid
uv run examinator serve my_exam -w /tmp/x/ws --fake           # the app
uv run examinator synth my_exam /tmp/x/st --stacks            # stacks of scans for intake
```

**Reading `status`.** A cell is the grade (`✓`, `✗`, a number, `blank`), or
`-` when undecided. A grade that does not count yet carries its state; the
total adds what counts, with `+?` while something does not. The states are
MODEL.md's: `unverified` (normal after `run`: a value it read is not verified),
`to_decide`, `not_run`, `outdated`, `decided`, `decided_outdated`, `graded`.

**`run` reads and checks every time** (the model is asked again; old values go
to history). Values a person set or verified are skipped unless
`--include-protected`; `--missing-only` reads only fields with no value and
runs only checks that are not current.

Add `tests/test_<exam>.py` like `tests/test_exam_01.py`: the self-test, the
definition's shape, checks called directly on values with
`try_check(exam, 'q2', 'check', {'q2/2.1.m': '1', 'info/...': ...})`, one test
per tricky key or rule, and the no-key-in-prompt test. Tests that need the
blanks skip without them.

**Done when:**

- [ ] `selftest` prints `ok`, with cases for every printing, a wrong answer, a blank answer and an unread printed field
- [ ] every region lands on its box or printed text on every blank
- [ ] the printed fields' readings, checked against each blank's text, give the right key for every printing
- [ ] the no-key-in-prompt test passes
- [ ] every custom key entry and grade rule has a unit test, including its undecided case
- [ ] the library is unchanged, or changed only generically, with `uv run pytest` passing

## 9. Common mistakes

- **Exam words in the library**: a test fails. Put them in the package.
- **Keys in prompts**: a note that hints at the answer, or a key in `ask`.
- **Regions in the wrong units**: per-mille of the printed page, not pixels.
- **A printed field that cannot tell printings apart**: the same text on every
  printing, or a band that also covers answer boxes (`q6_option_c` in
  `exams/exam_03/exam.py`).
- **Crashing instead of deciding**: catch `Failure` and return `'incorrect'`;
  notation the parser rejects is `None`, never `'incorrect'`.
- **Reading more than you use**: every field a check reads must be verified
  before its grade counts.
- **Callables where data would do**: text keys, `Choice` entries, `reads`,
  regions and notes can be edited live in the app; a key inside a function cannot.
- **Fixtures derived from the keys**, so a wrong key passes its own self-test.
  Write answers out independently, in other notations too.
