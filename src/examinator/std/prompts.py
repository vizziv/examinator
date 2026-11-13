"""Default prompt texts, as values, and small functions that build fragments.

Every reader accepts its prompts as strings. Adding a sentence is `notes=` or
concatenation:

    readers.answers(prompt=prompts.TRANSCRIBE_ANSWERS + " Angles are in degrees.")

Answer keys never appear in any prompt: only labels and notation do.
"""
from __future__ import annotations

from typing import Iterable

from ..core.defs import Question
from ..core.ask import HANDWRITTEN_PROMPT, PRINTED_PROMPT  # noqa: F401  (re-exported)
from ..model.client import PREAMBLE  # noqa: F401  (re-exported)

TRANSCRIBE_ANSWERS = """\
Transcribe only the final answers for the named question. Use the printed labels \
supplied below. Preserve every mistake and unsimplified expression. Do not solve, \
correct, grade, infer missing answers, or describe/check supporting work. Read \
crossed-out attempts only to distinguish the final answer. Use BLANK for genuinely \
empty final-answer boxes and [REVIEW: reason] for ambiguity. For choices, record \
the selected option text/letter faithfully. If several choices remain selected, \
mark REVIEW. Ignore unrelated questions on additional pages."""

TRANSCRIBE_PSEUDOCODE = """\
Transcribe the final pseudocode for every part of this question, including clearly \
referenced helper definitions. Preserve indentation, operators, constants, variable \
names, mistakes and omissions. Do not complete, solve, fix, grade, or assess \
supporting work. Include [REVIEW: reason] wherever reading is uncertain. Clearly \
identify an empty part as BLANK. Ignore other questions."""

PARSE_ANSWERS = """\
Normalize the supplied final-answer transcription into exactly this JSON structure, \
without Markdown: {"answers": {"printed label": "expression"}}. Use precisely the \
supplied labels. Scalars use explicit multiplication, ** powers, sqrt/exp/log and \
lowercase variable names. Blank answers are JSON null. Preserve mistakes. Do not \
solve, simplify, infer missing content, include a key, or assess work. For multiple \
choice retain the chosen option text or letter. For ambiguity use a string \
containing [REVIEW: reason]."""

PARSE_PSEUDOCODE = """\
Convert the supplied final pseudocode to the restricted interpreter language below. \
Preserve the student's algorithm and all mistakes. Do not repair missing steps, \
constants, helpers, or off-by-one mistakes. You may normalize \
syntax (e.g. ^ to **); preserve semantics. Keep every requested function and all \
referenced student helpers. Leave a missing/blank function absent, instead of adding \
a solution or pass stub. Return plain pseudocode, no Markdown. Use \
# [REVIEW: reason] for ambiguity or an unsupported construct."""


def context(question: Question, labels: Iterable[str]) -> str:
    """What every reading prompt is followed by: the question and the labels of
    the fields being read (printed fields are read separately)."""
    return f"Question: {question.title}\nAnswer labels: {', '.join(labels)}"


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
