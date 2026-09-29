"""Care-team API. Every route requires a signed-in clinician; every /patients/{patient_id}/... route
additionally requires an active care-team assignment (deps.patient_for_clinician)."""

from dataclasses import asdict
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import abdm_mock as abdm
from .. import appointments as appt
from .. import report_links as rl
from .. import labs, ratelimit, repo, security, storage
from ..config import DEFAULT_CONFIG
from ..deps import current_clinician, get_conn, now, patient_for_clinician, today
from ..detection import SOURCE_LABELS, emergency_visit, emergency_visits, emergency_window_start, fmt, last_clinic_visit
from ..services import current_medicine, event_dict, patient_summary

router = APIRouter(prefix="/api", tags=["clinical"])

LEVEL_ORDER = {"high_priority": 0, "quietly_worse": 1, "watch": 2, "none": 3}


def _record(conn, patient: dict):
    return repo.load_patient_record(conn, patient["id"])


# ================================================================== patients

@router.get("/patients")
def list_patients(flagged_only: bool = False, user=Depends(current_clinician), conn=Depends(get_conn)):
    as_of = today()
    patients = [patient_summary(repo.load_patient_record(conn, pid), as_of)
                for pid in repo.patient_ids_for_clinician(conn, user["id"])]
    if flagged_only:
        patients = [p for p in patients if p["assessment"]["flagged"]]
    patients.sort(key=lambda p: (LEVEL_ORDER[p["assessment"]["level"]], -p["assessment"]["soft_count"], p["full_name"]))
    return {"as_of": as_of, "patients": patients}


class PatientIn(BaseModel):
    full_name: str = Field(max_length=120)
    phone: str = Field(max_length=20)
    date_of_birth: date | None = None
    sex: Literal["M", "F", "O"] | None = None
    email: str | None = Field(None, max_length=200)
    abha_number: str | None = Field(None, max_length=20)


@router.post("/patients")
def create_patient(body: PatientIn, user=Depends(current_clinician), conn=Depends(get_conn)):
    if body.date_of_birth and body.date_of_birth > today():
        raise HTTPException(422, "Date of birth cannot be in the future.")
    temp_password = security.new_temp_password()
    try:
        p = repo.create_patient(conn, body.full_name, body.phone, body.date_of_birth.isoformat() if body.date_of_birth else None,
                                body.sex, body.email, body.abha_number,
                                password_hash=security.hash_password(temp_password), must_change_password=True)
    except repo.RepoError as e:
        msg = str(e)
        if "phone number already exists" in msg:
            msg = ("This mobile number is already registered. Use “Existing patient” and enter their Patient ID, "
                   f"or ask them to add you under “My doctors” with your Clinician ID {user['clinician_code']}.")
        raise HTTPException(422, msg)
    repo.grant_access(conn, user["id"], p["id"], granted_by=user["id"])
    repo.audit(conn, repo.clinician_actor(user), "PATIENT_CREATED", patient_id=p["id"], resource_type="patient",
               resource_id=p["id"], detail="Temporary password issued")
    conn.commit()
    # The temporary password is returned ONCE so the clinician can hand it over; only its hash is stored.
    return {"id": p["id"], "patient_code": p["patient_code"], "full_name": p["full_name"],
            "temporary_password": temp_password}


@router.post("/patients/{patient_id}/reset-password")
def reset_patient_password(patient=Depends(patient_for_clinician), user=Depends(current_clinician), conn=Depends(get_conn)):
    """Issue a new temporary password (patient forgot theirs). Signs the patient out everywhere."""
    temp_password = security.new_temp_password()
    repo.set_patient_password(conn, patient["id"], temp_password, must_change=True)
    repo.revoke_patient_sessions(conn, patient["id"])
    repo.audit(conn, repo.clinician_actor(user), "PATIENT_PASSWORD_RESET", patient_id=patient["id"],
               resource_type="patient", resource_id=patient["id"])
    conn.commit()
    return {"patient_code": patient["patient_code"], "full_name": patient["full_name"], "temporary_password": temp_password}


class LinkIn(BaseModel):
    patient_code: str = Field(max_length=20)
    phone: str = Field(max_length=20)


@router.post("/patients/link")
def link_existing_patient(body: LinkIn, user=Depends(current_clinician), conn=Depends(get_conn)):
    """Add an EXISTING patient (registered by the hospital or by themselves) to this clinician's list.
    Both the Patient ID and the patient's mobile number must match - Patient IDs are sequential, so the ID
    alone would let anyone guess their way through every record. Failures are rate-limited and give one
    generic message, so the endpoint reveals nothing about which part was wrong."""
    key = f"link:{user['id']}"
    ratelimit.check(key)
    patient = repo.find_patient(conn, body.patient_code) if body.patient_code.strip().upper().startswith("P-") else None
    phone = security.normalize_phone(body.phone)
    if patient is None or not phone or patient["phone"] != phone:
        ratelimit.fail(key)
        repo.audit(conn, repo.clinician_actor(user), "PATIENT_LINK_FAILED", resource_type="patient",
                   resource_id=body.patient_code.strip().upper()[:20])
        conn.commit()
        raise HTTPException(404, "No patient matches that Patient ID and mobile number. Please check both.")
    ratelimit.clear(key)
    already = repo.has_access(conn, user["id"], patient["id"])
    if not already:
        repo.grant_access(conn, user["id"], patient["id"], granted_by=user["id"])
        repo.audit(conn, repo.clinician_actor(user), "CARE_TEAM_ADDED", patient_id=patient["id"], resource_type="clinical_user",
                   resource_id=user["id"], detail=f"Linked by {user['full_name']} ({user['clinician_code']}) using Patient ID + mobile")
        conn.commit()
    return {"id": patient["id"], "patient_code": patient["patient_code"], "full_name": patient["full_name"],
            "already_linked": already}


