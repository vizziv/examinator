"""The course's pseudocode, and how this exam runs it.

- Points are lists and `+ - * /` act on them as vectors (`p + [2, 1]`, `2 * p`):
  the language's `vectors` option.
- `die(n)` is uniform on {0, ..., n-1}; `rotate(point, angle, center)` is provided.
- `^` is a power: replaced by `**` before running (as text, so line numbers hold).

A failure is undecided for a name nobody provides, and for point arithmetic
the checker cannot do; any other failure is a mistake.
"""
import dataclasses
import math
from functools import partial

from examinator.std import pseudocode
from examinator_kits.stochastic import checks, primitives

SEED = 4580


def rotate(point, angle, center=(0, 0)):
    x, y = _pair(point)
    cx, cy = _pair(center)
    c, s = math.cos(angle), math.sin(angle)
    return [cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c]


def _pair(p):
    if not isinstance(p, (list, tuple)) or len(p) != 2:
        raise ValueError("expected a point [x, y]")
    return p


LANGUAGE = dataclasses.replace(
    pseudocode.WITH_COLLECTIONS, vectors=True, extra="- `^` may be kept as written for a power.",
).with_builtins(
    **primitives.UNIF, coin=primitives.COIN, die=primitives.die(start=0),
    rotate=pseudocode.Primitive(rotate, "`rotate(point, angle, center)` turns a point counterclockwise about a center"))


def powers(source: str) -> str:
    """The source as run: `^` is a power. (As text: Python's `^` binds more loosely than `**`.)"""
    return source.replace('^', '**')


def undecided(failure: pseudocode.Failure) -> bool:
    return failure.kind == 'undefined' or checks.vector_arithmetic(failure)


draw = partial(checks.draw, seed=SEED, undecided=undecided)


def check(source: str, label: str, judge):
    """Parse and judge one sampler (see `checks.check_source`)."""
    return checks.check_source(source, LANGUAGE, label, judge, ran=powers(source))


def stopped(part: checks.Part, name: str, stop: checks.Stop) -> checks.Part:
    """A run that stopped early. The note is the failure and its line, without the expression."""
    e = stop.error
    note = f"line {e.line}: {e.detail}" if isinstance(e, pseudocode.Failure) and e.line else stop.note
    return part.stop(name, stop).settle(stop.outcome, note)
