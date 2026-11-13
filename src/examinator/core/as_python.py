"""The exam-wide overrides of a workspace as Python, for whoever maintains the
exam package: one line per override, the package's value and the new one."""
from __future__ import annotations

from typing import Any

from .defs import Exam
from .overrides import _get, _locate, _to_json
from .store import Store


def _python(kind: str, value: Any) -> str:
    if value is None:
        return 'None'
    if kind == 'region':
        return f"Region({', '.join(str(v) for v in value)})"
    if kind in ('pages', 'print_area'):
        return repr(list(value))
    if kind == 'choice':
        return f"Choice({', '.join(repr(v) for v in value)})"
    return repr(value)


def describe_changes(exam: Exam, store: Store) -> list[str]:
    """One line per exam-wide override: `path: old -> new  (who, date)`."""
    lines = []
    for path, o in store.overrides().items():
        try:
            loc = _locate(exam, path)
            kind = loc.kind
            old = _python(kind, _to_json(kind, _get(loc.base, loc.segs)))
        except (ValueError, KeyError):
            kind, old = '', "(no longer in the package)"
        new = _python(kind, o.value) if kind else repr(o.value)
        lines.append(f"{path}: {old} -> {new}  ({o.author or '?'}, {o.updated_at[:10]})")
    return lines
