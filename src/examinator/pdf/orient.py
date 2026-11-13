"""Orientation of a scanned page: a number of quarter turns clockwise, 0-3.

Orientations compose by addition mod 4. Turning an image uses exact
`transpose` operations only, so pixels move and are never resampled.
Orientation exists only during intake: pages are written upright into `pdfs/`,
and nothing downstream turns a page (what intake made records each sheet's
orientation, which only tags model calls).
"""
from __future__ import annotations

from PIL import Image

# The transpose for each number of quarter turns clockwise (PIL's ROTATE_* are counterclockwise).
CLOCKWISE = (None, Image.Transpose.ROTATE_270, Image.Transpose.ROTATE_180, Image.Transpose.ROTATE_90)


def turn(image: Image.Image, turns: int) -> Image.Image:
    """`image` turned `turns` quarter turns clockwise."""
    turns %= 4
    return image.transpose(CLOCKWISE[turns]) if turns else image


def label(turns: int) -> str:
    return f"{turns % 4 * 90}°" if turns % 4 else "upright"
