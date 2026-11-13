# Examinator: what is next

One list, kept current. Design background is in [`DESIGN.md`](DESIGN.md); its
§15 says what the core must already provide for later layers.

## Where things stand (2026-11-13)

For whoever picks this up next: read MODEL.md, then DESIGN.md, then this file.

- **Built and settled with the owner:** the field model (values, results,
  decisions, the status table); one store of scoped overrides; identification
  as a built-in group of ordinary fields plus a pick (`Exam(identity=...)`);
  `Ask`, the one region reader; fields with a `default` in place of check
  parameters; image blocks (the kit draws with matplotlib; no custom plot
  format, no raw HTML); the Read page in one step; Split as one ordered list
  of sheets with marks (start, extra, dropped, turn, missing).
- **Principles the owner confirmed:** few primitives that compose, and a new
  need should be met by an existing primitive first; when bugs are found,
  find the missing general principle and build it, not a patch per bug (a
  good sign is one change removing several bugs); history is for what a
  person edits (values, decisions), never for computed results; changing how
  a field is read changes no value; reads after the first big one are rare,
  so nothing is built around them; being already matched must not change a
  name proposal; rare actions live in menus; the past exams are test cases,
  not behaviour to preserve; the CLI is development tooling; prefer real page
  loads made fast over in-page switching, where a page has one subject.
