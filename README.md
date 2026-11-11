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
