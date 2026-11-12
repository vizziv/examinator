# Writing an exam package

For people and agents who write the grader for one exam. Read this, then
`exams/toy_exam/` (two files, about 70 lines). You should not need library source;
where this guide names a library file, read only its docstring.

**Student data boundary.** Write and test an exam package with the instructor's
blank PDFs and synthetic fixtures only. Never open real scans, transcriptions,
parses, feedback, model logs or caches, or a real workspace (`pdfs/`,
`exam.sqlite`, `cache/`, `exports/`). Tests use the scripted fake model.

## 1. Where a thing belongs

- **The app** (graders, manually): a misread answer, a variant, a grade, a region
  nudged on the scan, a key edited live.
- **The exam package** (you): pages, questions, regions, cues, keys, prompt
  notes, custom checks and grade rules, a course's pseudocode builtins.
- **The library** (`src/examinator`; rarely, generically): mechanism only. If
  several exams need something, prefer a small hook over an option named after a course.

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
`synth` and `selftest` check the cases first: an unknown question, field,
variant, grid column or cue is one line naming the case and the key. A question
a case leaves out of `answers` has no scripted reply: `run --fake` lists its
model stages as `failed no scripted response`, and the self-test ignores them.

**Imports** (everything an exam package needs):

```python
from examinator import Exam, Field, Region, Cue, Landmark, Identity, Bool, Number, Grade, Evidence, EvidenceView, Facet
from examinator import Blocked, Unavailable, BadOutput     # what a stage may raise or meet
from examinator.synth import Case
from examinator.std import stages, math, pseudocode        # stages.math (a bundle) is not the module math
#   stages: math, pseudocode (bundles); transcribe, parse_math, parse_pseudocode, check_math, check, grade
#   math: Choice, Approx, all_correct, parse_expr, Unsupported
#   pseudocode: BASE, WITH_COLLECTIONS, Language, Primitive, Constant, parse, Unsupported, Failure, Budget, Limitation
from examinator.std.blocks import Plot, Series, Table, Note, Code, Details, Raw
from examinator_kits.stochastic import primitives, fit, checks     # samplers (optional)
from examinator.core.variants import vote                          # inside a computed cue
```

## 3. Declaring the exam

### Exam and questions

```python
exam = Exam('toy', title="Toy exam", pages=4, scan_window=[50, 40, 950, 900], cover_pages=1,
            landmarks=[...], identity=[...])
exam.question('areas', title="Areas", pages=[2], result=Number(int, min=0, max=3),
              variants=[...], cues=[...], fields=[...], stages=[...])
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
`Exam.question` raises `ValueError`, naming the question, for a region off the
page (page outside `1..pages`, not `0 <= left < right <= 1000`, not
`0 <= top < bottom <= 1000`) or a repeated label; `Exam` does for landmarks and identity boxes.

Finding coordinates: render a blank page, read each box's pixels, divide by the
image size × 1000. Helpers keep rows of boxes short (`boxes()`, `band()` in
`exams/exam_03/exam.py`; `inset=` in `exams/exam_01/exam.py` for boxes closer
than the padding). To check, crop each region from a blank with
`Layout().crop(Renderer(cache, dpi=100).page(blank_pdf, region.page - 1), region)`
(`examinator.pdf.layout`, `examinator.pdf.render`) and look, or test for ink
as `tests/test_exam_01.py` does. In the app, **regions** on a question page
drags rectangles on a scan; the **Exam data** page lists the changes as Python
lines to copy into the package.

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
  them). Nothing is guessed: an undecided variant goes to a person, and a check that
  `uses_variant` stays undecided until then.
- A resolved variant counts without a person looking, so the cues are yours to
  make reliable: one cue is one piece of information, and two (often one from a
  neighbouring question's pages, as in Exams 1 and 3) catch a misreading by
  disagreeing. `verify_variant=True` has a person verify every variant of the
  question instead.
- Checked at definition: a map cue reading to a name that is not a variant, a
  variant with fewer cues able to select it than `require` (default: all cues),
  and `require` above the number of cues. A map cue reading only to other names
  (`'original'`/`'alternate'`) is allowed beside a computed cue that combines it.
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

`entries` is the list of function names the student must write (`['tri']`; the
parse prompt asks for them). `grade_rule=` is required there: your check
decides what the evidence means. Never list `variant`: it is added in front
whenever `variants` is set.

Assemble the list yourself when a bundle does not fit (`sampler()` and question 3
in `exams/exam_01/exam.py`). `stages=[]` makes a question graded manually only.

What the `Stage` flags mean in practice:

- `editable`: graders may edit it; editing clears verification, and saving or
  verifying a parse reruns the check. `uses_model`: blocked without a model.
- `isolate`, `timeout`: a worker process with a wall-clock limit (default 60 s).
- `uses_variant`: the stage gets `ctx.variant`, is undecided while it is
  unresolved and outdated when it changes. `check_math` sets it for a per-variant
  key (`stages.per_variant(key)`); a custom check must ask for it.
- `accepts_markers`: stages refuse inputs carrying `[REVIEW: ...]` unless set;
  `check_math` sets it and leaves the marked field undecided. **A marker in one
  field still holds back every part of the question**: the parse cannot be
  verified until a person removes it.
- `facets`: filters for the app's filter bar. `check_math` adds one per answer
  label, `parse_pseudocode` makes comments searchable; question-level ones go in
  `exam.question(..., facets=[...])` (`examinator.Facet`).

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
def roots(answers):                    # both roots, in either order: one result for the pair
    if None in answers:                # a blank field arrives as None
        return 'incorrect', "one root missing"
    got = {math.parse_expr(a) for a in answers}         # math.Unsupported -> undecided
    return 'correct' if got == {math.parse_expr('-2'), math.parse_expr('3')} else 'incorrect'

stages.math(symbols={'t': 'positive'}, key={
    'a': {'1': '2*t', '2': math.Approx(0.135, tol=0.001), ('3', '4'): roots},
    'b': {'1': '3*t', '2': math.Approx(0.05, rel=0.02), ('3', '4'): roots}})
```