- **Principles built on 2026-11-13** (two rounds of independent review, each
  finding turned into a design change):
  - *Split:* the plan is only the pile, sorted; what was written is `made`,
    and which run is which written submission is derived (`split.link`). A
    mark is a guess or a person's, and guessing touches only guesses. A
    submission is its kept sheets. An edit names a submission by its first
    sheet. (In progress: a verified submission is its sheets; a written file
    is never changed and the store names it; Accept asks about picks too.)
  - *The interface:* a POST is a change to a row (names the revision drawn),
    a run in a slot (one at a time), or a query; a test built from the route
    table covers every one. `Changes.send` is the only way a page sends a
    change: what the person said is fixed at the press, revisions are read
    when it is sent, one queue per subject. A revision is opaque to the page.
    A refusal shows the server's reason and what happened to the draft, in
    one place. A key's auto-repeat never triggers a change; moves repeat.
    The question page is one subject per load, rendered ahead where the
    browser can; image URLs name their content.
  - *Naming:* a proposal is one computation (who the evidence singles out,
    and the order shown); automatic matching acts only on unique evidence;
    ties, conflicts and evidence that fits nobody are doubts. A pick records
    the person; its link to the roster is derived; a roster is valid when
    everyone on it finds themselves.
  - *Reading:* what will be read and reading it are one computation
    (`runner.to_read`). A read asks the model; nothing remembers a field's
    reading but its value (only Split's page readings are remembered).
    Nothing tracks "read again": a person types the value or reads again
    explicitly. A rewritten submission's unverified readings are dropped.
  - *Definitions:* `Exam.validate` is the one validation; an override is
    valid exactly when the definition with it applied is valid. A workspace
    has its own definition (the package's plus its overrides); the package's
    is never changed. `Exam.questions` are the graded questions; the identity
    group is reached only by name. Skipped overrides are reported at every
    scope.
  - *Evidence:* `result.settle` is the boundary for the whole stored result,
    grade and blocks; images are stored by digest outside the result row. A
    kit figure is a pure function of its arguments, drawn only when shown.
- **Verified how:** every change passed the whole suite and the four exams'
  self-tests against the instructor blanks on `main` after merging. Most were
  exercised in a browser on synthetic workspaces by the agent that made them;
  the core fixes of the second round were not. Two independent reviews ran on
  2026-11-13 (morning: overrides, identity, images, Split; evening: all of
  the day's changes, core and web); the fixes for the evening review have not
  themselves been reviewed. Nothing has been run against a live model, and
  prerendering has not been exercised in a browser that does it. Subagent
  work is merged on tests and spot checks, not a line-by-line read.
- **Next, in the owner's rough order:** a first live-model run on synthetic
  handwriting (also tunes name matching and shows how reliable page readings
  are); a filter overlay on the question page; search facets; then a student
  view.

## The model (built 2026-11-12)

[`MODEL.md`](MODEL.md): fields, readers, checks, questions; values, results,
decisions; the status table. Built, with one test per row of its two tables.
Resolved by it: reading is one phase (no stored transcript); a field's region
for one submission; a decision is outdated when any field on its page changes;
how a field is read changes no value; a printed
field's region may be unset in code; one grade per check, any number of checks.
Overrides have one mechanism at three scopes (exam, source scan, submission;
`overrides.SCOPES`, `overrides.Layers`): a region, pages and the print area
all live there, with revisions. A submission maps the exam's
printed pages to sheets, with extra sheets and missing pages (DESIGN.md §16).

## Open after the rewrite

- **Which grades changed** after a check's code or key changed: results keep
  no history, so this would compare old and new at recompute time. Not built.
- **Per-field handwriting reads** (one crop, one value) as an alternative to
  the question's joint read: the reader protocol allows it; nothing compares
  the two (a live-model question).
- **A code-change fingerprint per check**: today a result is outdated by its
  check's definition hash or by any change to the package's code, so editing
  any file recomputes every result (in the background).
- The browser was exercised on a synthetic Exam 2 workspace (verify-next,
  editing a value and the outdated check recomputing, a pseudocode page, the
  grid); the Read page's live counts and one-click read, field defaults,
  turning on the Split tab and the box editors' save buttons on synthetic
  workspaces (2026-11-13), and on Split extra sheets, missing marks and moving
  a sheet, with undo, then the placeholder, extras and a pages override with an
  extra on a question page (synthetic Exam 3 stacks). Never in a browser yet: the restore button, Stop on a run slow enough to stop.

- **Split marks, what is left:** no drag to a far place in the pile (a sheet
  moves one place per press, stepping over dropped sheets); proposed marks
  cover a submission one sheet short or long only, from page readings; a
  placeholder page is shown on the question page as is.
- **Left from the reviews:** images are deduplicated within a result, not
  across results; a region can be saved for a submission or for all, not for
  one source scan (only the print area can); `create_app` still takes the
  exam and should read `ws.exam` per request; the Exam data page shows
  skipped overrides at exam scope only (`ws.skipped_overrides()` has all);
  the Read page's "missing only" is unticked by default, which is what a
  person resuming a stopped batch must tick.
- **Names, one page per submission** (an experiment, started and stopped
  2026-11-13, uncommitted in a worktree): would make Names like the question
  page and remove the reload of every submission after each pick
  (`/scans/names` at scale). Only worth it if as fast and much simpler.
- **Cosmetic, open:** the filter summary wraps the grid header at 1400 px;
  the Filter page shows raw state names; Names shows the raw
  "[REVIEW: not read]" marker in the value box; the Read page says "N will
  read" but counts to N plus one refresh per submission.

## Future

- **Non-rectangular regions:** click points for a polygon around the actual
  answer, so the model sees the answer and not the surrounding work. Pairs
  with the per-submission region override.
- **Own values of a removed submission** stay in the store (the Exam data page
  marks them "gone", revertible); intake does not clear them.
- **No full page load between submissions:** today "next submission" loads a
  new page (actions within a page already swap parts in place). Getting there
  means the question page's scripts start from a function that can run again
  on swapped-in HTML; no library needed.
- **Numeric grades in the filter:** the Filter page lists distinct values; a
  numeric check wants a range instead.
- **"Work is present" field:** a field a reader fills with whether there is
  work on the page, so a check can give credit for an answer with work. For
  now that credit is a check with no function, decided by a person (Exam 2).
- **Barcode / QR reader** for a printed exam version, treated as true.
- **Search facets**, then a **student view** (plots and the final call, no
  test-battery lists). Exports by version are dropped.
- A left-hand key preset, with a left-handed person's input.
- "Review me later" flag (DESIGN §15).
- A filter overlay on the question page, opening on that question's filters.
- Deployment behind an authenticating proxy: not ranked; ask first.
- Deployment: the server reaching the laptop's model through an SSH tunnel so
  it appears on the server's loopback (FERPA judgement is the owner's).

## Known gaps

- Filter page shows raw state names (`to_decide`).
- A narrow (~900 px) question page can scroll sideways with a wide crop.
- Never exercised in a browser: uploading scans, a roster from a file,
  Accept's confirmation dialog, the Names tab for PDFs handed in already split.
- Exam 3 declares no landmarks or identity fields (boxes are drawn on the
  Names tab; with `--fake` they read the synthetic stacks' names, but an email
  box drawn on synthetic stacks has no scripted answer).
- Picking a person already named for another submission names both (flagged
  "name twice"); it does not offer to move them.
- No live-model run yet.

## Declined so far, larger than a cleanup

- Definition overrides stored as artifacts (would give them undo).
- One mechanism for the three kinds of background job.
- The web app split into route groups.
- An "ok as is" acknowledgement for flagged submissions.
- A redesign of the Exam data page.

## Feature candidates to cut (inventory of 2026-11-12)

Still undecided: `--public-url` and the proxy user header; the `intake` CLI
command.

Library options no example exam uses (`Bool(worth=)`, `Printed(verify=True)`,
`Field(multiline=)`, `Exam(crop_pad=)`, `math.Approx`, a custom
`Question(facets=)`): keep the code, leave the unused ones out of the
authoring guide.

## Very tentative

- **Model-assisted name matching:** give the model the crop and the roster's
  fuzzy matches and let it say which is plausible. Opens onto the model using
  tools interactively, a much larger topic. The matching heuristic is internal
  and can change with testing; the candidate rule is "the read identity fields,
  fuzzy-searched together, turn up exactly one person". Whether a person is
  already matched elsewhere never ranks or settles anything: it is only shown.
