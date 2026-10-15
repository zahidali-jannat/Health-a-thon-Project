"""The report types a patient can send, from config/report_tests.json - the single list both apps use.

A test's `route` decides where its upload goes:
  external  one value (HbA1c, FPG, PPBS) -> external_reports, reviewed on the split screen
  lab       several values or findings   -> patient_documents (lab_report) + lab_reports, values entered by the team
"""

import io
import json
from functools import cache

from .settings import BACKEND_DIR

# Types stored before this list existed, so old uploads still show the right short name.
ALIASES = {"HbA1c": "hba1c", "Fasting Sugar": "fpg", "PP Sugar": "ppbs"}
MAX_PAGES = 10                      # files merged into one report
MAX_MERGED_BYTES = 25 * 1024 * 1024

MAGIC = {"application/pdf": b"%PDF-", "image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff"}


class ReportError(Exception):
    pass


@cache
def catalog() -> dict:
    data = json.loads((BACKEND_DIR / "config" / "report_tests.json").read_text(encoding="utf-8"))
    data.pop("_comment", None)
    return data


def test(key: str) -> dict | None:
    return next((t for t in catalog()["tests"] if t["key"] == key), None)


def sniff(data: bytes) -> str | None:
    """The real type from the first bytes - never the name or the type the browser claims."""
    return next((ct for ct, magic in MAGIC.items() if data.startswith(magic)), None)


def merge(files: list[tuple[bytes, str]]) -> tuple[bytes, str]:
    """Pages sent together become ONE file, in the order chosen: one page as it is, several as one PDF."""
    if not files:
        raise ReportError("Please add a photo or PDF.")
    if len(files) > MAX_PAGES:
        raise ReportError(f"Please send at most {MAX_PAGES} pages at a time.")
    if len(files) == 1:
        return files[0]
    from PIL import Image
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    for data, content_type in files:
        if content_type == "application/pdf":
            source = data
        else:
            image = Image.open(io.BytesIO(data))
            image.load()
            out = io.BytesIO()
            image.convert("RGB").save(out, format="PDF", resolution=150)
            source = out.getvalue()
        try:
            for page in PdfReader(io.BytesIO(source)).pages:
                writer.add_page(page)
        except Exception as e:                                     # noqa: BLE001 - a damaged PDF
            raise ReportError("One of the files could not be opened. Please send it again.") from e
    out = io.BytesIO()
    writer.write(out)
    merged = out.getvalue()
    if len(merged) > MAX_MERGED_BYTES:
        raise ReportError("These pages are too large together. Please send fewer pages at a time.")
    return merged, "application/pdf"
