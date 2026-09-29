"""Every report a patient has uploaded, for linking a care-team test value to the report it was read from.

Reports live in two tables: external_reports ("Send a test report": test type + test date chosen by the patient)
and patient_documents ("Send a document" -> lab report, or a file the care team uploaded: a name, no type/date).
This module lists both as ONE lightweight list (one query, uploader names joined in, no file contents), lets the
care team correct a report's name / type / test date (stored beside the report, never overwriting the upload),
and edits a value and its link in one transaction with an audit entry holding the old and the new value.
"""

import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import date

from . import repo
from .db import now_iso

TYPE_LABELS = {"egfr": "eGFR", "hemoglobin": "Hemoglobin", "hba1c": "HbA1c", "sugar": "Sugar", "other": "Other"}
# the value rows are these four tests; a report "matches" a row when its type is the row's own
FIELD_OF_NAME = {"Blood glucose": "sugar", "HbA1c": "hba1c", "Hemoglobin": "hemoglobin", "eGFR": "egfr"}

# Units a value may be entered in, per test. The first is the one stored (graphs use it); others are converted.
UNITS = {
    "sugar": {"mg/dL": lambda v: v, "mmol/L": lambda v: round(v * 18.016, 1)},
    "hba1c": {"%": lambda v: v, "mmol/mol": lambda v: round(v / 10.929 + 2.15, 1)},
    "hemoglobin": {"g/dL": lambda v: v},
    "egfr": {"mL/min/1.73m²": lambda v: v},
}
RANGES = {"sugar": (20, 600), "hba1c": (3, 20), "hemoglobin": (3, 25), "egfr": (1, 200)}


def _words_type(text: str | None) -> str | None:
    """The test a report's own name clearly names ("Kidney report" -> eGFR); None when it names none."""
    name = (text or "").lower()
    words = re.findall(r"[a-z0-9]+", name)
    for key, rule in repo.MANUAL_FIELD_TESTS.items():
        if any(w in words if len(w) <= 3 else w in name for w in rule["words"]):
            return key
    return None


def _uploaded_type(origin: str, test_type: str | None, name: str | None) -> str | None:
    if origin == "test_report":            # the patient picked a test type when uploading
        return {"HbA1c": "hba1c", "Fasting Sugar": "sugar", "PP Sugar": "sugar"}.get(test_type) or _words_type(name) or "other"
    return _words_type(name)               # a plain document: typed only if its name says which test


_LIST_SQL = (
    "SELECT x.id, 'test_report' AS origin, x.test_type, x.test_name AS given_name, x.file_name, x.test_date, "
    "x.upload_date AS uploaded_at, x.content_type, x.status, 'patient' AS uploaded_by_role, NULL AS uploader_name, "
    "d.display_name AS fix_name, d.report_type AS fix_type, d.test_date AS fix_date "
    "FROM external_reports x LEFT JOIN report_details d ON d.report_id = x.id "
    "WHERE x.patient_id = :pid AND x.document_type = 'lab_report' {one_x} "
    "UNION ALL "
    "SELECT p.id, 'document', NULL, p.description, p.file_name, NULL, p.uploaded_at, p.content_type, p.review_status, "
    "CASE WHEN p.uploaded_by_user_id IS NULL THEN 'patient' ELSE 'care_team' END, u.full_name, "
    "d.display_name, d.report_type, d.test_date "
    "FROM patient_documents p LEFT JOIN clinical_users u ON u.id = p.uploaded_by_user_id "
    "LEFT JOIN report_details d ON d.report_id = p.id "
    "WHERE p.patient_id = :pid AND p.document_type = 'lab_report' {one_p}")

_STATUS = {"pending_review": "not_reviewed", "pending": "not_reviewed", "reviewed": "reviewed", "confirmed": "reviewed",
           "rejected": "rejected"}


