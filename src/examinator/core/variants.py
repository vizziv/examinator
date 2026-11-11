"""Variants: which printing of a question a submission received.

Each cue that reads successfully eliminates the variants it does not match.
The variant is resolved when exactly one candidate remains and enough cues
agree. Otherwise it is unresolved and a person chooses. Never guess.

The stored body keeps the candidate set, so that constraints across questions
can be layered on later:

    {'cues': {name: {'reading': str | None, 'variant': str | None}},
     'candidates': [...], 'agree': int, 'resolved': str | None}
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .context import Context
from .defs import Cue, Question, Stage, digest

CUE_PROMPT = """\
Read the PRINTED text in this image of an exam page region. Ignore all handwriting.
Report: {ask}.
Answer with exactly this JSON, without Markdown: {{"quote": "a short quote of the printed text you relied on", "value": "the value"}}
Use null for the value if the printed text is unreadable or absent. Do not infer the value from anything other than the printed text in this image."""


def normalize(reading: str) -> str:
    """Fold away what a model varies when reporting the same printed value:
    case, whitespace, one layer of brackets or quotes, a trailing '.' or '%'."""
    s = re.sub(r'\s+', '', reading).lower().rstrip('.%')
    if len(s) >= 2 and (s[0], s[-1]) in (('[', ']'), ('(', ')'), ('"', '"'), ("'", "'"), ('{', '}')):
        s = s[1:-1]
    return s


def match(cue: Cue, reading: str | None) -> str | None:
    """The variant a reading points to, or `None`."""
    if reading is None:
        return None
    if cue.fn is not None:
        return reading
    table = {normalize(k): v for k, v in cue.reads.items()}
    return table.get(normalize(reading))


def narrow(question: Question, readings: Mapping[str, str | None]) -> dict:
    """Pure: from cue readings to the variant artifact body."""
    cues, candidates, votes = {}, set(question.variants), []
    for cue in question.cues:
        reading = readings.get(cue.name)
        v = match(cue, reading)
        if v not in question.variants:
            v = None
        cues[cue.name] = {'reading': reading, 'variant': v}
        if v is not None:
            candidates &= {v}
            votes.append(v)
    ordered = [v for v in question.variants if v in candidates]
    agree = votes.count(ordered[0]) if len(ordered) == 1 else 0
    need = len(question.cues) if question.require is None else question.require
    resolved = ordered[0] if len(ordered) == 1 and agree >= max(need, 1) else None
    return {'cues': cues, 'candidates': ordered, 'agree': agree, 'resolved': resolved}


def choose(question: Question, body: Mapping | None, variant: str) -> dict:
    """A grader's manual choice, keeping the recorded readings."""
    if variant not in question.variants:
        raise ValueError(f"unknown variant {variant!r}")
    cues = dict((body or {}).get('cues') or {})
    return {'cues': cues, 'candidates': [variant], 'agree': 0, 'resolved': variant, 'chosen': True}


def resolved(body: Mapping | None) -> str | None:
    return (body or {}).get('resolved')


@dataclass(frozen=True)
class ReadVariant:
    """The standard variant stage: read each cue, then narrow."""
    prompt: str = CUE_PROMPT

    def __call__(self, ctx: Context) -> dict:
        readings: dict[str, str | None] = {}
        for cue in ctx.question.cues:
            if cue.fn is not None:
                readings[cue.name] = cue.fn(ctx)
                continue
            raw = ctx.remember(f'cue:{cue.name}:{digest(cue)}',
                               lambda cue=cue: self._read(ctx, cue), refresh=ctx.rerun)
            readings[cue.name] = json.loads(raw)
        return narrow(ctx.question, readings)

    def _read(self, ctx: Context, cue: Cue) -> str:
        prompt = self.prompt.format(ask=cue.ask)
        if cue.reads:       # readings identify the printing; they are not answer keys
            prompt += "\nThe value is expected to be one of: " + " | ".join(cue.reads) + "."
        out = ctx.model(prompt, images=[ctx.crop(cue.region)], expect='json')
        try:
            value: Any = json.loads(out).get('value')
        except (ValueError, AttributeError):
            value = None
        if value is not None and not isinstance(value, str):
            value = json.dumps(value)
        return json.dumps(value)


def read_variant(prompt: str = CUE_PROMPT) -> Stage:
    return Stage('variant', ReadVariant(prompt), uses_model=True, editable=True)
