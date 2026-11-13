"""Default prompt texts, as values, and small functions that build fragments.

Every reader accepts its prompts as strings. Adding a sentence is `notes=` or
concatenation:

    readers.answers(prompt=prompts.TRANSCRIBE_ANSWERS + " Angles are in degrees.")

Answer keys never appear in any prompt: only labels and notation do. Every
reply that the program reads is JSON of field objects, `{"value": ..., "unsure":
...}` (`core.reading`): an empty value is a blank, and `unsure` says what a person
should look at.
"""
from __future__ import annotations

from typing import Iterable, Mapping

from ..core.defs import Question
from ..core.ask import HANDWRITTEN_PROMPT, PRINTED_PROMPT  # noqa: F401  (re-exported)
from ..model.client import PREAMBLE  # noqa: F401  (re-exported)

TRANSCRIBE_ANSWERS = """\
Transcribe only the final answers for the named question. Write one line per \
answer label, in the order the labels are listed below, as `label: answer`. The \
answer is only what is handwritten inside that label's answer box; printed text \
(including the label itself and printed options) is never an answer. If a box has \
no handwriting, write `label: (empty)`. If the printed labels on the page are \
missing or garbled, match boxes to labels by their order from top to bottom. \
Preserve every mistake and unsimplified expression. Do not solve, correct, grade, \
infer missing answers, or describe supporting work. Read crossed-out attempts only \
to tell the final answer. For a choice, record the option the student marked or \
wrote, faithfully. Where you are unsure what is written (several choices marked, \
an unclear character), give your best reading and add `(unsure: why)` after it. \
Ignore unrelated questions on additional pages."""

TRANSCRIBE_PSEUDOCODE = """\
Transcribe the final handwritten pseudocode for every part of this question, \
including any helper functions the student wrote. Copy it literally, character by \
character: every constant, operator, name and indentation exactly as written, with \
its mistakes and omissions. Do not complete, solve, fix, simplify or assess it. \
Where you are unsure what is written, copy your best reading and add \
`(unsure: why)` at the end of that line. Write each function's header, printed or \
handwritten, above the student's body; printed comments such as # TODO are not the \
student's. For a requested function whose body the student left empty, write its \
header followed by (empty). Ignore other questions."""

PARSE_ANSWERS = """\
Normalize the supplied final-answer transcription into exactly this JSON, without \
Markdown: {"<answer label>": {"value": "answer", "unsure": ""}, ...}, with one \
entry for every answer label listed below, keyed by that label exactly as written \
(for example "A.1"), and no other keys. Scalars use explicit multiplication, ** \
powers, sqrt/exp/log and lowercase variable names. An answer transcribed as \
(empty) has the value "". Where the transcription says (unsure: why), give the \
best reading as the value and the reason as "unsure"; otherwise "unsure" is "". \
Preserve mistakes. Do not solve, simplify, infer missing content, include a key, \
or assess work. For a choice keep the chosen option text or letter."""

PARSE_PSEUDOCODE = """\
Convert the supplied pseudocode transcription to the restricted language below, \
function by function, and reply with exactly this JSON, without Markdown: \
{"<function name>": {"value": "def <function name>(...):\\n    ...", "unsure": ""}, ...}, \
one entry for every requested function and every helper function the student \
wrote, keyed by the function's name, each value the source of that one function. Preserve the student's \
algorithm and all mistakes; copy every constant exactly. Do not repair missing \
steps, constants, helpers or off-by-one mistakes. You may normalize syntax (e.g. \
^ to **) without changing the meaning. A requested function marked (empty), or \
that has only its printed header, is its header with the body `pass`; never add \
code the student did not write. Where the transcription says (unsure: why), or a \
construct is outside the language, keep your best conversion and say in that \
function's "unsure" what is uncertain and on which line; otherwise "unsure" is ""."""


def context(question: Question, labels: Iterable[str]) -> str:
    """What every reading prompt is followed by: the question and the labels of
    the fields being read (printed fields are read separately)."""
    return f"Question: {question.title}\nAnswer labels: {', '.join(labels)}"


def functions(wanted: Mapping[str, Iterable[str]]) -> str:
    """Which functions a code question asks for, per answer label."""
    return "Requested functions: " + "; ".join(f"{', '.join(names)} ({label})" for label, names in wanted.items())


def notation(symbols: Iterable[str] = (), functions: Iterable[str] | None = None) -> str:
    """Notation hints for a parse prompt, from declared symbol and function names."""
    parts = []
    symbols = list(symbols)
    if symbols:
        parts.append(f"Variables that may appear: {', '.join(symbols)}.")
    if functions is not None:
        functions = list(functions)
        if functions:
            parts.append(f"Functions that may appear: {', '.join(functions)}.")
        else:
            parts.append("Expressions contain no function calls: treat juxtaposition as "
                         "multiplication, including a variable followed by parentheses.")
    return " ".join(parts)


def join(*parts: str) -> str:
    return "\n\n".join(p for p in parts if p)
