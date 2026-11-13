# The model

The model the core implements (built 2026-11-12). Everything here is per exam;
"stored" means per submission, in the workspace. Where each piece lives is at
the end.

## In one paragraph

Paper is read into **fields**. **Checks** compute results from field values.
People **settle** field values and, where a check cannot decide, make
**decisions**. A **question** only groups fields and a check for the page, the
grid and the totals; it has no state. All state is on fields, results and
decisions, and every action in the app is a combination of a few small changes
to those.

## Defined in code

| Object | What it is |
| --- | --- |
| **Field** | A named slot for one value read from paper: where to look (pages, region) and which reader reads it. |
| **Reader** | How values get from page images into fields: handwriting (model), printed text (model, from a closed set of expected readings), later a barcode. One read fills one or more fields. A reader may **vouch** for a value it read. |
| **Check** | A function from field values to a **result**: one proposed grade, and evidence to show. It may read any field in the exam. Nothing reads a check. Its points are part of its definition. |
| **Question** | A group: some fields and any number of checks. The page a grader sees; each check is a grid column. |

An **override** replaces one of these values from the app without editing the
code (a region, pages, the print area, a printed field's expected readings, an
answer key) at a **scope**. It is stored in the workspace and wins while it
exists; clearing it brings the next scope's value back. A value may also be
left unset in code and given only as an override. A field that cannot be read
yet is simply not read.

| Scope | Applies to | May set (`overrides.SCOPES`) |
| --- | --- | --- |
| submission | one submission | a region, pages (its extra sheets too), print area |
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
printed word; possibly with a `[REVIEW: ...]` marker).

| State | Meaning |
| --- | --- |
| **not read** | No value yet. |
| **unsettled** | Has a value nobody stands behind. Shown with the "unverified" badge. |
| **settled** | Someone stands behind the value: a person, or the reader that vouched for it. No badge. |

One attribute, `settled by`: nobody, the reader, or a named person. That is the
whole state. (In the code and on screen these are 'not_read', 'unverified' and
'verified': `status.FIELD_STATES`, and the `settled` attribute of a `Value`.)
Choices made here:

- **"Typed by a person" and "read, then verified by a person" are the same
  state.** A person who sets a value settles it. Who wrote a value is history,
  not state.
- **Settled by a reader and settled by a person are the same to a check.** They
  differ in one thing only: reading again replaces a value the reader settled,
  never one a person settled.
- **A value with a review marker cannot be settled.** A marker is not a state.
- **Blank is a value** (empty text, read as such). It differs from not read.
- **Reading again and getting the same value changes nothing**: same revision,
  still settled, nothing downstream moves.
- **No transcript.** Whatever a reader does on the way to the value is not
  stored.
- **A default is a value nobody need settle.** A field may have a default
  (`Field(label, default='10')`): a setting a person may type, read by no
  reader but its own. While it has no stored value, a check reading it gets
  the default, recorded at revision `DEFAULT` (0; stored revisions start at
  1). That read is current while the field still has no stored value, and
  counts as settled. Setting a value outdates the result like any change;
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
| **to decide** | No check; or the result is current, its fields settled, and the proposal is undecided. | No |
| **not run** | No result. | No |
| **outdated** | The result is outdated. | No |
| **unverified** | The result is current and some field it read is not settled (a field read at its default is settled). The proposal is shown. | No |
| **graded** | The result is current, every field it read is settled, the proposal is a grade. | Yes |

A check that read no field at all never counts by itself ("to decide").

## The small changes

Everything the app or a batch run does is one or more of these.

| On | Change | By |
| --- | --- | --- |
| Field value | **read**: a reader sets the value; settled by the reader if it vouches, else unsettled. Skips values a person settled. | run |
| Field value | **set**: a new value, settled by that person. | person |
| Field value | **verify**: settle the value as it stands. | person |
| Field value | **unverify**: back to unsettled, value kept. | person |
| Field value | **unset**: a field with a default back to it (its value dropped). | person |
| Result | **compute**: run the check on the current values. | automatic |
| Decision | **decide**, **clear**. | person |
| Override | **set**, **clear**, at a scope. | person |

