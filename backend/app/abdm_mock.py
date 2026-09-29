"""Mock ABDM gateway: a SIMULATION of India's ABDM consent flow, not a real integration.

It follows the real shape:
  HIU (our clinic) asks for consent  ->  gateway sends it to the patient's PHR app (our patient
  dashboard)  ->  patient approves/denies  ->  on approval the HIP (the other hospital) sends
  FHIR-style health records  ->  we map them into lab_reports / clinical_events.
Every step is written to `audit_log` (resource_type = consent_request).
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable

from . import repo
from .config import CONSENT_CONFIG

HIU_NAME = "UC2 Diabetes Clinic"
REQUESTER = f"Dr. Priya Nair, {HIU_NAME}"
GATEWAY = "ABDM gateway (mock)"
PURPOSE = ("CAREMGT", "Care management")

# ABDM health-information types we let the care team ask for, with plain-language labels.
HI_TYPES = {
    "Prescription": "Prescriptions",
    "DiagnosticReport": "Lab reports",
    "DischargeSummary": "Emergency & discharge summaries",
}


class ConsentError(ValueError):
    pass


# ------------------------------------------------------------------ Mock HIPs (other hospitals)

@dataclass
class MockHip:
    hip_id: str
    name: str
    records: Callable[[date], list[dict]]     # FHIR-lite resources, dated relative to today


def _ago(today: date, days: int) -> str:
    return (today - timedelta(days=days)).isoformat()


MOCK_HIPS: dict[str, MockHip] = {
    # Ramesh: went to another hospital's emergency room - only visible once consent is given.
    "91123456789012": MockHip("HIP-CITYCARE-PUNE", "City Care Hospital, Pune", lambda t: [
        {"resourceType": "Encounter", "hiType": "DischargeSummary", "class": "EMER", "date": _ago(t, 18),
         "reason": "High blood sugar (hyperglycaemia) with dehydration; treated and discharged.",
         "practitioner": "Dr. Sunil Patil (Emergency Medicine)"},
        {"resourceType": "Observation", "hiType": "DiagnosticReport", "code": "HbA1c", "loinc": "4548-4",
         "value": 8.4, "unit": "%", "date": _ago(t, 18),
         "referenceRange": {"low": 4.0, "high": 5.6, "text": "4.0 – 5.6 %"}},
        {"resourceType": "MedicationRequest", "hiType": "Prescription", "medication": "Insulin glargine 10 units",
         "daysSupply": 30, "date": _ago(t, 18)},
    ]),
    "91234567890123": MockHip("HIP-SUNRISE-CHENNAI", "Sunrise Clinic, Chennai", lambda t: [
        {"resourceType": "Observation", "hiType": "DiagnosticReport", "code": "HbA1c", "loinc": "4548-4",
         "value": 6.7, "unit": "%", "date": _ago(t, 40),
         "referenceRange": {"high": 6.0, "text": "< 6.0 %"}},
        {"resourceType": "MedicationRequest", "hiType": "Prescription", "medication": "Metformin 1000 mg",
         "daysSupply": 30, "date": _ago(t, 60)},
    ]),
    "91345678901234": MockHip("HIP-LAKEVIEW-BLR", "Lakeview Hospital, Bengaluru", lambda t: [
        {"resourceType": "Observation", "hiType": "DiagnosticReport", "code": "HbA1c", "loinc": "4548-4",
         "value": 6.9, "unit": "%", "date": _ago(t, 120)},
        {"resourceType": "MedicationRequest", "hiType": "Prescription", "medication": "Glimepiride 2 mg",
         "daysSupply": 30, "date": _ago(t, 120)},
    ]),
}


def _reference_range(rr: dict | None) -> dict:
    """FHIR Observation.referenceRange -> our columns. No range on the report -> no range stored."""
    if not rr or (rr.get("low") is None and rr.get("high") is None):
        return {}
    low, high = rr.get("low"), rr.get("high")
    kind = "range" if low is not None and high is not None else ("upper" if high is not None else "lower")
    return dict(ref_kind=kind, ref_low=low, ref_high=high, ref_text=rr.get("text"))


def _to_event_fields(resource: dict) -> dict | None:
    """Map one non-lab FHIR-lite resource from the other hospital onto a clinical_events row."""
    kind = resource["resourceType"]
    if kind == "MedicationRequest":
        # A prescription says the medicine is due; collection is not confirmed, so it stays "ordered".
        return dict(event_type="refill", name=resource["medication"], value=resource.get("daysSupply", 30),
                    unit="days", status="ordered", note="Prescribed at other hospital")
    if kind == "Encounter" and resource.get("class") == "EMER":
        return dict(event_type="visit", name="Emergency visit", status="resulted", note=resource.get("reason"),
                    clinician=resource.get("practitioner"))
    return None


# ------------------------------------------------------------------ Consent lifecycle

def _now_str(now: datetime) -> str:
    return now.isoformat(timespec="seconds")


def _audit(conn: sqlite3.Connection, patient_id: str, consent_id: int, action: str, actor: repo.Actor,
           detail: str, now: datetime) -> None:
    repo.audit(conn, actor, f"CONSENT_{action}", patient_id=patient_id, resource_type="consent_request",
               resource_id=consent_id, detail=detail, at=_now_str(now))


def create_request(conn: sqlite3.Connection, patient_id: str, abha_number: str, hi_types: list[str],
                   now: datetime, today: date, requested_by: dict | None = None) -> int:
    """requested_by: the clinical user (dict with id, full_name, clinician_code) asking for the records."""
    abha = "".join(ch for ch in abha_number if ch.isdigit())
    if len(abha) != 14:
        raise ConsentError("An ABHA number has 14 digits. Please check and try again.")
    types = [t for t in HI_TYPES if t in hi_types]
    if not types:
        raise ConsentError("Please choose at least one type of record.")
    patient = repo.get_patient(conn, patient_id)
    if patient is None:
        raise ConsentError("Patient not found.")
    if patient["abha_number"] and patient["abha_number"] != abha:
        raise ConsentError(f"This ABHA number does not match {patient['full_name']}'s record.")
    hip = MOCK_HIPS.get(abha)
    if hip is None:
        raise ConsentError("No hospital records are linked to this ABHA number on the ABDM network.")

    requester = f"{requested_by['full_name']}, {HIU_NAME}" if requested_by else REQUESTER
    actor = repo.clinician_actor(requested_by) if requested_by else repo.Actor("system", None, REQUESTER)
    cfg = CONSENT_CONFIG
    expires = now + timedelta(minutes=cfg.request_expiry_minutes)
    cid = conn.execute(
        """INSERT INTO consent_requests (patient_id, requested_by_user_id, abha_number, requester, hip_name, purpose_code,
               purpose_text, hi_types, date_from, date_to, access_until, expires_at, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'REQUESTED', ?)""",
        (patient_id, requested_by["id"] if requested_by else None, abha, requester, hip.name, *PURPOSE,
         json.dumps(types), (today - timedelta(days=30 * cfg.history_months)).isoformat(), today.isoformat(),
         (today + timedelta(days=cfg.access_days)).isoformat(), _now_str(expires), _now_str(now)),
    ).lastrowid
    _audit(conn, patient_id, cid, "REQUESTED", actor,
           f"Asked {hip.name} for {', '.join(HI_TYPES[t] for t in types)}. Patient must answer by "
           f"{expires:%d %b %Y, %H:%M}.", now)
    conn.commit()
    return cid


def expire_stale(conn: sqlite3.Connection, now: datetime) -> None:
    rows = conn.execute("SELECT id, patient_id, hip_name FROM consent_requests WHERE status = 'REQUESTED' AND expires_at < ?",
                        (_now_str(now),)).fetchall()
    for r in rows:
        conn.execute("UPDATE consent_requests SET status = 'EXPIRED' WHERE id = ?", (r["id"],))
        _audit(conn, r["patient_id"], r["id"], "EXPIRED", repo.Actor("system", None, GATEWAY),
               f"No answer from the patient in time. Nothing was shared by {r['hip_name']}.", now)
    if rows:
        conn.commit()


def respond(conn: sqlite3.Connection, consent_id: int, patient_id: str, approve: bool,
            now: datetime, today: date) -> dict:
    expire_stale(conn, now)
    req = conn.execute("SELECT * FROM consent_requests WHERE id = ? AND patient_id = ?", (consent_id, patient_id)).fetchone()
    if req is None:
        raise ConsentError("Request not found.")
    if req["status"] != "REQUESTED":
        raise ConsentError("This request is no longer waiting for your answer.")
    patient = repo.get_patient(conn, patient_id)
    actor = repo.Actor("patient", patient_id, f"{patient['full_name']} (patient)")

    if not approve:
        conn.execute("UPDATE consent_requests SET status = 'DENIED', responded_at = ? WHERE id = ?",
                     (_now_str(now), consent_id))
        _audit(conn, patient_id, consent_id, "DENIED", actor, "Patient said no. Nothing was shared.", now)
        conn.commit()
        return get_request(conn, consent_id)

    conn.execute("UPDATE consent_requests SET status = 'GRANTED', responded_at = ? WHERE id = ?",
                 (_now_str(now), consent_id))
    _audit(conn, patient_id, consent_id, "GRANTED", actor, "Patient approved.", now)

    # HIP pushes the records; we keep only what was consented to, within the date range.
    hip = MOCK_HIPS[req["abha_number"]]
    types = set(json.loads(req["hi_types"]))
    d_from, d_to = date.fromisoformat(req["date_from"]), date.fromisoformat(req["date_to"])
    count = 0
    reports: dict[date, str] = {}          # one lab report per date from this hospital
    for res in hip.records(today):
        when = date.fromisoformat(res["date"])
        if res["hiType"] not in types or not (d_from <= when <= d_to):
            continue
        if res["resourceType"] == "Observation":
            if when not in reports:
                reports[when] = repo.create_lab_report(conn, patient_id, hip.name, when, "external_hospital_abdm",
                                                       consent_id=consent_id)
            repo.add_lab_result(conn, reports[when], patient_id, res["code"], res["value"], res.get("unit"), when,
                                **_reference_range(res.get("referenceRange")))
            count += 1
            continue
        fields = _to_event_fields(res)
        if fields is None:
            continue
        if fields["event_type"] == "visit":
            fields["facility"] = hip.name
        repo.insert_event(conn, patient_id, effective_date=when, source="external_hospital_abdm", confidence="high",
                          asserted_by=hip.name, consent_id=consent_id, **fields)
        count += 1
    conn.execute("UPDATE consent_requests SET records_received = ? WHERE id = ?", (count, consent_id))
    _audit(conn, patient_id, consent_id, "DATA_RECEIVED", repo.Actor("system", None, hip.name),
           f"{count} record(s) received and added to the patient's timeline." if count
           else f"No matching records were sent by {hip.name}.", now)
    conn.commit()
    return get_request(conn, consent_id)


def simulate_expiry(conn: sqlite3.Connection, patient_id: str, consent_id: int, now: datetime) -> bool:
    """Demo helper: make a waiting request run out of time right now. Scoped to the patient."""
    cur = conn.execute("UPDATE consent_requests SET expires_at = ? WHERE id = ? AND patient_id = ? AND status = 'REQUESTED'",
                       (_now_str(now - timedelta(seconds=1)), consent_id, patient_id))
    expire_stale(conn, now)
    return cur.rowcount == 1


# ------------------------------------------------------------------ Read models

def _request_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    audit = conn.execute("SELECT action, actor_label AS actor, detail, at FROM audit_log "
                         "WHERE resource_type = 'consent_request' AND resource_id = ? ORDER BY id",
                         (str(row["id"]),)).fetchall()
    types = json.loads(row["hi_types"])
    return {
        "id": row["id"], "patient_id": row["patient_id"], "abha_number": row["abha_number"],
        "requester": row["requester"], "hip_name": row["hip_name"],
        "purpose": row["purpose_text"], "purpose_code": row["purpose_code"],
        "hi_types": types, "hi_type_labels": [HI_TYPES[t] for t in types],
        "date_from": row["date_from"], "date_to": row["date_to"], "access_until": row["access_until"],
        "expires_at": row["expires_at"], "status": row["status"], "records_received": row["records_received"],
        "created_at": row["created_at"], "responded_at": row["responded_at"],
        "audit": [{**dict(a), "action": a["action"].removeprefix("CONSENT_")} for a in audit],
    }


def get_request(conn: sqlite3.Connection, consent_id: int) -> dict:
    return _request_dict(conn, conn.execute("SELECT * FROM consent_requests WHERE id = ?", (consent_id,)).fetchone())


def list_requests(conn: sqlite3.Connection, now: datetime, patient_id: str, status: str | None = None) -> list[dict]:
    """Always scoped to one patient."""
    expire_stale(conn, now)
    sql, args = "SELECT * FROM consent_requests WHERE patient_id = ?", [patient_id]
    if status:
        sql, args = sql + " AND status = ?", args + [status]
    return [_request_dict(conn, r) for r in conn.execute(sql + " ORDER BY id DESC", args).fetchall()]


def audit_log(conn: sqlite3.Connection, now: datetime, patient_ids: list[str]) -> list[dict]:
    """Consent activity for the given patients only (the caller passes the clinician's own patients)."""
    expire_stale(conn, now)
    if not patient_ids:
        return []
    marks = ",".join("?" * len(patient_ids))
    rows = conn.execute(
        f"""SELECT a.id, a.actor_label AS actor, a.detail, a.at, a.resource_id AS consent_id,
                   REPLACE(a.action, 'CONSENT_', '') AS action, c.hip_name, p.full_name, p.patient_code
            FROM audit_log a JOIN consent_requests c ON c.id = CAST(a.resource_id AS INTEGER)
            JOIN patients p ON p.id = a.patient_id
            WHERE a.resource_type = 'consent_request' AND a.patient_id IN ({marks})
            ORDER BY a.id DESC""", patient_ids).fetchall()
    return [dict(r) for r in rows]
