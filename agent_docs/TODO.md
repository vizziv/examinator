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
  need should be met by an existing primitive first; history is for what a
  person edits (values, decisions, overrides), never for computed results;
  changing how a field is read changes no value, and verified values are never
  re-read unasked; being already matched must not raise a name match's
  confidence; rare actions live in menus; the past exams are test cases, not
  behaviour to preserve; the CLI is development tooling, not a user interface.
- **Principles added by the second review (2026-11-13), each built as a
  design change rather than a patch per bug:**
  - *Split:* the plan is only the pile, sorted; what was written is `made`,
    and which run is which written submission is derived (`split.link`). A
    mark is a guess or a person's, and guessing touches only guesses; Accept
    makes every sheet a person's. A submission is its kept sheets. The store
    says "written" only about files in place.
  - *Naming:* an action is a statement about what was on screen, bound at the
    keypress with the revision seen. Automatic matching acts only on unique
    evidence; ties and conflicts are doubts. A value a reader wrote records
    what it was read from, and is stale when that differs from now.
  - *Definitions:* an override is valid exactly when the definition with it
    applied is valid (`Exam.validate`, one validation). An open workspace
    always has its overrides. The identity group is reachable only through
    the Names step.
  - *Evidence:* a result has one stored shape, enforced where a check's
    return is settled; images are stored by digest outside the result row. A
    read needs its pages (`runner.unreadable`). A kit figure is a pure
    function of its arguments, drawn only when shown.
- **Verified how:** every change passed the fast suite and the four exams'
  self-tests against the instructor blanks, and was exercised in a browser on
  synthetic workspaces by the subagent that made it. The four redesigns above
  were built in parallel and merged; the whole suite and the self-tests pass
  on the merge. No change has been run against a live model. Subagent work is
  committed after tests and spot checks, not a line-by-line read, and the
  redesigns themselves have not been independently reviewed.
- **Next, in the owner's rough order:** a first live-model run on synthetic
  handwriting (also tunes name matching); a filter overlay on the question
  page; no full page load between submissions; search facets; then a student
  view.

## The model (built 2026-11-12)

[`MODEL.md`](MODEL.md): fields, readers, checks, questions; values, results,
decisions; the status table. Built, with one test per row of its two tables.
Resolved by it: reading is one phase (no stored transcript); a field's region
for one submission; a decision is outdated when any field on its page changes;
how a field is read changes no value (the app offers to read again); a printed
field's region may be unset in code; one grade per check, any number of checks.
Overrides have one mechanism at three scopes (exam, source scan, submission;
`overrides.SCOPES`, `overrides.Layers`): a region, pages and the print area
all live there, with revisions and history. A submission maps the exam's
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
- After a reader's or region's change, the app says "read again" but does not
  list which submissions' values came from the old reading.
- The browser was exercised on a synthetic Exam 2 workspace (verify-next,
  editing a value and the outdated check recomputing, a pseudocode page, the
  grid); the Read page's live counts and one-click read, field defaults,
  turning on the Split tab and the box editors' save buttons on synthetic
  workspaces (2026-11-13), and on Split extra sheets, missing marks and moving
  a sheet, with undo, then the placeholder, extras and a pages override with an
  extra on a question page (synthetic Exam 3 stacks). Never in a browser yet: the restore button, the
  Exam data page's "read again" message, Stop on a run slow enough to stop.

- **Split marks, what is left:** no drag to a far place in the pile (a sheet
  moves one place per press, stepping over dropped sheets); proposed marks
  cover a submission one sheet short or long only, from page readings; a
  placeholder page is shown on the question page as is.
- **Left from the second review:** ordinary fields record what they were read
  from, but the question page and the batch plan do not yet use
  `Value.stale` to say "read again"; overrides apply in path order, so one
  that is valid only together with a later one is skipped; images are
  deduplicated within a result, not across results.

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
