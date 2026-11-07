"""The document extraction pipeline: ingest -> text -> structured values -> validation -> saved as SUGGESTIONS.

States (document_processing.status, enforced by a trigger):
  uploaded -> processing -> extraction_completed | needs_review | failed -> verified | corrected | rejected
Nothing here is a clinical record. Values reach the record only through the clinical team's existing save (the
split-screen review or "enter results"), and the decision hooks below then mark each suggestion verified, corrected or
rejected - so it is always clear which values a person checked.

Handwritten or unclear documents: no value is suggested at all. The document is marked
"Handwritten or unclear - clinical team verification required" and the team reads it themselves.
"""

import json
import logging
import threading

from .. import db, storage
from ..db import now_iso
from ..repo import new_id
from . import match, parse, sarvam, text, validate
from .parse import Found

log = logging.getLogger("uc2.extraction")

DECIDED = ("verified", "corrected", "rejected")
STATUS_TEXT = {
    "uploaded": "Waiting to be read", "processing": "Reading…", "extraction_completed": "Read · check the values",
    "needs_review": "Needs a person", "failed": "Reading failed", "verified": "Verified", "corrected": "Corrected",
    "rejected": "Rejected",
}
REASON_TEXT = {
    "handwritten_or_unclear": "Handwritten or unclear - clinical team verification required",
    "ocr_not_configured": "Photo or scan - read it yourself (automatic reading of scans is off)",
    "unsupported_format": "This file type can't be read automatically",
    "issues_found": "Values read - problems found, check them",
    "nothing_found": "No values found - read it yourself",
    "too_many_pages": "Too many pages to read automatically",
}


# ------------------------------------------------------------------ ingest: one row per upload

