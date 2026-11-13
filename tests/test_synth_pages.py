"""Synthetic pages put answers where a student would. Everything here is synthetic."""
import numpy as np
from PIL import Image, ImageDraw

from examinator import synth
from examinator.core.defs import Region

REGION = Region(1, 100, 100, 700, 200)          # per mille of the page


def page_with_box():
    """A label left of an answer box inside REGION, like a printed exam."""
    im = Image.new('RGB', synth.PAGE, 'white')
    l, t, r, b = synth._box(REGION)
    d = ImageDraw.Draw(im)
    d.text((l + 5, (t + b) // 2 - 8), "1.1 =", fill='black', font=synth._font(18))
    d.rectangle((l + 150, t + 5, r - 5, b - 5), outline='black', width=2)
    return im, (l + 150, t + 5, r - 5, b - 5)


def pen(before, after):
    """Where the pen wrote: (rows, cols) of pixels that changed."""
    return np.nonzero(np.any(np.asarray(before) != np.asarray(after), axis=2))


def test_one_line_goes_inside_the_box_not_over_the_label():
    im, (bl, bt, br, bb) = page_with_box()
    before = im.copy()
    synth.write(im, REGION, "2*b*(1-a)")
    rows, cols = pen(before, im)
    assert len(rows) and cols.min() > bl + 2 and cols.max() < br - 2 and rows.min() > bt + 2 and rows.max() < bb - 2


def test_code_goes_below_a_printed_stub():
    im = Image.new('RGB', synth.PAGE, 'white')
    region = Region(1, 0, 100, 1000, 600)
    l, t, r, b = synth._box(region)
    ImageDraw.Draw(im).text((l + 100, t + 10), "def f(a):\n    # TODO", fill='black', font=synth._font(16))
    before = im.copy()
    synth.write(im, region, "def f(a):\n    return a\n")
    rows, _ = pen(before, im)
    assert rows.min() > t + 50          # below the stub's two printed lines


def test_options_box_text_goes_beside_it():
    """A box of printed options has no room to write in: the answer goes beside it."""
    im = Image.new('RGB', synth.PAGE, 'white')
    region = Region(1, 100, 100, 350, 200)
    l, t, r, b = synth._box(region)
    d = ImageDraw.Draw(im)
    d.rectangle((l + 5, t, r - 60, b), outline='black', width=2)
    for i, option in enumerate(["O Increases", "O Stays the same", "O Decreases"]):
        d.text((l + 10, t + 8 + 28 * i), option, fill='black', font=synth._font(18))
    before = im.copy()
    synth.write(im, region, "decreases")
    _, cols = pen(before, im)
    assert cols.min() > r - 60
