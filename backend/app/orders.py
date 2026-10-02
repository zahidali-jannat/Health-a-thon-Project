"""Test Orders: which tests a patient must do, by when, and how each result arrives.

Rules this module enforces (the database enforces the same ones again - migration 0013):
- Only clinician-verified values are results. A pending or rejected upload is shown as a report with its status,
  never as a value, and never feeds a trend.
- The status machine: ordered -> (sample_collected) -> result_received | submitted_by_patient -> verified -> closed;
  rejected (back to waiting for a result, reason required), cancelled / not_done (reason required). Every change goes
  through `transition`, which writes the audit entry (who, when, from, to, why). "Overdue" is computed, never stored.
- Due dates: default = next appointment minus `due_buffer_days`; never after the next appointment; a date inside the
  buffer is allowed with a warning. Appointment changes are re-checked on every read and flagged, never silently fixed.
- Numbers come only from the clinic lab system or from a clinician reading the report. Nothing is guessed.
Dates are stored as dates or UTC timestamps; the app shows them in Asia/Kolkata.
"""

import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta

from . import appointments as appt
from . import repo, storage
from .db import now_iso
from .detection import fmt
from .notify import notify_patient
from .settings import BACKEND_DIR

CONFIG_PATH = BACKEND_DIR / "config" / "test_orders.json"
CLINIC_LAB_ACTOR = repo.Actor("system", None, "Clinic lab system")

WAITING = ("ordered", "sample_collected", "rejected")             # still needs a result: overdue applies
AWAITING_VERIFICATION = ("result_received", "submitted_by_patient")
DONE = ("verified", "closed")
STOPPED = ("cancelled", "not_done")
LIVE = WAITING + AWAITING_VERIFICATION                            # "open" for the duplicate guard
LABELS = {"ordered": "ordered", "sample_collected": "sample collected", "result_received": "result received",
          "submitted_by_patient": "sent by the patient", "verified": "verified", "closed": "closed",
          "rejected": "rejected", "cancelled": "cancelled", "not_done": "not done (waived)"}
PATIENT_LABELS = {"ordered": "To do", "sample_collected": "Sample given", "result_received": "Result received - being checked",
                  "submitted_by_patient": "Sent for clinician review", "verified": "Done", "closed": "Done",
                  "rejected": "Needs to be done again", "cancelled": "Cancelled by your doctor", "not_done": "Not needed"}
# external_reports.test_type for an upload answering this catalog test (the review screen knows these types)
UPLOAD_TYPE = {"HBA1C": "HbA1c", "FBG": "Fasting Sugar", "PPBG": "PP Sugar"}


