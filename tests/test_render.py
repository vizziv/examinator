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


def rect(doc, page, x, y, w, h):
    obj = pdfium_c.FPDFPageObj_CreateNewRect(x, y, w, h)
    pdfium_c.FPDFPageObj_SetFillColor(obj, 255, 255, 255, 255)
    pdfium_c.FPDFPath_SetDrawMode(obj, pdfium_c.FPDF_FILLMODE_ALTERNATE, 0)
    pdfium_c.FPDFPage_InsertObject(page, obj)


def make_pdf(path, images=(), text=False, size=LETTER, under=(), over=(), matrix=None, rotation=0):
    """One page holding `images`, each (PIL image or JPEG bytes, (x, y, w, h) in points), or
    placed by `matrix` (a b c d e f). `under` and `over` are white rectangles below and above."""
    doc = pdfium.PdfDocument.new()
    page = doc.new_page(*size)
    for r in under:
        rect(doc, page, *r)
    for source, (x, y, w, h) in images:
        obj = pdfium.PdfImage.new(doc)
        if isinstance(source, bytes):
            obj.load_jpeg(io.BytesIO(source), inline=False)
        else:
            obj.set_bitmap(pdfium.PdfBitmap.from_pil(source))
        obj.set_matrix(pdfium.PdfMatrix(*matrix) if matrix else pdfium.PdfMatrix().scale(w, h).translate(x, y))
        page.insert_obj(obj)
    for r in over:
        rect(doc, page, *r)
    if rotation:
        page.set_rotation(rotation)
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

    def spy(page):
        out = real(page)
        seen.append(out is not None)
        return out
    monkeypatch.setattr(render, 'scan_image', spy)
    return seen


@pytest.mark.parametrize('native', [100, 200, 300, 600])
def test_jpeg_scan_is_copied_untouched_whatever_its_resolution(tmp_path, taken, native):
    data = jpeg(scan(native))
    pdf = make_pdf(tmp_path / 's.pdf', [(data, FULL)])
    assert Renderer(tmp_path / 'cache', dpi=160).page_path(pdf, 0).read_bytes() == data
    assert taken == [True]


@pytest.mark.parametrize('mode', ['RGB', 'L'])
def test_lossless_scan_pixels_are_the_source(tmp_path, taken, mode):
    src = scan(300, mode)
    pdf = make_pdf(tmp_path / 's.pdf', [(src, FULL)])
    image = render.scan_image(pdfium.PdfDocument(str(pdf))[0])
    assert np.array_equal(np.asarray(image.convert(mode)), np.asarray(src))


@pytest.mark.parametrize('content, text', [
    pytest.param(lambda s: [], True, id='vector'),
    pytest.param(lambda s: [(s, FULL), (s, (300, 300, 100, 100))], False, id='two-images'),
    pytest.param(lambda s: [(s, FULL)], True, id='image-and-text'),
])
def test_other_pages_are_rasterized(tmp_path, taken, content, text):
    pdf = make_pdf(tmp_path / 's.pdf', content(scan(100)), text=text)
    page = Renderer(tmp_path / 'cache', dpi=72).page(pdf, 0)
    assert taken == [False] and page.size == LETTER


def test_something_drawn_over_the_image_is_not_a_plain_scan(tmp_path, taken):
    pdf = make_pdf(tmp_path / 's.pdf', [(jpeg(scan(100)), FULL)], over=[(100, 100, 200, 50)])
    Renderer(tmp_path / 'cache', dpi=72).page(pdf, 0)
    assert taken == [False]


def test_a_background_and_a_wrong_page_size_do_not_matter(tmp_path, taken):
    """What scanning services produce: white rectangles, then the image on part of a page of the wrong size."""
    data = jpeg(scan(100))
    pdf = make_pdf(tmp_path / 's.pdf', [(data, (27.75, 77.25, 242.25, 342.75))], size=(298.08, 420),
                   under=[(0, 0, 298.08, 420), (27.75, 0, 242.25, 420)])
    assert Renderer(tmp_path / 'cache', dpi=160).page_path(pdf, 0).read_bytes() == data
    assert taken == [True]


W, H = LETTER
ORIENTATIONS = {                                    # a b c d e f: every quarter turn and mirror image
    'upright': (W, 0, 0, H, 0, 0), 'mirrored': (-W, 0, 0, H, W, 0), 'upside-down-mirrored': (W, 0, 0, -H, 0, H),
    'half-turn': (-W, 0, 0, -H, W, H), 'quarter': (0, W, -H, 0, H, 0), 'three-quarter': (0, -W, H, 0, 0, W),
    'diagonal': (0, W, H, 0, 0, 0), 'other-diagonal': (0, -W, -H, 0, H, W),
}


@pytest.mark.parametrize('name', ORIENTATIONS)
@pytest.mark.parametrize('rotation', [0, 90])
def test_the_image_comes_out_as_the_page_draws_it(tmp_path, taken, name, rotation):
    """The fast path against PDFium's own rendering, for each way an image can be drawn."""
    m = ORIENTATIONS[name]
    size = LETTER if m[0] else (H, W)
    pdf = make_pdf(tmp_path / 's.pdf', [(scan(50), FULL)], size=size, matrix=m, rotation=rotation)
    fast = Renderer(tmp_path / 'fast', dpi=50).page(pdf, 0)
    assert taken == [True]
    page = pdfium.PdfDocument(str(pdf))[0]
    want = page.render(scale=50 / 72).to_pil().convert('L').resize((40, 40), Image.Resampling.BOX)
    got = fast.convert('L').resize((40, 40), Image.Resampling.BOX)
    assert np.abs(np.asarray(got, dtype=float) - np.asarray(want, dtype=float)).mean() < 4


def test_a_skewed_image_is_rasterized(tmp_path, taken):
    pdf = make_pdf(tmp_path / 's.pdf', [(scan(50), FULL)], matrix=(W, 60, -60, H, 30, 0))
    Renderer(tmp_path / 'cache', dpi=72).page(pdf, 0)
    assert taken == [False]


def test_crops_land_in_the_same_place_on_both_paths(tmp_path, monkeypatch):
    pdf = make_pdf(tmp_path / 's.pdf', [(jpeg(scan(300)), FULL)])
    layout = Layout(print_area=(20, 30, 980, 970))
    box = Region(1, round(180 / 0.96), round(270 / 0.94), round(380 / 0.96), round(420 / 0.94))   # the dark box
    fast = Renderer(tmp_path / 'fast', dpi=160).page(pdf, 0)
    monkeypatch.setattr(render, 'scan_image', lambda page: None)
    slow = Renderer(tmp_path / 'slow', dpi=160).page(pdf, 0)
    assert fast.size == (2550, 3300) and slow.size == (1360, 1760)
    for region in (box, Region(1, 100, 200, 500, 600)):
        a, b = (np.asarray(layout.crop(p, region, pad=0).convert('L').resize((60, 60), Image.Resampling.BOX),
                           dtype=float) for p in (fast, slow))
        assert np.abs(a - b).mean() < 6
    assert np.asarray(layout.crop(fast, box, pad=0).convert('L')).mean() < 30


def test_cache_names_differ_from_rasterized_ones(tmp_path):
    pdf = make_pdf(tmp_path / 's.pdf', [(jpeg(scan(100)), FULL)])
    path = Renderer(tmp_path / 'cache', dpi=160).page_path(pdf, 0)
    assert path.name == 'p000-160i.jpg'
