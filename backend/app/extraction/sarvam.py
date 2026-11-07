"""OCR through Sarvam Document Intelligence (Sarvam Vision), over plain HTTPS.

Flow (docs.sarvam.ai, Document Intelligence): POST /doc-ai/v1/job/digitise (file, language, output_format=json)
-> poll GET /doc-ai/v1/job/{id}/status until a terminal state -> GET /doc-ai/v1/job/{id}/download-url -> a ZIP with
metadata/page_NNN.json per page. The page JSON's exact field names are not documented, so text is collected from
every string under a "text"/"content"/"markdown" key, in document order - robust to small format changes.

Only used when SARVAM_API_KEY is set. Setting it means scanned pages and photos of reports leave this server for
Sarvam's API: the clinic must be comfortable with Sarvam's data terms before setting it for real patients.
"""

import io
import json
import re
import time
import zipfile

import httpx

from ..settings import get_settings

BASE = "https://api.sarvam.ai"
TERMINAL = {"completed", "partially_completed", "failed", "rejected"}
MAX_PAGES_PER_JOB = 10


class OcrError(Exception):
    """A provider failure, worded without any patient content (it is stored and shown to the team)."""


def configured() -> bool:
    return bool(get_settings().sarvam_api_key)


def _client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=60, transport=transport,
                        headers={"api-subscription-key": get_settings().sarvam_api_key or ""})


def ocr(data: bytes, content_type: str, *, transport: httpx.BaseTransport | None = None,
        poll_seconds: float = 2.0, max_wait_seconds: float = 180.0) -> tuple[list[str], str]:
    """Text per page for one file (a PDF of at most 10 pages, or one image). Returns (pages, job_id)."""
    ext = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg"}[content_type]
    with _client(transport) as c:
        r = c.post("/doc-ai/v1/job/digitise",
                   data={"language": get_settings().sarvam_language, "output_format": "json"},
                   files={"file": (f"document.{ext}", data, content_type)})
        if r.status_code >= 400:
            raise OcrError(f"The OCR service refused the file (HTTP {r.status_code}).")
        job_id = r.json().get("job_id")
        if not job_id:
            raise OcrError("The OCR service did not start a job.")
        waited, status = 0.0, "pending"
        while status not in TERMINAL:
            if waited >= max_wait_seconds:
                raise OcrError("The OCR service took too long.")
            time.sleep(poll_seconds)
            waited += poll_seconds
            s = c.get(f"/doc-ai/v1/job/{job_id}/status")
            if s.status_code >= 400:
                raise OcrError(f"Could not check the OCR job (HTTP {s.status_code}).")
            status = s.json().get("status", "failed")
        if status in ("failed", "rejected"):
            raise OcrError(f"The OCR job {status}.")
        d = c.get(f"/doc-ai/v1/job/{job_id}/download-url")
        if d.status_code >= 400 or not d.json().get("url"):
            raise OcrError("Could not fetch the OCR result.")
        url = d.json()["url"]
    with httpx.Client(timeout=60, transport=transport) as plain:   # a pre-signed URL: never send our API key there
        archive = plain.get(url)
        if archive.status_code >= 400:
            raise OcrError("Could not download the OCR result.")
    return pages_from_archive(archive.content), job_id


def pages_from_archive(blob: bytes) -> list[str]:
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as e:
        raise OcrError("The OCR result was not readable.") from e
    pages = sorted((n for n in z.namelist() if re.search(r"page_(\d+)\.json$", n)),
                   key=lambda n: int(re.search(r"page_(\d+)\.json$", n).group(1)))
    if pages:
        return [_texts(json.loads(z.read(n))) for n in pages]
    main = next((n for n in z.namelist() if n.endswith((".json", ".md", ".html")) and "manifest" not in n), None)
    if main is None:
        return []
    raw = z.read(main).decode("utf-8", "replace")
    return [_texts(json.loads(raw))] if main.endswith(".json") else [raw]


def _texts(node) -> str:
    """Every text string in the page JSON, in order, one per line."""
    out: list[str] = []

    def walk(n):
        if isinstance(n, dict):
            for k, v in n.items():
                if isinstance(v, str) and k.lower() in ("text", "content", "markdown", "html", "value"):
                    out.append(v)
                else:
                    walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(node)
    return "\n".join(t for t in out if t.strip())