@router.get("/patients/{patient_id}")
def get_patient(patient=Depends(patient_for_clinician), user=Depends(current_clinician), conn=Depends(get_conn)):
    as_of = today()
    counts = dict(conn.execute("SELECT source, COUNT(*) FROM clinical_events WHERE patient_id = ? GROUP BY source",
                               (patient["id"],)).fetchall())
    for source, n in conn.execute("SELECT r.source, COUNT(*) FROM lab_test_results t JOIN lab_reports r ON r.id = t.report_id "
                                  "WHERE t.patient_id = ? GROUP BY r.source", (patient["id"],)):
        counts[source] = counts.get(source, 0) + n
    # Documents not already counted through the reading they back up (e.g. a glucometer photo).
    for source, n in conn.execute("SELECT d.source, COUNT(*) FROM patient_documents d WHERE d.patient_id = ? AND NOT EXISTS "
                                  "(SELECT 1 FROM clinical_events e WHERE e.document_id = d.id) GROUP BY d.source",
                                  (patient["id"],)):
        counts[source] = counts.get(source, 0) + n
    # Outside-lab reports (and the values read from them) are things the patient sent.
    counts["patient_upload"] = counts.get("patient_upload", 0) + counts.pop("patient_upload_reviewed", 0) + conn.execute(
        "SELECT COUNT(*) FROM external_reports x WHERE x.patient_id = ? AND NOT EXISTS "
        "(SELECT 1 FROM clinical_events e WHERE e.linked_document_id = x.id)", (patient["id"],)).fetchone()[0]
    # One "Opened record" per clinician per patient per 10 minutes - page refreshes aren't new visits.
    recent = conn.execute("SELECT 1 FROM audit_log WHERE action = 'PATIENT_VIEWED' AND actor_id = ? AND patient_id = ? "
                          "AND at > ?", (user["id"], patient["id"],
                                         (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(timespec="seconds"))
                          ).fetchone()
    if not recent:
        repo.audit(conn, repo.clinician_actor(user), "PATIENT_VIEWED", patient_id=patient["id"], resource_type="patient",
                   resource_id=patient["id"])
        conn.commit()
    return {"as_of": as_of, **patient_summary(_record(conn, patient), as_of), "source_counts": counts}


@router.get("/patients/{patient_id}/events")
def patient_events(source: str | None = None, limit: int = 12, patient=Depends(patient_for_clinician), conn=Depends(get_conn)):
    rows = [e for e in _record(conn, patient).events if (source is None or e.source == source) and e.effective_date <= today()]
    rows.sort(key=lambda e: (e.effective_date, str(e.id)), reverse=True)
    return [event_dict(e) for e in rows[:min(limit, 200)]]


@router.get("/patients/{patient_id}/emergency-visits")
def patient_emergency_visits(patient=Depends(patient_for_clinician), conn=Depends(get_conn)):
    as_of, p = today(), _record(conn, patient)
    since = emergency_window_start(p, as_of)
    visits = sorted(emergency_visits(p, as_of), key=lambda v: (v.effective_date, v.id or 0), reverse=True)
    return {
        "last_clinic_visit": last_clinic_visit(p, as_of),
        "counts_from": since,
        "visits": [{"id": v.id, "date": v.effective_date, "hospital": v.facility or v.asserted_by, "problem": v.note,
                    "doctor": v.clinician, "recorded_by": v.asserted_by, "source": v.source,
                    "source_label": SOURCE_LABELS.get(v.source, v.source),
                    "counts_toward_flag": v.effective_date >= since} for v in visits],
    }


# ================================================================== labs, reports, documents

@router.get("/patients/{patient_id}/labs")
def patient_labs(patient=Depends(patient_for_clinician), conn=Depends(get_conn)):
    return labs.lab_panels(_record(conn, patient), today())


@router.get("/patients/{patient_id}/lab-results")
def lab_history(test_name: str, patient=Depends(patient_for_clinician), conn=Depends(get_conn)):
    """Every result for one test, oldest first - nothing is ever overwritten."""
    for panel in labs.lab_panels(_record(conn, patient), today()):
        if panel["name"].lower() == test_name.lower():
            return panel["results"]
    return []


@router.get("/patients/{patient_id}/reports")
def patient_reports(patient=Depends(patient_for_clinician), conn=Depends(get_conn)):
    return repo.list_reports(conn, patient["id"])


@router.get("/patients/{patient_id}/reports/{report_id}")
def patient_report(report_id: str, patient=Depends(patient_for_clinician), conn=Depends(get_conn)):
    """One lab report with its results and the file it came from - for the lab-report review screen."""
    report = next((r for r in repo.list_reports(conn, patient["id"]) if r["id"] == report_id), None)
    if report is None:
        raise HTTPException(404, "Lab report not found.")
    doc = next((d for d in repo.list_documents(conn, patient["id"]) if d["id"] == report["document_id"]), None)
    return {**report, "patient_id": patient["id"], "patient_name": patient["full_name"], "patient_code": patient["patient_code"],
            "document": {k: doc[k] for k in ("id", "content_type", "uploaded_at", "review_status", "reviewed_by_name",
                                             "reviewed_at", "description")} if doc else None}


class ResultIn(BaseModel):
    test_name: str = Field(max_length=80)
    value: str = Field(max_length=40)
    unit: str | None = Field(None, max_length=30)
    reference: str | None = Field(None, max_length=60)


class ReportResultsIn(BaseModel):
    report_date: date | None = None
    results: list[ResultIn] = Field(min_length=1, max_length=30)


@router.post("/patients/{patient_id}/reports/{report_id}/results")
def enter_report_results(report_id: str, body: ReportResultsIn, patient=Depends(patient_for_clinician),
                         user=Depends(current_clinician), conn=Depends(get_conn)):
    """Type in the values from an uploaded lab report, exactly as printed. Entering them confirms the file."""
    report = repo.get_lab_report(conn, patient["id"], report_id)
    if report is None:
        raise HTTPException(404, "Lab report not found.")
    if report["document_status"] == "rejected":
        raise HTTPException(409, "This report was rejected, so its values can't be added.")
    if body.report_date and body.report_date > today():
        raise HTTPException(422, "The test date cannot be in the future.")
    rows = []
    for i, r in enumerate(body.results, 1):
        name, value = r.test_name.strip(), r.value.strip()
        if not name or not value:
            raise HTTPException(422, f"Row {i}: please enter both the test and its result.")
        try:
            numeric: float | str = float(value)
        except ValueError:
            numeric = value
        rows.append((name, numeric, value, (r.unit or "").strip() or None,
                     repo.parse_reference(r.reference, isinstance(numeric, float))))
    when = body.report_date or date.fromisoformat(report["report_date"])
    if body.report_date:
        conn.execute("UPDATE lab_reports SET report_date = ? WHERE id = ? AND patient_id = ?",
                     (when.isoformat(), report_id, patient["id"]))
    for name, value, as_typed, unit, ref in rows:
        repo.add_lab_result(conn, report_id, patient["id"], name, value, unit, when, value_text=as_typed, **ref)
    if report["document_id"] and report["document_status"] == "pending":
        repo.review_document(conn, patient["id"], report["document_id"], "confirmed", user["id"])
    repo.audit(conn, repo.clinician_actor(user), "REPORT_RESULTS_ENTERED", patient_id=patient["id"],
               resource_type="lab_report", resource_id=report_id, detail=f"{len(rows)} result(s)")
    conn.commit()
    return next(r for r in repo.list_reports(conn, patient["id"]) if r["id"] == report_id)


@router.get("/patients/{patient_id}/documents")
def patient_documents(patient=Depends(patient_for_clinician), conn=Depends(get_conn)):
    return repo.list_documents(conn, patient["id"])


@router.get("/patients/{patient_id}/documents/{document_id}/file")
def document_file(document_id: str, preview: Literal["thumbnail"] | None = None, patient=Depends(patient_for_clinician),
                  user=Depends(current_clinician), conn=Depends(get_conn)):
    doc = repo.get_document(conn, patient["id"], document_id)       # scoped to this patient
    if doc is None:
        raise HTTPException(404, "Document not found.")
    if preview is None:                       # opening the file is logged; the inbox's small thumbnails are not
        repo.audit(conn, repo.clinician_actor(user), "DOCUMENT_VIEWED", patient_id=patient["id"],
                   resource_type="document", resource_id=document_id)
        conn.commit()
    return Response(storage.read(doc["storage_key"]), media_type=doc["content_type"],
                    headers={"Content-Disposition": f'inline; filename="document{storage.EXTENSIONS[doc["content_type"]]}"',
                             "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


class ReviewIn(BaseModel):
    status: Literal["confirmed", "rejected"]


@router.post("/patients/{patient_id}/documents/{document_id}/review")
def review_document(document_id: str, body: ReviewIn, patient=Depends(patient_for_clinician),
                    user=Depends(current_clinician), conn=Depends(get_conn)):
    if not repo.review_document(conn, patient["id"], document_id, body.status, user["id"]):
        raise HTTPException(404, "Document not found.")
    repo.audit(conn, repo.clinician_actor(user), f"DOCUMENT_{body.status.upper()}", patient_id=patient["id"],
               resource_type="document", resource_id=document_id)
    conn.commit()
    return {"ok": True}


# ================================================================== External Report Review (clinician side)
# A patient uploaded a report from an outside lab. The clinician reads it (split screen) and types ONE value;
# saving writes the value together with its document link in one transaction (repo.save_reviewed_value).

def _external_out(x: dict) -> dict:
    out = {k: x[k] for k in ("id", "patient_id", "patient_name", "patient_code", "document_type", "test_type", "test_name",
                             "test_label", "test_date", "upload_date", "content_type", "status", "reviewed_by_name",
                             "reviewed_at", "reject_reason", "reject_code", "purchase_date", "disease", "expected_unit",
                             "plausible_low", "plausible_high")}
    out["result"] = ({"event_id": x["event_id"], "name": x["result_name"], "value": x["value"], "unit": x["unit"],
                      "date": x["result_date"]} if x["event_id"] else None)
    out["reference_line"] = (repo.reference_line(x["patient_name"], x["patient_code"], x["test_label"], x["upload_date"],
                                                 x["reviewed_by_name"], x["reviewed_at"]) if x["status"] == "reviewed" else None)
    return out


@router.get("/external-reports/pending")
def pending_external_reports(user=Depends(current_clinician), conn=Depends(get_conn)):
    """The Pending Reports queue - only the clinician's own patients."""
    return [_external_out(x) for x in
            repo.pending_external_reports(conn, repo.patient_ids_for_clinician(conn, user["id"]))]


@router.get("/patients/{patient_id}/external-reports")
def patient_external_reports(patient=Depends(patient_for_clinician), conn=Depends(get_conn)):
    return [_external_out(x) for x in repo.list_external_reports(conn, patient["id"])]


def _external_or_404(conn, patient_id: str, report_id: str) -> dict:
    x = repo.get_external_report(conn, patient_id, report_id)        # scoped to this patient
    if x is None:
        raise HTTPException(404, "Report not found.")
    return x


@router.get("/patients/{patient_id}/external-reports/{report_id}")
def external_report(report_id: str, patient=Depends(patient_for_clinician), conn=Depends(get_conn)):
    return _external_out(_external_or_404(conn, patient["id"], report_id))


@router.get("/patients/{patient_id}/external-reports/{report_id}/file")
def external_report_file(report_id: str, preview: Literal["thumbnail"] | None = None, patient=Depends(patient_for_clinician),
                         user=Depends(current_clinician), conn=Depends(get_conn)):
    x = repo.get_external_report(conn, patient["id"], report_id, None)
    if x is None:
        raise HTTPException(404, "Report not found.")
    if preview is None:                       # opening the report is logged; the queue's small thumbnails are not
        repo.audit(conn, repo.clinician_actor(user), "DOCUMENT_VIEWED", patient_id=patient["id"],
                   resource_type="external_report", resource_id=report_id)
        conn.commit()
    return Response(storage.read(x["storage_key"]), media_type=x["content_type"],
                    headers={"Content-Disposition": f'inline; filename="report{storage.EXTENSIONS[x["content_type"]]}"',
                             "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


class ExternalValueIn(BaseModel):
    value: float
    unit: str | None = Field(None, max_length=20)          # only for "Other" - the other types have a fixed unit
    test_date: date | None = None                          # only when the date given at upload is clearly wrong


@router.post("/patients/{patient_id}/external-reports/{report_id}/review")
def save_external_value(report_id: str, body: ExternalValueIn, patient=Depends(patient_for_clinician),
                        user=Depends(current_clinician), conn=Depends(get_conn)):
    """The single save: value + linked_document_id + reviewed_by + reviewed_date + report status, all or nothing."""
    x = _external_or_404(conn, patient["id"], report_id)
    if x["status"] != "pending_review":
        raise HTTPException(409, "This report has already been reviewed.")
    lo, hi = x["plausible_low"], x["plausible_high"]
    if lo is not None and not lo <= body.value <= hi:
        raise HTTPException(422, f"{x['test_label']} is usually between {lo:g} and {hi:g} {x['expected_unit']}. "
                                 "Please check the value on the report.")
    unit = (body.unit or "").strip() or None
    if x["test_type"] == "Other" and not unit:
        raise HTTPException(422, "Please enter the unit printed on the report.")
    if body.test_date and body.test_date > today():
        raise HTTPException(422, "The test date cannot be in the future.")
    try:
        repo.save_reviewed_value(conn, patient["id"], report_id, body.value, user, unit=unit, test_date=body.test_date)
        corrected = body.test_date and body.test_date.isoformat() != x["test_date"]
        repo.audit(conn, repo.clinician_actor(user), "EXTERNAL_REPORT_REVIEWED", patient_id=patient["id"],
                   resource_type="external_report", resource_id=report_id,
                   detail=f"{x['test_label']} {body.value:g} {x['expected_unit'] or unit}"
                          + (f"; test date corrected from {x['test_date']} to {body.test_date}" if corrected else ""))
        conn.commit()
    except repo.RepoError as e:
        conn.rollback()
        raise HTTPException(409, str(e))
    except sqlite3.IntegrityError as e:       # the database refused - e.g. someone else saved it a moment ago
        conn.rollback()
        raise HTTPException(409, str(e) if "report" in str(e) else "The value could not be saved. Nothing was changed.")
    return _external_out(repo.get_external_report(conn, patient["id"], report_id))


class ExternalRejectIn(BaseModel):
    reason: str | None = Field(None, max_length=200)


@router.post("/patients/{patient_id}/external-reports/{report_id}/reject")
def reject_external_report(report_id: str, body: ExternalRejectIn, patient=Depends(patient_for_clinician),
                           user=Depends(current_clinician), conn=Depends(get_conn)):
    """The report can't be used (unreadable, wrong file). No value is saved."""
    _external_or_404(conn, patient["id"], report_id)
    reason = (body.reason or "").strip() or None
    if not repo.reject_external_report(conn, patient["id"], report_id, user["id"], reason):
        raise HTTPException(409, "This report has already been reviewed.")
    repo.audit(conn, repo.clinician_actor(user), "EXTERNAL_REPORT_REJECTED", patient_id=patient["id"],
               resource_type="external_report", resource_id=report_id, detail=reason)
    conn.commit()
    return _external_out(repo.get_external_report(conn, patient["id"], report_id))


@router.get("/patients/{patient_id}/uploads")
def patient_uploads(all: bool = False, patient=Depends(patient_for_clinician), conn=Depends(get_conn)):
    """What the patient sent: self-reported values (with any photo) and documents.
    The last 60 days by default; `?all=true` returns everything the patient ever sent."""
    since = date.min if all else today() - timedelta(days=60)
    events = sorted((e for e in _record(conn, patient).events if e.source == "patient_upload" and e.effective_date >= since
                     and not (e.provenance or {}).get("kind") == "medicine_bill"),
                    key=lambda e: (e.effective_date, str(e.id)), reverse=True)
    docs = [d for d in repo.list_documents(conn, patient["id"])
            if d["source"] == "patient_upload" and (all or d["uploaded_at"][:10] >= since.isoformat())]
    linked = {e.document_id for e in events if e.document_id}
    doc_status = {d["id"]: d for d in docs}
    out = []
    for e in events:
        d = doc_status.get(e.document_id)
        status = d["review_status"] if d else repo.review_status_for_event(e.confidence)
        out.append({**event_dict(e), "photo_id": e.document_id,
                    # a reading with a photo shares the photo's review; otherwise its own
                    "review_status": status,
                    "reviewed_by_name": d["reviewed_by_name"] if d else
                    (repo.event_reviewer(conn, patient["id"], e.id) if status != "pending" else None)})
    external = [_external_out(x) for x in repo.list_external_reports(conn, patient["id"], None)
                if all or x["upload_date"][:10] >= since.isoformat()]
    return {"events": out, "documents": [d for d in docs if d["id"] not in linked], "external_reports": external}


@router.post("/patients/{patient_id}/events/{event_id}/review")
def review_event(event_id: int, body: ReviewIn, patient=Depends(patient_for_clinician), user=Depends(current_clinician),
                 conn=Depends(get_conn)):
    if not repo.review_patient_event(conn, patient["id"], event_id, body.status, repo.clinician_actor(user).label):
        raise HTTPException(404, "No unreviewed patient entry with that id. Entries with a file are reviewed with the file.")
    repo.audit(conn, repo.clinician_actor(user), f"PATIENT_ENTRY_{body.status.upper()}", patient_id=patient["id"],
               resource_type="clinical_event", resource_id=event_id)
    conn.commit()
    return {"ok": True}


# ================================================================== manual entry

class DatedValue(BaseModel):
    value: float
    date: date


class LinkedValue(DatedValue):
    linked_document_id: str | None = None      # the uploaded report this number was read from, if any


class RefillFlag(BaseModel):
    missed: bool
    date: date


class ScreeningFlag(BaseModel):
    type: Literal["Eye", "Foot", "Kidney"]
    date: date


class EmergencyIn(BaseModel):
    date: date
    problem: str = Field(max_length=300)
    doctor: str = Field(max_length=120)
    hospital: str = Field(max_length=120)


class ManualIn(BaseModel):
    sugar: LinkedValue | None = None
    hba1c: LinkedValue | None = None
    hemoglobin: LinkedValue | None = None
    egfr: LinkedValue | None = None
    missed_refill: RefillFlag | None = None
    overdue_screening: ScreeningFlag | None = None
    hypoglycemia: bool = False
    emergency: EmergencyIn | None = None


LAB_FIELDS = (("hba1c", "HbA1c", "%", 3, 20), ("hemoglobin", "Hemoglobin", "g/dL", 3, 25),
              ("egfr", "eGFR", "mL/min/1.73m²", 1, 200))


@router.post("/patients/{patient_id}/manual")
def manual_entry(body: ManualIn, patient=Depends(patient_for_clinician), user=Depends(current_clinician),
                 conn=Depends(get_conn)):
    as_of = today()
    errors = []
    if body.sugar and not 20 <= body.sugar.value <= 600:
        errors.append("Sugar level must be between 20 and 600 mg/dL.")
    for field, name, unit, lo, hi in LAB_FIELDS:
        v = getattr(body, field)
        if v and not lo <= v.value <= hi:
            errors.append(f"{name} must be between {lo} and {hi} {unit}.")
    if body.emergency:
        for text, name in ((body.emergency.problem, "the problem"), (body.emergency.doctor, "the treating doctor"),
                           (body.emergency.hospital, "the hospital")):
            if len(text.strip()) < 2:
                errors.append(f"Emergency visit: please enter {name}.")
    dated = (body.sugar, body.hba1c, body.hemoglobin, body.egfr, body.missed_refill, body.overdue_screening, body.emergency)
    if any(item and item.date > as_of for item in dated):
        errors.append("Dates cannot be in the future.")
    if not any((*dated, body.hypoglycemia)):
        errors.append("Nothing to save — fill in at least one field.")
    link_kind = {}          # report id -> "test_report" | "document" (the picker lists both kinds of upload)
    for field in ("sugar", "hba1c", "hemoglobin", "egfr"):
        v = getattr(body, field)
        if v and v.linked_document_id:
            found = rl.get_report(conn, patient["id"], v.linked_document_id)
            if found is None:
                errors.append("That report was not found for this patient.")
            elif not found["usable"]:
                errors.append("That report was marked as not usable, so a value cannot be linked to it.")
            else:
                link_kind[v.linked_document_id] = found["origin"]
    if errors:
        raise HTTPException(422, " ".join(errors))

    actor = repo.clinician_actor(user)
    label = f"{user['full_name']} (care team)"
    p = _record(conn, patient)

    def add(event_type, when, status, **kw):
        repo.insert_event(conn, patient["id"], event_type, when, "care_team_manual", "high", status, label,
                          recorded_by_user_id=user["id"], **kw)

    saved = []
    # Test values typed by the care team are lab events (on the graphs, no range invented), each optionally linked
    # to the uploaded report it was read from - the link is written in the same INSERT as the value.
    for field, name, unit, v in (("sugar", "Blood glucose", "mg/dL", body.sugar),
                                 *((f, n, u, getattr(body, f)) for f, n, u, _, _ in LAB_FIELDS)):
        if not v:
            continue
        event_id = repo.insert_event(conn, patient["id"], "lab", v.date, "care_team_manual", "high", "resulted",
                                     f"Entered by {user['full_name']}", name=name, value=v.value, unit=unit,
                                     recorded_by_user_id=user["id"],
                                     linked_document_id=v.linked_document_id if link_kind.get(v.linked_document_id) == "test_report" else None,
                                     linked_upload_id=v.linked_document_id if link_kind.get(v.linked_document_id) == "document" else None)
        if v.linked_document_id:
            repo.audit(conn, actor, "VALUE_LINKED_TO_REPORT", patient_id=patient["id"], resource_type="clinical_event",
                       resource_id=str(event_id), detail=f"{name}: linked to report {v.linked_document_id}")
        label = "Sugar level" if field == "sugar" else name
        saved.append(f"{label} {v.value:g}{'%' if unit == '%' else ' ' + unit}"
                     + (" (linked to report)" if v.linked_document_id else ""))

    if body.missed_refill:
        med = current_medicine(p)
        missed = body.missed_refill.missed
        add("refill", body.missed_refill.date, "ordered" if missed else "active", name=med,
            value=DEFAULT_CONFIG.default_days_supply, unit="days",
            note="Care team: refill was due and not collected" if missed else "Care team: refill collected")
        saved.append(f"Missed refill of {med} (due {fmt(body.missed_refill.date)})" if missed
                     else f"{med} refill collected {fmt(body.missed_refill.date)}")
    if body.overdue_screening:
        s = body.overdue_screening
        add("screening", s.date, "ordered", name=s.type, note="Marked overdue by care team")
        saved.append(f"{s.type} screening overdue since {fmt(s.date)}")
    if body.emergency:
        er = body.emergency
        add("visit", er.date, "resulted", name="Emergency visit", note=er.problem.strip(),
            facility=er.hospital.strip(), clinician=er.doctor.strip())
        saved.append(f"Emergency visit on {fmt(er.date)} at {er.hospital.strip()}")
    if body.hypoglycemia:
        add("hypo_event", as_of, "resulted", name="Hypoglycaemia", note="Recorded by care team")
        saved.append("Hypoglycaemia event")

    repo.audit(conn, actor, "CLINICAL_DATA_ENTERED", patient_id=patient["id"], resource_type="patient",
               resource_id=patient["id"], detail=f"{len(saved)} item(s)")
    conn.commit()
    p = _record(conn, patient)
    if body.emergency and not emergency_visit(p, as_of):
        saved.append("(this emergency visit is before the last clinic visit or outside the "
                     f"{DEFAULT_CONFIG.emergency_lookback_days}-day window, so it does not raise a flag)")
    return {"saved": saved, **patient_summary(p, as_of)}


# ================================================================== Medicine Purchase Verification (care-team side)

@router.get("/medicine-bills/pending")
def pending_medicine_bills(user=Depends(current_clinician), conn=Depends(get_conn)):
    """The Pending Medicine Bills queue - only the clinician's own patients."""
    return [_external_out(x) for x in
            repo.pending_external_reports(conn, repo.patient_ids_for_clinician(conn, user["id"]), "medicine_bill")]


@router.get("/medicine-bills")
def all_medicine_bills(user=Depends(current_clinician), conn=Depends(get_conn)):
    """Every bill of the clinician's own patients, whatever its status, newest first."""
    return [_external_out(x) for x in
            repo.all_bills_for_patients(conn, repo.patient_ids_for_clinician(conn, user["id"]))]


@router.get("/patients/{patient_id}/medicine-bills")
def patient_medicine_bills(patient=Depends(patient_for_clinician), conn=Depends(get_conn)):
    return [_external_out(x) for x in repo.list_medicine_bills(conn, patient["id"])]


def _bill_or_404(conn, patient_id: str, bill_id: str) -> dict:
    x = repo.get_external_report(conn, patient_id, bill_id, "medicine_bill")
    if x is None:
        raise HTTPException(404, "Bill not found.")
    return x


@router.get("/patients/{patient_id}/medicine-bills/{bill_id}")
def medicine_bill(bill_id: str, patient=Depends(patient_for_clinician), conn=Depends(get_conn)):
    return {**_external_out(_bill_or_404(conn, patient["id"], bill_id)), "reject_reasons": repo.BILL_REJECT_REASONS}


class BillApproveIn(BaseModel):
    purchase_date: date | None = None          # only if the bill clearly shows it; otherwise the upload time is used


class BillRejectIn(BaseModel):
    reason: Literal["unclear", "medicine_mismatch", "date_mismatch", "other"]
    note: str | None = Field(None, max_length=200)


def _decide_bill(conn, patient, user, bill_id: str, approve: bool, **kw):
    x = _bill_or_404(conn, patient["id"], bill_id)
    try:
        result = repo.decide_medicine_bill(conn, patient["id"], bill_id, user["id"], approve, **kw)
        repo.audit(conn, repo.clinician_actor(user), "MEDICINE_BILL_APPROVED" if approve else "MEDICINE_BILL_REJECTED",
                   patient_id=patient["id"], resource_type="external_report", resource_id=bill_id,
                   detail=result["message"])
        conn.commit()
    except repo.RepoError as e:
        conn.rollback()
        raise HTTPException(409 if "already been reviewed" in str(e) else 422, str(e))
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise HTTPException(409, str(e))
    return {**_external_out(repo.get_external_report(conn, patient["id"], x["id"], "medicine_bill")),
            "notification": result}


@router.post("/patients/{patient_id}/medicine-bills/{bill_id}/approve")
def approve_medicine_bill(bill_id: str, body: BillApproveIn, patient=Depends(patient_for_clinician),
                          user=Depends(current_clinician), conn=Depends(get_conn)):
    if body.purchase_date:
        bill = _bill_or_404(conn, patient["id"], bill_id)
        uploaded = datetime.fromisoformat(bill["upload_date"]).astimezone().date()
        if body.purchase_date > uploaded:
            raise HTTPException(422, "The purchase date can't be after the bill was uploaded.")
        if body.purchase_date < uploaded - timedelta(days=365):
            raise HTTPException(422, "The purchase date is more than a year before the upload. Please check the bill.")
    return _decide_bill(conn, patient, user, bill_id, True, purchase_date=body.purchase_date)


@router.post("/patients/{patient_id}/medicine-bills/{bill_id}/reject")
def reject_medicine_bill(bill_id: str, body: BillRejectIn, patient=Depends(patient_for_clinician),
                         user=Depends(current_clinician), conn=Depends(get_conn)):
    return _decide_bill(conn, patient, user, bill_id, False, reject_code=body.reason, reject_note=body.note)


# ================================================================== "Link to Report" for manual values

@router.get("/patients/{patient_id}/linkable-reports")
def linkable_reports(field: Literal["sugar", "hba1c", "hemoglobin", "egfr"] | None = None, all: bool = False,
                     patient=Depends(patient_for_clinician), conn=Depends(get_conn)):
    """The picker: this patient's uploads that fit the field (or all of them), newest test first."""
    found = repo.linkable_reports(conn, patient["id"], field, all)
    return {"total": found["total"], "reports": [_external_out(x) for x in found["reports"]]}


@router.get("/patients/{patient_id}/manual-values")
def manual_values(patient=Depends(patient_for_clinician), conn=Depends(get_conn)):
    return rl.list_values(conn, patient["id"])


class LinkIn(BaseModel):
    document_id: str | None = None             # None = "No report - this is manual only"


@router.put("/patients/{patient_id}/manual-values/{event_id}/link")
def change_manual_link(event_id: int, body: LinkIn, patient=Depends(patient_for_clinician),
                       user=Depends(current_clinician), conn=Depends(get_conn)):
    """Change which report a saved manual value came from: updates the same row and records the change."""
    try:
        change = repo.set_manual_link(conn, patient["id"], event_id, body.document_id or None, user["id"])
        repo.audit(conn, repo.clinician_actor(user), "VALUE_LINK_CHANGED", patient_id=patient["id"],
                   resource_type="clinical_event", resource_id=str(event_id),
                   detail=f"report {change['from'] or 'none'} -> {change['to'] or 'none'}")
        conn.commit()
    except repo.RepoError as e:
        conn.rollback()
        raise HTTPException(409 if "already linked" in str(e) else 422, str(e))
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise HTTPException(422, str(e))
    return next(v for v in rl.list_values(conn, patient["id"]) if v["id"] == event_id)


# ================================================================== care team & access log

class ShareIn(BaseModel):
    clinician_code: str = Field(max_length=20)


@router.get("/patients/{patient_id}/care-team")
def get_care_team(patient=Depends(patient_for_clinician), conn=Depends(get_conn)):
    return repo.care_team(conn, patient["id"])


@router.post("/patients/{patient_id}/care-team")
def add_to_care_team(body: ShareIn, patient=Depends(patient_for_clinician), user=Depends(current_clinician),
                     conn=Depends(get_conn)):
    """Only someone already on the care team can add a colleague, by their Clinician ID."""
    row = repo.find_active_clinician_by_code(conn, body.clinician_code)
    if row is None:
        raise HTTPException(422, "No clinician with that ID.")
    repo.grant_access(conn, row["id"], patient["id"], granted_by=user["id"])
    repo.audit(conn, repo.clinician_actor(user), "CARE_TEAM_ADDED", patient_id=patient["id"], resource_type="clinical_user",
               resource_id=row["id"], detail=f"Added {row['full_name']} ({row['clinician_code']})")
    conn.commit()
    return repo.care_team(conn, patient["id"])


@router.delete("/patients/{patient_id}/care-team/{clinician_code}")
def remove_from_care_team(clinician_code: str, patient=Depends(patient_for_clinician), user=Depends(current_clinician),
                          conn=Depends(get_conn)):
    """Revoke a clinician's access to this patient. Takes effect on their next request.
    The last person with access can't be removed, so the patient is never left with no one able to see them."""
    team = repo.care_team(conn, patient["id"])
    code = clinician_code.strip().upper()
    if not any(m["clinician_code"] == code for m in team):
        raise HTTPException(404, "That clinician does not have access to this patient.")
    if len(team) == 1:
        raise HTTPException(409, "This is the only person with access. Give someone else access before removing it.")
    row = repo.find_active_clinician_by_code(conn, code)
    repo.revoke_access(conn, row["id"], patient["id"])
    repo.audit(conn, repo.clinician_actor(user), "CARE_TEAM_REMOVED", patient_id=patient["id"], resource_type="clinical_user",
               resource_id=row["id"], detail=f"Removed {row['full_name']} ({row['clinician_code']})")
    conn.commit()
    return repo.care_team(conn, patient["id"])


@router.get("/patients/{patient_id}/access-log")
def access_log(patient=Depends(patient_for_clinician), conn=Depends(get_conn)):
    return repo.patient_audit(conn, patient["id"])


# ================================================================== other hospital via ABHA (mock ABDM)

class ConsentIn(BaseModel):
    abha_number: str = Field(max_length=20)
    hi_types: list[str]


@router.get("/abdm/hi-types")
def hi_types(user=Depends(current_clinician)):
    return [{"code": k, "label": v} for k, v in abdm.HI_TYPES.items()]


@router.post("/patients/{patient_id}/consent-requests")
def create_consent(body: ConsentIn, patient=Depends(patient_for_clinician), user=Depends(current_clinician),
                   conn=Depends(get_conn)):
    try:
        cid = abdm.create_request(conn, patient["id"], body.abha_number, body.hi_types, now(), today(), requested_by=user)
    except abdm.ConsentError as e:
        raise HTTPException(422, str(e))
    return abdm.get_request(conn, cid)


@router.get("/patients/{patient_id}/consent-requests")
def list_consents(patient=Depends(patient_for_clinician), conn=Depends(get_conn)):
    return abdm.list_requests(conn, now(), patient["id"])


@router.post("/patients/{patient_id}/consent-requests/{consent_id}/simulate-expiry")
def simulate_expiry(consent_id: int, patient=Depends(patient_for_clinician), conn=Depends(get_conn)):
    if not abdm.simulate_expiry(conn, patient["id"], consent_id, now()):
        raise HTTPException(404, "No waiting request with that id.")
    return abdm.get_request(conn, consent_id)


# ================================================================== Pending reports: ONE inbox for everything waiting
# Everything a patient sent from the app that still needs a decision: outside-lab test reports, medicine bills,
# photos (prescriptions, lab reports, meter photos) and typed readings. The Overview uses the same list.

GROUP_OF_KIND = {"external": "test_report", "results": "photo", "bill": "medicine_bill", "document": "photo", "entry": "reading"}


def _pending_items(conn, ids: list[str], who) -> list[dict]:
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    items = []

    # outside-lab test reports and medicine bills (split-screen review pages)
    for x in repo.pending_external_reports(conn, ids):
        items.append({**who(x["patient_id"]), "kind": "external", "id": x["id"], "at": x["upload_date"],
                      "label": f"{x['test_label']} report from an outside lab",
                      "detail": f"Test on {fmt(date.fromisoformat(x['test_date']))}",
                      "file": {"source": "external", "id": x["id"], "content_type": x["content_type"]}})
    for x in repo.pending_external_reports(conn, ids, "medicine_bill"):
        items.append({**who(x["patient_id"]), "kind": "bill", "id": x["id"], "at": x["upload_date"],
                      "label": "Medicine bill", "detail": "Diabetes medicine",
                      "file": {"source": "external", "id": x["id"], "content_type": x["content_type"]}})

    # photos and files: prescriptions, lab reports, meter photos (a meter photo carries its sugar reading)
    for r in conn.execute(
            f"SELECT d.id, d.patient_id, d.document_type, d.description, d.uploaded_at, d.content_type, "
            f"r.id AS report_id, EXISTS (SELECT 1 FROM lab_test_results t WHERE t.report_id = r.id) AS has_values, "
            f"e.value AS reading, e.unit AS reading_unit FROM patient_documents d "
            f"LEFT JOIN lab_reports r ON r.document_id = d.id AND r.patient_id = d.patient_id "
            f"LEFT JOIN clinical_events e ON e.document_id = d.id AND e.patient_id = d.patient_id "
            f"WHERE d.review_status = 'pending' AND d.patient_id IN ({marks}) ORDER BY d.uploaded_at", ids):
        t = r["document_type"]
        label = {"lab_report": f"Lab report: {r['description'] or 'report'}", "prescription": "Prescription",
                 "glucometer_photo": "Sugar reading with meter photo"}.get(t, "Document")
        detail = (f"Reading {r['reading']:g} {r['reading_unit'] or ''}".strip() if t == "glucometer_photo" and r["reading"] is not None
                  else "Values not entered yet" if t == "lab_report" and not r["has_values"] else None)
        items.append({**who(r["patient_id"]), "kind": "document", "id": r["id"], "at": r["uploaded_at"],
                      "document_type": t, "label": label, "detail": detail, "report_id": r["report_id"],
                      "file": {"source": "document", "id": r["id"], "content_type": r["content_type"]}})

    # lab-report photos already confirmed whose values still have to be typed in
    for r in conn.execute(
            f"SELECT r.id, r.patient_id, r.report_type, r.report_date, r.document_id, d.content_type FROM lab_reports r "
            f"JOIN patient_documents d ON d.id = r.document_id AND d.patient_id = r.patient_id "
            f"WHERE r.source = 'patient_upload' AND d.review_status = 'confirmed' "
            f"AND NOT EXISTS (SELECT 1 FROM lab_test_results t WHERE t.report_id = r.id) AND r.patient_id IN ({marks})", ids):
        items.append({**who(r["patient_id"]), "kind": "results", "id": r["id"], "at": r["report_date"],
                      "label": f"Lab report: {r['report_type'] or 'report'}", "detail": "Confirmed · values not entered yet",
                      "file": {"source": "document", "id": r["document_id"], "content_type": r["content_type"]}})

    # readings typed in with no photo (sugar, "medicine not collected yet")
    for r in conn.execute(
            f"SELECT id, patient_id, name, value, unit, effective_date, created_at FROM clinical_events "
            f"WHERE source = 'patient_upload' AND confidence = 'medium' AND document_id IS NULL "
            f"AND linked_document_id IS NULL AND patient_id IN ({marks})", ids):
        if r["name"] == "Refill check-in":
            label, detail = "Medicine refill", "Says the medicine is not collected yet"
        else:
            label = "Sugar reading" if (r["name"] or "").lower() in ("glucose", "fasting glucose") else (r["name"] or "Reading")
            detail = f"{r['value']:g} {r['unit'] or ''}".strip() if r["value"] is not None else None
        items.append({**who(r["patient_id"]), "kind": "entry", "id": r["id"], "at": r["created_at"],
                      "reading_date": r["effective_date"], "label": label, "detail": detail, "file": None})

    for it in items:
        it["group"] = GROUP_OF_KIND[it["kind"]]
    items.sort(key=lambda it: it["at"])                  # oldest first: the longest wait at the top
    return items


@router.get("/pending-items")
def pending_items(user=Depends(current_clinician), conn=Depends(get_conn)):
    """Pending reports: everything the clinician's OWN patients sent that is waiting for a decision."""
    ids = repo.patient_ids_for_clinician(conn, user["id"])
    names = {r["id"]: (r["full_name"], r["patient_code"]) for r in conn.execute(
        f"SELECT id, full_name, patient_code FROM patients WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
    return _pending_items(conn, ids, lambda pid: {"patient_id": pid, "patient_name": names[pid][0],
                                                  "patient_code": names[pid][1]})


@router.get("/worklist")
def worklist(user=Depends(current_clinician), conn=Depends(get_conn)):
    """Overview page: real, actionable items across the clinician's OWN patients only."""
    as_of = today()
    ids = repo.patient_ids_for_clinician(conn, user["id"])
    if not ids:
        return {"as_of": as_of, "upcoming_visits": [], "reviews": [], "consents": [], "activity": []}
    marks = ",".join("?" * len(ids))
    names = {r["id"]: (r["full_name"], r["patient_code"]) for r in
             conn.execute(f"SELECT id, full_name, patient_code FROM patients WHERE id IN ({marks})", ids)}

    def who(pid):
        return {"patient_id": pid, "patient_name": names[pid][0], "patient_code": names[pid][1]}

    upcoming = [{**who(r["patient_id"]), "date": r["effective_date"], "name": r["name"]} for r in conn.execute(
        f"SELECT patient_id, effective_date, name FROM clinical_events WHERE event_type = 'visit' AND status = 'ordered' "
        f"AND LOWER(COALESCE(name, '')) NOT LIKE '%emergency%' AND effective_date BETWEEN ? AND ? AND patient_id IN ({marks}) "
        f"ORDER BY effective_date", (as_of.isoformat(), (as_of + timedelta(days=30)).isoformat(), *ids))]
    # Appointments patients booked themselves (confirmed straight away), and ones waiting for a new time.
    upcoming += [{**who(r["patient_id"]), "date": r["date"], "time": r["start_time"], "status": r["status"],
                  "name": (f"{appt.clock(r['start_time'])} · {r['doctor_name']}" if r["status"] == "confirmed"
                           else f"Needs a new time · {r['doctor_name']}")} for r in conn.execute(
        f"SELECT a.patient_id, a.status, s.date, s.start_time, d.name AS doctor_name FROM appointments a "
        f"JOIN appointment_slots s ON s.id = a.slot_id JOIN doctors d ON d.id = a.doctor_id "
        f"WHERE a.status IN ('confirmed', 'needs_reschedule') AND s.date BETWEEN ? AND ? AND a.patient_id IN ({marks})",
        (as_of.isoformat(), (as_of + timedelta(days=30)).isoformat(), *ids))]
    upcoming.sort(key=lambda v: (str(v["date"]), v.get("time", "")))

    reviews = _pending_items(conn, ids, who)

    now_dt = now()
    consents = [{**who(c["patient_id"]), "id": c["id"], "hip_name": c["hip_name"], "status": c["status"],
                 "created_at": c["created_at"], "expires_at": c["expires_at"]}
                for pid in ids for c in abdm.list_requests(conn, now_dt, pid) if c["status"] == "REQUESTED"]

    activity = [{**who(r["patient_id"]), "at": r["effective_date"], "label": r["label"]} for r in conn.execute(
        f"SELECT patient_id, effective_date, CASE WHEN value IS NOT NULL THEN name || ': ' || value || ' ' || COALESCE(unit, '') "
        f"ELSE name END AS label FROM clinical_events WHERE source = 'patient_upload' AND linked_document_id IS NULL "
        f"AND effective_date >= ? "
        f"AND patient_id IN ({marks}) UNION ALL "
        f"SELECT patient_id, substr(uploaded_at, 1, 10), CASE document_type WHEN 'lab_report' THEN 'Sent lab report: ' || COALESCE(description, '') "
        f"WHEN 'prescription' THEN 'Sent prescription' ELSE 'Sent photo' END FROM patient_documents "
        f"WHERE source = 'patient_upload' AND uploaded_at >= ? AND patient_id IN ({marks}) UNION ALL "
        f"SELECT patient_id, substr(upload_date, 1, 10), CASE document_type WHEN 'medicine_bill' THEN 'Sent medicine bill' "
        f"ELSE 'Sent ' || COALESCE(test_name, test_type) || ' report from an outside lab' END "
        f"FROM external_reports WHERE upload_date >= ? AND patient_id IN ({marks}) "
        f"ORDER BY 2 DESC LIMIT 100", ((as_of - timedelta(days=14)).isoformat(), *ids, (as_of - timedelta(days=14)).isoformat(), *ids,
                                      (as_of - timedelta(days=14)).isoformat(), *ids))]
    return {"as_of": as_of, "upcoming_visits": upcoming, "reviews": reviews, "consents": consents, "activity": activity}


@router.get("/access-log")
def my_access_log(patient_id: str | None = None, user=Depends(current_clinician), conn=Depends(get_conn)):
    """Settings > Access log: activity on the clinician's own patients (all, or one they can access)."""
    return repo.access_log_for_clinician(conn, user["id"], patient_id)


@router.get("/consent-log")
def consent_log(user=Depends(current_clinician), conn=Depends(get_conn)):
    return abdm.audit_log(conn, now(), repo.patient_ids_for_clinician(conn, user["id"]))


@router.get("/config")
def get_config(user=Depends(current_clinician)):
    return asdict(DEFAULT_CONFIG)