def register(conn, patient_id: str, source_kind: str, source_id: str, document_kind: str, sha256: str,
             content_type: str, expected_test: str | None = None) -> str:
    """Called inside the upload's own transaction - the file and its processing row exist together or not at all."""
    pid, now = new_id(), now_iso()
    conn.execute("INSERT INTO document_processing (id, patient_id, source_kind, source_id, document_kind, expected_test, "
                 "sha256, content_type, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (pid, patient_id, source_kind, source_id, document_kind, expected_test, sha256, content_type, now, now))
    return pid


def _source(conn, row) -> dict | None:
    if row["source_kind"] == "external_report":
        r = conn.execute("SELECT storage_key, test_type FROM external_reports WHERE id = ? AND patient_id = ?",
                         (row["source_id"], row["patient_id"])).fetchone()
    else:
        r = conn.execute("SELECT storage_key, description FROM patient_documents WHERE id = ? AND patient_id = ?",
                         (row["source_id"], row["patient_id"])).fetchone()
    return dict(r) if r else None


# ------------------------------------------------------------------ stage 2: text

def read_text(data: bytes, content_type: str) -> dict:
    """Stage 2 on its own - used at upload time to check the report before it is saved."""
    return _read_text(data, content_type)


def mismatch_message(expected: str | None, document_kind: str, found: list[str]) -> str:
    from .. import report_tests
    t = report_tests.test(expected) if expected else None
    chosen = "prescription" if document_kind == "prescription" else f"{t['short'] if t else 'chosen'} report"
    return f"Not a {chosen} · {', '.join(found)} found"


def _read_text(data: bytes, content_type: str) -> dict:
    q = parse.rules()["quality"]
    out = {"pages": [], "method": None, "jobs": [], "reason": None, "quality": []}
    if content_type not in ("application/pdf", "image/png", "image/jpeg"):
        out["reason"] = "unsupported_format"
        return out
    if content_type == "application/pdf":
        texts = text.pdf_pages(data)
        if len(texts) > q["max_pages"]:
            out["reason"] = "too_many_pages"
            texts = texts[:q["max_pages"]]
        pages = [{"page_no": i, "method": "pdf_text", "text": t, "quality": []} for i, t in enumerate(texts, 1)]
        need = [p["page_no"] for p in pages if len(p["text"].strip()) < q["min_text_chars_per_page"]]
        if need and sarvam.configured():
            for start in range(0, len(need), sarvam.MAX_PAGES_PER_JOB):
                chunk = need[start:start + sarvam.MAX_PAGES_PER_JOB]
                got, job = sarvam.ocr(text.pdf_subset(data, chunk), "application/pdf")
                out["jobs"].append(job)
                for n, t in zip(chunk, got + [""] * (len(chunk) - len(got))):
                    pages[n - 1].update(method="ocr_sarvam", text=parse.normalize_text(t))
        for p in pages:
            if len(p["text"].strip()) < q["min_text_chars_per_page"]:
                p["quality"].append("little_or_no_text")
        used = {p["method"] for p in pages if p["text"].strip()}
        out["method"] = "pdf_text+ocr_sarvam" if len(used) == 2 else (used.pop() if used else None)
        if not used and need and not sarvam.configured():
            out["reason"] = out["reason"] or "ocr_not_configured"
        out["pages"] = pages
        return out
    notes = text.image_quality(data)                                   # a photo of a page
    out["quality"] = notes
    if not sarvam.configured():
        out["reason"] = "ocr_not_configured"
        return out
    got, job = sarvam.ocr(data, content_type)
    out["jobs"].append(job)
    out["pages"] = [{"page_no": 1, "method": "ocr_sarvam", "text": parse.normalize_text("\n".join(got)), "quality": notes}]
    out["method"] = "ocr_sarvam"
    return out


# ------------------------------------------------------------------ stages 3-5

def process(processing_id: str) -> str | None:
    """Read one upload. Safe to call again (a retry replaces the earlier suggestions). Returns the new status."""
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM document_processing WHERE id = ?", (processing_id,)).fetchone()
        if row is None or row["status"] not in ("uploaded", "failed", "needs_review", "extraction_completed"):
            return row and row["status"]
        conn.execute("UPDATE document_processing SET status = 'processing', attempts = attempts + 1, error = NULL, "
                     "updated_at = ? WHERE id = ?", (now_iso(), processing_id))
        conn.commit()
        src = _source(conn, row)
        try:
            if src is None:
                raise sarvam.OcrError("The uploaded file is missing.")
            read = _read_text(storage.read(src["storage_key"]), row["content_type"])
        except Exception as e:                                         # noqa: BLE001 - recorded, retried by a person
            msg = str(e) if isinstance(e, sarvam.OcrError) else "The file could not be read."
            log.warning("extraction %s failed: %s", processing_id, type(e).__name__)
            conn.execute("UPDATE document_processing SET status = 'failed', error = ?, updated_at = ? "
                         "WHERE id = ? AND status = 'processing'", (msg, now_iso(), processing_id))
            conn.commit()
            return "failed"
        status = _store(conn, dict(row), read)
        conn.commit()
        return status
    finally:
        conn.close()


def _store(conn, row: dict, read: dict) -> str:
    pid, patient_id, now = row["id"], row["patient_id"], now_iso()
    q = parse.rules()["quality"]
    for table in ("validation_issues", "extracted_fields", "document_pages"):
        conn.execute(f"DELETE FROM {table} WHERE processing_id = ?", (pid,))
    for p in read["pages"]:
        conn.execute("INSERT INTO document_pages (processing_id, page_no, method, text, quality) VALUES (?, ?, ?, ?, ?)",
                     (pid, p["page_no"], p["method"], p["text"], json.dumps(p["quality"]) if p["quality"] else None))
    texts = [p["text"] for p in read["pages"]]
    all_text = "\n".join(texts)
    reason = read["reason"]

    doc_issues: list[validate.Issue] = []
    for note in read["quality"]:
        doc_issues.append(validate.Issue("image_quality", "warning",
                                         {"low_resolution": "The photo is small - small print may be misread.",
                                          "blurry": "The photo looks blurred.",
                                          "unreadable_image": "The image could not be opened."}.get(note, note)))
    fields: list[Found] = []
    for p in read["pages"]:
        if row["document_kind"] == "lab_report":
            fields += parse.observations(p["text"], p["page_no"])
        else:
            fields += parse.medications(p["text"], p["page_no"])
        fields += parse.allergies(p["text"], p["page_no"])

    # Handwritten or unclear: text that is mostly fragments, or almost none after reading. No value is suggested.
    readable = parse.readable_share(all_text)
    unclear = bool(read["pages"]) and reason is None and (
        len(all_text.strip()) < q["min_text_chars_per_page"] or readable < q["min_readable_word_share"])
    if unclear:
        reason, fields = "handwritten_or_unclear", []

    observed = parse.report_date(texts) if row["document_kind"] == "lab_report" and fields else None
    lab = parse.lab_name(texts[0]) if texts and row["document_kind"] == "lab_report" else None
    norms, field_notes = [], []
    for f in fields:
        n, notes = validate.normalize(f)
        norms.append(n)
        field_notes.append(notes)
    issues = list(doc_issues)
    if fields and row["document_kind"] == "lab_report":
        from ..deps import today
        issues += validate.check_date(observed, today(), required=True)
    issues += validate.within_document(fields, norms)
    issues += validate.against_record(conn, patient_id, fields, norms, observed and observed.value, row["source_id"])
    m = match.check(row["expected_test"], row["document_kind"], texts) if reason is None else match.Match("unknown")
    if m.result == "mismatch":                         # the report is not the test the patient chose
        msg = mismatch_message(row["expected_test"], row["document_kind"], m.found)
        issues.append(validate.Issue("test_mismatch", "error", msg + (" · sent anyway" if row.get("patient_override") else "")))
    elif reason is None and row["document_kind"] == "lab_report":
        exp = row["expected_test"]
        from .. import report_tests
        t = report_tests.test(exp) if exp else None
        issues += validate.expected_found(exp, fields, t and t["short"])
    dup = conn.execute("SELECT created_at FROM document_processing WHERE patient_id = ? AND sha256 = ? AND id != ? "
                       "ORDER BY created_at LIMIT 1", (patient_id, row["sha256"], pid)).fetchone()
    if dup:
        issues.append(validate.Issue("duplicate_document", "warning",
                                     f"The same file was sent before ({dup[0][:10]}). It may be a repeat."))
    for i, notes in enumerate(field_notes):
        issues += [validate.Issue(c, s, m, i) for c, s, m in notes]

    method = read["method"] or "pdf_text"
    page_methods = {p["page_no"]: p["method"] for p in read["pages"]}
    ids = []
    for i, f in enumerate(fields):
        own = [x for x in issues if x.field == i]
        cur = conn.execute(
            "INSERT INTO extracted_fields (processing_id, patient_id, field_kind, analyte, name, value_text, value_num, unit, "
            "normalized_value, normalized_unit, conversion, reference_range, observed_on, date_text, lab_name, page_no, "
            "source_text, method, confidence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, patient_id, f.kind, f.analyte, f.name, f.value_text, f.value_num, f.unit, norms[i].value, norms[i].unit,
             norms[i].conversion, f.reference_range,
             observed.value.isoformat() if observed and observed.value and f.kind == "observation" else None,
             observed.text if observed and f.kind == "observation" else None, lab, f.page_no, f.source_text,
             page_methods.get(f.page_no, method), validate.confidence(page_methods.get(f.page_no, method), f, own), now))
        ids.append(cur.lastrowid)
    for x in issues:
        conn.execute("INSERT INTO validation_issues (processing_id, field_id, code, severity, message, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (pid, ids[x.field] if x.field is not None else None, x.code, x.severity,
                                                   x.message, now))

    if reason is None and not fields:
        reason = "nothing_found"
    if reason is None and any(x.severity == "error" for x in issues):
        reason = "issues_found"
    status = "needs_review" if reason else "extraction_completed"
    conn.execute("UPDATE document_processing SET status = ?, review_reason = ?, extraction_method = ?, page_count = ?, "
                 "provider_job_ids = ?, processed_at = ?, updated_at = ? WHERE id = ? AND status = 'processing'",
                 (status, reason, read["method"], len(read["pages"]) or None, json.dumps(read["jobs"]) if read["jobs"] else None,
                  now, now, pid))
    return status


