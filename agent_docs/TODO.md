# Examinator: what is next

One list, kept current. Design background is in [`DESIGN.md`](DESIGN.md); its
§15 says what the core must already provide for later layers.

## Organizing principle (decided 2026-11-11)

- **Field:** a region, a way to read it (handwriting, printed text, later a
  barcode), and the value read. The only things read from paper.
- **Check:** a function from field values to a result, possibly with parts.
- **Question:** some fields and at most one check: the page a grader sees, the
  unit of verification, and the unit of grading.
- **Checks read fields; nothing reads a check.** A check may read any
  question's fields; the library records what it read. Two layers, no graph.
- No separate concept for versions, cues or exam information: a printed input
  is a field, and "exam information" is a question with printed fields.
- Names and IDs stay their own hard-coded thing.

## Revision round, after the rewrite lands

- **Reading is one phase.** A handwritten field is one read with one value to
  verify. Two prompts may stay inside the reader; the transcript is not shown,
  not verified, not stored. No "parse again from the old transcript".
- **A field's region for one submission.** Same shape as the per-submission
  page override: exam default, optionally replaced for one submission, then the
  ordinary read runs. ("Read again" is not a feature: it is the read, after
  changing where it looks.)
- Open after the rewrite: one unread field in a shared information question
  holds back every question that reads it; a manual grade is not outdated when
  a question it would read changes; editing how a printed field is read
  outdates nothing until the parse is run again.
- The guide's code blocks are checked by hand, not by a test.

## Future

- **Non-rectangular regions:** click points for a polygon around the actual
  answer, so the model sees the answer and not the surrounding work. Pairs
  with the per-submission region override.
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
  Accept's confirmation dialog, "read names", the Names tab for PDFs handed in
  already split.
- Exam 3 declares no landmarks or identity boxes.
- No live-model run yet (`compare` untested live).
- The guide (`WRITING_AN_EXAM.md`) is longer than intended.

## Declined so far, larger than a cleanup

- Definition overrides stored as artifacts (would give them undo).
- One mechanism for the three kinds of background job.
- The web app split into route groups.
- An "ok as is" acknowledgement for flagged submissions.
- A redesign of the Exam data page.
