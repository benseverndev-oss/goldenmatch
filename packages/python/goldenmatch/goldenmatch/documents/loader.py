"""Load a document file into normalized PNG page images."""
from __future__ import annotations

import io
from pathlib import Path

from goldenmatch.documents.types import PageImage

_PDF = {".pdf"}
_IMG = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
_PDF_DPI = 200  # rasterization DPI; enough for text, bounded for cost


def _import_pil():
    try:
        from PIL import Image
    except ModuleNotFoundError as e:
        raise ModuleNotFoundError(
            "document ingest needs the extra: pip install 'goldenmatch[documents]'"
        ) from e
    return Image


def _import_fitz():
    try:
        import fitz
    except ModuleNotFoundError as e:
        raise ModuleNotFoundError(
            "document ingest needs the extra: pip install 'goldenmatch[documents]'"
        ) from e
    return fitz


def _png(img) -> PageImage:
    Image = _import_pil()
    if img.mode not in ("RGB", "L"):
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            # img.convert("RGB") on its own composites alpha onto black, burying dark
            # text/ink on a transparent background. Composite onto white first.
            rgba = img.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[3])
            img = bg
        else:
            img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return PageImage(png_bytes=buf.getvalue(), width=img.width, height=img.height, index=0)


def load_pages(path: str | Path) -> list[PageImage]:
    path = Path(path)
    ext = path.suffix.lower()
    if ext in _IMG:
        Image = _import_pil()
        with Image.open(path) as img:
            img.load()
            return [_png(img)]
    if ext in _PDF:
        fitz = _import_fitz()  # imported lazily so the extra is only needed for PDFs
        Image = _import_pil()
        out: list[PageImage] = []
        with fitz.open(str(path)) as doc:
            zoom = _PDF_DPI / 72.0
            mat = fitz.Matrix(zoom, zoom)
            for i, page in enumerate(doc):
                pix = page.get_pixmap(matrix=mat, alpha=False)
                with Image.open(io.BytesIO(pix.tobytes("png"))) as img:
                    pg = _png(img)
                # NOTE: source_page is coarse -- it's the page index within THIS file, not
                # a global page counter across a multi-file batch. Accepted limitation;
                # assemble()/ingest_documents() only ever compare it within one file's rows.
                out.append(PageImage(pg.png_bytes, pg.width, pg.height, index=i))
        return out
    raise ValueError(f"unsupported file type: {ext!r} (path={path})")
