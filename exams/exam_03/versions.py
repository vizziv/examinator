"""The eight printed versions: three independent bits, each switching a group of questions.

Version `alt<i>` has bit 1 set if `i & 1`, bit 2 if `i & 2`, bit 4 if `i & 4`
(checked against the printed cues of the eight blank PDFs).
"""
import os
from pathlib import Path

BITS = {'q1': 1, 'q5': 1, 'q9': 1, 'q3': 2, 'q6': 2, 'q4': 4, 'q8': 4}

# alt index -> question -> variant
VERSIONS = {alt: {q: 'alternate' if alt & bit else 'original' for q, bit in BITS.items()}
            for alt in range(8)}

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
