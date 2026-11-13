# Examinator: what is next

One list, kept current. Design background is in [`DESIGN.md`](DESIGN.md); its
§15 says what the core must already provide for later layers.

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

- **Which grades changed** after a check's code or key changed: old results
  are in history; nothing lists them yet.
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

- **Split marks, what is left:** a sheet moved within its submission keeps
  its place in `order` if a boundary later moves across it (it then sorts
  into the neighbouring submission by that order, which may look odd);
  proposed marks cover a submission one sheet short or long only, from page
  readings; a placeholder page is shown on the question page as is (its crop
  is withheld, nothing is read from it).

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