- **Per-variant keys:** if every value of `key` is a dict, the key is chosen
  by variant. Its variant names must be exactly the question's `variants`
  (a `ValueError` at definition otherwise).
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
| `pages()`, `crop(region)`, `model(...)`, `remember(...)` | for model stages only. Inside an isolated check they raise `Unavailable` |

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
(the message never quotes the reply). **Anything else a check raises, a timeout,
or a `data` that is not a dict makes every part undecided** (the grade rule is
not called; the error is shown to graders): decide outcomes instead of crashing.

**Run-time policy for checks that run student programs** (the stochastic kit's
`checks.draw` applies it; follow it in your own checks):

- Ran and failed, `pseudocode.Failure` of any `kind` (`'undefined'`, `'arity'`,
  `'domain'`, `'type'`, `'other'`, and `'unsupported'`, from a primitive's
  `Limitation`): **incorrect**.
- Out of budget, `pseudocode.Budget` (`kind` `'steps'`, `'depth'`, `'size'`,
  `'time'` or your own): **undecided**; a grader may run it longer.
- Could not run, `pseudocode.Unsupported` (outside the language): **undecided**;
  a grader fixes the parse.

A primitive's `Limitation` is a failed run like any other, so incorrect: the
student called a helper in a way it does not take. A grader who finds the call
obviously fine (a typo, an unimplemented but evident helper) overrides the grade.

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
`pseudocode.Limitation` for a call it does not handle (graded incorrect, like any failed run).

In a check: `program = pseudocode.parse(source, LANGUAGE)` (raises `Unsupported`,
never runs anything), then `program.is_blank('f')` (missing, or only `pass`),
`has('g')`, `references(...)`, `has_loop(...)`, and
`program.sample('f', args, n=, seed=0, deadline=, max_steps=, primitives=, constants=)`:
`args` is a tuple, and it returns a **list** of `n` results, so one
deterministic call is `program.sample('f', (3,), n=1, seed=0)[0]`. It raises
`Failure` or `Budget`; `max_steps` (default 2,000,000) is per call, `deadline`
a `time.monotonic()` value. `primitives=` substitutes a trusted reference for a
helper the student also wrote (`reference_weibull` in `exams/exam_03/weibullish.py`).

**A complete pseudocode question** (run-verified; add it to the minimal package):

```python
# my_exam/exam.py, continued
from examinator import Evidence, Grade
from examinator.std import pseudocode
from examinator.std.blocks import Note, Table

TESTS = [(0, 0), (3, 6), (10, 55)]       # tri(n) = 0 + 1 + ... + n

def check_tri(ctx):
    try:
        program = pseudocode.parse(ctx.input('parse'), pseudocode.BASE)
    except pseudocode.Unsupported as e:          # outside the language: a grader fixes the parse
        return Evidence({'outcome': None, 'why': "cannot run"}, [Note(str(e), tone='warn')])
    if program.is_blank('tri'):
        return Evidence({'outcome': 'blank', 'why': None})
    rows, outcome, why = [], 'correct', None
    for n, want in TESTS:
        try:
            got = program.sample('tri', (n,), n=1, seed=0)[0]    # one deterministic call
        except pseudocode.Budget as e:           # out of budget: undecided, unless already wrong
            rows.append([n, want, f"out of {e.kind}"])
            if outcome != 'incorrect':
                outcome, why = None, f"tri({n}) ran out of {e.kind}"
            break
        except pseudocode.Failure as e:          # ran and failed: incorrect
            got = f"failed: {e.detail}"
        if got != want:
            outcome = 'incorrect'
        rows.append([n, want, got])
    return Evidence({'outcome': outcome, 'why': why}, [Table(['n', "expected", "got"], rows)])

def tri_rule(evidence):
    outcome = evidence.data['outcome']
    return Grade(None, reason=evidence.data['why']) if outcome is None else outcome

exam.question(
    'tri', title="Triangular numbers", pages=[2], result=Bool(),
    fields=[Field('code', region=Region(2, 100, 300, 900, 900))],
    stages=stages.pseudocode(['tri'], check_tri, grade_rule=tri_rule, timeout=30))
```

