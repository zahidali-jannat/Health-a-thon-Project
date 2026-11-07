"""Getting text out of a file: the text layer of a PDF (on this computer, no AI), and quality notes for photos.
Pages without a usable text layer (scans, photos) are handed to OCR by the pipeline."""

import io

from .parse import rules


def pdf_pages(data: bytes) -> list[str]:
    """The embedded text of each page, in order ('' for a page with no text layer)."""
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    out = []
    for page in reader.pages:
        try:
            out.append(page.extract_text() or "")
        except Exception:                                          # noqa: BLE001 - one unreadable page, not the file
            out.append("")
    return out


def pdf_subset(data: bytes, page_numbers: list[int]) -> bytes:
    """A new PDF holding only these pages (1-based) - what is sent to OCR, at most 10 pages per job."""
    from pypdf import PdfReader, PdfWriter
    reader, writer = PdfReader(io.BytesIO(data)), PdfWriter()
    for n in page_numbers:
        writer.add_page(reader.pages[n - 1])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def image_quality(data: bytes) -> list[str]:
    """Notes for a photo of a page: too small to read, or blurred. Notes only - the photo is still read."""
    from PIL import Image, ImageFilter, ImageStat
    q = rules()["quality"]
    notes = []
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:                                              # noqa: BLE001
        return ["unreadable_image"]
    if min(image.size) < q["min_image_side_px"]:
        notes.append("low_resolution")
    gray = image.convert("L")
    gray.thumbnail((900, 900))
    if ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).var[0] < q["blur_edge_variance"]:
        notes.append("blurry")
    return notes