class OrderError(Exception):
    def __init__(self, code: str, message: str, status: int = 422, payload: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.payload = code, message, status, payload


def section(status: str) -> str:
    if status in ("ordered", "sample_collected"):
        return "open"
    if status in AWAITING_VERIFICATION:
        return "awaiting_verification"
    if status == "rejected":
        return "rejected"
    return "completed"


# ------------------------------------------------------------------ configuration and catalog

_config_cache: dict = {}


def config() -> dict:
    """backend/config/test_orders.json, re-read when the file changes."""
    mtime = CONFIG_PATH.stat().st_mtime
    if _config_cache.get("mtime") != mtime:
        _config_cache.update(mtime=mtime, data=json.loads(CONFIG_PATH.read_text()))
    return _config_cache["data"]


_CATALOG_FIELDS = ("display_name", "category", "fasting_required", "default_instructions", "default_repeat_interval_days",
                   "expected_unit", "ucum_unit", "result_name", "plausible_low", "plausible_high", "loinc_code", "is_other")


def sync_catalog(conn) -> None:
    """Upsert the catalog from the config file by code; codes no longer in the file become inactive. Caller commits."""
    tests = config()["catalog"]
    for t in tests:
        values = [t.get(f) for f in _CATALOG_FIELDS]
        values[2], values[-1] = int(bool(t.get("fasting_required"))), int(bool(t.get("is_other")))
        conn.execute(
            f"INSERT INTO test_catalog (code, {', '.join(_CATALOG_FIELDS)}, active, updated_at) "
            f"VALUES (?, {', '.join('?' * len(_CATALOG_FIELDS))}, ?, ?) ON CONFLICT (code) DO UPDATE SET "
            + ", ".join(f"{f} = excluded.{f}" for f in (*_CATALOG_FIELDS, "active", "updated_at")),
            (t["code"], *values, int(t.get("active", True)), now_iso()))
    conn.execute(f"UPDATE test_catalog SET active = 0 WHERE code NOT IN ({','.join('?' * len(tests))})",
                 [t["code"] for t in tests])


def catalog(conn, active_only: bool = True) -> list[dict]:
    if not conn.execute("SELECT 1 FROM test_catalog LIMIT 1").fetchone():
        sync_catalog(conn)
        conn.commit()
    rows = conn.execute("SELECT * FROM test_catalog " + ("WHERE active = 1 " if active_only else "")
                        + "ORDER BY is_other, id").fetchall()
    return [{**dict(r), "fasting_required": bool(r["fasting_required"]), "is_other": bool(r["is_other"])} for r in rows]


# ------------------------------------------------------------------ helpers

def _begin(conn) -> None:
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")


def _date(value, what: str) -> date:
    try:
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    except ValueError:
        raise OrderError("invalid", f"Please enter a valid {what}.") from None


def test_name(item: dict) -> str:
    return item.get("custom_name") or item["display_name"]


def next_appointment(conn, patient_id: str, today: date) -> dict | None:
    """The patient's next visit: a booked appointment, or a clinic visit on the record - whichever is first."""
    a = conn.execute(
        "SELECT a.id, s.date, s.start_time, d.name AS doctor_name FROM appointments a "
        "JOIN appointment_slots s ON s.id = a.slot_id JOIN doctors d ON d.id = a.doctor_id "
        "WHERE a.patient_id = ? AND a.status = 'confirmed' AND s.date >= ? ORDER BY s.date, s.start_time LIMIT 1",
        (patient_id, today.isoformat())).fetchone()
    v = conn.execute(
        "SELECT effective_date FROM clinical_events WHERE patient_id = ? AND event_type = 'visit' AND status = 'ordered' "
        "AND LOWER(COALESCE(name, '')) NOT LIKE '%emergency%' AND effective_date >= ? ORDER BY effective_date LIMIT 1",
        (patient_id, today.isoformat())).fetchone()
    if a and (not v or a["date"] <= v[0]):
        return {"date": a["date"], "time": a["start_time"], "appointment_id": a["id"], "doctor_name": a["doctor_name"],
                "source": "booked appointment"}
    if v:
        return {"date": v[0], "time": None, "appointment_id": None, "doctor_name": None, "source": "clinic visit"}
    return None


# ------------------------------------------------------------------ the status machine

def transition(conn, item: dict, to: str, actor: repo.Actor, why: str, *, reason: str | None = None,
               version: int | None = None, extra: dict | None = None) -> None:
    """The ONLY way an item's status changes: checks the move, bumps the version (optimistic lock) and audits it.
    `version` is the version the caller saw; a mismatch means someone else changed the item first."""
    frm = item["status"]
    if not conn.execute("SELECT 1 FROM test_order_transitions WHERE from_status = ? AND to_status = ?", (frm, to)).fetchone():
        raise OrderError("illegal_transition", f"A test that is {LABELS[frm]} cannot become {LABELS[to]}.", 409)
    if to in ("cancelled", "not_done", "rejected") and len((reason or "").strip()) < 3:
        raise OrderError("reason_required", "Please give a reason (at least 3 characters).")
    sets = {"status": to, "status_reason": reason.strip() if to in ("cancelled", "not_done", "rejected") else None,
            **(extra or {})}
    seen = item["version"] if version is None else version
    cur = conn.execute(
        f"UPDATE test_order_items SET {', '.join(f'{k} = ?' for k in sets)}, version = version + 1, updated_at = ? "
        "WHERE id = ? AND version = ?", (*sets.values(), now_iso(), item["id"], seen))
    if cur.rowcount != 1:
        raise OrderError("stale", "This test was changed by someone else a moment ago. Please reload and try again.", 409)
    repo.audit(conn, actor, "TEST_ORDER_STATUS_CHANGED", patient_id=item["patient_id"], resource_type="test_order_item",
               resource_id=item["id"], old_value=frm, new_value=to, detail=(f"{why} Reason: {reason.strip()}" if reason else why))
    item.update(sets, status=to, version=seen + 1)


def _load_item(conn, item_id: str, patient_id: str | None = None) -> dict | None:
    row = conn.execute(
        "SELECT i.*, c.code, c.display_name, c.result_name, c.expected_unit, c.plausible_low, c.plausible_high, c.is_other "
        "FROM test_order_items i JOIN test_catalog c ON c.id = i.test_catalog_id WHERE i.id = ?"
        + (" AND i.patient_id = ?" if patient_id else ""), (item_id, patient_id) if patient_id else (item_id,)).fetchone()
    return dict(row) if row else None


def item_for_clinician(conn, item_id: str, user_id: str) -> dict:
    """An item, only if the clinician is on that patient's care team (else 404, as for patients)."""
    item = _load_item(conn, item_id)
    if item is None or not repo.has_access(conn, user_id, item["patient_id"]):
        raise OrderError("not_found", "Test order not found.", 404)
    return item


# ------------------------------------------------------------------ ordering

def plan_order(conn, patient_id: str, items: list[dict], today: date) -> dict:
    """Checks a set of tests before it is saved: defaults the due dates, finds errors and warnings. Saves nothing."""
    buffer = int(config()["due_buffer_days"])
    nxt = next_appointment(conn, patient_id, today)
    appt_day = date.fromisoformat(nxt["date"]) if nxt else None
    tests = {c["id"]: c for c in catalog(conn)}
    open_now = conn.execute(
        "SELECT test_catalog_id, LOWER(COALESCE(custom_name, '')) AS custom, MIN(due_by) AS due_by FROM test_order_items "
        f"WHERE patient_id = ? AND status IN ({','.join('?' * len(LIVE))}) GROUP BY test_catalog_id, custom",
        (patient_id, *LIVE)).fetchall()
    open_map = {(r["test_catalog_id"], r["custom"]): r["due_by"] for r in open_now}
    out, seen = [], set()
    for raw in items:
        errors, warnings = [], []
        c = tests.get(raw.get("test_catalog_id"))
        if c is None:
            out.append({**raw, "name": "Unknown test", "errors": [{"code": "unknown_test", "message": "Choose a test from the list."}],
                        "warnings": []})
            continue
        custom = (raw.get("custom_name") or "").strip() if c["is_other"] else None
        if c["is_other"] and not 2 <= len(custom) <= 80:
            errors.append({"code": "name_required", "message": "Write the name of the test (2 to 80 characters)."})
        name = custom or c["display_name"]
        key = (c["id"], (custom or "").lower())
        if key in seen:
            errors.append({"code": "duplicate_in_order", "message": f"{name} is in this list twice."})
        seen.add(key)
        default_due = max(appt_day - timedelta(days=buffer), today) if appt_day else None
        due = _date(raw["due_by"], "due date") if raw.get("due_by") else default_due
        if due is None:
            errors.append({"code": "due_required", "message": "There is no next appointment yet, so please choose a due date."})
        else:
            if due < today:
                errors.append({"code": "due_in_past", "message": f"The due date {fmt(due)} is in the past."})
            if appt_day and due > appt_day:
                errors.append({"code": "after_appointment",
                               "message": f"Due {fmt(due)} is after the next appointment on {fmt(appt_day)}. "
                                          f"Choose {fmt(appt_day)} or earlier."})
            elif appt_day and due > appt_day - timedelta(days=buffer):
                warnings.append({"code": "buffer_not_met",
                                 "message": f"Due {fmt(due)} leaves less than {buffer} days to check the result before "
                                            f"the appointment on {fmt(appt_day)}."})
        if key in open_map:
            warnings.append({"code": "duplicate", "message": f"{name} is already ordered and still open "
                                                             f"(due {fmt(date.fromisoformat(open_map[key]))})."})
        instructions = raw.get("instructions")
        instructions = instructions.strip() if instructions and instructions.strip() else c["default_instructions"]
        out.append({"test_catalog_id": c["id"], "code": c["code"], "name": name, "custom_name": custom,
                    "due_by": due.isoformat() if due else None, "default_due_by": default_due.isoformat() if default_due else None,
                    "priority": raw.get("priority") or "routine", "fulfilment_route": raw.get("fulfilment_route") or "either",
                    "instructions": instructions, "fasting_required": c["fasting_required"],
                    "errors": errors, "warnings": warnings})
    problems = [] if out else [{"code": "no_tests", "message": "Choose at least one test."}]
    return {"next_appointment": nxt, "buffer_days": buffer, "items": out, "errors": problems,
            "ok": not problems and all(not i["errors"] for i in out), "has_warnings": any(i["warnings"] for i in out)}


def create_order(conn, patient: dict, user: dict, body: dict, today: date, idem: tuple | None = None) -> tuple[int, dict]:
    """One transaction: the set and all its items, or nothing. Warnings must be acknowledged (the confirmation
    summary). Returns (http status, response); a repeated idempotency key returns the first response."""
    _begin(conn)
    try:
        if idem and (stored := _idem_replay(conn, *idem)):
            conn.commit()
            return stored
        plan = plan_order(conn, patient["id"], body["items"], today)
        if not plan["ok"]:
            first = (plan["errors"] or [e for i in plan["items"] for e in i["errors"]])[0]
            raise OrderError(first["code"], first["message"], 422, {"plan": plan})
        if plan["has_warnings"] and not body.get("acknowledge_warnings"):
            raise OrderError("confirm_warnings", "Please check the warnings and confirm.", 409, {"plan": plan})
        appt.ensure_doctors(conn, [user["id"]])
        doctor = appt.get_doctor(conn, body["ordered_by_doctor_id"]) if body.get("ordered_by_doctor_id") \
            else appt.doctor_for_user(conn, user["id"])
        if doctor is None and not body.get("ordered_by_doctor_id"):   # clinic team: the patient's own doctor, if just one
            own = [d for d in appt.bookable_doctors(conn, patient["id"]) if repo.has_access(conn, d["clinical_user_id"], patient["id"])]
            doctor = own[0] if len(own) == 1 else None
        if doctor is None:
            raise OrderError("unknown_doctor", "Choose the doctor who ordered these tests.")
        set_id, now, nxt = repo.new_id(), now_iso(), plan["next_appointment"]
        conn.execute("INSERT INTO test_order_sets (id, patient_id, consultation_id, ordered_by_doctor_id, entered_by_user_id, "
                     "next_appointment_id, next_appointment_date, note, created_at) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)",
                     (set_id, patient["id"], doctor["id"], user["id"], nxt and nxt["appointment_id"], nxt and nxt["date"],
                      (body.get("note") or "").strip()[:500] or None, now))
        actor = repo.clinician_actor(user)
        for i in plan["items"]:
            item_id = repo.new_id()
            conn.execute("INSERT INTO test_order_items (id, order_set_id, patient_id, test_catalog_id, custom_name, instructions, "
                         "due_by, priority, fulfilment_route, status, version, created_at, updated_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ordered', 1, ?, ?)",
                         (item_id, set_id, patient["id"], i["test_catalog_id"], i["custom_name"], i["instructions"],
                          i["due_by"], i["priority"], i["fulfilment_route"], now, now))
            repo.audit(conn, actor, "TEST_ORDERED", patient_id=patient["id"], resource_type="test_order_item",
                       resource_id=item_id, new_value=f"{i['name']} · due {i['due_by']} · {i['priority']} · {i['fulfilment_route']}",
                       detail=f"Ordered by {doctor['name']}"
                              + (f"; warnings accepted: {', '.join(w['code'] for w in i['warnings'])}" if i["warnings"] else ""))
        response = {"order_set_id": set_id, "items": len(plan["items"])}
        if idem:
            _idem_store(conn, *idem, 201, response)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return 201, response


def edit_item(conn, item: dict, changes: dict, version: int, user: dict, today: date) -> list[dict]:
    """Change due date, instructions, priority or route of an item that still waits for a result. Returns warnings."""
    _begin(conn)
    try:
        item = _load_item(conn, item["id"])
        if item["status"] not in WAITING:
            raise OrderError("not_editable", f"A test that is {LABELS[item['status']]} can no longer be changed.", 409)
        warnings, new = [], {}
        if "due_by" in changes:
            due = _date(changes["due_by"], "due date")
            nxt = next_appointment(conn, item["patient_id"], today)
            buffer = int(config()["due_buffer_days"])
            if due < today:
                raise OrderError("due_in_past", f"The due date {fmt(due)} is in the past.")
            if nxt and due > date.fromisoformat(nxt["date"]):
                raise OrderError("after_appointment", f"Due {fmt(due)} is after the next appointment on "
                                                      f"{fmt(date.fromisoformat(nxt['date']))}.")
            if nxt and due > date.fromisoformat(nxt["date"]) - timedelta(days=buffer):
                warnings.append({"code": "buffer_not_met", "message": f"Less than {buffer} days are left to check the "
                                                                      "result before the appointment."})
            new["due_by"] = due.isoformat()
        for k in ("instructions", "priority", "fulfilment_route"):
            if k in changes:
                new[k] = (changes[k] or "").strip() or None if k == "instructions" else changes[k]
        new = {k: v for k, v in new.items() if v != item[k]}
        if not new:
            raise OrderError("nothing_changed", "Nothing was changed.")
        cur = conn.execute(f"UPDATE test_order_items SET {', '.join(f'{k} = ?' for k in new)}, version = version + 1, "
                           "updated_at = ? WHERE id = ? AND version = ?", (*new.values(), now_iso(), item["id"], version))
        if cur.rowcount != 1:
            raise OrderError("stale", "This test was changed by someone else a moment ago. Please reload and try again.", 409)
        repo.audit(conn, repo.clinician_actor(user), "TEST_ORDER_EDITED", patient_id=item["patient_id"],
                   resource_type="test_order_item", resource_id=item["id"],
                   old_value=json.dumps({k: item[k] for k in new}), new_value=json.dumps(new),
                   detail=(changes.get("reason") or "").strip()[:200] or "Edited by the care team")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return warnings


def change_status(conn, item: dict, to: str, version: int, user: dict, reason: str | None, why: str) -> None:
    """Cancel, waive (not_done) or close an item, as a clinician."""
    _begin(conn)
    try:
        current = _load_item(conn, item["id"])
        transition(conn, current, to, repo.clinician_actor(user), why, reason=reason, version=version)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ------------------------------------------------------------------ the patient's upload ("I did this test elsewhere")

def upload_for_item(conn, patient: dict, item_id: str, *, custom_name: str | None, test_date: date, lab_name: str,
                    file_name: str, content_type: str, data: bytes, today: date, idem: tuple | None = None) -> tuple[int, dict]:
    """Stores the report (pending clinician review) and moves the item to submitted_by_patient, in one transaction.
    item_id 'other' = a test that was not ordered: it goes to the care team's unlinked queue."""
    lab = (lab_name or "").strip()
    if not 2 <= len(lab) <= 120:
        raise OrderError("lab_required", "Please write the name of the lab (2 to 120 characters).")
    if test_date > today:
        raise OrderError("future_date", "The test date cannot be in the future.")
    if test_date < today - timedelta(days=365):
        raise OrderError("too_old", "Please send a report from the last 12 months.")
    _begin(conn)
    report_id = None
    try:
        if idem and (stored := _idem_replay(conn, *idem)):
            conn.commit()
            return stored
        actor = repo.Actor("patient", patient["id"], f"{patient['full_name']} (patient)")
        if item_id == "other":
            name = (custom_name or "").strip()
            if not 2 <= len(name) <= 80:
                raise OrderError("name_required", "Please write which test this is.")
            report_id = repo.create_external_report(conn, patient["id"], "Other", test_date, file_name, content_type, data,
                                                    test_name=name, lab_name=lab)
            repo.audit(conn, actor, "EXTERNAL_REPORT_UPLOADED", patient_id=patient["id"], resource_type="external_report",
                       resource_id=report_id, detail="Test not on the order list - waiting for the care team to link it")
            response = {"report_id": report_id, "test_order_item_id": None, "status": "sent_for_review",
                        "message": "Sent for clinician review."}
        else:
            item = _load_item(conn, item_id, patient["id"])
            if item is None:
                raise OrderError("not_found", "Test not found.", 404)
            if item["fulfilment_route"] == "clinic_lab":
                raise OrderError("clinic_only", "Your doctor asked for this test to be done at our clinic lab.", 409)
            if item["status"] in AWAITING_VERIFICATION:
                raise OrderError("already_sent", "A report for this test is already waiting for your care team.", 409)
            if item["status"] not in WAITING:
                raise OrderError("not_open", "This test is no longer waiting for a report.", 409)
            test_type = UPLOAD_TYPE.get(item["code"], "Other")
            report_id = repo.create_external_report(
                conn, patient["id"], test_type, test_date, file_name, content_type, data,
                test_name=None if test_type != "Other" else (item["custom_name"] or item["result_name"] or item["display_name"]),
                test_order_item_id=item["id"], lab_name=lab)
            transition(conn, item, "submitted_by_patient", actor, "Patient uploaded a report from an outside lab.",
                       extra={"linked_report_id": report_id})
            response = {"report_id": report_id, "test_order_item_id": item["id"], "status": "submitted_by_patient",
                        "message": "Sent for clinician review."}
        if idem:
            _idem_store(conn, *idem, 201, response)
        conn.commit()
    except Exception:
        conn.rollback()
        if report_id:                                  # the file was stored before the rollback - remove it
            storage.delete(storage.storage_key(patient["id"], report_id, content_type))
        raise
    return 201, response


# ------------------------------------------------------------------ hooks into the existing report review

def on_report_verified(conn, patient_id: str, report_id: str, user: dict) -> None:
    """Called inside the review endpoint's transaction, after the value was saved with its report link."""
    row = conn.execute("SELECT id FROM test_order_items WHERE linked_report_id = ? AND patient_id = ? "
                       "AND status = 'submitted_by_patient'", (report_id, patient_id)).fetchone()
    if row:
        transition(conn, _load_item(conn, row["id"]), "verified", repo.clinician_actor(user),
                   "The clinician read the uploaded report and saved its value.")


def on_report_rejected(conn, patient_id: str, report_id: str, user: dict, reason: str | None) -> None:
    """Rejecting an upload that answers an order: the item waits for a result again, and the patient is told why."""
    row = conn.execute("SELECT id FROM test_order_items WHERE linked_report_id = ? AND patient_id = ? "
                       "AND status = 'submitted_by_patient'", (report_id, patient_id)).fetchone()
    if not row:
        return
    item = _load_item(conn, row["id"])
    transition(conn, item, "rejected", repo.clinician_actor(user), "The clinician could not use the uploaded report.",
               reason=reason)
    notify_patient(conn, patient_id, "test_rejected",
                   f"Your {test_name(item)} report could not be used: {reason.strip()}. Please upload it again from My tests.",
                   test_order_item_id=item["id"])


# ------------------------------------------------------------------ clinic lab system results

def _record_lab_result(conn, patient_id: str, item_or_catalog: dict, value: float, test_date: date, lab_name: str) -> str:
    report = repo.create_lab_report(conn, patient_id, lab_name, test_date, "hospital_internal")
    return repo.add_lab_result(conn, report, patient_id, item_or_catalog["result_name"] or item_or_catalog["display_name"],
                               value, item_or_catalog["expected_unit"], test_date)


def ingest_lab_result(conn, payload: dict, today: date) -> dict:
    """A result from the clinic lab system. Idempotent per (lab_order_ref, test_code): a repeat changes nothing.
    Matched + plausible -> recorded and the item verified automatically. Matched but implausible (or a different
    unit) -> clinician review. No open order -> the unlinked queue."""
    ref, code = payload["lab_order_ref"].strip(), payload["test_code"].strip().upper()
    _begin(conn)
    try:
        dup = conn.execute("SELECT * FROM lab_result_ingestions WHERE lab_order_ref = ? AND test_code = ?", (ref, code)).fetchone()
        if dup:
            conn.commit()
            return {"outcome": "duplicate", "ingestion_id": dup["id"], "status": dup["status"]}
        patient = repo.get_patient(conn, payload["patient_id"]) if payload.get("patient_id") else \
            conn.execute("SELECT * FROM patients WHERE patient_code = ?", ((payload.get("patient_code") or "").upper(),)).fetchone()
        if patient is None:
            raise OrderError("unknown_patient", "No patient with that id or code.")
        patient = dict(patient)
        test = conn.execute("SELECT * FROM test_catalog WHERE code = ? AND is_other = 0", (code,)).fetchone()
        if test is None:
            raise OrderError("unknown_test", f"Unknown test code {code}.")
        test = dict(test)
        test_date = _date(payload["test_date"], "test date")
        if test_date > today:
            raise OrderError("future_date", "The test date cannot be in the future.")
        value, unit = float(payload["value"]), (payload.get("unit") or None)
        lab = (payload.get("lab_name") or config()["clinic_lab_name"]).strip()[:120]
        row = conn.execute(f"SELECT id FROM test_order_items WHERE patient_id = ? AND test_catalog_id = ? "
                           f"AND status IN ({','.join('?' * len(WAITING))}) ORDER BY due_by, created_at LIMIT 1",
                           (patient["id"], test["id"], *WAITING)).fetchone()
        item = _load_item(conn, row["id"]) if row else None
        lo, hi = test["plausible_low"], test["plausible_high"]
        plausible = (lo is None or lo <= value) and (hi is None or value <= hi)
        unit_ok = unit is None or unit == test["expected_unit"]
        ing_id = repo.new_id()
        base = (ing_id, ref, code, patient["id"], value, unit or test["expected_unit"], test_date.isoformat(), lab, now_iso())
        insert = ("INSERT INTO lab_result_ingestions (id, lab_order_ref, test_code, patient_id, value, unit, test_date, lab_name, "
                  "received_at, status, test_order_item_id, lab_result_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
        if item and plausible and unit_ok:
            result_id = _record_lab_result(conn, patient["id"], test, value, test_date, lab)
            conn.execute(insert, (*base, "recorded", item["id"], result_id))
            transition(conn, item, "result_received", CLINIC_LAB_ACTOR, "Result received from the clinic lab system.")
            transition(conn, item, "verified", CLINIC_LAB_ACTOR,
                       "Clinic lab result within the plausible range - verified automatically.", extra={"lab_result_id": result_id})
            outcome = "verified"
        elif item:
            conn.execute(insert, (*base, "needs_review", item["id"], None))
            why = "outside the plausible range" if not plausible else f"reported in {unit}, expected {test['expected_unit']}"
            transition(conn, item, "result_received", CLINIC_LAB_ACTOR, f"Clinic lab result {why} - sent to clinician review.")
            outcome = "needs_review"
        else:
            conn.execute(insert, (*base, "unlinked", None, None))
            outcome = "unlinked"
        repo.audit(conn, CLINIC_LAB_ACTOR, "LAB_RESULT_INGESTED", patient_id=patient["id"], resource_type="lab_result_ingestion",
                   resource_id=ing_id, detail=f"{code} · {outcome}")
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        if "lab_result_ingestions" in str(e) or "UNIQUE" in str(e):     # the same result arrived twice at once
            dup = conn.execute("SELECT id, status FROM lab_result_ingestions WHERE lab_order_ref = ? AND test_code = ?",
                               (ref, code)).fetchone()
            if dup:
                return {"outcome": "duplicate", "ingestion_id": dup["id"], "status": dup["status"]}
        raise
    except Exception:
        conn.rollback()
        raise
    return {"outcome": outcome, "ingestion_id": ing_id, "test_order_item_id": item and item["id"]}


def _load_ingestion(conn, patient_id: str, ingestion_id: str) -> dict:
    row = conn.execute("SELECT g.*, c.id AS catalog_id, c.display_name, c.result_name, c.expected_unit FROM lab_result_ingestions g "
                       "JOIN test_catalog c ON c.code = g.test_code WHERE g.id = ? AND g.patient_id = ?",
                       (ingestion_id, patient_id)).fetchone()
    if row is None:
        raise OrderError("not_found", "Lab result not found.", 404)
    return dict(row)


def accept_ingestion(conn, patient_id: str, ingestion_id: str, user: dict, item_id: str | None, today: date) -> None:
    """The clinician checked a clinic-lab result that needed review (or had no order) and accepts it."""
    _begin(conn)
    try:
        g = _load_ingestion(conn, patient_id, ingestion_id)
        if g["status"] not in ("needs_review", "unlinked"):
            raise OrderError("already_decided", "This lab result was already decided.", 409)
        item = None
        target = g["test_order_item_id"] or item_id
        if target:
            item = _load_item(conn, target, patient_id)
            if item is None or item["test_catalog_id"] != g["catalog_id"]:
                raise OrderError("wrong_item", "Choose an open order for the same test.")
            if g["status"] == "unlinked" and item["status"] not in WAITING:
                raise OrderError("wrong_item", "That test order is not waiting for a result.", 409)
        result_id = _record_lab_result(conn, patient_id, g, g["value"], date.fromisoformat(g["test_date"]), g["lab_name"])
        conn.execute("UPDATE lab_result_ingestions SET status = 'recorded', lab_result_id = ?, test_order_item_id = ?, "
                     "reviewed_by = ?, reviewed_at = ? WHERE id = ?", (result_id, item and item["id"], user["id"], now_iso(), g["id"]))
        actor = repo.clinician_actor(user)
        if item:
            if item["status"] in WAITING:
                transition(conn, item, "result_received", actor, "Clinic lab result linked to this order by the clinician.")
            transition(conn, item, "verified", actor, "Clinic lab result checked and accepted by the clinician.",
                       extra={"lab_result_id": result_id})
        repo.audit(conn, actor, "LAB_RESULT_ACCEPTED", patient_id=patient_id, resource_type="lab_result_ingestion",
                   resource_id=g["id"], detail="linked to an order" if item else "accepted without an order")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def reject_ingestion(conn, patient_id: str, ingestion_id: str, user: dict, reason: str) -> None:
    _begin(conn)
    try:
        g = _load_ingestion(conn, patient_id, ingestion_id)
        if g["status"] not in ("needs_review", "unlinked"):
            raise OrderError("already_decided", "This lab result was already decided.", 409)
        if len((reason or "").strip()) < 3:
            raise OrderError("reason_required", "Please give a reason (at least 3 characters).")
        conn.execute("UPDATE lab_result_ingestions SET status = 'rejected', review_note = ?, reviewed_by = ?, reviewed_at = ? "
                     "WHERE id = ?", (reason.strip(), user["id"], now_iso(), g["id"]))
        actor = repo.clinician_actor(user)
        if g["test_order_item_id"]:
            item = _load_item(conn, g["test_order_item_id"])
            if item["status"] == "result_received":
                transition(conn, item, "rejected", actor, "The clinician could not use the clinic lab result.", reason=reason)
                notify_patient(conn, patient_id, "test_rejected",
                               f"Your {test_name(item)} result could not be used: {reason.strip()}. Please repeat the test.",
                               test_order_item_id=item["id"])
        repo.audit(conn, actor, "LAB_RESULT_REJECTED", patient_id=patient_id, resource_type="lab_result_ingestion",
                   resource_id=g["id"], detail=reason.strip())
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ------------------------------------------------------------------ reading

_ITEM_SELECT = (
    "SELECT i.*, s.created_at AS set_created_at, s.note AS set_note, s.next_appointment_id, s.next_appointment_date, "
    "s.ordered_by_doctor_id, d.name AS doctor_name, u.full_name AS entered_by_name, "
    "c.code, c.display_name, c.fasting_required, c.default_repeat_interval_days, c.expected_unit, c.is_other, c.loinc_code, "
    "c.ucum_unit, c.result_name, c.category, "
    "x.status AS report_status, x.upload_date AS report_uploaded_at, x.content_type AS report_content_type, "
    "x.test_date AS report_test_date, x.lab_name AS report_lab_name, x.reject_reason AS report_reject_reason, "
    "ev.value AS up_value, ev.unit AS up_unit, ev.effective_date AS up_test_date, ev.reviewed_date AS up_verified_at, "
    "lr.numeric_value AS lab_value, lr.unit AS lab_unit, lr.test_date AS lab_test_date, lr.created_at AS lab_recorded_at, "
    "lrep.laboratory_name AS lab_lab_name, g.received_at AS g_received_at, g.reviewed_at AS g_reviewed_at, "
    "rv.id AS review_id, rv.value AS review_value, rv.unit AS review_unit, rv.test_date AS review_test_date, "
    "rv.lab_name AS review_lab_name, ap.status AS appt_status, aps.date AS appt_date "
    "FROM test_order_items i JOIN test_order_sets s ON s.id = i.order_set_id "
    "JOIN test_catalog c ON c.id = i.test_catalog_id "
    "LEFT JOIN doctors d ON d.id = s.ordered_by_doctor_id LEFT JOIN clinical_users u ON u.id = s.entered_by_user_id "
    "LEFT JOIN external_reports x ON x.id = i.linked_report_id "
    "LEFT JOIN clinical_events ev ON ev.linked_document_id = i.linked_report_id AND ev.source = 'patient_upload_reviewed' "
    "LEFT JOIN lab_test_results lr ON lr.id = i.lab_result_id LEFT JOIN lab_reports lrep ON lrep.id = lr.report_id "
    "LEFT JOIN lab_result_ingestions g ON g.lab_result_id = i.lab_result_id "
    "LEFT JOIN lab_result_ingestions rv ON rv.test_order_item_id = i.id AND rv.status = 'needs_review' "
    "LEFT JOIN appointments ap ON ap.id = s.next_appointment_id LEFT JOIN appointment_slots aps ON aps.id = ap.slot_id ")


def _view(r, today: date, nxt: dict | None) -> dict:
    """One item for the screens. Only VERIFIED values appear as `result`; an upload waiting for review is only `report`."""
    status, due = r["status"], date.fromisoformat(r["due_by"])
    waiting = status in WAITING
    flags = []
    if waiting and nxt and due > date.fromisoformat(nxt["date"]):
        flags.append({"code": "after_next_appointment",
                      "message": f"Due after the next appointment ({fmt(date.fromisoformat(nxt['date']))})."})
    if waiting and r["next_appointment_id"]:
        if r["appt_status"] != "confirmed":
            flags.append({"code": "appointment_changed", "message": "The appointment these dates were set against "
                          + ("was cancelled." if r["appt_status"] == "cancelled" else "needs a new time.")})
        elif r["appt_date"] != r["next_appointment_date"]:
            flags.append({"code": "appointment_changed", "message": "The appointment these dates were set against moved to "
                          f"{fmt(date.fromisoformat(r['appt_date']))}."})
    result = None
    if status in DONE and r["lab_value"] is not None:
        result = {"value": r["lab_value"], "unit": r["lab_unit"], "test_date": r["lab_test_date"],
                  "uploaded_at": r["g_received_at"] or r["lab_recorded_at"], "verified_at": r["g_reviewed_at"] or r["lab_recorded_at"],
                  "lab_name": r["lab_lab_name"], "source": "clinic_lab_system"}
    elif status in DONE and r["up_value"] is not None:
        result = {"value": r["up_value"], "unit": r["up_unit"], "test_date": r["up_test_date"],
                  "uploaded_at": r["report_uploaded_at"], "verified_at": r["up_verified_at"],
                  "lab_name": r["report_lab_name"] or "Outside lab", "source": "patient_upload"}
    repeat = r["default_repeat_interval_days"]
    return {
        "id": r["id"], "order_set_id": r["order_set_id"], "patient_id": r["patient_id"], "version": r["version"],
        "test_catalog_id": r["test_catalog_id"], "code": r["code"], "name": r["custom_name"] or r["display_name"],
        "category": r["category"], "fasting_required": bool(r["fasting_required"]), "instructions": r["instructions"],
        "due_by": r["due_by"], "priority": r["priority"], "fulfilment_route": r["fulfilment_route"],
        "status": status, "status_label": LABELS[status], "patient_status_label": PATIENT_LABELS[status],
        "section": section(status), "status_reason": r["status_reason"],
        "overdue": waiting and due < today, "days_to_due": (due - today).days, "flags": flags,
        "ordered_by": r["doctor_name"], "entered_by": r["entered_by_name"], "ordered_at": r["set_created_at"],
        "order_note": r["set_note"], "next_appointment_date": r["next_appointment_date"],
        "report": ({"id": r["linked_report_id"], "status": r["report_status"], "uploaded_at": r["report_uploaded_at"],
                    "content_type": r["report_content_type"], "test_date": r["report_test_date"], "lab_name": r["report_lab_name"],
                    "reject_reason": r["report_reject_reason"]} if r["linked_report_id"] else None),
        "result": result,
        "review": ({"ingestion_id": r["review_id"], "value": r["review_value"], "unit": r["review_unit"],
                    "test_date": r["review_test_date"], "lab_name": r["review_lab_name"], "expected_unit": r["expected_unit"]}
                   if r["review_id"] else None),
        "suggested_next_due": ((date.fromisoformat(result["test_date"]) + timedelta(days=repeat)).isoformat()
                               if result and repeat else None),
        "can_upload": waiting and r["fulfilment_route"] != "clinic_lab",
        "updated_at": r["updated_at"],
    }


def _summary(items: list[dict]) -> dict:
    count = lambda f: sum(1 for i in items if f(i))   # noqa: E731
    return {"open": count(lambda i: i["section"] == "open"), "awaiting_verification": count(lambda i: i["section"] == "awaiting_verification"),
            "rejected": count(lambda i: i["section"] == "rejected"), "completed": count(lambda i: i["section"] == "completed"),
            "overdue": count(lambda i: i["overdue"]), "flagged": count(lambda i: bool(i["flags"]))}


def patient_orders(conn, patient_id: str, today: date, page: int = 1, page_size: int = 50) -> dict:
    """The clinician's view: order sets (newest first, paginated) with their items, plus counts over ALL items,
    the next appointment and clinic-lab results with no order. A fixed number of queries - no N+1."""
    nxt = next_appointment(conn, patient_id, today)
    all_items = [_view(r, today, nxt) for r in conn.execute(_ITEM_SELECT + "WHERE i.patient_id = ? ORDER BY s.created_at DESC, i.due_by",
                                                             (patient_id,))]
    set_ids = [r[0] for r in conn.execute("SELECT id FROM test_order_sets WHERE patient_id = ? ORDER BY created_at DESC "
                                          "LIMIT ? OFFSET ?", (patient_id, page_size, (page - 1) * page_size))]
    total_sets = conn.execute("SELECT COUNT(*) FROM test_order_sets WHERE patient_id = ?", (patient_id,)).fetchone()[0]
    sets = {}
    for i in all_items:
        if i["order_set_id"] in set_ids:
            sets.setdefault(i["order_set_id"], {"id": i["order_set_id"], "ordered_at": i["ordered_at"], "ordered_by": i["ordered_by"],
                                                "entered_by": i["entered_by"], "note": i["order_note"],
                                                "next_appointment_date": i["next_appointment_date"], "items": []})["items"].append(i)
    unlinked = [dict(r) for r in conn.execute(
        "SELECT g.id, g.test_code, c.display_name, g.value, g.unit, g.test_date, g.lab_name, g.received_at, c.plausible_low, "
        "c.plausible_high FROM lab_result_ingestions g JOIN test_catalog c ON c.code = g.test_code "
        "WHERE g.patient_id = ? AND g.status = 'unlinked' ORDER BY g.received_at", (patient_id,))]
    return {"next_appointment": nxt, "buffer_days": int(config()["due_buffer_days"]), "summary": _summary(all_items),
            "sets": [sets[s] for s in set_ids if s in sets], "page": page, "page_size": page_size, "total_sets": total_sets,
            "unlinked_lab_results": unlinked}


def my_tests(conn, patient_id: str, today: date) -> dict:
    """The patient's to-do list: everything still to do first (soonest due), then what was done in the last 6 months."""
    nxt = next_appointment(conn, patient_id, today)
    items = [_view(r, today, nxt) for r in conn.execute(_ITEM_SELECT + "WHERE i.patient_id = ? ORDER BY i.due_by", (patient_id,))]
    since = (today - timedelta(days=183)).isoformat()
    todo = [i for i in items if i["section"] != "completed"]
    done = [i for i in items if i["section"] == "completed" and i["status"] in DONE and (i["updated_at"] or "")[:10] >= since]
    keep = ("id", "version", "name", "category", "fasting_required", "instructions", "due_by", "priority", "fulfilment_route",
            "status", "patient_status_label", "section", "status_reason", "overdue", "days_to_due", "can_upload", "result")
    slim = lambda i: {**{k: i[k] for k in keep}, "report_sent_at": i["report"] and i["report"]["uploaded_at"]}  # noqa: E731
    return {"next_appointment": nxt and {"date": nxt["date"], "time": nxt["time"]},
            "clinic_lab_name": config()["clinic_lab_name"], "todo": [slim(i) for i in todo], "done": [slim(i) for i in done]}


def overdue_tests(conn, user_id: str, today: date, doctor_id: str | None = None, within_days: int | None = None,
                  page: int = 1, page_size: int = 50) -> dict:
    """The clinic dashboard: tests still waiting for a result past their due date, across the clinician's own
    patients, with each patient's next appointment. Filter by ordering doctor and by days to the appointment."""
    ids = repo.patient_ids_for_clinician(conn, user_id)
    if not ids:
        return {"items": [], "page": page, "page_size": page_size, "total": 0}
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        "SELECT i.id, i.patient_id, i.due_by, i.status, i.priority, i.custom_name, c.display_name, s.ordered_by_doctor_id, "
        "d.name AS doctor_name, p.full_name, p.patient_code FROM test_order_items i "
        "JOIN test_order_sets s ON s.id = i.order_set_id JOIN test_catalog c ON c.id = i.test_catalog_id "
        "JOIN patients p ON p.id = i.patient_id LEFT JOIN doctors d ON d.id = s.ordered_by_doctor_id "
        f"WHERE i.patient_id IN ({marks}) AND i.status IN ({','.join('?' * len(WAITING))}) AND i.due_by < ? "
        + ("AND s.ordered_by_doctor_id = ? " if doctor_id else "") + "ORDER BY i.due_by",
        (*ids, *WAITING, today.isoformat(), *([doctor_id] if doctor_id else []))).fetchall()
    appts = {r[0]: r[1] for r in conn.execute(
        f"SELECT a.patient_id, MIN(s.date) FROM appointments a JOIN appointment_slots s ON s.id = a.slot_id "
        f"WHERE a.status = 'confirmed' AND s.date >= ? AND a.patient_id IN ({marks}) GROUP BY a.patient_id", (today.isoformat(), *ids))}
    for pid, d in conn.execute(
            f"SELECT patient_id, MIN(effective_date) FROM clinical_events WHERE event_type = 'visit' AND status = 'ordered' "
            f"AND LOWER(COALESCE(name, '')) NOT LIKE '%emergency%' AND effective_date >= ? AND patient_id IN ({marks}) "
            f"GROUP BY patient_id", (today.isoformat(), *ids)):
        appts[pid] = min(d, appts.get(pid, d))
    out = []
    for r in rows:
        nxt = appts.get(r["patient_id"])
        days = (date.fromisoformat(nxt) - today).days if nxt else None
        if within_days is not None and (days is None or days > within_days):
            continue
        out.append({"id": r["id"], "patient_id": r["patient_id"], "patient_name": r["full_name"], "patient_code": r["patient_code"],
                    "test": r["custom_name"] or r["display_name"], "due_by": r["due_by"], "days_overdue": (today - date.fromisoformat(r["due_by"])).days,
                    "status": r["status"], "priority": r["priority"], "ordered_by": r["doctor_name"],
                    "next_appointment_date": nxt, "days_to_appointment": days})
    start = (page - 1) * page_size
    return {"items": out[start:start + page_size], "page": page, "page_size": page_size, "total": len(out)}


# ------------------------------------------------------------------ reminders

REMINDER_KIND = {7: "due_in_7", 3: "due_in_3", 1: "due_in_1"}


def run_reminders(conn, today: date) -> int:
    """7, 3 and 1 day(s) before due_by, and once when it becomes overdue - only for items still waiting for a result.
    Each (item, kind) and each (item, day) is sent at most once: the INSERT is the dedupe. Returns how many were sent."""
    days_before = set(config().get("reminder_days_before", [7, 3, 1])) & set(REMINDER_KIND)
    sent = 0
    _begin(conn)
    try:
        for r in conn.execute(
                "SELECT i.id, i.patient_id, i.due_by, i.custom_name, c.display_name FROM test_order_items i "
                f"JOIN test_catalog c ON c.id = i.test_catalog_id WHERE i.status IN ({','.join('?' * len(WAITING))})",
                WAITING).fetchall():
            due = date.fromisoformat(r["due_by"])
            left = (due - today).days
            kind = REMINDER_KIND.get(left) if left in days_before else ("overdue" if left < 0 else None)
            if kind is None:
                continue
            claimed = conn.execute("INSERT OR IGNORE INTO test_reminders_sent (test_order_item_id, kind, sent_on) VALUES (?, ?, ?)",
                                   (r["id"], kind, today.isoformat())).rowcount
            if not claimed:
                continue
            name = r["custom_name"] or r["display_name"]
            message = (f"Your {name} test was due on {fmt(due)}. Please get it done, or upload the report in My tests."
                       if kind == "overdue" else
                       f"Reminder: your {name} test is due by {fmt(due)} ({left} day{'' if left == 1 else 's'} left).")
            nid = notify_patient(conn, r["patient_id"], "test_reminder", message, test_order_item_id=r["id"])
            conn.execute("UPDATE test_reminders_sent SET notification_id = ? WHERE test_order_item_id = ? AND kind = ?",
                         (nid, r["id"], kind))
            sent += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return sent


# ------------------------------------------------------------------ idempotency keys

def request_hash(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _idem_replay(conn, scope: str, key: str, route: str, body_hash: str):
    row = conn.execute("SELECT route, request_hash, status_code, response FROM idempotency_keys WHERE scope = ? AND key = ?",
                       (scope, key)).fetchone()
    if row is None:
        return None
    if row["route"] != route or row["request_hash"] != body_hash:
        raise OrderError("idempotency_mismatch", "This Idempotency-Key was already used for a different request.", 422)
    return row["status_code"], {**json.loads(row["response"]), "replayed": True}


def _idem_store(conn, scope: str, key: str, route: str, body_hash: str, status: int, response: dict) -> None:
    conn.execute("INSERT INTO idempotency_keys (scope, key, route, request_hash, status_code, response, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)", (scope, key, route, body_hash, status, json.dumps(response), now_iso()))


def today_in_clinic(dt: datetime | None = None) -> date:
    return (dt or datetime.now(appt.CLINIC_TZ)).astimezone(appt.CLINIC_TZ).date()
