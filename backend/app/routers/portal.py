"""Patient portal API. The patient is ALWAYS taken from the session cookie - no route here accepts
a patient id, so one patient can never act on another's record by editing a URL."""

from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import abdm_mock as abdm
from .. import repo, storage
from ..config import DEFAULT_CONFIG
from ..deps import current_patient, get_conn, now, read_upload, today
from ..detection import fmt
from ..services import current_medicine

router = APIRouter(prefix="/api/me", tags=["patient portal"])

SELF = "Patient (self-reported)"


def _actor(patient: dict) -> repo.Actor:
    return repo.Actor("patient", patient["id"], f"{patient['full_name']} (patient)")


class ConsentAnswer(BaseModel):
    approve: bool


class RefillIn(BaseModel):
    taken: bool
    refill_date: date | None = None


@router.get("/consents")
def my_consents(patient=Depends(current_patient), conn=Depends(get_conn)):
    return abdm.list_requests(conn, now(), patient["id"], status="REQUESTED")


@router.post("/consents/{consent_id}")
def answer_consent(consent_id: int, body: ConsentAnswer, patient=Depends(current_patient), conn=Depends(get_conn)):
    try:
        req = abdm.respond(conn, consent_id, patient["id"], body.approve, now(), today())
    except abdm.ConsentError as e:
        raise HTTPException(409, str(e))
    msg = (f"Thank you. {req['hip_name']} will share your records with your care team." if body.approve
           else "Okay. Nothing has been shared.")
    return {"message": msg, **req}


@router.post("/refill")
def confirm_refill(body: RefillIn, patient=Depends(current_patient), conn=Depends(get_conn)):
    as_of = today()
    if not body.taken:
        repo.insert_event(conn, patient["id"], "engagement_log", as_of, "patient_upload", "medium", "resulted", SELF,
                          name="Refill check-in", value=0, unit="check-in", note="Patient says refill not collected yet")
        conn.commit()
        return {"message": "Thank you. We have noted that you have not collected your medicine yet. "
                           "Your care team can help if you need it."}
    # A collected refill is recorded only with its bill (POST /me/medicine-bills) - never as text alone.
    raise HTTPException(422, "Please upload a photo or PDF of your medicine bill to record a refill.")


# ------------------------------------------------------------------ medicine purchase verification (bill upload)

def _bill_public(x: dict) -> dict:
    status_text = repo.BILL_STATUS_TEXT[x["status"]]
    return {"id": x["id"], "disease": x["disease"], "upload_date": x["upload_date"], "content_type": x["content_type"],
            "status": x["status"], "reject_reason": x["reject_reason"], "purchase_date": x["purchase_date"],
            "status_text": f"{status_text} — {x['reject_reason']}" if x["status"] == "reviewed_rejected" else status_text}


@router.post("/medicine-bills")
async def upload_medicine_bill(file: UploadFile | None = File(None), patient=Depends(current_patient),
                               conn=Depends(get_conn)):
    """The bill is compulsory. No date is taken from the patient: the server's time is the upload time."""
    if file is None or not file.filename:
        raise HTTPException(422, "Please attach a photo or PDF of your medicine bill.")
    data = await read_upload(file)
    medicine = current_medicine(repo.load_patient_record(conn, patient["id"]))
    bill_id = repo.create_medicine_bill(conn, patient["id"], file.filename, file.content_type, data, medicine,
                                        DEFAULT_CONFIG.default_days_supply, SELF, today())
    repo.audit(conn, _actor(patient), "MEDICINE_BILL_UPLOADED", patient_id=patient["id"], resource_type="external_report",
               resource_id=bill_id)
    conn.commit()
    return {"id": bill_id, "message": "Your bill has been submitted. Your care team will verify it shortly."}


@router.get("/medicine-bills")
def my_medicine_bills(patient=Depends(current_patient), conn=Depends(get_conn)):
    return [_bill_public(x) for x in repo.list_medicine_bills(conn, patient["id"])]