def _shape(r) -> dict:
    given = r["given_name"] or (f"{r['test_type']} report" if r["test_type"] and r["test_type"] != "Other" else None)
    report_type = r["fix_type"] or _uploaded_type(r["origin"], r["test_type"], r["given_name"])
    status = _STATUS[r["status"]]
    pdf = r["content_type"] == "application/pdf"
    return {
        "id": r["id"], "origin": r["origin"],
        "display_name": r["fix_name"] or given or r["file_name"],
        "file_name": r["file_name"],
        "report_type": report_type,
        "report_type_label": f"{TYPE_LABELS[report_type]} report" if report_type else "Unlabelled",
        "test_date": r["fix_date"] or r["test_date"],            # never the upload date standing in for it
        "uploaded_at": r["uploaded_at"],
        "uploaded_by_role": r["uploaded_by_role"],
        "uploaded_by_name": "Patient" if r["uploaded_by_role"] == "patient" else r["uploader_name"],
        "status": status, "usable": status != "rejected",
        "file_kind": "pdf" if pdf else "image", "page_count": None if pdf else 1, "thumbnail_url": None,
    }


def list_reports(conn, patient_id: str) -> list[dict]:
    """All of this patient's uploaded lab reports - one query; newest test first, unknown test dates last."""
    rows = conn.execute(_LIST_SQL.format(one_x="", one_p=""), {"pid": patient_id}).fetchall()
    out = [_shape(r) for r in rows]
    out.sort(key=lambda x: (x["test_date"] is not None, x["test_date"] or "", x["uploaded_at"]), reverse=True)
    return out


def get_report(conn, patient_id: str, report_id: str) -> dict | None:
    """One report, only if it belongs to this patient."""
    row = conn.execute(_LIST_SQL.format(one_x="AND x.id = :rid", one_p="AND p.id = :rid"),
                       {"pid": patient_id, "rid": report_id}).fetchone()
    return _shape(row) if row else None


def etag(reports: list[dict]) -> str:
    return 'W/"' + hashlib.sha256(json.dumps(reports, sort_keys=True, default=str).encode()).hexdigest()[:32] + '"'


def _begin(conn) -> None:
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")


def _stamp() -> str:
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="microseconds")


# ------------------------------------------------------------------ correcting a report's label

def _describe(r: dict) -> str:
    return f"{r['display_name']} · {r['report_type_label']} · test date {r['test_date'] or 'not recorded'}"


def update_report(conn, patient_id: str, report_id: str, changes: dict, user: dict, today: date) -> dict:
    """Care-team correction of name / type / test date. Stored beside the upload; audited with old and new."""
    before = get_report(conn, patient_id, report_id)
    if before is None:
        raise repo.RepoError("Report not found for this patient.")
    name = changes.get("display_name", before["display_name"])
    name = (name or "").strip()
    if not 1 <= len(name) <= 120:
        raise repo.RepoError("Please give the report a name (up to 120 characters).")
    rtype = changes.get("report_type", before["report_type"])
    if rtype is not None and rtype not in TYPE_LABELS:
        raise repo.RepoError("Please choose a report type.")
    tdate = changes.get("test_date", before["test_date"])
    if tdate is not None:
        try:
            d = date.fromisoformat(str(tdate))
        except ValueError:
            raise repo.RepoError("Please enter a valid test date.") from None
        if d > today:
            raise repo.RepoError("The test date cannot be in the future.")
        tdate = d.isoformat()
    _begin(conn)
    try:
        conn.execute(
            "INSERT INTO report_details (report_id, patient_id, display_name, report_type, test_date, updated_by, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (report_id) DO UPDATE SET display_name = excluded.display_name, "
            "report_type = excluded.report_type, test_date = excluded.test_date, updated_by = excluded.updated_by, "
            "updated_at = excluded.updated_at",
            (report_id, patient_id, name, rtype, tdate, user["id"], now_iso()))
        after = get_report(conn, patient_id, report_id)
        repo.audit(conn, repo.clinician_actor(user), "REPORT_DETAILS_CHANGED", patient_id=patient_id,
                   resource_type="report", resource_id=report_id, old_value=_describe(before), new_value=_describe(after))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return after


# ------------------------------------------------------------------ editing a care-team value (and its link)

