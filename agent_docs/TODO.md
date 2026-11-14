# Examinator: what is next

One list, kept current. Design background is in [`DESIGN.md`](DESIGN.md); its
§15 says what the core must already provide for later layers.

## Where things stand (2026-11-14)

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
    sheet. A verified submission is its sheets (V says the whole run;
    `verified` means a person looked at it, `said` that a person placed a
    mark). A written file is never changed: Accept writes fresh files in
    `made/` and one commit names them. Accept speaks for a person and asks
    about everything a person said (grades, picks, own boxes).
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
  - *Values:* a value is what was read; whether a person should look is a
    status (`Value.review`, a note), set by the model's reply (`unsure`) or
    from the field's menu, cleared by verifying or editing. No text markers.
    Silence stores nothing: a field the reply says nothing usable about stays
    not read. A blank is an ordinary value and is never verified without a
    person. Every reader's reply is JSON, `{"value", "unsure"}` per field. The
    model transcribes code once and the program splits it by function.
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
- **Quality review and cleanup (2026-11-14):** an independent review of core
  and web, then two cleanups, merged. *Core:* a value's small changes are
  `Value`'s, and a read never undoes what a person said (verified, or
  flagged); one loader of stored rows (`status.Ledger`), `Store.version` moves
  on every write; the evidence block grammar is the block classes, in
  `core.result`; a reader is a `core.reading.Reader`; roster parsing and
  matching are `core.roster`; renames and dead surface out. *Web:* element ids
  are one namespace; `look(st)` draws every status; a POST route's kind (change,
  run, query) is its mechanism in `create_app`, the app-wide lock marked
  `exclusive`; a run is `Changes.run`/`Changes.watch` in the browser; the two
  rectangle editors share shape, drag, words and toolbar; one word per idea.
  The cleanup itself has not been independently reviewed.
