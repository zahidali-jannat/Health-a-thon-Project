"""Re-upload requests. A patient cannot send the same report again while it is still being checked (no silent
duplicates); instead they pick that upload and ask the clinical team. A team member accepts - the old upload is set
aside and the patient may send the report again - or declines, with an optional note. Every step is audited and the
patient gets a message in the app.
"""

from . import notify, repo, report_tests
from .db import now_iso


class RequestError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def _external_label(test_type: str, test_name: str | None) -> tuple[str | None, str]:
    key = report_tests.ALIASES.get(test_type)
    t = report_tests.test(key) if key else None
    return key, t["short"] if t else (test_name or test_type)


def _document_label(document_type: str, description: str | None) -> tuple[str | None, str]:
    if document_type == "prescription":
        return "prescription", "Prescription"
    from .extraction.pipeline import expected_key
    key = expected_key(description)
    return key, description or "Lab report"


def waiting_uploads(conn, patient_id: str) -> list[dict]:
    """The patient's uploads still being checked - the ones a re-upload request can be made for."""
    out = []
    for r in conn.execute("SELECT id, test_type, test_name, upload_date FROM external_reports WHERE patient_id = ? "
                          "AND document_type = 'lab_report' AND status = 'pending_review' ORDER BY upload_date DESC", (patient_id,)):
        key, label = _external_label(r["test_type"], r["test_name"])
        out.append({"source_kind": "external_report", "source_id": r["id"], "test_key": key, "label": label,
                    "uploaded_at": r["upload_date"]})
    for r in conn.execute("SELECT id, document_type, description, uploaded_at FROM patient_documents WHERE patient_id = ? "
                          "AND source = 'patient_upload' AND review_status = 'pending' AND document_type IN ('lab_report', 'prescription') "
                          "ORDER BY uploaded_at DESC", (patient_id,)):
        key, label = _document_label(r["document_type"], r["description"])
        out.append({"source_kind": "document", "source_id": r["id"], "test_key": key, "label": label,
                    "uploaded_at": r["uploaded_at"]})
    open_ = {(r[0], r[1]) for r in conn.execute("SELECT source_kind, source_id FROM reupload_requests WHERE patient_id = ? "
                                                "AND status = 'requested'", (patient_id,))}
    for u in out:
        u["requested"] = (u["source_kind"], u["source_id"]) in open_
    return sorted(out, key=lambda u: u["uploaded_at"], reverse=True)


def same_waiting(conn, patient_id: str, kind: str, test_key: str | None, other_name: str | None) -> dict | None:
    """Is a report of this very kind already waiting to be checked? Then the patient asks instead of sending again."""
    for u in waiting_uploads(conn, patient_id):
        if kind == "prescription" and u["test_key"] == "prescription":
            return u
        if kind == "lab_report" and test_key and test_key != "other" and u["test_key"] == test_key:
            return u
        if kind == "lab_report" and test_key == "other" and other_name and u["label"].strip().lower() == other_name.strip().lower():
            return u
    return None


def create(conn, patient: dict, source_kind: str, source_id: str, reason: str | None) -> dict:
    upload = next((u for u in waiting_uploads(conn, patient["id"])
                   if u["source_kind"] == source_kind and u["source_id"] == source_id), None)
    if upload is None:
        raise RequestError("Choose one of your reports that is still being checked.", 422)
    if upload["requested"]:
        raise RequestError("A request for this report is already waiting.")
    reason = (reason or "").strip() or None
    rid = conn.execute("INSERT INTO reupload_requests (patient_id, source_kind, source_id, test_key, label, reason, requested_at) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)", (patient["id"], source_kind, source_id, upload["test_key"], upload["label"],
                                                        reason, now_iso())).lastrowid
    repo.audit(conn, repo.Actor("patient", patient["id"], f"{patient['full_name']} (patient)"), "REUPLOAD_REQUESTED_BY_PATIENT",
               patient_id=patient["id"], resource_type="reupload_request", resource_id=rid,
               detail=f"{upload['label']}" + (f": {reason}" if reason else ""))
    return get(conn, rid)


def get(conn, request_id: int) -> dict | None:
    row = conn.execute("SELECT q.*, u.full_name AS decided_by_name FROM reupload_requests q "
                       "LEFT JOIN clinical_users u ON u.id = q.decided_by WHERE q.id = ?", (request_id,)).fetchone()
    return dict(row) if row else None