def store_now(conn, source_kind: str, source_id: str, read: dict, override: str | None = None) -> None:
    """The upload already read the file (to check it matches the chosen test): store the result now, in the upload's
    own transaction, instead of reading the file a second time in the background."""
    row = _row(conn, source_kind, source_id)
    if row is None:
        return
    conn.execute("UPDATE document_processing SET status = 'processing', attempts = attempts + 1, patient_override = ?, "
                 "updated_at = ? WHERE id = ?", (override, now_iso(), row["id"]))
    _store(conn, dict(_row(conn, source_kind, source_id)), read)


# ------------------------------------------------------------------ background worker

_wake = threading.Event()
_started = False


def wake() -> None:
    """Called after an upload commits: read it now instead of at the next poll."""
    _wake.set()


def run_pending(limit: int = 20) -> int:
    conn = db.connect()
    try:
        ids = [r[0] for r in conn.execute("SELECT id FROM document_processing WHERE status = 'uploaded' "
                                          "ORDER BY created_at LIMIT ?", (limit,))]
    finally:
        conn.close()
    for pid in ids:
        process(pid)
    return len(ids)


def recover_interrupted() -> None:
    """A restart in the middle of reading: mark those failed so a person can retry them."""
    conn = db.connect()
    try:
        conn.execute("UPDATE document_processing SET status = 'failed', error = 'Reading was interrupted by a restart.', "
                     "updated_at = ? WHERE status = 'processing'", (now_iso(),))
        conn.commit()
    finally:
        conn.close()


