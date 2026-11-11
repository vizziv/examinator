"""The course's pseudocode, and how this exam runs it.

Randomness is `unif`, `coin(p)` and `die(n)` (from 1 to n); question 7 adds
collections. Run-time failures follow the kit's default (`checks.draw`).
"""
from functools import partial

from examinator.std import pseudocode
from examinator_kits.stochastic import checks, primitives

RANDOM = {**primitives.UNIF, 'coin': primitives.COIN, 'die': primitives.die(start=1)}
SCALAR = pseudocode.BASE.with_builtins(**RANDOM)
COLLECTIONS = pseudocode.WITH_COLLECTIONS.with_builtins(**RANDOM)

SEED = 4580
draw = partial(checks.draw, seed=SEED)
