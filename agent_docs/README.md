# Examiner rewrite: brief for the lead agent

*The original brief, kept as history (some of it, such as PDF exports, has
since been dropped). The project as it is: [`MODEL.md`](MODEL.md),
[`DESIGN.md`](DESIGN.md), [`WRITING_AN_EXAM.md`](WRITING_AN_EXAM.md) and
[`TODO.md`](TODO.md).*

Start here. You are starting a new project from an empty repository. This folder
is everything you inherit: there is no existing code to extend.

| File | What it is |
| --- | --- |
| `README.md` | This brief: goal, decisions, constraints, how to run the work, milestones. |
| [`MODEL.md`](MODEL.md) | The model the core implements: fields, checks, values, results, decisions, statuses. |
| [`DESIGN.md`](DESIGN.md) | The architecture and API as built. |
| [`LESSONS.md`](LESSONS.md) | What three earlier hand-built graders taught us. Read before designing anything. |
| [`EXAM3.md`](EXAM3.md) | A real exam, described in full, and how it would be written in the new library. It is the acceptance target, not the template. |
| [`WRITING_AN_EXAM.md`](WRITING_AN_EXAM.md) | How to write an exam package against the library as it is now. |
| [`TODO.md`](TODO.md) | Where things stand, and what is next. |

## Goal

Build **Examiner**: a Python library for building exam graders, plus the app
that runs one. An instructor scans handwritten exams to PDF. For each
submission and each question, Examiner:

1. reads which printing of the exam (or question) the student received,
2. transcribes the handwritten final answer with a local vision model,
3. parses the transcription into a structured answer,
4. checks it with local, deterministic code, and
5. proposes a grade.

Graders (the professor and a few TAs) review all of this in a browser beside the
scan, correct anything the model misread, and make the final decisions.

Three earlier graders were each written for one exam by copying the previous one
forward. Improvements never propagated. The point of this rewrite is the split:

- **The library** rarely changes and knows nothing about any particular exam.
- **An exam** is a small Python package built with the library. The expected
  workflow is that the user asks an agent to "make a configuration for this
  exam", then tweaks it by hand.

The user's framing, which should guide your taste: think of the library less as
"batteries included" and more as **a small language for building exam graders**,
with a lean standard library. Things must compose. When a built-in does not fit,
departing from it should cost one step, not a rewrite of what the built-in gave.

## Status of everything in these documents

Each statement falls into one of three classes. Treat them differently.

- **Decided** — the user said so. Do not reopen without asking.
- **Recommended** — my design, which the user has seen and broadly accepted.
  Implementation detail and style are explicitly yours; change them when you
  find something cleaner, and say that you did.
- **Open** — not settled. Decide if it is an implementation detail; ask the user
  if it changes what they will experience.

## Decided

- **Fresh start.** New repository, no backward compatibility with any earlier
  grader or its data.
- **Library plus per-exam Python.** Each exam's configuration is real Python and
  lives with that exam. Real Python is not edited through the web interface.
- **SQLite.** One database file per exam. Relational, keyed rows. It need not be
  append-only, and nothing should require a materialized-view engine.
- **One exam per process for now.** Multiple exams in one process may come
  later; do not build it, and do not make it hard (keep no module-level state).
