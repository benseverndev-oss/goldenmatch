import io

import fitz  # pymupdf
from goldenmatch.documents.loader import load_pages
from PIL import Image


def _make_pdf(path, n_pages):
    doc = fitz.open()
    for i in range(n_pages):
        page = doc.new_page(width=200, height=200)
        page.insert_text((20, 40), f"page {i}")
    doc.save(str(path))
    doc.close()


def test_load_pdf_yields_one_pageimage_per_page(tmp_path):
    p = tmp_path / "two.pdf"
    _make_pdf(p, 2)
    pages = load_pages(p)
    assert len(pages) == 2
    assert [pg.index for pg in pages] == [0, 1]
    assert all(pg.png_bytes[:8] == b"\x89PNG\r\n\x1a\n" for pg in pages)
    assert all(pg.width > 0 and pg.height > 0 for pg in pages)


def test_load_image_yields_single_page(tmp_path):
    p = tmp_path / "card.png"
    Image.new("RGB", (120, 80), "white").save(p)
    pages = load_pages(p)
    assert len(pages) == 1
    assert pages[0].index == 0
    assert pages[0].png_bytes[:8] == b"\x89PNG\r\n\x1a\n"


def test_load_unsupported_extension_raises(tmp_path):
    p = tmp_path / "x.txt"
    p.write_text("hi")
    import pytest
    with pytest.raises(ValueError, match="unsupported"):
        load_pages(p)


def test_rgba_image_composited_on_white(tmp_path):
    # fully transparent RGBA canvas with one small opaque black square in the middle
    img = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    for x in range(15, 20):
        for y in range(15, 20):
            img.putpixel((x, y), (0, 0, 0, 255))
    p = tmp_path / "card.png"
    img.save(p)

    pages = load_pages(p)
    assert len(pages) == 1
    assert pages[0].png_bytes[:8] == b"\x89PNG\r\n\x1a\n"

    with Image.open(io.BytesIO(pages[0].png_bytes)) as out:
        assert out.mode == "RGB"
        corner = out.getpixel((0, 0))
        assert all(c > 240 for c in corner)  # transparent area composited onto white