LINK_ACTIONS = {"VALUE_REPORT_LINKED": "Report linked", "VALUE_REPORT_CHANGED": "Report changed",
                "VALUE_REPORT_UNLINKED": "Report unlinked", "VALUE_EDITED": "Value edited",
                # written before this feature
                "VALUE_LINKED_TO_REPORT": "Report linked", "VALUE_LINK_CHANGED": "Report changed"}


def _value_row(conn, patient_id: str, event_id: int):
    return conn.execute("SELECT * FROM clinical_events WHERE id = ? AND patient_id = ? AND source = 'care_team_manual' "
                        "AND event_type = 'lab'", (event_id, patient_id)).fetchone()


def _value_text(value: float, unit: str, day: str) -> str:
    from .detection import fmt
    return f"{value:g} {unit} · {fmt(date.fromisoformat(day))}"


def edit_value(conn, patient_id: str, event_id: int, changes: dict, user: dict, today: date) -> None:
    """ONE transaction: the value's own UPDATE plus one audit entry per kind of change (edit / link / change / unlink).
    `changes` may hold value, unit, effective_date and report_id (present with None = unlink)."""
    _begin(conn)
    try:
        row = _value_row(conn, patient_id, event_id)
        if row is None:
            raise repo.RepoError("That value was not found, or it is not a value entered by the care team.")
        field = FIELD_OF_NAME.get(row["name"])
        actor = repo.clinician_actor(user)

        # --- value / unit / test date
        if any(k in changes for k in ("value", "unit", "effective_date")):
            units = UNITS[field]
            unit = changes.get("unit", row["unit"])
            if unit not in units:
                raise repo.RepoError(f"Please choose one of: {', '.join(units)}.")
            value = changes.get("value", row["value"])
            if value is None:
                raise repo.RepoError("Please enter a value.")
            stored_unit = next(iter(units))
            stored = units[unit](float(value))                     # kept in the unit the graphs use
            lo, hi = RANGES[field]
            if not lo <= stored <= hi:
                raise repo.RepoError(f"{row['name']} must be between {lo} and {hi} {stored_unit}.")
            day = str(changes.get("effective_date", row["effective_date"]))
            try:
                parsed = date.fromisoformat(day)
            except ValueError:
                raise repo.RepoError("Please enter a valid test date.") from None
            if parsed > today:
                raise repo.RepoError("The test date cannot be in the future.")
            old_text, new_text = _value_text(row["value"], row["unit"], row["effective_date"]), _value_text(stored, stored_unit, day)
            if old_text != new_text:
                conn.execute("UPDATE clinical_events SET value = ?, unit = ?, effective_date = ? WHERE id = ? AND patient_id = ?",
                             (stored, stored_unit, day, event_id, patient_id))
                repo.audit(conn, actor, "VALUE_EDITED", patient_id=patient_id, resource_type="clinical_event",
                           resource_id=event_id, old_value=old_text, new_value=new_text,
                           detail=None if unit == stored_unit else f"entered as {float(value):g} {unit}")

        # --- the linked report
        if "report_id" in changes:
            new_id = changes["report_id"] or None
            old_id = row["linked_document_id"] or row["linked_upload_id"]
            if new_id == old_id:
                raise repo.RepoError("The value is already linked that way.")
            new = get_report(conn, patient_id, new_id) if new_id else None
            if new_id and new is None:
                raise repo.RepoError("That report was not found for this patient.")
            if new and not new["usable"]:
                raise repo.RepoError("That report was marked as not usable, so a value cannot be linked to it.")
            old = get_report(conn, patient_id, old_id) if old_id else None
            conn.execute(
                "UPDATE clinical_events SET previous_document_id = linked_document_id, previous_upload_id = linked_upload_id, "
                "linked_document_id = ?, linked_upload_id = ?, link_changed_by = ?, link_changed_date = ? "
                "WHERE id = ? AND patient_id = ?",
                (new_id if new and new["origin"] == "test_report" else None,
                 new_id if new and new["origin"] == "document" else None, user["id"], _stamp(), event_id, patient_id))
            action = ("VALUE_REPORT_LINKED" if not old_id else "VALUE_REPORT_UNLINKED" if not new_id else "VALUE_REPORT_CHANGED")
            repo.audit(conn, actor, action, patient_id=patient_id, resource_type="clinical_event", resource_id=event_id,
                       old_value=old and f"{old['display_name']} ({old_id})", new_value=new and f"{new['display_name']} ({new_id})")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def list_values(conn, patient_id: str) -> list[dict]:
    """Care-team test values with their current report (either kind) and the latest action on each - 3 queries."""
    rows = [dict(r) for r in conn.execute(
        "SELECT e.id, e.name, e.value, e.unit, e.effective_date, e.created_at, e.linked_document_id, e.linked_upload_id, "
        "e.previous_document_id, e.previous_upload_id, e.link_changed_date, m.full_name AS entered_by, "
        "c.full_name AS link_changed_by_name FROM clinical_events e "
        "LEFT JOIN clinical_users m ON m.id = e.recorded_by_user_id LEFT JOIN clinical_users c ON c.id = e.link_changed_by "
        "WHERE e.patient_id = ? AND e.source = 'care_team_manual' AND e.event_type = 'lab' "
        "ORDER BY e.effective_date DESC, e.id DESC", (patient_id,))]
    reports = {r["id"]: r for r in list_reports(conn, patient_id)}
    latest = {}
    if rows:
        marks = ",".join("?" * len(rows))
        for a in conn.execute(
                f"SELECT a.resource_id, a.action, a.at, COALESCE(u.full_name, a.actor_label) AS who FROM audit_log a "
                f"LEFT JOIN clinical_users u ON u.id = a.actor_id AND a.actor_type = 'clinical_user' "
                f"WHERE a.id IN (SELECT MAX(id) FROM audit_log WHERE resource_type = 'clinical_event' "
                f"AND action IN ({','.join('?' * len(LINK_ACTIONS))}) AND resource_id IN ({marks}) GROUP BY resource_id)",
                (*LINK_ACTIONS, *(str(r["id"]) for r in rows))):
            latest[int(a["resource_id"])] = {"action": a["action"], "text": LINK_ACTIONS[a["action"]], "by": a["who"],
                                             "at": a["at"]}
    for r in rows:
        r["field"] = FIELD_OF_NAME.get(r["name"])
        r["units"] = list(UNITS[r["field"]]) if r["field"] else [r["unit"]]
        linked_id = r["linked_document_id"] or r["linked_upload_id"]
        r["linked"] = reports.get(linked_id) if linked_id else None
        r["latest_action"] = latest.get(r["id"])
    return rows


