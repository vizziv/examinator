"""This course's pseudocode, and how this exam runs it.

- Randomness: `unif`, `coin(p)`, `die(n)` (1 to n) and `weighted_die(w)`.
- Points are lists and arithmetic on them is elementwise: the `vectors` option.
  `rotate(p, angle, center)` is provided.
- Arrays are indexed from 1 in the course; the language indexes from 0, so
  the parse note asks the model to shift every index (`notes.POINTS`).
- "Do not use accept-reject": at most `LIMIT` random draws per sample,
  counted exactly by the generator (`Draws`); a sampler that passes with a
  `while` loop or recursion goes to a person (`settle_direct`).

A failure is undecided for a name nobody provides, a limit of the checker,
or point arithmetic the checker cannot do; any other failure is a mistake.
"""
import dataclasses
import math
from functools import partial

import numpy as np

from examinator.std import pseudocode
from examinator_kits.stochastic import checks, primitives

SEED = 4580
LIMIT = 1000        # random draws per sample


def weighted_die(rng, w):
    """k from 1 to len(w), with probability w[k - 1] / sum(w)."""
    if not (isinstance(w, (list, tuple)) and len(w)
            and all(type(x) in (int, float) and x >= 0 for x in w) and 0 < sum(w) < math.inf):
        raise ValueError("weighted_die needs nonnegative weights with a positive sum")
    total, u = 0.0, float(rng.random()) * sum(w)
    for i, x in enumerate(w, 1):
        total += x
        if u < total:
            return i
    return max(i for i, x in enumerate(w, 1) if x > 0)


def rotate(p, angle, center=(0, 0)):
    """`p` turned counterclockwise by `angle` radians about `center`."""
    if not (isinstance(p, (list, tuple)) and len(p) == 2 and isinstance(center, (list, tuple)) and len(center) == 2):
        raise pseudocode.Limitation("rotate needs a 2-D point and center")
    x, y, c, s = p[0] - center[0], p[1] - center[1], math.cos(angle), math.sin(angle)
    return [center[0] + c * x - s * y, center[1] + s * x + c * y]


RANDOM = {**primitives.UNIF, 'coin': primitives.COIN, 'die': primitives.die(start=1)}
SCALAR = pseudocode.BASE.with_builtins(**RANDOM)
COLLECTIONS = pseudocode.WITH_COLLECTIONS.with_builtins(**RANDOM)
POINTS = dataclasses.replace(pseudocode.WITH_COLLECTIONS, vectors=True).with_builtins(
    **RANDOM,
    weighted_die=pseudocode.Primitive(weighted_die, "`weighted_die(w)` is k from 1 to len(w) with probability "
                                      "proportional to w[k - 1]", group=primitives.RANDOMNESS, random=True),
    rotate=pseudocode.Primitive(rotate, "`rotate(p, angle, center)` is p turned counterclockwise by angle "
                                "radians about center"))


def undecided(failure: pseudocode.Failure) -> bool:
    return failure.kind in ('undefined', 'unsupported') or checks.vector_arithmetic(failure)


draw = partial(checks.draw, seed=SEED, undecided=undecided)


class Draws:
    """A generator factory for `draw(rng=...)` that counts random draws per sample.

    Going over the limit is a demonstrated mistake (kind 'limit'), not an
    exhausted budget. `most` is the largest count in any one sample.
    """

    def __init__(self, limit: int = LIMIT):
        self.limit, self.most = limit, 0

    def __call__(self, seed: int, i: int) -> '_Counted':
        return _Counted(self, np.random.default_rng(np.random.SeedSequence(seed, spawn_key=(i,))))


class _Counted:
    def __init__(self, draws: Draws, gen):
        self.draws, self.gen, self.count = draws, gen, 0

    def random(self):
        self.count += 1
        self.draws.most = max(self.draws.most, self.count)
        if self.count > self.draws.limit:
            raise pseudocode.Failure(f"more than {self.draws.limit} random calls in one sample", kind='limit')
        return self.gen.random()


def settle_direct(part: checks.Part, program: pseudocode.Program, entry: str, draws: Draws) -> checks.Part:
    """Combine a part's tests, then apply the no-accept-reject rule: a sampler
    that passes but loops without a fixed bound goes to a person."""
    part.tests.append(["random calls per sample", 'correct', f"at most {draws.most}"])
    part.combine()
    why = checks.accept_reject(program, entry)
    if part.outcome == 'correct' and why:
        part.settle(None, f"{why}: accept-reject?")
    return part
