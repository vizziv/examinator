"""Random primitives typically used in course pseudocode, as `Language` builtins.

    language = WITH_COLLECTIONS.with_builtins(**UNIF, coin=COIN, die=die(start=1))

Each draws from the sample's generator (`rng.random()` only), so an exam's own
generator (`Program.sample(rng=...)`) can count or cap draws.
"""
from __future__ import annotations

from types import MappingProxyType

from examinator.std.pseudocode import Failure, Primitive

RANDOMNESS = 'Randomness'      # the description line the docs share


def _number(v) -> bool:
    return type(v) in (int, float, bool)


def _short(v) -> str:
    return format(v, '.6g') if type(v) is float else repr(v)[:40]


def _unif(rng, *bounds):
    u = rng.random()
    if not bounds:
        return u
    if len(bounds) != 2:
        raise Failure(f"unif() takes 0 or 2 arguments, got {len(bounds)}", kind='arity')
    a, b = bounds
    if not (_number(a) and _number(b)):
        raise TypeError("unif needs two numbers")
    return a + (b - a) * u


def _coin(rng, p):
    if not _number(p) or not 0 <= p <= 1:
        raise Failure(f"probability {_short(p)} not in [0, 1]", kind='domain')
    return bool(rng.random() < p)


UNIF = MappingProxyType({
    'unif': Primitive(_unif, "`unif()` is uniform on [0, 1); `unif(a, b)` is uniform on [a, b)",
                      group=RANDOMNESS, random=True, numeric=True, arity=(0, 2)),
})
COIN = Primitive(_coin, "`coin(p)` is True with probability p", group=RANDOMNESS, random=True)


def die(start: int = 1) -> Primitive:
    """`die(n)`: a uniform whole number from `start` to `start + n - 1`.

    Courses differ: `die(start=1)` is 1 to n, `die(start=0)` is 0 to n - 1.
    """
    if type(start) is not int:
        raise TypeError("start must be an int")

    def fn(rng, n):
        if type(n) is float and n == int(n):
            n = int(n)
        if type(n) is not int or n < 1:
            raise Failure(f"die needs a positive whole number, got {_short(n)}", kind='domain')
        return start + min(int(rng.random() * n), n - 1)

    last = "n" if start == 1 else "n - 1" if start == 0 else f"n + {start - 1}"
    return Primitive(fn, f"`die(n)` is a uniform whole number from {start} to {last}",
                     group=RANDOMNESS, random=True)
