"""Small helpers for readings: the `value` of a model's JSON reply, the normal
form of a printed reading, and the memo key of reading one region (`core.ask`,
intake's landmarks)."""
from __future__ import annotations

import json
import re
from typing import Any


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


def region_key(kind: str, name: str, defn: str, layout: Any, *where: Any) -> str:
    """The memo key of reading one region under a scan layout (`pdf.layout.Layout`):
    what is read (`kind`, `name`), a digest of its definition, the layout's print
    area (where on the page it is cropped), and anything else the crop depends on
    (`where`: a sheet's orientation, its index)."""
    return ':'.join(str(x) for x in (kind, name, defn, list(layout.print_area), *where))
