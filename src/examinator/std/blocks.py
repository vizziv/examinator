"""The evidence block vocabulary.

A check returns `Result(grade, fields, blocks)`. Blocks are what graders see;
the app renders them, and exams never write layout. A block is one of the
classes here or the plain dict its `to_json()` gives:

    {'type': 'note', 'text': str, 'tone': 'info' | 'good' | 'bad' | 'warn'}
    {'type': 'table', 'columns': [str], 'rows': [[str | number | bool | None]]}
    {'type': 'code', 'text': str, 'highlight': [line numbers, 1-based]}
    {'type': 'details', 'summary': str, 'blocks': [block]}       # collapsed
    {'type': 'image', 'media_type': str, 'data': bytes, 'alt': str, 'title': str}

`result.settle` checks every block against this grammar and the size limits
below when a check returns, and stores it: an image's bytes are kept apart,
named by their digest, and its stored block is `{'type': 'image', 'digest',
'media_type', 'alt', 'title'}`. What breaks the grammar or the limits is a
check error, never a stored block. Top-level images are stored first and shown
as a row of figures above the rest; an image inside `Details` stays there.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

# What an <img> shows safely (an SVG there runs no script and loads nothing).
MEDIA_TYPES = ('image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/svg+xml')
TONES = ('info', 'good', 'bad', 'warn')
MAX_IMAGE = 2 << 20         # bytes per image: evidence at a glance, not a data dump
MAX_EVIDENCE = 8 << 20      # bytes per result: all its images and text together
MAX_DEPTH = 8               # details within details


@dataclass
class Note:
    text: str
    tone: str = 'info'

    def to_json(self) -> dict:
        return {'type': 'note', 'text': self.text, 'tone': self.tone}


@dataclass
class Table:
    columns: Sequence[str]
    rows: Sequence[Sequence[Any]]

    def to_json(self) -> dict:
        return {'type': 'table', 'columns': list(self.columns), 'rows': [list(r) for r in self.rows]}


@dataclass
class Code:
    text: str
    highlight: Sequence[int] = ()

    def to_json(self) -> dict:
        return {'type': 'code', 'text': self.text, 'highlight': list(self.highlight)}


class Details:
    """Collapsed by default: `Details("Samples", Table(...), Note(...))`."""

    def __init__(self, summary: str, *blocks: Any):
        self.summary = summary
        self.blocks = blocks[0] if len(blocks) == 1 and isinstance(blocks[0], (list, tuple)) else blocks

    def to_json(self) -> dict:
        return {'type': 'details', 'summary': self.summary, 'blocks': list(self.blocks)}


@dataclass(eq=False)
class Image:
    """An image file's bytes, shown with an <img>: `Image(png)`. Light
    backgrounds are inverted in dark mode, as scans are."""
    data: bytes
    media_type: str = 'image/png'
    alt: str = ""
    title: str = ""

    def to_json(self) -> dict:
        return {'type': 'image', 'media_type': self.media_type, 'data': self.data,
                'alt': self.alt, 'title': self.title}