```python
# my_exam/fixtures.py: add to the list in cases()
GOOD = "def tri(n):\n    total = 0\n    for i in range(n + 1):\n        total = total + i\n    return total\n"

Case('tri-right', answers={'tri': GOOD}, expect={'tri': True}),
Case('tri-wrong', answers={'tri': GOOD.replace('n + 1', 'n')}, expect={'tri': False}),
Case('tri-crash', answers={'tri': "def tri(n):\n    return n // (n - 3)\n"}, expect={'tri': False}),
Case('tri-loops', answers={'tri': "def tri(n):\n    while True:\n        n = n + 1\n"}, expect={'tri': None}),
Case('tri-outside', answers={'tri': "import math\n"}, expect={'tri': None}),
Case('tri-blank', answers={'tri': None}, expect={'tri': 'blank'}),
```

A pseudocode answer in a case is the parsed source as one string; `None` is
blank. The parse stage ends the source with a newline, so the check receives
`"\n"` for a blank answer: `parse` gives a program with no functions and
`is_blank('tri')` is true. The rule reads `evidence.data`, which is whatever
the check returned; `'blank'` on a `Bool` is false with the blank flag.

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

`synth` wants a new (or empty) directory and leaves nothing behind if it
fails. `--fake` uses the script `synth` wrote into the workspace
(`fake_model.json`); a workspace without one is refused in one line.

**Reading `status`** (and the app's grid). A cell is the grade (`✓`, `✗`, a
number, `blank`), or `-` when there is none or it is undecided (the app shows
`?`). A cell that does not count yet carries its state; the total adds what
counts, with `+?` while something does not:

- `unverified`: a grade is proposed, but a person has not verified the parse
  (or the variant, with `verify_variant=True`). **Normal after `run`**;
  only the self-test and graders verify.
- `not_started`: nothing written (after `run --fake`: no scripted answer).
  `in_progress`: no grade yet, because a stage failed or was blocked.
- `needs_review`: verified, but undecided (the reason is in the app), or graded manually.
- `outdated`: computed from an older parse, variant or definition; run again.
- `manual`, `manual_outdated`: a grader decided (and the parse changed since).

**`run` re-runs the selected stages every time, by design** (the model is
asked again, each check reruns, and every new result is a new revision; old
ones go to history). Use `--missing-only` to fill gaps only, and `--dry-run`
to see the plan. What a person wrote or verified (an edited or verified parse
or variant, a manual grade) is skipped as `skip_protected` unless you pass
`--include-protected`; a check downstream of a verified parse still reruns.

**Synthetic stacks.** `synth --stacks DIR` writes `DIR/incoming/stack-1.pdf`
and `stack-2.pdf`, `DIR/roster.csv` and the script `DIR/fake_model.json`; the
next `intake` command is printed. The stacks hold deliberate defects, so flags
are expected: stack 1 has six students, one missing printed page 3, one with a
stray blank sheet after page 2, one upside down; stack 2 is fed upside down as a
whole, with three students and the first two sheets of a fourth. Name and ID
boxes are misread on purpose too (`STACK_READS` in `synth.py`; one student is
not on the roster, and the roster lists one person with no exam). The
landmarks work when `intake` says `checked` and every flag is one of those
defects: for the toy exam, stack 1 flags only #2 (too few pages), #3 (too many,
unexpected page number) and #5 (first page not recognised: upside down), and
stack 2 all four. `--no-check` gives the unchecked baseline (every N pages).

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
- **Regions in the wrong units.** Per-mille of the printed page, not of the
  scan and not pixels (out of range is refused; plausible but wrong is not).
  A field on a page outside `pages` is never sent to the model. Boxes closer
  than `crop_pad` show their neighbours.
- **A cue that cannot tell variants apart:** the same text on every printing,
  or a band that also covers answer boxes with the same letters (comment above
  `BIT2` in `exams/exam_03/exam.py`).
- **A custom check without `uses_variant=True`** (it sees `ctx.variant is None`
  and is not outdated when the variant changes).
- **Crashing instead of deciding.** A check that raises is undecided, so a
  failing student program shows `?`: catch `Failure` and return `'incorrect'`.
  Conversely, notation the parser rejects or an inconclusive comparison is
  `None`, never `'incorrect'`.
- **A review marker in a fixture** holds back every part of its question.
- **Callables where data would do.** Text keys, `Choice` entries, cue `reads`,
  regions and notes can be edited live in the app; a key inside a function cannot.
- **Fixtures derived from the keys**, so a wrong key passes its own self-test.
  Write answers out independently, in other notations too (`OTHER_FORMS` in
  `exams/exam_03/fixtures.py`).
