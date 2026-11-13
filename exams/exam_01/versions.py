"""The two printings: `exam_01.pdf` (alt 0) and `exam_01_alt1.pdf` (alt 1).

One choice switches every question except question 3, which is printed the
same in both. Five printed fields, one per varied question, live in the `info`
question (`FIELDS`); each varied question reads two of them, its own and a
neighbour's, which must agree (`PRINTING`; checked against the printed text of
the two blank PDFs).
"""
import os
from pathlib import Path

from examinator import Field, Printed, Region
from examinator.std import fields

INFO = 'info'


def band(page, top, bottom):
    return Region(page, 0, top, 1000, bottom)


# printed field -> {its text on a printing: that printing}
TEXTS: dict[str, dict[str, str]] = {}


def printed(label, region, ask, original, alternate):
    TEXTS[label] = {original: 'original', alternate: 'alternate'}
    return Field(label, region=region, read=Printed(ask))


FIELDS = [
    printed('q1_parameter', band(2, 115, 230),
            "whether question 1 describes m as the 'mean' or the 'rate' of the exponential distribution",
            'mean', 'rate'),
    printed('q2_m_goal', band(4, 445, 510),
            "whether question 2's task asks for the 'smallest' or the 'largest' possible positive value of m",
            'smallest', 'largest'),
    printed('q4_y_axis_top', Region(8, 140, 345, 500, 650),
            "the largest number printed along the vertical (y) axis of the figure", '5', '3'),
    printed('q5_function', band(10, 115, 165),
            "the name of the function question 5 asks for, without parentheses", 'unit_cone', 'twonit_cone'),
    printed('q6_sine_frequency', band(12, 160, 225),
            "the number that multiplies x inside sin in the printed formula for f(x)", '40', '20'),
]


def _agree(*labels):
    return fields.agree(*(fields.table(fields.value(label, question=INFO), TEXTS[label]) for label in labels))


# question -> its printing, 'original' or 'alternate': two printed fields that must agree
PRINTING = {
    'q1': _agree('q1_parameter', 'q2_m_goal'),
    'q2': _agree('q1_parameter', 'q2_m_goal'),
    'q4': _agree('q4_y_axis_top', 'q5_function'),
    'q5': _agree('q5_function', 'q6_sine_frequency'),
    'q6': _agree('q6_sine_frequency', 'q5_function'),
}

VARIED = tuple(PRINTING)

# alt index -> question -> its printing
VERSIONS = {alt: {q: 'alternate' if alt else 'original' for q in VARIED} for alt in (0, 1)}


def printed_on(alt: int) -> dict:
    """What `info`'s printed fields read on one printing: `Case.printed`."""
    want = 'alternate' if alt else 'original'
    return {INFO: {f.label: next(t for t, p in TEXTS[f.label].items() if p == want) for f in FIELDS}}


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