- **Printings are read into fields.** An exam may be printed in several
  versions, usually **covertly**: there is no printed version marker, because
  students should not be able to tell. The printing is inferred from printed
  data, read into *printed fields* (in the question itself, or in one question
  holding the whole exam's printing) and verified like any value. Checks read
  any question's fields; the library records what they read (`MODEL.md`).
- **Grades are typed values, not check marks.** A check proposes one grade: a
  `bool`, an `int` or a `float`, optionally with a range. A question with two
  grades has two checks.
- **Checks are isolated and time-limited**, with a sensible default timeout that
  each question can override.
- **Checks run on the server**, with NumPy available. Plots are part of what a
  check reports to the grader.
- **Pieces compose.** The library ships readers (`std.readers`) and a keyed
  check with grade rules (`std.math`), small pieces rather than bundles. Checks, grader plots and student
  feedback are expected to be custom per question much of the time.
- **No general "tunable parameter" machinery.** Unusual per-question behaviour
  (see the `huge_constant` example in `EXAM3.md`) is custom code in the exam,
  written by an agent on request. The library only has to make that possible.
- **Clean prototype over maximum flexibility.**
- **Technology:** Python is fine and NumPy is wanted. The user has a mild bias
  against JavaScript and Node; avoid a Node toolchain and keep browser code small.
- **In scope eventually:** live editing of exam *data* (keys, regions, prompt
  notes) through the app; a static per-student view of results; PDF exports
  grouped by question and printing.

## Not negotiable

**Student data boundary.** This software processes real student exams. During
development:

- Use only instructor-provided blank exams and explicitly synthetic fixtures.
- Never read, list, or search real scans, transcriptions, parsed answers,
  feedback, model logs or model caches, even if such folders are reachable.
- Automated tests use mocked model responses. Live-model tests are opt-in, use
  only blank or synthetic material, and write to a new directory outside the repo.
- Put this rule in the new repository's agent instructions on day one, and
  repeat it in every subagent brief.

**Local model only.** All model calls go to a loopback HTTP endpoint. The client
must refuse non-loopback URLs, proxies and redirects. Error messages and logs
never contain prompt or response text. Answer keys never appear in any prompt.

**Grading UI style.** The interface is a working tool for trained staff. Screen
space and attention are scarce.

- Prefer a short label or no text over an explanatory sentence. Do not explain
  ordinary actions, define statuses, or repeat what a badge already shows.
- Lead with plots whenever numeric evidence exists. Collapse sample dumps,
  tracebacks and long notes into secondary details.
- Settings and batch-run screens may explain more, because their consequences
  are larger.

**Python string quotes** (the user's convention across their repositories):
single quotes when a string's exact contents are meaningful (a marker, mode,
key, field name; anything a richer type system would make an enum), double
quotes for prose and human-facing messages. Apply it pragmatically.

## How I would run this

You will probably lead a team of subagents. What I would want in your position:

1. **Read `LESSONS.md` and `EXAM3.md` before `DESIGN.md`.** The design only
   makes sense once you know what graders actually do and what went wrong before.
2. **Write the interfaces yourself, first, as real stubs.** The exam-definition
   types, the reader and check contexts, the store API, the evidence blocks. Typed
   signatures and docstrings, no bodies. Everything else hangs off these, and
   parallel work is only safe once they exist. Expect to revise them once after
   the Exam 3 port; budget for that round.
3. **Then fan out along the seams in `DESIGN.md`:** store and workflow rules;
   page rendering and scan layout; model client; math toolkit; pseudocode
   interpreter; sampling toolkit; server and UI. These barely touch each other.
   One writer per set of files.
4. **Give each subagent** the goal, the stubs it implements against, the files it
   owns, what done looks like (tests it must pass), what is out of scope, and
   the data boundary. Point it at the relevant section here instead of retyping.
5. **Keep the Exam 3 port for the end, with one owner.** It is the integration
   test. Every awkward line in it is feedback on the library, not a reason to
   special-case the library.
6. **Verify, don't relay.** Run the tests and open the app yourself before
   reporting a milestone as done. Get an independent review of the workflow
   rules (`DESIGN.md` §7): they decide whether a grade counts, and a mistake
   there is a wrong grade for a real student.
7. **Guard against overfitting mechanically.** Two checks, both cheap:
   - The library package contains no Exam 3 identifiers. A test greps it for
     question ids and topic words (`q2`, `weibull`, `poisson`, `huge`, `shiny`).
   - A second, deliberately different **toy exam** runs through the same app:
     one question scored as a number in a range with three printings, one boolean
     question printed one way. If the toy exam needs a library change, the
     library was not generic.
8. **Ask the user when it changes what they see or do; decide the rest.** They
   have said style and implementation detail are yours.

## Milestones

Each one ends with something runnable and a check you can perform.

**M0 — Skeleton.** Repository, packaging, agent instructions with the data
boundary, test runner, and a synthetic-workspace generator: blank exam PDFs,
a fabricated roster, and a scripted fake model.
*Check:* one command creates a synthetic workspace; tests run.

**M1 — Core, no UI.** Exam definition types, the SQLite store, the runner,
workflow rules, and a command-line entry point that reads and checks a
submission. Uses the fake model.
*Check:* the toy exam runs end to end from the command line; workflow-rule tests
cover every row of the table in `DESIGN.md` §7.

**M2 — Toolkits.** Page rendering with a cache, scan-layout mapping, the model
client, the math checker, the pseudocode interpreter, sampling diagnostics.
*Check:* each has unit tests on synthetic inputs; rendering and the model client
have been exercised once against real blank PDFs and the real loopback model.

**M3 — The app.** Grid, question page (scan crops beside editable parsed answer,
evidence, grade), verify and decide, single runs, batch runs with a preview.
*Check:* a real-browser smoke test on the synthetic workspace: edit a parse,
verify it, watch the check rerun, override a grade, run a protected batch.

**M4 — Exam 3.** Write Exam 3 as an exam package per `EXAM3.md`.
*Check:* on a synthetic workspace with canonical answers for all eight printed
versions, every question grades correct; deliberately wrong answers grade
incorrect; the overfitting checks in step 7 pass.

**M5 and after, in the order the user prefers:** PDF exports by question and
printing; live editing of exam data; filter-and-search facets; the static student
view; server deployment behind an authenticating proxy. `DESIGN.md` §15 says what
each needs from the core so that M1–M3 do not block them.

## Ask the user for

- The eight blank Exam 3 PDFs, and where the new repository should live.
- Whether the previous Exam 3 grader's source is available to you as reference.
  If it is, **port** its pseudocode interpreter and its restricted math-expression
  checker with their tests instead of rewriting them; both are well tested and
  already exam-agnostic in spirit. Do not port its server, its pipeline plumbing,
  or its file-based storage.
- The Python interpreter to use, and the local model's endpoint and model name
  on their machine (previously `http://localhost:8241/v1`, an OpenAI-compatible
  server).
- Before M5: which later feature matters most.
