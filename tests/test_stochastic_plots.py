"""examinator_kits.stochastic.plots: figures as PNG image blocks, from seeded synthetic samples."""
import hashlib
import io
import os
import subprocess
import sys
import time
from pathlib import Path

import matplotlib
import numpy as np
import pytest
from cycler import cycler
from PIL import Image as PILImage
from scipy import stats

from examinator.std.blocks import Image, Note
from examinator_kits.stochastic import plots

X = np.random.default_rng(1).exponential(1.0, 3000)


def png(image: Image) -> tuple[int, int]:
    assert isinstance(image, Image) and image.media_type == 'image/png'
    with PILImage.open(io.BytesIO(image.data)) as im:
        im.verify()
        assert im.format == 'PNG'
        return im.size


@pytest.mark.parametrize('make', [
    lambda: plots.cdf(X, stats.expon.cdf, title="T", xlabel="x"),
    lambda: plots.hist(X, bins=40, density=stats.expon.pdf, title="T"),
    lambda: plots.hist(np.random.default_rng(2).poisson(3, 2000), pmf=stats.poisson(3).pmf, title="T"),
    lambda: plots.scatter(X[:1000], X[1000:2000], title="T", xlabel="x", ylabel="y"),
    lambda: plots.cdfs({"samples": X[:500], "reference": X[500:]}, title="T"),
])
def test_figures_are_small_deterministic_pngs(make):
    first, again = make(), make()
    w, h = png(first)
    assert 300 < w <= 600 and 200 < h <= 400
    assert len(first.data) < 60_000
    assert first.data == again.data                 # no timestamps: a recompute changes nothing
    assert first.to_json()['title'] == "T"


def test_what_cannot_be_drawn_is_a_note():
    for make in (lambda: plots.cdf([], title="T"), lambda: plots.hist([float('nan')], title="T"),
                 lambda: plots.scatter([], [], title="T"), lambda: plots.cdfs({"a": []}, title="T"),
                 lambda: plots.cdf(X, lambda x: 1 / 0, title="T"),                  # a reference that fails
                 lambda: plots.hist(X, bins='auto', title="T"),
                 lambda: plots.hist(X, title=r"$\frac{1}$", math=True)):            # bad math, asked for
        got = make()
        assert isinstance(got, Note) and got.tone == 'warn' and "not drawn" in got.text
    assert plots.cdf([]).text == "plot: not drawn (nothing finite to plot)"


def test_text_is_literal_unless_math_is_asked_for():
    for title in (r"$\frac{1}$", "costs $5 and $6", "a_b^c $x$"):
        assert isinstance(plots.cdf(X, title=title, xlabel=title, name=title), Image), title
    assert isinstance(plots.cdf(X, title=r"$\alpha$", math=True), Image)
    assert plots.cdf(X, title="$x^2$").data != plots.cdf(X, title="$x^2$", math=True).data


def test_sizes_are_bounded():
    start = time.monotonic()
    for bins in (10**6, 10**9, np.linspace(0, 10, 10**6), -5, 0):
        assert isinstance(plots.hist(X, bins=bins), Image)
    assert isinstance(plots.cdfs({str(i): X[:50] for i in range(1000)}), Image)
    assert isinstance(plots.hist(np.arange(10**6), pmf=stats.poisson(3).pmf), Image)
    assert time.monotonic() - start < 30


HOSTILE = {'font.family': 'serif', 'font.size': 23, 'lines.linewidth': 7, 'axes.facecolor': 'black',
           'figure.dpi': 33, 'savefig.dpi': 300, 'axes.grid': False, 'axes.prop_cycle': cycler(color=['red']),
           'text.parse_math': True, 'path.simplify_threshold': 0.9, 'xtick.direction': 'in',
           'savefig.transparent': True, 'image.cmap': 'gray'}


def figures() -> list[bytes]:
    return [plots.cdf(X, stats.expon.cdf, title="$x$ T", xlabel="x").data,
            plots.hist(X, bins=40, density=stats.expon.pdf, title="T").data,
            plots.scatter(X[:1000], X[1000:2000], title="T").data]


def test_figures_ignore_ambient_settings():
    plain = figures()
    with matplotlib.rc_context(HOSTILE):
        assert figures() == plain
        assert matplotlib.rcParams['font.size'] == 23          # and left as they were


FRESH = """
import hashlib, sys
sys.path.insert(0, sys.argv[1])
import matplotlib
from test_stochastic_plots import HOSTILE, figures
matplotlib.rcParams.update(HOSTILE)
print(hashlib.sha256(b''.join(figures())).hexdigest())
"""


def test_figures_are_the_same_bytes_in_fresh_processes(tmp_path):
    rc = tmp_path / 'matplotlibrc'          # a user's settings file
    rc.write_text("font.family: serif\nlines.linewidth: 5\nfigure.facecolor: black\n")
    runs = [subprocess.run([sys.executable, '-c', FRESH, str(Path(__file__).parent)], capture_output=True,
                           text=True, check=True, env={**os.environ, **env}).stdout.strip()
            for env in ({}, {'MATPLOTLIBRC': str(rc)})]
    assert runs[0] == runs[1] == hashlib.sha256(b''.join(figures())).hexdigest()


def test_absurd_values_still_draw():
    png(plots.hist([1e308, 1.0, 2.0]))
    png(plots.cdf([1e300, -1e300, 0.5], stats.norm.cdf))