Changing how a field is read changes no value. The app then offers to read
again the values no person settled.

Larger actions are combinations: "verify all here" verifies every unsettled
field on the page; "save and verify" is one set; "read again" is a read of one
question's fields; a batch run is reads, then computes.

## Changing the exam while grading

Stored things are keyed by name (submission and field; submission and check), and a result records which version of the
exam's code computed it. So the exam package can change under a workspace:

| Change | What happens to stored work |
| --- | --- |
| A check's code or key changes | Field values and decisions untouched. That check's results are outdated and recomputed. Results keep no history (they are recomputable); listing which grades changed would be done by comparing at recompute time (not built). |
| A question or check is added | Its fields are not read, its checks not run. Nothing else moves. |
| A field is added to a question | The other fields keep their values and who settled them. The new one is not read. A check is affected only once it reads the new field: then it is recomputed and waits for it. |
| A field or check is removed or renamed | Its stored values are ignored, not deleted (restoring the name restores them). A rename loses the work. |
| Points change | Totals follow at once; they are derived. |
| How a field is read changes | No value changes; the app offers to read again what no person settled. |

Two things this asks of the build: a read can fill only the fields that have
no value yet, and the app can reload the exam package without losing state.

## What the interface does with it (not part of the model)

- A question's page shows its own fields and the fields its check read from
  elsewhere, so "verify all here" settles everything the grade rests on.
- A field shows a badge only when unsettled.
- The grid shows one cell per check, by the table above.

## What goes away

- Stages with inputs (transcribe, parse, evidence, grade) as stored steps:
  what remains is field values, results, decisions.
- The difference between a check's own question and other questions.
- "Waits on another question", the trusted-fields list, verification per
  question, and the special no-grade question: it is fields with no check.
- Identification is a built-in group of ordinary fields plus a pick. The
  group (`defs.IDENTITY`, '@identity') has one field per roster column (name,
  ID, email; those the exam maps placed, the others drawn in the app): values,
  settling, reads, regions at every scope, all as for any field. It is kept
  apart from grading (`Question.apart`: no grid column, total, facet or batch
  read; read on the Names step). Who wrote a submission is then one stored
  pick per submission, a person's choice among proposals computed purely from
  those values and the roster (`core.identity`).

## Open, to settle by trying

- **One read per field or per question?** The model allows both (a read fills
  one or more fields; a field may have a reader of its own). The library's
  handwriting readers read a question's fields in one go. Which reads
  handwriting more reliably is a live-model question.
- Whether the grid needs a column for a question with no check, once its
  fields show on the pages of the questions that read them. (It has one now:
  not read, unverified, or verified.)

## Where it lives

| Piece | Code |
| --- | --- |
| Field, Check, Question, Exam, points (`Bool`, `Number`) | `core/defs.py` |
| What a check returns (`Result`, `Judgement`, `Undecided`) | `core/result.py` |
| `ctx.read` (recorded reads), the reader's context | `core/context.py` |
| Stored value, result, decision; the status table; totals | `core/status.py` |
| The small changes (read, set, verify, compute, decide, clear) | `core/runner.py` |
| Asking about one region (`Ask`, `Printed`); handwriting readers | `core/ask.py`; `std/readers.py` |
| Overrides at every scope; what applies to one submission (`Layers`) | `core/overrides.py`; `Workspace.layout`, `fields` |
| Batch: reads, then computes | `core/batch.py` |
| One row table, addressed `(submission, name, kind)` | `core/store.py` |

Choices made while building it:

- A value's revision changes only with its text; settling or unsettling keeps
  it (and goes to history). So verifying never outdates a result or a decision.
- "A person settled it" protects a value from reads; a value a person set and
  then unverified is unsettled, so a read may replace it.
- A result is current while its check's definition hash (`Exam.check_hash`:
  the check function and its configuration, keys included) and the exam
  package's code hash are unchanged and every field it read is at the revision
  it read. Points are not part of it.
- A decision records the question's own fields and the fields that check's
  result read.
- Automatic recomputation recomputes outdated results only (and, after a
  change on a question's page, that question's checks that never ran). A
  value a grader sets for a check (a constant, say) is a field with a default,
  so it outdates and recomputes like any other.
