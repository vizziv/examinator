# The model

The model the core implements. Everything here is per exam; "stored" means
per submission, in the workspace. Where each piece lives is at the end.

## In one paragraph

Paper is read into **fields**. **Checks** compute results from field values.
People **verify** field values and, where a check cannot decide, make
**decisions**. A **question** only groups fields and a check for the page, the
grid and the totals; it has no state. All state is on fields, results and
decisions, and every action in the app is a combination of a few small changes
to those.

## Defined in code

| Object | What it is |
| --- | --- |
| **Field** | A named slot for one value read from paper: where to look (pages, region) and which reader reads it. |
| **Reader** | How values get from page images into fields: handwriting (model), printed text (model), later a barcode. One read fills one or more fields. A reader may **vouch** for a value it read. |
| **Check** | A function from field values to a **result**: one proposed grade, and evidence to show. It may read any field in the exam. Nothing reads a check. Its points are part of its definition. |
| **Question** | A group: some fields and any number of checks. The page a grader sees; each check is a grid column. |

An **override** replaces one of these values from the app without editing the
code (a region, the print area, a printed field's question, an
answer key) at a **scope**. It is stored in the workspace and wins while it
exists; clearing it brings the next scope's value back. A value may also be
left unset in code and given only as an override. A field that cannot be read
yet is simply not read. An override is valid exactly when the definition with
it is: the one validation the package goes through (`Exam.validate`) decides.
Each is valid on its own, never only together with another; a question's
pages are not overridable.

| Scope | Applies to | May set (`overrides.SCOPES`) |
| --- | --- | --- |
| submission | one submission | a region (on its extra sheets too), print area |
| source scan | every submission split from one scanned file | print area |
| exam | every submission | anything editable |
| (package) | what the code says | |

The value in force is the most specific one: the submission's, its scan's,
the exam's, the package's.

## Stored per submission

There are three kinds of stored thing. Each has a revision that changes only
when its content changes. Values and decisions keep their history; results do
not, since they can be recomputed.

### 1. Field value

One per field. Its content is text (a number, an expression, source code, a
printed word): what was read, never a note about it.

| State | Meaning |
| --- | --- |
| **not read** | No value yet. |
| **review** | Has a value someone said a person should look at: the reader was unsure, or a person flagged it (with a note, possibly empty). Shown with the "review" badge. |
| **unverified** | Has a value nobody stands behind. Shown with the "unverified" badge. |
| **verified** | Someone stands behind the value: a person, or the reader that vouched for it. No badge. |

Three attributes: `verified` (nobody, the reader, or a person, named in
`by`), `review` (none, or a note) and `flagged` (who asked for the review: the
reader or a person). That is the whole state (`status.FIELD_STATES`;
`Value.state`). `Value` holds the rule that a value needing review is never
verified, and its methods are the small changes to a value. The rules:

- **"Typed by a person" and "read, then verified by a person" are the same
  state.** A person who sets a value verifies it. Who wrote a value is history,
  not state.
- **Verified by a reader and verified by a person are the same to a check.** They
  differ in one thing only: reading again replaces a value the reader verified,
  never one a person verified.
- **Whether a person should look is a status, not text.** A value that needs
  review is never verified and never vouched for; a check that read it is
  **review** (below). A person verifying it, or saving an edit, clears it; a
  person may flag a value (or take the flag off) at any time. Flagging keeps
  the revision, like verifying.
- **Blank is a value** (empty text, read as such). It differs from not read. No
  reader vouches for a blank: a person verifies it.
- **Silence is not a value.** A read writes a field only from an explicit
  statement about that field in the model's reply (`core.reading`: one field
  object, `{"value": ..., "unsure": ...}`). Anything else (the field missing
  from the reply, null, the string "null", a reply that is not that shape)
  writes nothing: the field stays not read, and the read says how many fields
  it asked for and how many got a value.
- **What a person said about a value is never undone by a reader.** A read
  skips a value a person verified (unless asked: `include_protected`). A read
  of the same text changes nothing a person set (verified, or flagged for
  review) and keeps the revision, so nothing downstream moves. A read of
  different text is a new value with the reader's state (for review if unsure,
  verified by the reader if it vouches, else unverified).
