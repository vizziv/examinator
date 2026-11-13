# Examinator: instructions for agents

Examinator is a Python library for building exam graders, plus the app that
runs one. Background and design live in [`agent_docs/`](agent_docs/README.md)
(the documents call the project "Examiner"; the name is now **Examinator**).
Writing an exam package: [`agent_docs/WRITING_AN_EXAM.md`](agent_docs/WRITING_AN_EXAM.md).

## Student data boundary (not negotiable)

This software processes real student exams.

- Use only instructor-provided blank exams and explicitly synthetic fixtures.
- Never read, list, or search real scans, transcriptions, parsed answers,
  feedback, model logs or model caches, even if such folders are reachable.
  A real workspace (`pdfs/`, `exam.sqlite`, `cache/`, `exports/`) is off limits.
- Automated tests use mocked model responses. Live-model tests are opt-in
  (`pytest -m live`), use only blank or synthetic material, and write to a new
  directory outside this repository.
- Repeat this rule in every subagent brief.

## Local model only

All model calls go to a loopback HTTP endpoint. The client refuses non-loopback
URLs, proxies and redirects. Errors and logs never contain prompt or response
text. Answer keys never appear in any prompt.

## Layout

```text
src/examinator/
  core/     definitions (one validation for the package and overrides), store, status rules
            (the model: MODEL.md), runner, batch, workspace, overrides;
            ask (one region, asked of the model: printed fields, names) and reading;
            split (pure boundary logic) and intake (sources, plans, accepting into pdfs/);
            identity (roster, reading name/ID boxes, pure proposals, the pick)
  pdf/      page rendering, scan layout, orientation, writing submission PDFs
  model/    loopback model client, scripted fake model
  std/      standard library: readers, prompts, fields, math (the keyed check), pseudocode, blocks
  web/      server and browser interface
  synth.py  synthetic-workspace generator
src/examinator_kits/
  stochastic/  optional kit for sampler questions: random primitives, fit tests,
               check scaffolding; uses the core, which never imports it
exams/      example exam packages (toy exam, Exams 1-3); never imported by the library
tests/
```

## Rules of the house

- **The library knows nothing about any particular exam.** No question ids or
  topic words in `src/examinator` (a test greps for them). If only one exam
  needs something, it goes in that exam's package.
- **No module-level state** in the library. Everything hangs off an `Exam` and
  an open `Workspace`.
- **Status rules** (`core/status.py`, `agent_docs/MODEL.md`) decide whether a
  grade counts. Change them only with a test for the row you touch.
- **Student-derived text is never executed as Python.** Math goes through the
  restricted expression parser; pseudocode through the bounded interpreter.
- **Grading UI:** a working tool for trained staff. Short labels over
  sentences; plots first; details collapsed. No Node toolchain; keep browser
  JavaScript small and plain.
- **String quotes:** single quotes when the exact contents are meaningful
  (keys, modes, markers, field names: anything that would be an enum), double
  quotes for prose and human-facing messages.

## Commands

```sh
uv sync                                   # install
uv run pytest                             # tests (offline, fake model)
uv run examinator synth exams/toy_exam /path/outside/repo/ws   # synthetic workspace
uv run examinator synth exams/toy_exam /outside/repo/st --stacks          # synthetic stacks of scans
uv run examinator intake exams/toy_exam -w WS --fake --roster R.csv A.pdf   # add scans, propose splits
uv run examinator intake exams/toy_exam -w WS --fake --names                # read name/ID boxes, list proposals
uv run examinator run exams/toy_exam --workspace WS --fake     # read, then check, from the CLI
uv run examinator serve exams/toy_exam --workspace WS --fake   # the app
```