- **Verified how:** every change passed the whole suite and the four exams'
  self-tests against the instructor blanks on `main` after merging, and most
  were exercised in a browser on synthetic workspaces by the agent that made
  them. Independent reviews: 2026-11-13 morning (overrides, identity, images,
  Split) and evening (all of the day's changes, core and web); an end-to-end
  browser pass after the evening fixes. What came after that pass (the
  end-to-end fixes, review as a status, the code split, synth's placement)
  has not been independently reviewed. Prerendering has not been exercised in
  a browser that does it. Subagent work is merged on tests and spot checks,
  not a line-by-line read.
- **Live model (2026-11-14, gemma 26b on the owner's laptop, synthetic pages
  on the real blanks only):** the client, reads, Stop and resume work. Exam
  3, 14 submissions: short answers 347/350 exact, none stored blank without
  being stated; code 48/48; printed 70/83 (8 are spacing the checks ignore, 4
  one question read from the wrong list item); all 235 replies followed the
  JSON shape. About 24 s and 24 calls per submission; two calls at once is
  only ~16% faster. Says nothing about real handwriting: synthetic answers
  are a font. Not run live: Exams 1 and 2, the browser under a slow model,
  name-matching doubts, a second model for comparison.
- **Model (decided 2026-11-14):** gemma 26b (`google/gemma-4-26b-a4b-qat`,
  the CLI default) for everything. Compared on identical synthetic
  workspaces of the toy exam and Exams 1-3, gemma 12b was about 2.5 times
  slower per call (58 s against 23 s per Exam 3 submission) and no more
  accurate except on a few printed values; it makes up answers on blank pages
  (34 fields) and misreads digits. The owner will not try a smaller model.
  About 1 h (Exam 3) to 1.5 h (Exam 2) to read 150 students on 26b, plus
  about 35 min of page readings at intake for a 22-page exam.
- **Accepted as they are (the owner, 2026-11-14):** reading pseudocode is
  unreliable with any model and no effort goes into making the code reader
  more reliable; a person corrects what it gets wrong. Known and left: it
  drops a function the model keys with parentheses, and treats a bare `pass`
  as unstated. A reader is not expected to notice an upside-down page:
  nothing is read from a scan before a person accepts its split, except the
  page readings Split uses, and turning is done there. Not built: detecting
  orientation; synthetic multiple choice as filled bubbles.
- **Next, in the owner's rough order:** a filter overlay on the question page;
  search facets; then a student view.
- **For the owner:** Exam 3's package labels question 1's fields 1.1-1.4
  while the page prints the course's placeholder notation; the reader copes
  by matching boxes in order. `fields.table` (std) maps each printed text to a
  version name for exams whose version is told by fields with different
  texts. Exam 3 uses `readers.code(then=)`, which stores the rewritten code as
  the value, while the kit's `check_source(ran=)` keeps the value as written
  and shows what ran: two ways to the same end, pick one. Split uses W/S for
  previous/next submission where other pages use A/D, and shows 14 controls
  at once.
- **Size:** the rework and the cleanup did not make the library smaller:
  `git diff 9c630bf --shortstat -- src` (2026-11-13 morning to the cleanup)
  is 5,357 lines in, 4,239 out, about 1,100 more.

## Left by the cleanup

- **Renames not taken:** `Row` in both `store` and `grid`; `Value` and
  `Table` in `std.fields` against `status.Value` and `result.Table`;
  `groups` (`Question.groups`, `Exam.groups`, `Reads.groups`); `settle`
  (`result.settle`, `split.settle`); `propose`/`proposals`; `plan` (batch,
  intake); `unread`; `Made.pages` holding sheets; the store column
  `submission` holding a source id for split rows; the intake edit-op key
  'restore' (calls `split.put_back`); `overrides.set`/`split`/`join`/`scope`
  shadowing builtins, and a scope in three representations (level and id, the
  stored string, keyword arguments); the 'choice' override kind duck-typing
  `accepted`; the join that fills `Submission`'s name inside the store.
- Lower-case factories in std (`readers.answers`, `readers.code`,
  `math.keyed`, ...) that a dataclass's `__post_init__` could replace.
- `expect_rev` defaults to ANY in some `Runner` methods (`set`, `unset`,
  `decide`, `clear`); 48 test call sites rely on it.
- Core logic still shaped in the web layer: `picked_key`/`key_entries`,
  `show_value` with entries.js's `readEntry`, `plan_view`, `scans_names`
  adding keys, the web adding the author to override conflict wording,
  `intake.accept` setting its own `current`.
- The two rectangle editors still save differently: Names saves every changed
  box at once and drops a box equal to the wider scope; the question page
  saves the selected region at the chosen scope. One editor means choosing one
  behaviour: the owner's call.
- The question page's grade buttons are mapped to their actions through a
  second table of their classes (`GRADES` in question.js).

## Open

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
  a sheet, with undo, then the placeholder and extras on a question page
  (synthetic Exam 3 stacks). Never in a browser yet: a value's restore
  button, Stop on a run slow enough to stop.

- **Split marks, what is left:** no drag to a far place in the pile (a sheet
  moves one place per press, stepping over dropped sheets); proposed marks
  cover a submission one sheet short or long only, from page readings; a
  placeholder page is shown on the question page as is.
- **Left from the reviews:** images are deduplicated within a result, not
  across results; a region can be saved for a submission or for all, not for
  one source scan (only the print area can); the Read page's "missing only"
  is unticked by default, which is what a person resuming a stopped batch
  must tick.
- **Names, one page per submission** (an experiment, started and stopped
  2026-11-13, uncommitted in a worktree): would make Names like the question
  page and remove the reload of every submission after each pick
  (`/scans/names` at scale). Only worth it if as fast and much simpler.
- **Cosmetic, open:** the filter summary wraps the grid header at 1400 px.

## Future

- **Decided against, 2026-11-13 (the owner raised both and walked them
  back):** building an exam's structure in the app, and versions as a concept
  of their own. Structure lives in the Python package: the expected way to get
  one is to hand an agent the exam PDFs and have it write the package with
  this library, so large structural edits in the app are unlikely. A version
  is just a field; an answer key that differs by version is a key that
  differs by that field's value. The app edits values only (regions, the
  print area, a reader's or check's own configuration), each valid on its
  own. If a field's set of possible values ever comes back, it is a plain
  list on the field (no translation, no per-question override).

- **Non-rectangular regions:** click points for a polygon around the actual
  answer, so the model sees the answer and not the surrounding work. Pairs
  with the per-submission region override.
- **Own values of a removed submission** stay in the store (the Exam data page
  marks them "gone", revertible); intake does not clear them.
- **No full page load between submissions** (at odds with "real page loads
  made fast", which the owner confirmed later; ask before starting): today
  "next submission" loads a new page, rendered ahead where the browser can.
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

- A narrow (~900 px) question page can scroll sideways with a wide crop.
- Never exercised in a browser: uploading scans, a roster from a file,
  Accept's confirmation dialog, the Names tab for PDFs handed in already split.
- Exam 3 declares no landmarks or identity fields (boxes are drawn on the
  Names tab; with `--fake` they read the synthetic stacks' names, but an email
  box drawn on synthetic stacks has no scripted answer).
- Picking a person already named for another submission names both (flagged
  "name twice"); it does not offer to move them.

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