- **No transcript, no memo.** Whatever a reader does on the way to the value
  is not stored, so every read asks the model again. A read happens only
  when a person asks for one; "missing only" resumes a read that stopped.
- **Changing how a field is read changes no value.** A value does not record
  what it was read from; reading again is a person's choice (a question's
  "read", the Read page, "read names").
- **A default is a value nobody need verify.** A field may have a default
  (`Field(label, default='10')`): a setting a person may type, read by no
  reader but its own. While it has no stored value, a check reading it gets
  the default, recorded at revision `DEFAULT` (0; stored revisions start at
  1). That read is current while the field still has no stored value, and
  counts as verified. Setting a value outdates the result like any change;
  clearing the value (back to the default) outdates it again.

### 2. Result

One per check. Content: a proposed grade (a grade, or "undecided" with a
reason), the evidence, **which fields it read and at which revisions**, and
which definition of the check computed it.

Evidence has two parts, both optional:

- **Per field:** for any field the check judged, an outcome ('correct',
  'incorrect', 'blank', or none) and optionally the expected answer and a short
  note. Interfaces show this beside the field. "4 of 5 right" is the grade;
  which four is this.
- **Blocks:** an ordered list of things to show (text, table, code,
  collapsed details, images such as plots).

Nothing computes from evidence. It is for people.

A result is a cache of a pure function. Its states are derived, never stored:

| State | Meaning |
| --- | --- |
| **not run** | No result. |
| **current** | Every field it read is at the revision it read, under the current check. |
| **outdated** | Otherwise. Recomputed automatically; visible only until that finishes. |

A check with no result runs automatically once every field of its question
has a value (stored, or its default); so "not run" means a field is not read.

### 3. Decision

One per check, only where a person decided the grade by hand. Content:
the grade, a reason, who, and **the revisions of the fields on the page when
they decided**. It is outdated when any of those fields has changed since.

## The grade of a check (derived)

In order; the first that applies:

| Status | When | Counts |
| --- | --- | --- |
| **decided** | A decision exists and its fields are unchanged. | Yes |
| **decided, outdated** | A decision exists, a field changed since. | No |
| **to decide** | The check has no function (`Check(None)`); or the result is current, its fields verified, and the proposal is undecided. | No |
| **not run** | No result. | No |
| **outdated** | The result is outdated. | No |
| **review** | The result is current and some field it read needs review ("x needs review"). The proposal is shown. | No |
| **unverified** | The result is current and some field it read is not verified (a field read at its default is verified). The proposal is shown. | No |
| **graded** | The result is current, every field it read is verified, the proposal is a grade. | Yes |

A check that read no field at all never counts by itself ("to decide").

## The small changes

Everything the app or a batch read does is one or more of these.

| On | Change | By |
| --- | --- | --- |
| Field value | **read**: a reader sets the value, for review if the model was unsure; verified by the reader if it vouches, else unverified. Writes nothing for a field the reply says nothing about. Skips values a person verified; the same text keeps what a person said. | run |
| Field value | **set**: a new value, verified by that person. | person |
| Field value | **verify**: the value as it stands, now verified by that person (any review cleared). | person |
| Field value | **review**: flag the value for review, with a note (unverified, flagged by a person); or take the flag off. | person |
| Field value | **unverify**: back to unverified, value kept. | person |
| Field value | **unset**: a field with a default back to it (its value dropped). | person |
| Result | **compute**: run the check on the current values. | automatic |
| Decision | **decide**, **clear**. | person |
| Override | **set**, **clear**, at a scope. | person |

Changing how a field is read changes no value. Reading again is asked for
explicitly; it replaces only values no person verified.

Larger actions are combinations: "verify all here" verifies every unverified
field on the page (not one that needs review: that is verified one by one);
"save and verify" is one set; a question's "read" is a read of its fields; a
batch read is reads, then computes.

## Changing the exam while grading

Stored things are keyed by name (submission and field; submission and check), and a result records which version of the
exam's code computed it. So the exam package can change under a workspace:

