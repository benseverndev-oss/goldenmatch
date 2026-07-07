"""Load a document file into normalized PNG page images."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from goldenmatch.documents.types import PageImage

_PDF = {".pdf"}
_IMG = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
_PDF_DPI = 200  # rasterization DPI; enough for text, bounded for cost


def _png(img: Image.Image) -> PageImage:
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return PageImage(png_bytes=buf.getvalue(), width=img.width, height=img.height, index=0)


def load_pages(path: str | Path) -> list[PageImage]:
    path = Path(path)
    ext = path.suffix.lower()
    if ext in _IMG:
        with Image.open(path) as img:
            img.load()
            return [_png(img)]
    if ext in _PDF:
        import fitz  # imported lazily so the extra is only needed for PDFs
        out: list[PageImage] = []
        with fitz.open(str(path)) as doc:
            zoom = _PDF_DPI / 72.0
            mat = fitz.Matrix(zoom, zoom)
            for i, page in enumerate(doc):
                pix = page.get_pixmap(matrix=mat, alpha=False)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                pg = _png(img)
                out.append(PageImage(pg.png_bytes, pg.width, pg.height, index=i))
        return out
    raise ValueError(f"unsupported file type: {ext!r} (path={path})")
