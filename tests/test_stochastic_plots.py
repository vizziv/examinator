"""examinator_kits.stochastic.plots: figures as PNG image blocks, from seeded synthetic samples."""
import io

import numpy as np
import pytest
from PIL import Image as PILImage
from scipy import stats

from examinator.std.blocks import Image
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


def test_nothing_to_draw():
    for make in (lambda: plots.cdf([]), lambda: plots.hist([float('nan')]), lambda: plots.scatter([], []),
                 lambda: plots.cdfs({"a": []})):
        with pytest.raises(ValueError):
            make()


def test_absurd_values_still_draw():
    png(plots.hist([1e308, 1.0, 2.0]))
    png(plots.cdf([1e300, -1e300, 0.5], stats.norm.cdf))
