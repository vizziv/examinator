"""Asking the model what one region of a page says: the one mechanism behind
variant cues (`core.variants`), intake landmarks (`core.intake`) and identity
boxes (`core.identity`).

Each of those is a region plus a question (`ask`); the model answers with JSON
whose `value` is the reading. What a reading depends on is its `basis`, and
answers are memoised under a key built by `memo_key`. What is remembered, and
what a failed call means, is each caller's business.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterable

from .defs import digest


def prompt(template: str, ask: str, expected: Iterable[str] = ()) -> str:
    """The template with the question filled in; with `expected`, the readings
    that are possible (they name a printing or a page, never an answer)."""
    text = template.format(ask=ask)
    expected = list(expected)
    if expected:
        text += "\nThe value is expected to be one of: " + " | ".join(expected) + "."
    return text


def ask(call: Callable[..., str], template: str, question: str, image: Any,
        expected: Iterable[str] = ()) -> str | None:
    """One reading: `call(prompt, images=[image], expect='json')` (a model's
    `complete` or a stage context's `model`), reduced to its `value`."""
    return json_value(call(prompt(template, question, expected), images=[image], expect='json'))


def json_value(out: str) -> str | None:
    """The `value` of a model's JSON reply, as text; None if absent, null or not JSON."""
    try:
        value: Any = json.loads(out).get('value')
    except (ValueError, AttributeError):
        return None
    if value is not None and not isinstance(value, str):
        value = json.dumps(value)
    return value


def normalize(reading: str) -> str:
    """Fold away what a model varies when reporting the same printed value:
    case, whitespace, one layer of brackets or quotes, a trailing '.' or '%'."""
    s = re.sub(r'\s+', '', reading).lower().rstrip('.%')
    if len(s) >= 2 and (s[0], s[-1]) in (('[', ']'), ('(', ')'), ('"', '"'), ("'", "'"), ('{', '}')):
        s = s[1:-1]
    return s


def basis(thing: Any) -> str:
    """Digest of what a reading of `thing` (a `Cue`, `Landmark` or `Identity`)
    depends on: its region and its question."""
    return digest({'region': thing.region, 'ask': thing.ask})


def memo_key(kind: str, name: str, defn: str, *where: Any) -> str:
    """The memo key of one reading: what is read (`kind`, `name`), a digest of its
    definition, and whatever else the crop depends on (scan window, orientation, ...)."""
    return ':'.join(str(x) for x in (kind, name, defn, *where))
