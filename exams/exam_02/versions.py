"""The eight printings: three independent bits, each read from two printed fields.

Printing `alt<i>` has bit 1 set if `i & 1`, bit 2 if `i & 2`, bit 4 if `i & 4`
(checked against the eight blank PDFs). A question's printing depends on one,
two or all three bits:

    bit 1   q8                      bits 1, 4      q1, q3, q5
    bit 2   q9                      bits 2, 4      q6
    bit 4   q2, q7, q10, q12        bits 1, 2, 4   q4
    none    q11

The six printed fields live in the `info` question (`FIELDS`). A bit is its two
fields agreeing (`std.fields.agree`); a question's printing is named from its
bits by `Printing`, which its check uses to pick a key. Question 1 is printed
four ways with one key, so it reads nothing.
"""
import os
from dataclasses import dataclass
from itertools import product
from pathlib import Path

from examinator import Field, Printed, Region
from examinator.std import fields

ORIGINAL, ALTERNATE = 'original', 'alternate'
STATES = (ORIGINAL, ALTERNATE)
INFO = 'info'


def band(page, top, bottom):
    return Region(page, 0, top, 1000, bottom)


# printed field -> {a text it may show: the printing it shows it on}, the first text per printing as printed
TEXTS: dict[str, dict[str, str]] = {}


def printed(label, region, ask, original, alternate):
    TEXTS[label] = {text: state for state, texts in ((ORIGINAL, original), (ALTERNATE, alternate)) for text in texts}
    return Field(label, region=region, read=Printed(ask))


# bit -> its two printed fields (in the `info` question)
FIELDS = {
    1: (printed('q5_totals', band(10, 395, 510),
                "the name of the list of totals defined in the printed code, exactly as printed",
                ['totals_all'], ['totals_few']),
        printed('q8_first', band(16, 280, 340),
                "what printed question 8.1 supposes about m1 and m2: 'unequal' if m1 ≠ m2, 'equal' if m1 = m2",
                ['unequal', '≠', '!=', 'm1 ≠ m2', 'm1 != m2'], ['equal', '=', 'm1 = m2'])),
    2: (printed('q6_density', Region(12, 480, 205, 760, 330),
                "the fraction printed for the case 0 <= r < 1 of the density f(r), as numerator/denominator",
                ['3/6'], ['1/6']),
        printed('q9_variance', band(18, 300, 365),
                "the sample variance stated in the printed text, as a number",
                ['4'], ['9'])),
    4: (printed('q2_animal', band(4, 85, 165),
                "the name of the function being written in question 2, without parentheses",
                ['koala'], ['wombat']),
        printed('q7_quantile', band(14, 85, 200),
                "which quantile of D the unknown m is: 'median' or 'upper quartile'",
                ['median'], ['upper quartile', 'quartile'])),
}


def bit(n):
    """One bit's state, 'original' or 'alternate': its two fields, which must agree."""
    return fields.agree(*(fields.table(fields.value(f.label, question=INFO), TEXTS[f.label]) for f in FIELDS[n]))


def _l_piece(mirrored, shifted):
    return ('left' if mirrored else 'right') + ('+1' if shifted else '')


# question -> (the bits it depends on, {bit states: printing name}, or None for original/alternate)
PRINTINGS = {
    'q1': ((1, 4), _l_piece),
    'q2': ((4,), None),
    'q3': ((1, 4), _l_piece),
    # Pages 8-9 come in four printings: bit 2 matters only without bit 4, bit 1 only with it.
    'q4': ((1, 2, 4), lambda b1, b2, b4: ('d' if b1 else 'c') if b4 else ('b' if b2 else 'a')),
    'q5': ((1, 4), lambda b1, b4: ('means' if b1 else 'totals') + ('/coordinate' if b4 else '/distance')),
    'q6': ((2, 4), lambda b2, b4: ('rising' if b2 else 'falling') + ('/half' if b4 else '/third')),
    'q7': ((4,), None),
    'q8': ((1,), None),
    'q9': ((2,), None),
    'q10': ((4,), None),
    'q12': ((4,), None),
}


def _table(qid) -> dict:
    """{bit states (a tuple of bools): printing name} for one question."""
    bits, name = PRINTINGS[qid]
    if name is None:
        return {(False,): ORIGINAL, (True,): ALTERNATE}
    return {states: name(*states) for states in product((False, True), repeat=len(bits))}


def names(qid) -> list:
    """A question's printing names, in table order."""
    return list(dict.fromkeys(_table(qid).values()))


@dataclass(frozen=True)
class Printing:
    """A question's printing, from the bits it depends on (`fn(ctx) -> name`, for
    `math.keyed(by=...)` and the custom checks). Undecided while a bit is."""
    qid: str

    def __call__(self, ctx):
        bits, _ = PRINTINGS[self.qid]
        return _table(self.qid)[tuple(bit(b)(ctx) == ALTERNATE for b in bits)]


def printing(qid):
    """What picks a question's key: one bit directly, or the bits combined."""
    bits, name = PRINTINGS[qid]
    return bit(bits[0]) if name is None else Printing(qid)


def bits_of(alt: int) -> dict:
    return {b: bool(alt & b) for b in FIELDS}


# alt index -> question -> printing name
VERSIONS = {alt: {qid: _table(qid)[tuple(bits_of(alt)[b] for b in bits)] for qid, (bits, _) in PRINTINGS.items()}
            for alt in range(8)}


def readings(alt: int) -> dict:
    """What each printed field reads on one printing: label -> reading."""
    out = {}
    for b, on in bits_of(alt).items():
        for f in FIELDS[b]:
            out[f.label] = next(text for text, state in TEXTS[f.label].items() if state == STATES[on])
    return out


BLANKS = 'EXAM2_BLANKS'     # environment variable: the directory holding the blank PDFs


def blank(alt: int) -> Path | None:
    """The blank PDF of one printing, if the blanks directory is configured."""
    root = os.environ.get(BLANKS)
    if not root:
        return None
    path = Path(root) / f'exam_02_alt{alt}.pdf'
    if not path.is_file():
        raise FileNotFoundError(f"{BLANKS} has no {path.name}")
    return path