def backfill() -> int:
    """Uploads that arrived before automatic reading existed and are still waiting for the team: read them too."""
    from .. import report_tests
    conn = db.connect()
    try:
        n = 0
        for r in conn.execute(
                "SELECT x.id, x.patient_id, x.test_type, x.sha256, x.content_type FROM external_reports x "
                "WHERE x.document_type = 'lab_report' AND x.status = 'pending_review' AND NOT EXISTS "
                "(SELECT 1 FROM document_processing p WHERE p.source_kind = 'external_report' AND p.source_id = x.id)").fetchall():
            register(conn, r["patient_id"], "external_report", r["id"], "lab_report", r["sha256"], r["content_type"],
                     report_tests.ALIASES.get(r["test_type"]))
            n += 1
        for r in conn.execute(
                "SELECT d.id, d.patient_id, d.document_type, d.description, d.sha256, d.content_type FROM patient_documents d "
                "WHERE d.source = 'patient_upload' AND d.review_status = 'pending' AND d.document_type IN ('lab_report', 'prescription') "
                "AND NOT EXISTS (SELECT 1 FROM document_processing p WHERE p.source_kind = 'document' AND p.source_id = d.id)").fetchall():
            register(conn, r["patient_id"], "document", r["id"], r["document_type"], r["sha256"], r["content_type"],
                     expected_key(r["description"]))
            n += 1
        conn.commit()
        return n
    finally:
        conn.close()


def expected_key(description: str | None) -> str | None:
    from .. import report_tests
    low = (description or "").strip().lower()
    return next((t["key"] for t in report_tests.catalog()["tests"] if t["short"].lower() == low), None)


def start_worker() -> None:
    global _started
    if _started:
        return
    _started = True

    def loop():
        while True:
            try:
                run_pending()
            except Exception:                                          # noqa: BLE001 - keep the worker alive
                log.exception("extraction worker run failed")
            _wake.wait(timeout=10)
            _wake.clear()
    threading.Thread(target=loop, name="uc2-extraction", daemon=True).start()


# ------------------------------------------------------------------ decisions (called from the existing save paths)

def _row(conn, kind: str, source_id: str):
    return conn.execute("SELECT * FROM document_processing WHERE source_kind = ? AND source_id = ?", (kind, source_id)).fetchone()


def _close(conn, row, status: str, user_id: str) -> None:
    if row["status"] in DECIDED:
        return
    conn.execute("UPDATE document_processing SET status = ?, decided_by = ?, decided_at = ?, updated_at = ? WHERE id = ?",
                 (status, user_id, now_iso(), now_iso(), row["id"]))


def _mark(conn, field_id: int, status: str, user_id: str, final_value: str | None = None, final_unit: str | None = None):
    conn.execute("UPDATE extracted_fields SET status = ?, reviewed_by = ?, reviewed_at = ?, final_value_text = ?, final_unit = ? "
                 "WHERE id = ? AND status = 'needs_review'", (status, user_id, now_iso(), final_value, final_unit, field_id))


