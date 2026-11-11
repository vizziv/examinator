"""The eight printings: three independent bits, each read from two printed cues.

Printing `alt<i>` has bit 1 set if `i & 1`, bit 2 if `i & 2`, bit 4 if `i & 4`
(checked against the eight blank PDFs). A question's printing depends on one,
two or all three bits:

    bit 1   q8                      bits 1, 4      q1, q3, q5
    bit 2   q9                      bits 2, 4      q6
    bit 4   q2, q7, q10, q12        bits 1, 2, 4   q4
    none    q11

A one-bit question reads that bit's two cues directly. For the others no single
printed region shows the whole printing, so they declare the cues of every bit
they depend on (which vote for nothing there) plus two computed cues, each
combining one reading per bit into the question's variant name.
"""
import os
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Mapping

from examinator import Cue, Region
from examinator.core import variants

ORIGINAL, ALTERNATE = 'original', 'alternate'
STATES = (ORIGINAL, ALTERNATE)


def band(page, top, bottom):
    return Region(page, 0, top, 1000, bottom)


def cue(name, region, ask, original, alternate):
    reads = {text: state for state, texts in ((ORIGINAL, original), (ALTERNATE, alternate)) for text in texts}
    return Cue(name, region=region, ask=ask, reads=reads)


# bit -> its two printed cues
CUES = {
    1: (cue('q5_totals', band(10, 395, 510),
            "the name of the list of totals defined in the printed code, exactly as printed",
            ['totals_all'], ['totals_few']),
        cue('q8_first', band(16, 280, 340),
            "what printed question 8.1 supposes about m1 and m2: 'unequal' if m1 ≠ m2, 'equal' if m1 = m2",
            ['unequal', '≠', '!=', 'm1 ≠ m2', 'm1 != m2'], ['equal', '=', 'm1 = m2'])),
    2: (cue('q6_density', Region(12, 480, 205, 760, 330),
            "the fraction printed for the case 0 <= r < 1 of the density f(r), as numerator/denominator",
            ['3/6'], ['1/6']),
        cue('q9_variance', band(18, 300, 365),
            "the sample variance stated in the printed text, as a number",
            ['4'], ['9'])),
    4: (cue('q2_animal', band(4, 85, 165),
            "the name of the function being written in question 2, without parentheses",
            ['koala'], ['wombat']),
        cue('q7_quantile', band(14, 85, 200),
            "which quantile of D the unknown m is: 'median' or 'upper quartile'",
            ['median'], ['upper quartile', 'quartile'])),
}


def _l_piece(mirrored, shifted):
    return ('left' if mirrored else 'right') + ('+1' if shifted else '')


# question -> (the bits it depends on, {bit states: variant name})
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
    """{bit states (a tuple of bools): variant name} for one question."""
    bits, name = PRINTINGS[qid]
    if name is None:
        return {(False,): ORIGINAL, (True,): ALTERNATE}
    return {states: name(*states) for states in product((False, True), repeat=len(bits))}


@dataclass(frozen=True)
class Combine:
    """A computed cue: one reading per bit, combined into a variant name.

    The readings are the ones the standard variant stage has just made for the
    question's own declared cues (shared per submission)."""
    cues: tuple
    names: Mapping          # {'original,alternate': variant}

    def __call__(self, ctx):
        states = []
        for c in self.cues:
            state = variants.vote(ctx, c)
            if state is None:
                return None
            states.append(state)
        return self.names[','.join(states)]


def printing(qid) -> dict:
    """`variants`, `cues` and `require` for one question."""
    bits, _ = PRINTINGS[qid]
    table = _table(qid)
    names = list(dict.fromkeys(table.values()))
    if len(bits) == 1:
        return dict(variants=names, cues=list(CUES[bits[0]]))
    by_state = {','.join(STATES[s] for s in states): name for states, name in table.items()}
    declared = [c for bit in bits for c in CUES[bit]]
    combined = [Cue(f'{qid}_{which}', fn=Combine(tuple(CUES[bit][i] for bit in bits), by_state))
                for i, which in enumerate(('first', 'second'))]
    return dict(variants=names, cues=declared + combined, require=2)


def bits_of(alt: int) -> dict:
    return {bit: bool(alt & bit) for bit in CUES}


# alt index -> question -> variant
VERSIONS = {alt: {qid: _table(qid)[tuple(bits_of(alt)[b] for b in bits)] for qid, (bits, _) in PRINTINGS.items()}
            for alt in range(8)}


def readings(alt: int) -> dict:
    """What each printed cue reads on one printing: cue name -> reading."""
    out = {}
    for bit, on in bits_of(alt).items():
        for c in CUES[bit]:
            out[c.name] = next(text for text, state in c.reads.items() if state == STATES[on])
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
