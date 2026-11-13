"""What a model's reply makes of a field: the one rule every reader uses, plus
small helpers (the normal form of a printed reading, the memo key of reading a
landmark region, which intake re-reads whenever it plans a split).

**Silence is not a value.** A field gets a value only from an explicit
statement about that field in the model's reply:

    "some text"           the value
    "BLANK"               the blank value '' (the prompt says this is how to state it)
    anything else         NOT_READ: the key missing, null, "", the string "null",
                          a reply that is not what was asked for

A field that is not read carries a review marker, so it is never vouched for
and a person must look before any grade that reads it counts; what the model
did say, if anything, follows the marker.
"""
from __future__ import annotations

import json
import re
from typing import Any

BLANK = 'BLANK'                         # how a model states that nothing is written
NOT_READ = "[REVIEW: not read]"
_NULLS = ('', 'null')       # (not "None": an answer may be that word)


def statement(said: Any) -> str | None:
    """What a model stated as one reading, as text (a number or list as its JSON),
    or None when it stated nothing: absent, null, empty, or the string "null"."""
    if said is None:
        return None
    text = said.strip() if isinstance(said, str) else json.dumps(said)
    return None if text.lower() in _NULLS else text


def value(said: Any, *, also: Any = None) -> str:
    """A field's value from what the model said about that field (see the module).
    `also`: what else it said (a quote, say), kept after the marker when the field
    is not read."""
    text = statement(said)
    if text is None:
        extra = statement(also)
        return NOT_READ if extra is None else f"{NOT_READ} {extra}"
    return '' if text == BLANK else text


def json_object(out: str) -> dict:
    """A model's reply as a JSON object; {} when it is not one (nothing stated)."""
    try:
        data = json.loads(out)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def normalize(reading: str) -> str:
    """Fold away what a model varies when reporting the same printed value:
    case, whitespace, one layer of brackets or quotes, a trailing '.' or '%'."""
    s = re.sub(r'\s+', '', reading).lower().rstrip('.%')
    if len(s) >= 2 and (s[0], s[-1]) in (('[', ']'), ('(', ')'), ('"', '"'), ("'", "'"), ('{', '}')):
        s = s[1:-1]
    return s


def region_key(kind: str, name: str, defn: str, layout: Any, *where: Any) -> str:
    """The memo key of reading one region under a scan layout (`pdf.layout.Layout`):
    what is read (`kind`, `name`), a digest of its definition, the layout's print
    area (where on the page it is cropped), and anything else the crop depends on
    (`where`: a sheet's orientation, its index)."""
    return ':'.join(str(x) for x in (kind, name, defn, list(layout.print_area), *where))
