"""Scanned pages: the embedded image is taken directly; anything else is rasterized.

Every PDF here is built in the test from synthetic pixels.
"""
import io

import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
import pytest
from PIL import Image, ImageDraw

from examinator.core.defs import Region
from examinator.pdf import render
from examinator.pdf.layout import Layout
from examinator.pdf.render import Renderer

LETTER = (612, 792)   # points


def scan(dpi, mode='RGB'):
    """A synthetic letter-size scan: smooth blotches plus a dark box at 200-400 x 300-450 per mille."""
    w, h = round(8.5 * dpi), 11 * dpi
    rng = np.random.default_rng(dpi)
    im = Image.fromarray(rng.integers(150, 256, (h // 16, w // 16, 3), dtype=np.uint8))
    im = im.resize((w, h), Image.Resampling.BILINEAR).convert(mode)
    ImageDraw.Draw(im).rectangle((0.2 * w, 0.3 * h, 0.4 * w, 0.45 * h), fill='black')
    return im


def jpeg(im):
    buf = io.BytesIO()
    im.save(buf, 'JPEG', quality=90)
    return buf.getvalue()


def make_pdf(path, images=(), text=False):
    """One letter page holding `images`, each (PIL image or JPEG bytes, (x, y, w, h) in points)."""
    doc = pdfium.PdfDocument.new()
    page = doc.new_page(*LETTER)
    for source, (x, y, w, h) in images:
        obj = pdfium.PdfImage.new(doc)
        if isinstance(source, bytes):
            obj.load_jpeg(io.BytesIO(source), inline=False)
        else:
            obj.set_bitmap(pdfium.PdfBitmap.from_pil(source))
        obj.set_matrix(pdfium.PdfMatrix().scale(w, h).translate(x, y))
        page.insert_obj(obj)
    if text:
        font = pdfium_c.FPDFText_LoadStandardFont(doc, b'Helvetica')
        obj = pdfium_c.FPDFPageObj_CreateTextObj(doc, font, 24.0)
        words = "Name:\0".encode('utf-16-le')
        pdfium_c.FPDFText_SetText(obj, (pdfium_c.FPDF_WCHAR * (len(words) // 2)).from_buffer_copy(words))
        pdfium_c.FPDFPageObj_Transform(obj, 1, 0, 0, 1, 72, 700)
        pdfium_c.FPDFPage_InsertObject(page, obj)
    page.gen_content()
    doc.save(str(path))
    doc.close()
    return path


FULL = (0, 0) + LETTER


@pytest.fixture
def taken(monkeypatch):
    """Records whether each rendering took the fast path."""
    seen = []
    real = render.scan_image

    def spy(page, dpi):
        out = real(page, dpi)
        seen.append(out is not None)
        return out
    monkeypatch.setattr(render, 'scan_image', spy)
    return seen


def test_jpeg_scan_at_native_resolution_is_copied_untouched(tmp_path, taken):
    data = jpeg(scan(160))
    pdf = make_pdf(tmp_path / 's.pdf', [(data, FULL)])
    assert Renderer(tmp_path / 'cache', dpi=160).page_path(pdf, 0).read_bytes() == data
    assert taken == [True]


@pytest.mark.parametrize('native, dpi, size', [(300, 160, (1275, 1650)),   # halved: 150 is closer than 300
                                               (600, 160, (1275, 1650)),   # quartered
                                               (200, 160, (1700, 2200)),   # 100 is further than 200
                                               (100, 160, (850, 1100))])   # never enlarged
def test_jpeg_scan_is_shrunk_by_a_power_of_two(tmp_path, taken, native, dpi, size):
    src = scan(native)
    pdf = make_pdf(tmp_path / 's.pdf', [(jpeg(src), FULL)])
    page = Renderer(tmp_path / 'cache', dpi=dpi).page(pdf, 0)
    assert taken == [True] and page.size == size
    want = np.asarray(src.resize(size, Image.Resampling.BOX), dtype=float)
    assert np.abs(np.asarray(page, dtype=float) - want).mean() < 6


@pytest.mark.parametrize('mode', ['RGB', 'L'])
def test_lossless_scan_pixels_are_the_source(tmp_path, taken, mode):
    src = scan(300, mode)
    pdf = make_pdf(tmp_path / 's.pdf', [(src, FULL)])
    image = render.scan_image(pdfium.PdfDocument(str(pdf))[0], 160)
    assert np.array_equal(np.asarray(image.convert(mode)), np.asarray(src.reduce(2)))


@pytest.mark.parametrize('content, text', [
    pytest.param(lambda s: [], True, id='vector'),
    pytest.param(lambda s: [(s, FULL), (s, (300, 300, 100, 100))], False, id='two-images'),
    pytest.param(lambda s: [(s, FULL)], True, id='image-and-text'),
    pytest.param(lambda s: [(s, (0, 0, 306, 792))], False, id='image-on-half-the-page'),
    pytest.param(lambda s: [(s.rotate(90, expand=True), FULL)], False, id='sideways-pixels'),
])
def test_other_pages_are_rasterized(tmp_path, taken, content, text):
    pdf = make_pdf(tmp_path / 's.pdf', content(scan(100)), text=text)
    page = Renderer(tmp_path / 'cache', dpi=72).page(pdf, 0)
    assert taken == [False] and page.size == LETTER


def test_crops_land_in_the_same_place_on_both_paths(tmp_path, monkeypatch):
    pdf = make_pdf(tmp_path / 's.pdf', [(jpeg(scan(300)), FULL)])
    layout = Layout(window=(20, 30, 980, 970))
    box = Region(1, round(180 / 0.96), round(270 / 0.94), round(380 / 0.96), round(420 / 0.94))   # the dark box
    fast = Renderer(tmp_path / 'fast', dpi=160).page(pdf, 0)
    monkeypatch.setattr(render, 'scan_image', lambda page, dpi: None)
    slow = Renderer(tmp_path / 'slow', dpi=160).page(pdf, 0)
    assert fast.size == (1275, 1650) and slow.size == (1360, 1760)
    for region in (box, Region(1, 100, 200, 500, 600)):
        a, b = (np.asarray(layout.crop(p, region, pad=0).convert('L').resize((60, 60), Image.Resampling.BOX),
                           dtype=float) for p in (fast, slow))
        assert np.abs(a - b).mean() < 6
    assert np.asarray(layout.crop(fast, box, pad=0).convert('L')).mean() < 30


def test_cache_names_differ_from_rasterized_ones(tmp_path):
    pdf = make_pdf(tmp_path / 's.pdf', [(jpeg(scan(100)), FULL)])
    path = Renderer(tmp_path / 'cache', dpi=160).page_path(pdf, 0)
    assert path.name == 'p000-160s.jpg'