| Change | What happens to stored work |
| --- | --- |
| A check's code or key changes | Field values and decisions untouched. That check's results are outdated and recomputed. Results keep no history (they are recomputable); listing which grades changed would be done by comparing at recompute time (not built). |
| A question or check is added | Its fields are not read, its checks not run. Nothing else moves. |
| A field is added to a question | The other fields keep their values and who verified them. The new one is not read. A check is affected only once it reads the new field: then it is recomputed and waits for it. |
| A field or check is removed or renamed | Its stored values are ignored, not deleted (restoring the name restores them). A rename loses the work. |
| Points change | Totals follow at once; they are derived. |
| How a field is read changes | No value changes. Reading again, when a person asks, replaces what no person verified. |
| A submission's pages are rewritten (Split's Accept) | Values no person verified are dropped (not read), and so are its own overrides (where things are on its old pages); values a person verified, decisions and the pick stay. |

## What the interface does with it (not part of the model)

- A question's page shows its own fields and the fields its check read from
  elsewhere, so "verify all here" verifies everything the grade rests on.
- A field shows a badge only when it needs review or is unverified.
- The grid shows one cell per check, by the table above.

## Identity

Who wrote a submission is a built-in group of ordinary fields plus a pick. The
group (`defs.IDENTITY`, '@identity') has one field per roster column
(`defs.ROSTER_FIELDS`: name, ID, email; those the exam places, the others
drawn in the app): values, verifying, reads and regions at every scope, all as
for any field. It is kept apart from grading: not one of `Exam.questions` (no
grid column, total, facet or batch read), reached only by its name, read on the
Names step. The pick is one stored row per submission, a person's choice among
proposals computed purely from those values and the roster (`core.roster`).
The pick records the person; their place on the roster is derived. Automatic
matching acts only on unique evidence; a tie or a conflict is a doubt for a
person.

## Open, to settle by trying

- **One read per field or per question?** The model allows both (a read fills
  one or more fields; a field may have a reader of its own). The library's
  handwriting readers read a question's fields in one go. Which reads
  handwriting more reliably is a live-model question.
- Whether the grid needs a column for a question with no check, once its
  fields show on the pages of the questions that read them. (It has one now:
  not read, review, unverified, or verified.)

## Where it lives

| Piece | Code |
| --- | --- |
| Field, Check, Question, Exam, points (`Bool`, `Number`) | `core/defs.py` |
| What a check returns (`Result`, `Judgement`, `Undecided`) | `core/result.py` |
| `ctx.read` (recorded reads), the reader's context | `core/context.py` |
| What a read reads, and why not (`to_read`) | `core/runner.py` |
| What a reply says about a field (`Reading`); what a reader is (`Reader`) | `core/reading.py` |
| Stored value, result, decision; the status table; totals; loading them (`Ledger`) | `core/status.py` |
| The small changes to a value (`Value.read`, `set`, `verify`, `unverify`, `flag`) | `core/status.py` |
| The small changes on a workspace (read, set, verify, unverify, review, unset, compute, decide, clear) | `core/runner.py` |
| Asking about one region (`Ask`, `Printed`); handwriting readers | `core/ask.py`; `std/readers.py` |
| A workspace's own definition (`Workspace.exam`); overrides at every scope; what applies to one submission (`Layers`: `question`, `layout`) | `core/workspace.py`; `core/overrides.py`; `Workspace.layers` |
| Batch: reads (counted, stoppable), then the refresh of what was read for | `core/batch.py` |
| One row table, addressed `(submission, name, kind)` | `core/store.py` |
| Identity: the pick and the identity fields; the roster and matching | `core/identity.py`; `core/roster.py` |

Further rules:

- A value's revision changes only with its text; verifying or unverifying keeps
  it (and goes to history). So verifying never outdates a result or a decision.
- "A person verified it" protects a value from reads; a value a person set and
  then unverified is unverified again, so a read may replace it.
- A result is current while its check's definition hash (`Exam.check_hash`:
  the check function and its configuration, keys included) and the exam
  package's code hash are unchanged and every field it read is at the revision
  it read. Points are not part of it.
- A decision records the question's own fields and the fields that check's
  result read.
- Automatic recomputation is one rule (`Runner.refresh`): an outdated result
  is recomputed; a check that never ran runs once its question's fields all
  have a value. DESIGN.md §7 lists where it is applied. A value a grader sets
  for a check (a constant, say) is a field with a default, so it outdates and
  recomputes like any other.
