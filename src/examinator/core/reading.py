"""What a model's reply makes of a field: the one rule every reader uses, plus
small helpers (the normal form of a printed reading); and what a reader is
(`Reader`).

**A value is what was read; whether a person should look is a status, not
text.** Every reader asks for one reply shape: per field, a JSON object

    {"value": "the text", "unsure": "why, and where (optional)"}

(several fields: an object of them keyed by label; code: keyed by function).
A field object with a text `value` is a `Reading`: its value ('' is a blank,
an ordinary value), and, when `unsure` says something, a review note.
Anything else for a field (the key missing, null, the string "null", not an
object, an unparseable reply) is silence: no reading, and nothing is written,
so the field stays not read.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class Reading:
    """One field's value as read, and why a person should look (None: no reason)."""
    text: str
    review: str | None = None


def field_object(given: Any) -> dict:
    """The field object a model would reply to read `given` (for scripted replies):
    text, a `Reading` (with its review as `unsure`), or None (states no value)."""
    if isinstance(given, Reading):
        return {'value': given.text, 'unsure': given.review or ("unsure" if given.review is not None else "")}
    return {'value': given, 'unsure': ""}


def text_of(said: Any) -> str | None:
    """A stated value as text (a number or list as its JSON), or None for silence:
    absent, null, the string "null", a bool or an object."""
    if isinstance(said, str):
        return None if said.strip().lower() == 'null' else said.strip()
    if isinstance(said, (int, float, list)) and not isinstance(said, bool):
        return json.dumps(said)
    return None


def reading(obj: Any) -> Reading | None:
    """The reading in one field object `{"value": ..., "unsure": ...}`, or None."""
    if not isinstance(obj, dict):
        return None
    text = text_of(obj.get('value'))
    if text is None:
        return None
    unsure = obj.get('unsure')
    if unsure in (None, False, ''):
        return Reading(text)
    return Reading(text, unsure.strip() if isinstance(unsure, str) else '')


def json_object(out: str) -> dict:
    """A model's reply as a JSON object; {} when it is not one (nothing stated)."""
    try:
        data = json.loads(out)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def readings(out: str, names) -> dict[str, Reading]:
    """The readings a reply `{name: {"value": ..., "unsure": ...}}` states, for `names`."""
    data = json_object(out)
    return {n: r for n in names if (r := reading(data.get(n))) is not None}


def normalize(reading: str) -> str:
    """Fold away what a model varies when reporting the same printed value:
    case, whitespace, one layer of brackets or quotes, a trailing '.' or '%'."""
    s = re.sub(r'\s+', '', reading).lower().rstrip('.%')
    if len(s) >= 2 and (s[0], s[-1]) in (('[', ']'), ('(', ')'), ('"', '"'), ("'", "'"), ('{', '}')):
        s = s[1:-1]
    return s


class Reader:
    """How values get from page images into fields (MODEL.md: a reader). A reader
    is an instance of a subclass (a frozen dataclass, so its configuration is
    plain data in the definition); it defines `read`, and the rest default:

    - `read(ctx, fields) -> {label: Reading or text}` (`context.ReadContext`): a
      label left out is not read.
    - `vouches(field, reading)`: a person need not verify this reading (never).
    - `needs_region`: it reads each field's region, not the question's pages (no).
    - `multiline`: its values may span lines (no).
    - `validate(what)`: `ValueError` for a configuration that cannot read.
    - `facets(question)`: filters it offers (`core.focus`; none).
    - `script(fields, given)`: the replies that read `given`, for a scripted model
      (`examinator.synth`; none).
    """
    needs_region = False
    multiline = False

    def read(self, ctx, fields) -> Mapping[str, 'Reading | str']:
        raise NotImplementedError

    def vouches(self, field, reading: Reading) -> bool:
        return False

    def validate(self, what: str) -> None:
        pass

    def facets(self, question) -> list:
        return []

    def script(self, fields, given: Mapping[str, Any]) -> dict[str, str]:
        return {}
