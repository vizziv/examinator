"""Evidence: what a check found.

Machine-readable `data` for the question's grade rule, and an ordered list of
display `blocks` for people (see `examinator.std.blocks`). Stored as
`{'data': ..., 'blocks': [...]}`.

Standard checks put `{'fields': {label: {'answer': ..., 'outcome': ...}}}` in
`data`, with outcome 'correct', 'incorrect', 'blank' or None (could not
decide). Custom checks may put anything there; only their own grade rule reads it.

A check that could not run at all (timeout, crash, unresolved variant) is
stored with `data == {'undecided': reason}`; every part is then undecided.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

OUTCOMES = ('correct', 'incorrect', 'blank', None)


@dataclass
class Evidence:
    data: Mapping[str, Any] = field(default_factory=dict)
    blocks: Sequence[Any] = ()      # block objects (with `to_json`) or plain dicts

    def to_json(self) -> dict:
        return {'data': dict(self.data),
                'blocks': [b.to_json() if hasattr(b, 'to_json') else b for b in self.blocks]}


def undecided(reason: str, blocks: Sequence[Any] = ()) -> dict:
    """Stored evidence for a check that could not reach any verdict."""
    return Evidence({'undecided': reason},
                    [{'type': 'note', 'text': reason, 'tone': 'warn'}, *blocks]).to_json()


class EvidenceView:
    """Read access to stored evidence, as a grade rule sees it."""

    def __init__(self, body: Mapping[str, Any]):
        self.data: Mapping[str, Any] = body.get('data') or {}
        self.blocks: Sequence[Mapping] = body.get('blocks') or ()

    @property
    def fields(self) -> Mapping[str, Mapping]:
        return self.data.get('fields') or {}

    def outcome(self, label: str) -> str | None:
        """The standard outcome recorded for one field."""
        return (self.fields.get(label) or {}).get('outcome')

    def outcomes(self) -> list[str | None]:
        return [f.get('outcome') for f in self.fields.values()]


def body_of(result: Any) -> Any:
    """Normalize what a stage function returned into a storable body."""
    return result.to_json() if hasattr(result, 'to_json') else result
