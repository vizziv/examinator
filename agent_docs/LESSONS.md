# Lessons from three earlier graders

Three exams were graded with three hand-built tools, each copied from the last.
This is what held up, what hurt, and what was tried and abandoned. It is
background for judgment, not a specification.

## What grading actually looks like

- A few hundred submissions, around ten questions, graded by the professor and a
  few TAs over several days, partly in parallel from different browsers.
- Scans come from a scanning service as one PDF per student, often with one
  prepended cover page, with the printed page reduced and inset on the scan.
- The model is a local vision model on the instructor's machine. It is good but
  not reliable: every parsed answer is checked against the scan by a person.
  The tool's job is to make that check fast and to make the rest automatic.
- Graders work question by question across students far more than student by
  student. "Next student, same question" is the most-used action.
- The model server is sometimes not running. Reviewing, editing, checking and
  deciding must all work without it.

## Principles that held up

1. **Transcribe, then parse, as two model calls.** The first reads the page and
   records what is there. The second, text only, puts it in a structured form.
   A person can fix a misreading in one place and rerun only what follows.
2. **Never ask the model to grade, solve, fix or complete.** Prompts say to
   preserve mistakes. Checks are local code.
3. **Answer keys never reach the model.** Only field labels and notation do.
4. **One uncertainty channel.** The model writes `[REVIEW: reason]` where it is
   unsure. Consumers refuse marked input. People resolve markers by editing.
5. **Evidence is not a decision.** A check proposes; a result counts only once a
   person has verified the parse it was computed from, or decided directly.
   Unmarked model output is never treated as verified merely because it has no
   marker.
6. **Undecided is different from wrong.** Unsupported notation, a timeout, an
   exhausted budget or an inconclusive symbolic comparison leaves the answer
   for a person. Only a demonstrated mismatch is "incorrect". Blank is a third
   thing, worth its own badge and filter.
   *(Revised 2026-11-11 for Examinator: verification is its own axis, so err
   towards incorrect. For pseudocode: it ran and failed, incorrect; it ran out
   of budget, undecided (a grader may run it longer); it could not be run
   (outside the language), undecided. A grader can override; a crashed check
   stays undecided.)*
7. **Manual work is sacred.** Batch runs never overwrite something a person
   wrote or verified unless explicitly told to, and the old content is kept.
8. **Preview before bulk work.** Show exactly what will run, what is skipped and
   why, and refuse to run a stale preview.
9. **Student-derived text is never executed as host code.** Expressions go
   through a whitelisting parser; pseudocode through a bounded interpreter.
10. **Statistical checks are evidence, not proof**, and the interface should not
    keep saying so. Show the plot.
11. **Supporting work is judged by people.** Attempts to have the model record or
    assess it were dropped.

## What hurt

- **No seam between tool and exam.** The "generic" server imported the exam's
  modules, knew its question ids, its version bits and its page arithmetic, and
  matched on strings from its checker messages. Every new exam meant a fork.
- **An accidental contract.** The exam was a task file for a build tool; the app
  discovered stages by parsing task names, decided which stages used the model
  by comparing function identities, and learned page defaults by monkeypatching.
  Declare these things.
- **Three report vocabularies at once** (`passed`/`needs_review`,
  `met`/`not_met`, `correct`/`incorrect`), with an adapter layer between them
  and the interface. Pick one result model.
- **Status mixed two ideas.** Labels such as Correct, Blank, Not checked and Not
  transcribed combined "what is the grade" with "where is this in the
  workflow". Keep them separate.
- **Files as a database.** Outputs were files with hidden sidecars for origin,
  hashes and verification, plus history folders and an in-process lock. It
  worked, at the cost of scanning every file of every student to draw the grid
  and of intricate snapshot-and-compare code to publish a check safely.
- **A process per stage.** Every stage run started an interpreter and re-executed
  the whole exam definition; checks started another. Rendering a crop re-rendered
  its whole page every time, with caching disabled.
- **Invalidation at the wrong grain.** One edition marked everything stale when
  any line of the exam's source changed. The next removed staleness tracking
  entirely. Neither was right.
- **Two-tier credit baked in.** "Normal" and "shiny" marks were hard-coded
  through the review logic and the interface.
- **Exam-specific interface code.** Hints, legends and one-off controls for
  particular questions lived in the shared browser script.

## Tried and abandoned

- **Asking the model for bounding boxes** of the text it read. Readings were
  right but the boxes were malformed or invalid. Use instructor-defined regions.
- **Detecting the exam version from printed graphics** (shapes in figures). It
  conflicted with text readings on half the versions. Reading printed text in a
  defined region was reliable.
- **A single reading per version bit.** Replaced by requiring two independent
  printed sources that agree. With covert per-question variants there may be
  only one distinguishing feature, so plan for review of single-cue readings.
- **Picking the best-scoring version** when evidence conflicted. Replaced by
  "unresolved, ask a person".
- **A special fast path** for one common shape of rejection-sampling loop.
  Replaced by a general batch interpreter that runs many samples together.

## Ideas worth having available

Not all belong in the standard library; several were lost between editions
because nothing made them reusable.

- **Math rules beyond exact equality:** numeric answers with a tolerance and
  several accepted values; intervals; unordered pairs; "at least k of n correct"
  and "these fields are required"; values that should trigger review instead of
  a verdict.
- **Declared symbols and substitutions** for symbolic answers, so equivalent
  forms pass; a subprocess time limit, because symbolic simplification can hang.
- **Testing samplers by distribution:** seeded sampling of the student's
  function, a support check, then goodness of fit; for multi-dimensional output,
  KS on random projections against a reference sampler, which catches a wrong
  joint law with correct marginals.
- **Trying several global interpretations** of ambiguous notation (degrees or
  radians; several values for an idealized constant), keeping every attempt as
  evidence and accepting any pass.
- **A manual attestation** for rules that tests cannot establish ("does not use
  rejection"), stored beside the answer and able to cap credit.
- **A resource limit used as a rubric heuristic**, such as a cap on random draws
  to flag probable rejection sampling for review.
- **Independent diagnostics that can raise a review** without overriding the
  rubric, for example sampling from a student's formula when the symbolic
  comparison rejects it.
- **Per-question page overrides with a note** for answers that spill onto extra
  pages.
- **Grouping by normalized answer** on the grid, and searching the comments in
  parsed code across all students.
- **A synthetic-workspace generator** built from blank exams and the answer
  keys. It doubles as a self-test of every question and variant.
- **Exports grouped by question variant** for a downstream grading service:
  per-student page stubs, a combined batch, a blank template and a manifest.

## Operational details worth keeping

- Roster formats: the scanning service's YAML metadata (file name to a list of
  submitters with names and emails), or a CSV with `Submission ID`, `First
  Name`, `Last Name`, `Email`. Exactly one must be present.
- Private file permissions, atomic writes, no symlinks in served paths.
- Deployment behind an authenticating reverse proxy with the app on loopback;
  one process per workspace; backups of the output store while running.
- Record who made each manual decision. The earlier tools did not, and with
  several graders it was missed.
