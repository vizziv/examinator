"""The two printings: `exam_01.pdf` (alt 0) and `exam_01_alt1.pdf` (alt 1).

One choice switches every question except question 3, which is printed the
same in both. Variants are still resolved per question, from that question's
own cues (checked against the printed text of the two blank PDFs).
"""
import os
from pathlib import Path

VARIED = ('q1', 'q2', 'q4', 'q5', 'q6')

# alt index -> question -> variant
VERSIONS = {alt: {q: 'alternate' if alt else 'original' for q in VARIED} for alt in (0, 1)}

FILES = {0: 'exam_01.pdf', 1: 'exam_01_alt1.pdf'}
BLANKS = 'EXAM1_BLANKS'     # environment variable: the directory holding the blank PDFs


def blank(alt: int) -> Path | None:
    """The blank PDF of one printing, if the blanks directory is configured."""
    root = os.environ.get(BLANKS)
    if not root:
        return None
    path = Path(root) / FILES[alt]
    if not path.is_file():
        raise FileNotFoundError(f"{BLANKS} has no {path.name}")
    return path
