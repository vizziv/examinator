"""The eight printed versions: three independent bits, each switching a group of questions.

Version `alt<i>` has bit 1 set if `i & 1`, bit 2 if `i & 2`, bit 4 if `i & 4`
(checked against the printed fields of the eight blank PDFs). A fact about the
blanks, for fixtures and tests: grading reads the printing from the `info`
question's printed fields.
"""
import os
from pathlib import Path

BITS = {'q1': 1, 'q5': 1, 'q9': 1, 'q3': 2, 'q6': 2, 'q4': 4, 'q8': 4}

# bit -> the `info` fields that tell it
FIELDS = {1: ('q5_confidence', 'q9_observations'), 2: ('q6_option_c', 'q3_first_height'),
          4: ('q8_first_n', 'q4_negative_branch')}

# alt index -> question -> its printing
VERSIONS = {alt: {q: 'alternate' if alt & bit else 'original' for q, bit in BITS.items()}
            for alt in range(8)}


def printed(alt: int) -> dict:
    """What `info`'s printed fields read on one version: `Case.printed`."""
    from .exam import TEXTS
    return {'info': {label: next(t for t, p in TEXTS[label].items() if p == ('alternate' if alt & bit else 'original'))
                     for bit, labels in FIELDS.items() for label in labels}}


BLANKS = 'EXAM3_BLANKS'     # environment variable: the directory holding the blank PDFs


def blank(alt: int) -> Path | None:
    """The blank PDF of one version, if the blanks directory is configured."""
    root = os.environ.get(BLANKS)
    if not root:
        return None
    path = Path(root) / f'exam_03_alt{alt}.pdf'
    if not path.is_file():
        raise FileNotFoundError(f"{BLANKS} has no {path.name}")
    return path
