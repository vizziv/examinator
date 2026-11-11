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

For a real workspace, put `ID.pdf` scans and one roster file (the scanning
service's YAML, or a CSV with `Submission ID`, `First Name`, `Last Name`,
`Email`) in `WORKSPACE/pdfs/`, then:

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
