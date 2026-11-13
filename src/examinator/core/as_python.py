"""The exam-wide overrides of a workspace as Python, for whoever maintains the
exam package: one line per override, the package's value and the new one, each
as the package writes it (its `repr`)."""
from __future__ import annotations

from .defs import Exam
from .overrides import package_value
from .store import Store


def describe_changes(exam: Exam, store: Store) -> list[str]:
    """One line per exam-wide override: `path: old -> new  (who, date)`."""
    lines = []
    for path, o in store.overrides().items():
        try:
            old, new = map(repr, package_value(exam, path, o.value))
        except (ValueError, KeyError):
            old, new = "(no longer in the package)", repr(o.value)
        lines.append(f"{path}: {old} -> {new}  ({o.author or '?'}, {o.updated_at[:10]})")
    return lines
