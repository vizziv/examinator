"""Default prompt texts, as values, and small functions that build fragments.

Every reader accepts its prompts as strings. Adding a sentence is `notes=` or
concatenation:

    readers.answers(prompt=prompts.TRANSCRIBE_ANSWERS + " Angles are in degrees.")

Answer keys never appear in any prompt: only labels and notation do. How a
model states that nothing is written is BLANK, in every prompt
(`core.reading`): anything else that states no value is "not read".
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
no handwriting, write `label: BLANK`. If the printed labels on the page are missing \
or garbled, match boxes to labels by their order from top to bottom. Preserve every \
mistake and unsimplified expression. Do not solve, correct, grade, infer missing \
answers, or describe supporting work. Read crossed-out attempts only to tell the \
final answer. For a choice, record the option the student marked or wrote, \
faithfully; if several remain selected, or the writing is ambiguous, write \
[REVIEW: reason] as the answer. Ignore unrelated questions on additional pages."""

TRANSCRIBE_PSEUDOCODE = """\
Transcribe the final handwritten pseudocode for every part of this question, \
including any helper functions the student wrote. Copy it literally, character by \
character: every constant, operator, name and indentation exactly as written, with \
its mistakes and omissions. Do not complete, solve, fix, simplify or assess it. \
Where you are unsure what is written, copy your best reading and add \
# [REVIEW: reason] on that line. Write each function's header, printed or \
handwritten, above the student's body; printed comments such as # TODO are not the \
student's. For a requested function whose body the student left empty, write its \
header followed by BLANK. Ignore other questions."""

PARSE_ANSWERS = """\
Normalize the supplied final-answer transcription into exactly this JSON \
structure, without Markdown: {"answers": {"label": "answer"}}, with one entry for \
every supplied label and no other keys. Scalars use explicit multiplication, ** \
powers, sqrt/exp/log and lowercase variable names. An answer transcribed as BLANK \
is the string "BLANK"; never use null or leave a label out. Preserve mistakes. Do \
not solve, simplify, infer missing content, include a key, or assess work. For a \
choice keep the chosen option text or letter. For ambiguity use a string \
containing [REVIEW: reason]."""

PARSE_PSEUDOCODE = """\
Convert the supplied pseudocode transcription to the restricted language below, \
and return only that code: plain text, no Markdown, no commentary. Write every \
requested function, and every helper function the student wrote, once each. \
Preserve the student's algorithm and all mistakes; copy every constant exactly. Do \
not repair missing steps, constants, helpers or off-by-one mistakes. You may \
normalize syntax (e.g. ^ to **) without changing the meaning. A requested \
function the transcription marks BLANK, or that has only its printed header, is \
written as its header with the body `pass`; never add code the student did not \
write. Keep every [REVIEW: reason] as a comment, and use # [REVIEW: reason] for \
ambiguity or a construct outside the language."""


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
