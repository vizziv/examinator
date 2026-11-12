"""Handwriting readers: how a question's handwritten fields are read.

    exam.question('sum', ..., read=readers.answers(notes=NUMBERS))          # short answers
    exam.question('sampler', ..., read=readers.code(['sampler'], language=LANG))  # pseudocode

A reader reads all the fields it is given in one go and returns one value per
field (text; '' is blank). Inside, it may make two model calls: one to
transcribe the pages, one to turn the transcript into values. The transcript
is not stored, shown or verified: one read, one value per field.

Each reader is a small frozen dataclass, so its configuration (prompts,
notes) is part of the definition and can be edited in the app. Model calls
are tagged with a step ('transcribe', 'parse', 'parse:<label>') for a
scripted model; `script` writes the replies that would read given values
(`examinator.synth`).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from ..core.context import BadOutput, ReadContext
from ..core.defs import Field
from . import prompts

BLANK = 'BLANK'


def answers_from_json(text: str, labels: Sequence[str]) -> dict[str, str]:
    """Validate a parse reply structurally: `{"answers": {label: str | null}}`.
    A missing or null answer, or BLANK, is blank ('')."""
    try:
        data = json.loads(text)
    except ValueError:
        raise BadOutput("parse output is not JSON") from None
    answers = data.get('answers') if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        raise BadOutput("parse output has no answers object")
    out = {}
    for label in labels:
        v = answers.get(label)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            v = str(v)
        if v is not None and not isinstance(v, str):
            raise BadOutput("parse output has a non-text answer")
        out[label] = '' if v is None or v.strip() in ('', BLANK) else v.strip()
    return out


@dataclass(frozen=True)
class Answers:
    """Short answers: transcribe the question's pages (or each field's crop),
    then turn the transcript into one value per field."""
    prompt: str = prompts.TRANSCRIBE_ANSWERS
    parse_prompt: str = prompts.PARSE_ANSWERS
    symbols: tuple[str, ...] = ()
    functions: tuple[str, ...] | None = None
    notes: str = ""
    crops: bool = False     # send each field's crop instead of whole pages

    def read(self, ctx: ReadContext, fields: Sequence[Field]) -> dict[str, str]:
        labels = [f.label for f in fields]
        context = prompts.context(ctx.question, labels, ctx.note)
        images = [ctx.crop(f.region) for f in fields if f.region is not None] if self.crops else ctx.pages()
        transcript = ctx.model(prompts.join(self.prompt, context, self.notes), images=images,
                               step='transcribe').strip()
        out = ctx.model(prompts.join(self.parse_prompt, context, prompts.notation(self.symbols, self.functions),
                                     self.notes), text=transcript, expect='json', step='parse')
        return answers_from_json(out, labels)

    def script(self, fields: Sequence[Field], given: Mapping[str, Any]) -> dict[str, str]:
        labels = [f.label for f in fields]
        return {'transcribe': "\n".join(f"{label}: {given.get(label) or BLANK}" for label in labels),
                'parse': json.dumps({'answers': {label: given.get(label) or None for label in labels}})}


def answers(*, symbols: Sequence[str] = (), functions: Sequence[str] | None = None, notes: str = "",
            crops: bool = False, prompt: str = prompts.TRANSCRIBE_ANSWERS,
            parse_prompt: str = prompts.PARSE_ANSWERS) -> Answers:
    """Short answers: expressions, numbers, choices. `symbols` and `functions`
    are notation hints for the prompt (never a key)."""
    return Answers(prompt, parse_prompt, tuple(symbols), None if functions is None else tuple(functions),
                   notes, crops)


@dataclass(frozen=True)
class Code:
    """Pseudocode: transcribe the question's pages, then turn the transcript
    into source in the pseudocode language, once per field (each field asks for
    its own functions)."""
    entries: Mapping[str, tuple[str, ...]] | tuple[str, ...]    # per field, or the same for every field
    language: Any = None                 # a `pseudocode.Language`; None means the base language
    prompt: str = prompts.TRANSCRIBE_PSEUDOCODE
    parse_prompt: str = prompts.PARSE_PSEUDOCODE
    notes: str = ""
    then: Callable[[str], str] | None = None    # deterministic rewrite of the parsed source

    def wanted(self, label: str) -> tuple[str, ...]:
        if isinstance(self.entries, Mapping):
            return tuple(self.entries.get(label, ()))
        return tuple(self.entries)

    def read(self, ctx: ReadContext, fields: Sequence[Field]) -> dict[str, str]:
        from . import pseudocode
        language = self.language or pseudocode.BASE
        context = prompts.context(ctx.question, [f.label for f in fields], ctx.note)
        transcript = ctx.model(prompts.join(self.prompt, context, self.notes), images=ctx.pages(), step='transcribe')
        out = {}
        for f in fields:
            source = ctx.model(
                prompts.join(self.parse_prompt, language.description, context,
                             f"Requested functions: {', '.join(self.wanted(f.label))}", self.notes),
                text=transcript, expect='code', step=f'parse:{f.label}')
            source = source.strip('\n') + '\n' if source.strip() else ''
            out[f.label] = self.then(source) if self.then and source else source
        return out

    def script(self, fields: Sequence[Field], given: Mapping[str, Any]) -> dict[str, str]:
        return {'transcribe': "\n\n".join(given.get(f.label) or BLANK for f in fields),
                **{f'parse:{f.label}': given.get(f.label) or '' for f in fields}}

    def facets(self, question) -> list:
        """The comments in each field's source, searchable."""
        from .pseudocode import comment_facet
        fields = [f.label for f in question.written]
        return [comment_facet(label, name='comments' if len(fields) == 1 else f'comments/{label}',
                              label="comments" if len(fields) == 1 else f"{label} comments") for label in fields]


def code(entries: Sequence[str] | Mapping[str, Sequence[str]], *, language: Any = None, notes: str = "",
         then: Callable[[str], str] | None = None, prompt: str = prompts.TRANSCRIBE_PSEUDOCODE,
         parse_prompt: str = prompts.PARSE_PSEUDOCODE) -> Code:
    """Pseudocode. `entries` names the functions the student writes: a list (for
    a question with one code field), or `{label: [functions]}` per field. `then`
    rewrites the parsed source deterministically (it is what is stored)."""
    entries = {k: tuple(v) for k, v in entries.items()} if isinstance(entries, Mapping) else tuple(entries)
    return Code(entries, language, prompt, parse_prompt, notes, then)
