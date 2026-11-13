"""The exam-wide overrides of a workspace as Python, for whoever maintains the
exam package: one line per override, the package's value and the new one."""
from __future__ import annotations

from typing import Any

from .defs import Exam
from .overrides import _get, _item_fields, _locate, _to_json
from .store import Store


def _python(kind: str, value: Any, cls: type | None = None, name: str = '') -> str:
    if value is None:
        return 'None'
    if kind == 'item':
        fields = _item_fields(cls)
        args = [repr(name)] + [f'{k}={_python(fields[k][0], v)}' for k, v in value.items()]
        return f"{cls.__name__}({', '.join(args)})"
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
        loc = None
        try:
            loc = _locate(exam, path)
            kind = loc.kind
            old = "(added)" if kind == 'item' else _python(kind, _to_json(kind, _get(loc.base, loc.segs)))
        except (ValueError, KeyError):
            kind, old = '', "(no longer in the package)"
        new = _python(kind, o.value, loc.cls, loc.segs[0] if loc.segs else '') if loc is not None else repr(o.value)
        lines.append(f"{path}: {old} -> {new}  ({o.author or '?'}, {o.updated_at[:10]})")
    return lines
