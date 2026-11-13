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
  grid); never in a browser yet: the per-submission region editor, the
  restore button, the Read page, the Exam data page's "read again"
  message.

## Future

- **Non-rectangular regions:** click points for a polygon around the actual
  answer, so the model sees the answer and not the surrounding work. Pairs
  with the per-submission region override.
- **No full page load between submissions:** today "next submission" loads a
  new page (actions within a page already swap parts in place). Getting there
  means the question page's scripts start from a function that can run again
  on swapped-in HTML; no library needed.
- **"Work is present" field:** a field a reader fills with whether there is
  work on the page, so a check can give credit for an answer with work. For
  now that credit is a check with no function, decided by a person (Exam 2).
- **Barcode / QR reader** for a printed exam version, treated as true.
- **Search facets**, then a **student view** (plots and the final call, no
  test-battery lists). Exports by version are dropped.
- A left-hand key preset, with a left-handed person's input.
- "Review me later" flag (DESIGN §15).
- Deployment behind an authenticating proxy: not ranked; ask first.

## Known gaps

- Filter page shows raw state names (`needs_review`).
- A narrow (~900 px) question page can scroll sideways with a wide crop.
- Never exercised in a browser: uploading scans, a roster from a file,
  Accept's confirmation dialog, the Names tab for PDFs handed in already split.
- Exam 3 declares no landmarks or identity boxes (boxes can now be drawn on
  the Names tab; the scripted fake model has no answers for drawn boxes, so
  "read names" fails there with `--fake`).
- Drawn name boxes are not on the Exam page's list of changes
  (`describe_changes`), and have no undo beyond "revert".
- Picking a person already named for another submission names both (flagged
  "name twice"); it does not offer to move them.
- No live-model run yet (`compare` untested live).

## Declined so far, larger than a cleanup

- Definition overrides stored as artifacts (would give them undo).
- One mechanism for the three kinds of background job.
- The web app split into route groups.
- An "ok as is" acknowledgement for flagged submissions.
- A redesign of the Exam data page.
