"""The evidence block vocabulary.

A check returns `Result(grade, fields, blocks)`. Blocks are what graders see; the app
renders them, and exams never write layout. Every block serializes with
`to_json()` to a dict with a `'type'`.

Stored shapes (the renderer's contract):

    {'type': 'note', 'text': str, 'tone': 'info' | 'good' | 'bad' | 'warn'}
    {'type': 'table', 'columns': [str], 'rows': [[cell]]}
    {'type': 'code', 'text': str, 'highlight': [line numbers, 1-based]}
    {'type': 'details', 'summary': str, 'blocks': [block]}       # collapsed
    {'type': 'image', 'media_type': str, 'data': base64 str, 'alt': str, 'title': str}

Images (a plot rendered to PNG, say) are shown first, in a row of figures.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Sequence

# What an <img> shows safely (an SVG there runs no script and loads nothing).
MEDIA_TYPES = ('image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/svg+xml')
MAX_IMAGE = 2 << 20     # bytes: evidence at a glance, not a data dump


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
        return {'type': 'details', 'summary': self.summary,
                'blocks': [b.to_json() if hasattr(b, 'to_json') else b for b in self.blocks]}


@dataclass(eq=False)
class Image:
    """An image file's bytes, shown with an <img>: `Image(png)`. Light
    backgrounds are inverted in dark mode, as scans are."""
    data: bytes
    media_type: str = 'image/png'
    alt: str = ""
    title: str = ""

    def __post_init__(self):
        if self.media_type not in MEDIA_TYPES:
            raise ValueError(f"image type {self.media_type!r} is not one of {', '.join(MEDIA_TYPES)}")
        if not isinstance(self.data, (bytes, bytearray)):
            raise TypeError("image data is bytes")
        if len(self.data) > MAX_IMAGE:
            raise ValueError(f"image of {len(self.data)} bytes; at most {MAX_IMAGE}")

    def to_json(self) -> dict:
        return {'type': 'image', 'media_type': self.media_type, 'alt': self.alt, 'title': self.title,
                'data': base64.b64encode(self.data).decode('ascii')}
