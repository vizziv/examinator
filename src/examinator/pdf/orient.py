"""Orientation of a scanned page: one of the eight symmetries of a square.

An `Orient` is a mirror (left to right) applied first, then `turn` quarter turns
clockwise. Applying one to an image uses exact `transpose` operations only, so
pixels move and are never resampled. Orientation exists only during intake:
pages are written upright into `pdfs/`, and nothing downstream turns a page
(what intake made records each sheet's orientation, which only tags model calls).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from PIL import Image

# The transpose for each number of quarter turns clockwise (PIL's ROTATE_* are counterclockwise).
CLOCKWISE = (None, Image.Transpose.ROTATE_270, Image.Transpose.ROTATE_180, Image.Transpose.ROTATE_90)


@dataclass(frozen=True)
class Orient:
    turn: int = 0          # quarter turns clockwise, 0-3
    flip: bool = False     # mirror left to right, before turning

    def __post_init__(self):
        object.__setattr__(self, 'turn', int(self.turn) % 4)
        object.__setattr__(self, 'flip', bool(self.flip))

    @property
    def identity(self) -> bool:
        return self.turn == 0 and not self.flip

    @property
    def swaps(self) -> bool:
        """Width and height trade places."""
        return self.turn % 2 == 1

    def then(self, other: 'Orient') -> 'Orient':
        """This orientation followed by `other`. A mirror reverses the sense of
        earlier turns: M R^t = R^-t M."""
        turn = other.turn + (-self.turn if other.flip else self.turn)
        return Orient(turn, self.flip != other.flip)

    def apply(self, image: Image.Image) -> Image.Image:
        if self.flip:
            image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if self.turn:
            image = image.transpose(CLOCKWISE[self.turn])
        return image

    def to_json(self) -> dict:
        return {'turn': self.turn, 'flip': self.flip}

    @staticmethod
    def from_json(value: Any) -> 'Orient':
        if isinstance(value, Orient):
            return value
        if not isinstance(value, Mapping):
            return Orient()
        return Orient(int(value.get('turn') or 0), bool(value.get('flip')))

    @property
    def code(self) -> str:
        """Two characters: the quarter turns and whether mirrored ('21': a half turn, mirrored)."""
        return f'{self.turn}{int(self.flip)}'

    @staticmethod
    def from_code(code: str) -> 'Orient':
        """The orientation `code` names; ValueError for anything else."""
        if not isinstance(code, str) or len(code) != 2 or code[0] not in '0123' or code[1] not in '01':
            raise ValueError(f"not an orientation code: {code!r}"[:80])
        return Orient(int(code[0]), code[1] == '1')

    def label(self) -> str:
        return (f"{self.turn * 90}°" if self.turn else "") + (" mirrored" if self.flip else "") or "upright"


IDENTITY = Orient()
QUARTER = Orient(1)
HALF = Orient(2)
MIRROR = Orient(0, True)
ALL = tuple(Orient(t, f) for f in (False, True) for t in range(4))
