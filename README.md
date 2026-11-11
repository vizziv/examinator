# Examinator

A small language for building exam graders, and the app that runs one.

An instructor scans handwritten exams to PDF. For each submission and question,
Examinator works out which variant the student received, transcribes the final
answer with a local vision model, parses it into a structured answer, checks it
with local deterministic code, and proposes a grade. Graders review all of this
in a browser beside the scan, fix what the model misread, and decide.

- **The library** (`src/examinator`) knows nothing about any particular exam.
- **Kits** (`src/examinator_kits`) are optional batteries for a kind of course,
  outside the lean core: `examinator_kits.stochastic` grades pseudocode samplers.
- **An exam** is a small Python package built with the library. See
  [`exams/toy_exam/exam.py`](exams/toy_exam/exam.py) for a complete one.

```sh
uv sync
uv run pytest                                          # offline; scripted model

uv run examinator selftest exams/toy_exam              # an exam's fixtures, end to end
uv run examinator synth exams/toy_exam /tmp/toy-ws     # a synthetic workspace
uv run examinator serve exams/toy_exam -w /tmp/toy-ws --fake
```

## Scans in

Examinator takes the raw scans itself: stacks of exams scanned in one go, many
students per PDF.

```sh
uv run examinator intake path/to/my_exam -w WORKSPACE stack-1.pdf stack-2.pdf --roster students.csv
uv run examinator serve path/to/my_exam -w WORKSPACE      # then the Scans page
```

(or add the PDFs with **Add scans** on the Scans page). Each PDF is kept
unchanged under `WORKSPACE/sources/`; adding the same file twice is noticed.
Examinator proposes a split, every N pages (N = the exam's pages: a raw stack has no cover sheets; set a larger N on the page if yours does), and
if the exam declares landmarks (`Exam(landmarks=[...])`: a printed title on
page 1, a page-number footer) and the model is up, **check** reads them only
at the expected boundaries and their neighbours, moving each boundary to where
a first page really is and flagging what looks wrong: too few or too many
pages, an unexpected page number, a first page not recognised.

On the Scans page you step through the boundaries one at a time: the guessed
first page of the next exam, large, between its neighbours.

| Key (left hand) | Right hand | |
| --- | --- | --- |
| `V` | `N` | this is the first page; go to the next guess |
| `A` / `D` (or arrows) | `J` / `L` | the guess is a page too late / too early |
| `W` / `S` | `I` / `K` | previous / next boundary |
| `X` | `M` | drop this page (blank or stray) |
| `R` | `U` | turn this exam 180° |
| `Shift+R` | `Shift+U` | turn the whole scan 90° (also: mirror, as a button) |

A confirmed first page is your decision: it outranks the every-N guess and any
landmark reading, and later guesses follow from it. Turn a stack fed in
sideways or upside down before checking: landmarks are read on the turned page.
The overview below lists every exam (doubtful ones first) with its pages, where
pages are dropped, an exam is started at a page or joined with the next.

**Accept** writes one PDF per exam into `pdfs/`, named by an opaque id
(`s0001`, ...) that never changes, with pages upright (turned pages are written
as new single-image pages; others are copied untouched). Accepting again after
a fix rewrites only the exams whose pages or turning changed; one that already
has grading work is not changed unless you insist.

**Names.** Then say who wrote each exam, on the Scans page's **Names** tab.
The roster comes from **Roster** (paste, or load a file) or `--roster`: either
a CSV or tab-separated table with a header (`Name`, or `First Name` and
`Last Name`; `Student ID`; optionally `Email`), or plain lines, one person per
line, name and ID separated by a comma or a tab, in either order. On a plain
line the ID is the part with no spaces and a digit (failing that, the part
with no spaces). The box shows how many people it read, the first few rows,
and any line it could not read, before you save.

If the exam declares where students write who they are
(`Exam(identity=[Identity('name', Region(...), ask=..., roster='name'),
Identity('id', ..., roster='id')])`), **read** asks the model what is
written in each box (once per exam; reading again is free) and each exam gets
ranked proposals from the roster, with why ("ID exact, name close", "ID 1
off") and how sure: *both agree*, *ID only*, *name only*, *they disagree*,
*nobody close*, *not read*. Nothing is named until a person says so. One
exam at a time, its boxes large beside the best proposal:

| Key (left hand) | Right hand | |
| --- | --- | --- |
| `V` | `N` | this person; go to the next unnamed exam |
| `A` / `D` (or arrows) | `J` / `L` | previous / next proposal |
| `W` / `S` | `I` / `K` | previous exam / skip |
| `E` | `U` | type a name or ID (the roster picker); `↵` names the exam, also someone not on the roster |

Unnamed and doubtful exams come first; a person named for two exams is
flagged *name twice*. Below: every exam, and the roster entries no exam is
named for (**No exam**). The name and ID then show wherever the submission's
name does (the grid's name column searches the ID too). `intake --names`
reads the boxes from the command line and lists the proposals.

Try it on synthetic stacks (a missing page, a stray page, an upside-down exam,
an upside-down stack, a length that is not a multiple of N; and for names: an
ID misread by one character, a misread name, an ID that is someone else's, an
empty ID box, an unreadable name, a student not on the roster, and one on the
roster with no exam):

```sh
uv run examinator synth exams/toy_exam /tmp/st --stacks
uv run examinator intake exams/toy_exam -w /tmp/st --fake --roster /tmp/st/roster.csv /tmp/st/incoming/*.pdf
uv run examinator serve exams/toy_exam -w /tmp/st --fake
```

## PDFs already split

A workspace can instead be handed `ID.pdf` scans and one roster file (the
scanning service's YAML, or a CSV with `Submission ID`, `First Name`,
`Last Name`, `Email`) in `WORKSPACE/pdfs/`, then:

```sh
uv run examinator serve path/to/my_exam -w WORKSPACE
```

The model is an OpenAI-compatible server on loopback (`--model-url`, `--model`).
Without it, reviewing, editing, checking and deciding all still work.

Design notes and history: [`agent_docs/`](agent_docs/README.md). Rules for
agents working here, including the student data boundary: [`AGENTS.md`](AGENTS.md).

## Comparing transcription models

`examinator compare` has several local models transcribe the same answers and
writes a side-by-side report. It never writes to the workspace's store.

```sh
uv run examinator compare path/to/my_exam -w WORKSPACE --limit 5 \
  --model google/gemma-4-26b-a4b-qat \
  --ocr PaddleOCR-VL-1.6@http://localhost:8242/v1 \
  --ocr 'LightOnOCR@http://localhost:8243/v1::'
```

- `--model NAME[@URL]` runs the exam's transcript stage as is with that model.
- `--ocr NAME[@URL][::PROMPT]` is for a dedicated OCR model: each answer box is
  sent alone with a short prompt (default `OCR:`; `::` with nothing after it
  sends no prompt text) and no system message.
- Models run one after another, so the times are comparable. Endpoints must be
  loopback, as everywhere else.
- The report (`report.html`, `results.json`) goes to
  `WORKSPACE/exports/compare-TIME/`. For a real exam it contains student work:
  leave it in the workspace.