@router.get("/medicine-bills/{bill_id}/file")
def my_medicine_bill_file(bill_id: str, patient=Depends(current_patient), conn=Depends(get_conn)):
    x = repo.get_external_report(conn, patient["id"], bill_id, "medicine_bill")
    if x is None:
        raise HTTPException(404, "Bill not found.")
    return Response(storage.read(x["storage_key"]), media_type=x["content_type"],
                    headers={"Content-Disposition": f'inline; filename="bill{storage.EXTENSIONS[x["content_type"]]}"',
                             "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/notifications")
def my_notifications(patient=Depends(current_patient), conn=Depends(get_conn)):
    return repo.list_notifications(conn, patient["id"])


@router.post("/notifications/{notification_id}/read")
def read_notification(notification_id: int, patient=Depends(current_patient), conn=Depends(get_conn)):
    repo.mark_notification_read(conn, patient["id"], notification_id)
    conn.commit()
    return {"ok": True}


@router.post("/sugar")
async def log_sugar(value: float = Form(..., ge=20, le=600), photo: UploadFile | None = File(None),
                    patient=Depends(current_patient), conn=Depends(get_conn)):
    data = await read_upload(photo) if photo and photo.filename else None
    doc_id = None
    if data:
        doc_id = repo.create_document(conn, patient["id"], "glucometer_photo", photo.filename, photo.content_type, data,
                                      "patient_upload", description="Glucometer reading",
                                      uploaded_by_patient_id=patient["id"])
        repo.audit(conn, _actor(patient), "DOCUMENT_UPLOADED", patient_id=patient["id"], resource_type="document",
                   resource_id=doc_id)
    repo.insert_event(conn, patient["id"], "engagement_log", today(), "patient_upload", "medium", "resulted", SELF,
                      name="Glucose", value=value, unit="mg/dL", note="Meter photo attached" if doc_id else None,
                      document_id=doc_id)
    conn.commit()
    return {"message": f"Thank you. Your sugar reading of {value:g} mg/dL is saved"
                       f"{' with a photo of your meter' if doc_id else ''}."}


@router.post("/documents")
async def upload_document(kind: Literal["prescription", "lab_report"] = Form(...), file: UploadFile = File(...),
                          description: str | None = Form(None, max_length=100),
                          patient=Depends(current_patient), conn=Depends(get_conn)):
    description = (description or "").strip() or None
    if kind == "lab_report" and (description is None or len(description) < 2):
        raise HTTPException(422, "Please write what type of lab report this is (for example: HbA1c, kidney test).")
    if kind == "prescription":
        description = None
    data = await read_upload(file)
    doc_id = repo.create_document(conn, patient["id"], kind, file.filename, file.content_type, data, "patient_upload",
                                  description=description, uploaded_by_patient_id=patient["id"])
    if kind == "lab_report":
        # Listed under Lab reports right away; the care team enters its values from the file.
        repo.create_lab_report(conn, patient["id"], "Uploaded by patient", today(), "patient_upload",
                               document_id=doc_id, report_type=description)
    repo.audit(conn, _actor(patient), "DOCUMENT_UPLOADED", patient_id=patient["id"], resource_type="document",
               resource_id=doc_id)
    conn.commit()
    label = "prescription" if kind == "prescription" else f"lab report ({description})"
    return {"id": doc_id, "message": f"Thank you. Your {label} was sent to your care team."}


# ------------------------------------------------------------------ External Report Review: patient upload
# A test done OUTSIDE the hospital. The patient sends the report (type, date, file) - never a value;
# the care team reads it and enters the value, linked to this exact document.

EXTERNAL_STATUS_TEXT = {"pending_review": "Waiting for your care team", "reviewed": "Checked by your care team",
                        "rejected": "Not accepted"}


def _external_public(x: dict) -> dict:
    return {"id": x["id"], "test_type": x["test_type"], "test_label": x["test_label"], "test_date": x["test_date"],
            "upload_date": x["upload_date"], "content_type": x["content_type"], "status": x["status"],
            "status_text": EXTERNAL_STATUS_TEXT[x["status"]],
            "result": {"value": x["value"], "unit": x["unit"]} if x["status"] == "reviewed" else None}


@router.post("/external-reports")
async def upload_external_report(test_type: Literal["HbA1c", "Fasting Sugar", "PP Sugar", "Other"] = Form(...),
                                 test_date: date = Form(...), file: UploadFile = File(...),
                                 test_name: str | None = Form(None, max_length=80),
                                 patient=Depends(current_patient), conn=Depends(get_conn)):
    as_of = today()
    if test_date > as_of:
        raise HTTPException(422, "The test date cannot be in the future.")
    if test_date < as_of - timedelta(days=3 * 365):
        raise HTTPException(422, "Please send a report from the last 3 years.")
    test_name = (test_name or "").strip() or None
    if test_type == "Other" and (test_name is None or len(test_name) < 2):
        raise HTTPException(422, "Please write which test this is.")
    data = await read_upload(file)
    report_id = repo.create_external_report(conn, patient["id"], test_type, test_date, file.filename, file.content_type,
                                            data, test_name=test_name)
    repo.audit(conn, _actor(patient), "EXTERNAL_REPORT_UPLOADED", patient_id=patient["id"],
               resource_type="external_report", resource_id=report_id,
               detail=f"{repo.external_test_label(test_type, test_name)} test on {fmt(test_date)}")
    conn.commit()
    return {"id": report_id, "message": "Report uploaded. Your care team will review it shortly."}


@router.get("/external-reports")
def my_external_reports(patient=Depends(current_patient), conn=Depends(get_conn)):
    return [_external_public(x) for x in repo.list_external_reports(conn, patient["id"])]


@router.get("/external-reports/{report_id}/file")
def my_external_report_file(report_id: str, patient=Depends(current_patient), conn=Depends(get_conn)):
    x = repo.get_external_report(conn, patient["id"], report_id)
    if x is None:
        raise HTTPException(404, "Report not found.")
    return Response(storage.read(x["storage_key"]), media_type=x["content_type"],
                    headers={"Content-Disposition": f'inline; filename="report{storage.EXTENSIONS[x["content_type"]]}"',
                             "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


# ------------------------------------------------------------------ age (asked once at sign-in if no date of birth)

class AgeIn(BaseModel):
    age: int = Field(ge=1, le=120)


@router.post("/age")
def give_age(body: AgeIn, patient=Depends(current_patient), conn=Depends(get_conn)):
    repo.set_reported_age(conn, patient["id"], body.age, today())
    repo.audit(conn, _actor(patient), "PATIENT_AGE_GIVEN", patient_id=patient["id"], resource_type="patient",
               resource_id=patient["id"], detail=f"Age {body.age}")
    conn.commit()
    return {"ok": True, "age": body.age, "needs_age": False}


# ------------------------------------------------------------------ profile & "My doctors"

@router.get("/profile")
def my_profile(patient=Depends(current_patient)):
    return {k: patient[k] for k in ("patient_code", "full_name", "date_of_birth", "sex", "abha_number")} | {
        "phone_hint": "•••••" + patient["phone"][-4:]}


class DoctorIn(BaseModel):
    clinician_code: str


@router.get("/care-team")
def my_doctors(patient=Depends(current_patient), conn=Depends(get_conn)):
    return repo.care_team(conn, patient["id"])


@router.post("/care-team")
def add_my_doctor(body: DoctorIn, patient=Depends(current_patient), conn=Depends(get_conn)):
    """The patient gives a doctor access to their record, using the doctor's Clinician ID."""
    doctor = repo.find_active_clinician_by_code(conn, body.clinician_code)
    if doctor is None:
        raise HTTPException(422, "We could not find a doctor with that ID. Please check it with your clinic.")
    repo.grant_access(conn, doctor["id"], patient["id"], granted_by=None)
    repo.audit(conn, _actor(patient), "CARE_TEAM_ADDED", patient_id=patient["id"], resource_type="clinical_user",
               resource_id=doctor["id"], detail=f"Patient added {doctor['full_name']} ({doctor['clinician_code']})")
    conn.commit()
    return repo.care_team(conn, patient["id"])


@router.delete("/care-team/{clinician_code}")
def remove_my_doctor(clinician_code: str, patient=Depends(current_patient), conn=Depends(get_conn)):
    """The patient withdraws a doctor's access. Takes effect on the doctor's very next request."""
    doctor = repo.find_active_clinician_by_code(conn, clinician_code)
    if doctor is None or not repo.revoke_access(conn, doctor["id"], patient["id"]):
        raise HTTPException(404, "That doctor is not on your list.")
    repo.audit(conn, _actor(patient), "CARE_TEAM_REMOVED", patient_id=patient["id"], resource_type="clinical_user",
               resource_id=doctor["id"], detail=f"Patient removed {doctor['full_name']} ({doctor['clinician_code']})")
    conn.commit()
    return repo.care_team(conn, patient["id"])


@router.get("/documents")
def my_documents(patient=Depends(current_patient), conn=Depends(get_conn)):
    return [{k: d[k] for k in ("id", "document_type", "description", "file_name", "uploaded_at", "review_status")}
            for d in repo.list_documents(conn, patient["id"])]


@router.get("/documents/{document_id}/file")
def my_document_file(document_id: str, patient=Depends(current_patient), conn=Depends(get_conn)):
    doc = repo.get_document(conn, patient["id"], document_id)       # someone else's id -> None -> 404
    if doc is None:
        raise HTTPException(404, "Document not found.")
    repo.audit(conn, _actor(patient), "DOCUMENT_VIEWED", patient_id=patient["id"], resource_type="document",
               resource_id=document_id)
    conn.commit()
    return Response(storage.read(doc["storage_key"]), media_type=doc["content_type"],
                    headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