# ------------------------------------------------------------------ opening a file: short-lived signed link

_SECRET = secrets.token_bytes(32)          # per server process; links are short-lived anyway
LINK_SECONDS = 300


def _sign(report_id: str, patient_id: str, user_id: str, expires: int) -> str:
    return hmac.new(_SECRET, f"{report_id}|{patient_id}|{user_id}|{expires}".encode(), hashlib.sha256).hexdigest()


def signed_path(report_id: str, patient_id: str, user_id: str) -> dict:
    expires = int(time.time()) + LINK_SECONDS
    sig = _sign(report_id, patient_id, user_id, expires)
    return {"url": f"/api/report-files/{report_id}?patient={patient_id}&expires={expires}&sig={sig}", "expires": expires}


def check_signature(report_id: str, patient_id: str, user_id: str, expires: int, sig: str) -> bool:
    return expires >= time.time() and hmac.compare_digest(_sign(report_id, patient_id, user_id, expires), sig)


def file_of(conn, patient_id: str, report_id: str) -> dict | None:
    """storage_key + content type of this patient's report, from whichever table holds it."""
    row = conn.execute("SELECT storage_key, content_type FROM external_reports WHERE id = ? AND patient_id = ? "
                       "AND document_type = 'lab_report' UNION ALL SELECT storage_key, content_type FROM patient_documents "
                       "WHERE id = ? AND patient_id = ? AND document_type = 'lab_report'",
                       (report_id, patient_id, report_id, patient_id)).fetchone()
    return dict(row) if row else None