def _same(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(a - b) < 1e-6


def decided_external(conn, report_id: str, user_id: str, value: float, unit: str | None, test_type: str) -> None:
    """The team saved ONE value from an outside-lab report. The suggestion it matches is verified; if the team typed
    something else, the suggestion is marked corrected (with what was saved). Other suggestions were not used."""
    row = _row(conn, "external_report", report_id)
    if row is None:
        return
    from .. import report_tests
    target = report_tests.ALIASES.get(test_type)
    fields = conn.execute("SELECT id, analyte, value_num, normalized_value FROM extracted_fields WHERE processing_id = ? "
                          "AND status = 'needs_review'", (row["id"],)).fetchall()
    mine = [f for f in fields if f["analyte"] == target] if target else []
    hit = next((f for f in mine if _same(f["value_num"], value) or _same(f["normalized_value"], value)), None)
    for f in fields:
        if hit and f["id"] == hit["id"]:
            _mark(conn, f["id"], "verified", user_id)
        elif not hit and mine and f["id"] == mine[0]["id"]:
            _mark(conn, f["id"], "corrected", user_id, f"{value:g}", unit)
        else:
            _mark(conn, f["id"], "rejected", user_id)
    _close(conn, row, "corrected" if (mine and not hit) else "verified", user_id)


def decided_lab_results(conn, document_id: str, user_id: str, saved: list[tuple[str, str, str | None]]) -> None:
    """The team typed the values of a lab report. Each suggestion with the same test and value is verified; the same
    test with another value is corrected; suggestions the team did not keep are marked rejected."""
    row = _row(conn, "document", document_id)
    if row is None:
        return
    keyed = []
    for name, value_text, unit in saved:
        try:
            num = float(str(value_text).replace(",", "."))
        except ValueError:
            num = None
        keyed.append((validate.analyte_of(name), value_text, num, unit))
    used_rows, changed = set(), False
    for f in conn.execute("SELECT id, analyte, value_text, value_num FROM extracted_fields WHERE processing_id = ? "
                          "AND status = 'needs_review' AND field_kind = 'observation'", (row["id"],)).fetchall():
        match = next((i for i, k in enumerate(keyed) if i not in used_rows and k[0] == f["analyte"]), None)
        if match is None:
            _mark(conn, f["id"], "rejected", user_id)
            continue
        used_rows.add(match)
        _, value_text, num, unit = keyed[match]
        if _same(num, f["value_num"]) or (num is None and str(value_text).strip().lower() == (f["value_text"] or "").lower()):
            _mark(conn, f["id"], "verified", user_id)
        else:
            _mark(conn, f["id"], "corrected", user_id, str(value_text), unit)
            changed = True
    if len(used_rows) < len(keyed):
        changed = True                                                 # the team added values nobody suggested
    _close(conn, row, "corrected" if changed else "verified", user_id)


def decided_document(conn, document_id: str, user_id: str, status: str) -> None:
    """A prescription confirmed or rejected as a whole (its suggested medicines with it). A lab report is decided only
    when its values are entered, so confirming it alone changes nothing here."""
    row = _row(conn, "document", document_id)
    if row is None:
        return
    if status == "rejected":
        rejected(conn, "document", document_id, user_id)
    elif row["document_kind"] == "prescription":
        for f in conn.execute("SELECT id FROM extracted_fields WHERE processing_id = ? AND status = 'needs_review'", (row["id"],)):
            _mark(conn, f[0], "verified", user_id)
        _close(conn, row, "verified", user_id)


def request_reupload(conn, processing_id: str, patient_id: str, user: dict) -> dict:
    """The clinical team checked it: wrong report. The upload is rejected and the patient gets a message to upload the
    right one (in the app; SMS / WhatsApp are stubs). Returns what was sent."""
    from .. import notify, repo, report_tests
    row = conn.execute("SELECT * FROM document_processing WHERE id = ? AND patient_id = ?", (processing_id, patient_id)).fetchone()
    if row is None:
        raise LookupError("Not found.")
    t = report_tests.test(row["expected_test"]) if row["expected_test"] else None
    what = "prescription" if row["document_kind"] == "prescription" else f"{t['short'] if t else 'lab'} report"
    message = f"The {what} you sent does not match. Please upload the correct {what}."
    nid = None
    if row["source_kind"] == "external_report":
        reason = "Wrong report - please upload the correct one"
        repo.reject_external_report(conn, patient_id, row["source_id"], user["id"], reason)
        order = conn.execute("SELECT test_order_item_id FROM external_reports WHERE id = ?", (row["source_id"],)).fetchone()
        if order and order[0]:                       # an ordered test: its own flow reopens it and tells the patient
            from .. import orders
            orders.on_report_rejected(conn, patient_id, row["source_id"], user, reason)
            nid = -1
    else:
        repo.review_document(conn, patient_id, row["source_id"], "rejected", user["id"])
    rejected(conn, row["source_kind"], row["source_id"], user["id"])
    if nid is None:
        nid = notify.notify_patient(conn, patient_id, "reupload_request", message, processing_id=processing_id)
    repo.audit(conn, repo.clinician_actor(user), "REUPLOAD_REQUESTED", patient_id=patient_id, resource_type="document_processing",
               resource_id=processing_id, detail=message)
    return {"notification_id": nid, "message": message}


def rejected(conn, kind: str, source_id: str, user_id: str) -> None:
    row = _row(conn, kind, source_id)
    if row is None:
        return
    for f in conn.execute("SELECT id FROM extracted_fields WHERE processing_id = ? AND status = 'needs_review'", (row["id"],)):
        _mark(conn, f[0], "rejected", user_id)
    _close(conn, row, "rejected", user_id)


# ------------------------------------------------------------------ what the clinical team sees

def view(conn, kind: str, source_id: str, patient_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM document_processing WHERE source_kind = ? AND source_id = ? AND patient_id = ?",
                       (kind, source_id, patient_id)).fetchone()
    if row is None:
        return None
    fields = [dict(f) for f in conn.execute(
        "SELECT f.*, u.full_name AS reviewed_by_name FROM extracted_fields f LEFT JOIN clinical_users u ON u.id = f.reviewed_by "
        "WHERE f.processing_id = ? ORDER BY f.page_no, f.id", (row["id"],))]
    issues = [dict(i) for i in conn.execute("SELECT field_id, code, severity, message FROM validation_issues "
                                            "WHERE processing_id = ? ORDER BY id", (row["id"],))]
    for f in fields:
        f["label"] = (parse.analyte(f["analyte"]) or {}).get("label") if f["analyte"] else None
        f["issues"] = [i for i in issues if i["field_id"] == f["id"]]
    pages = [{"page_no": p["page_no"], "method": p["method"], "chars": len(p["text"].strip()),
              "quality": json.loads(p["quality"]) if p["quality"] else []}
             for p in conn.execute("SELECT * FROM document_pages WHERE processing_id = ? ORDER BY page_no", (row["id"],))]
    return {"id": row["id"], "status": row["status"], "status_text": STATUS_TEXT[row["status"]],
            "review_reason": row["review_reason"], "reason_text": REASON_TEXT.get(row["review_reason"]),
            "method": row["extraction_method"], "page_count": row["page_count"], "attempts": row["attempts"],
            "error": row["error"], "processed_at": row["processed_at"], "expected_test": row["expected_test"],
            "fields": fields, "document_issues": [i for i in issues if i["field_id"] is None and i["code"] != "test_mismatch"],
            "mismatch": next((i["message"] for i in issues if i["code"] == "test_mismatch"), None),
            "patient_override": row["patient_override"], "pages": pages,
            "ocr_available": sarvam.configured()}


def summary(conn, kind: str, source_ids: list[str]) -> dict[str, dict]:
    """A short status per upload, for the Pending reports list."""
    if not source_ids:
        return {}
    marks = ",".join("?" * len(source_ids))
    out = {}
    for r in conn.execute(
            f"SELECT p.source_id, p.id, p.status, p.review_reason, "
            f"(SELECT COUNT(*) FROM extracted_fields f WHERE f.processing_id = p.id) AS fields, "
            f"(SELECT COUNT(*) FROM validation_issues i WHERE i.processing_id = p.id AND i.severity = 'error') AS errors, "
            f"(SELECT message FROM validation_issues i WHERE i.processing_id = p.id AND i.code = 'test_mismatch') AS mismatch "
            f"FROM document_processing p WHERE p.source_kind = ? AND p.source_id IN ({marks})", (kind, *source_ids)):
        out[r["source_id"]] = {"id": r["id"], "status": r["status"], "status_text": STATUS_TEXT[r["status"]],
                               "review_reason": r["review_reason"], "reason_text": REASON_TEXT.get(r["review_reason"]),
                               "fields": r["fields"], "errors": r["errors"], "mismatch": r["mismatch"]}
    return out