def for_patient(conn, patient_id: str, limit: int = 20) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT q.id, q.source_kind, q.source_id, q.test_key, q.label, q.reason, q.status, q.requested_at, q.decided_at, q.note, "
        "u.full_name AS decided_by_name FROM reupload_requests q LEFT JOIN clinical_users u ON u.id = q.decided_by "
        "WHERE q.patient_id = ? ORDER BY q.requested_at DESC, q.id DESC LIMIT ?", (patient_id, limit))]


def open_for(conn, patient_ids: list[str]) -> list[dict]:
    if not patient_ids:
        return []
    marks = ",".join("?" * len(patient_ids))
    return [dict(r) for r in conn.execute(
        f"SELECT * FROM reupload_requests WHERE status = 'requested' AND patient_id IN ({marks}) ORDER BY requested_at",
        patient_ids)]


def _open(conn, request_id: int, patient_id: str) -> dict:
    q = get(conn, request_id)
    if q is None or q["patient_id"] != patient_id:
        raise RequestError("Request not found.", 404)
    if q["status"] != "requested":
        raise RequestError("This request was already decided.")
    return q


def accept(conn, request_id: int, patient_id: str, user: dict) -> dict:
    """The old upload is set aside (rejected, so it is never used) and the patient may send the report again."""
    from .extraction import pipeline
    q = _open(conn, request_id, patient_id)
    reason = "Replaced at the patient's request"
    told = False
    if q["source_kind"] == "external_report":
        if not repo.reject_external_report(conn, patient_id, q["source_id"], user["id"], reason):
            raise RequestError("This report was already checked, so it can't be replaced. Decline the request instead.")
        if conn.execute("SELECT test_order_item_id FROM external_reports WHERE id = ?", (q["source_id"],)).fetchone()[0]:
            from . import orders                       # an ordered test: its own flow reopens it and tells the patient
            orders.on_report_rejected(conn, patient_id, q["source_id"], user, reason)
            told = True
    else:
        doc = conn.execute("SELECT review_status FROM patient_documents WHERE id = ? AND patient_id = ?",
                           (q["source_id"], patient_id)).fetchone()
        if doc is None or doc[0] != "pending":
            raise RequestError("This report was already checked, so it can't be replaced. Decline the request instead.")
        repo.review_document(conn, patient_id, q["source_id"], "rejected", user["id"])
    pipeline.rejected(conn, q["source_kind"], q["source_id"], user["id"])
    conn.execute("UPDATE reupload_requests SET status = 'accepted', decided_by = ?, decided_at = ? WHERE id = ?",
                 (user["id"], now_iso(), request_id))
    what = "prescription" if q["test_key"] == "prescription" else f"{q['label']} report"
    if not told:
        _tell(conn, patient_id, "reupload_accepted", request_id, f"Request accepted. You can send your {what} again.")
    repo.audit(conn, repo.clinician_actor(user), "REUPLOAD_REQUEST_ACCEPTED", patient_id=patient_id,
               resource_type="reupload_request", resource_id=request_id, detail=q["label"])
    return get(conn, request_id)


def decline(conn, request_id: int, patient_id: str, user: dict, note: str | None) -> dict:
    q = _open(conn, request_id, patient_id)
    note = (note or "").strip() or None
    conn.execute("UPDATE reupload_requests SET status = 'declined', decided_by = ?, decided_at = ?, note = ? WHERE id = ?",
                 (user["id"], now_iso(), note, request_id))
    what = "prescription" if q["test_key"] == "prescription" else f"{q['label']} report"
    _tell(conn, patient_id, "reupload_declined", request_id,
          f"Your request to re-send the {what} was declined." + (f" {note}" if note else ""))
    repo.audit(conn, repo.clinician_actor(user), "REUPLOAD_REQUEST_DECLINED", patient_id=patient_id,
               resource_type="reupload_request", resource_id=request_id, detail=note)
    return get(conn, request_id)


def _tell(conn, patient_id: str, kind: str, request_id: int, message: str) -> None:
    conn.execute("INSERT INTO patient_notifications (patient_id, kind, reupload_request_id, message, email_to, email_status, "
                 "created_at) VALUES (?, ?, ?, ?, NULL, 'no_email_on_file', ?)", (patient_id, kind, request_id, message, now_iso()))
    for channel in notify.STUB_CHANNELS:
        notify.log.info("%s stub: re-upload notice for request %s queued (not sent - no provider connected)", channel, request_id)
