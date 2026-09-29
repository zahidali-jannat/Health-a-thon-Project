"""Uploaded reports for the care team: the one list the "link a report" picker uses, label corrections,
care-team uploads, editing a care-team value (and its link), and opening a file through a short-lived signed link.

Every patient route depends on patient_for_clinician: only this patient's care team, only this patient's rows.
"""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .. import repo, storage
from .. import report_links as rl
from ..deps import current_clinician, get_conn, patient_for_clinician, read_upload, today

router = APIRouter(prefix="/api", tags=["uploaded reports"])

ReportType = Literal["egfr", "hemoglobin", "hba1c", "sugar", "other"]


def _fail(e: Exception) -> HTTPException:
    return HTTPException(409 if "already linked" in str(e) else 422, str(e))


@router.get("/patients/{patient_id}/uploaded-reports")
def uploaded_reports(request: Request, patient=Depends(patient_for_clinician), conn=Depends(get_conn)):
    """Lightweight list of every lab report this patient uploaded (no file contents). Weak ETag: an unchanged list
    answers 304, so the page can re-check cheaply."""
    reports = rl.list_reports(conn, patient["id"])
    tag = rl.etag(reports)
    headers = {"ETag": tag, "Cache-Control": "private, no-cache"}
    if request.headers.get("if-none-match") == tag:
        return Response(status_code=304, headers=headers)
    return JSONResponse({"reports": reports, "total": len(reports)}, headers=headers)


class ReportFix(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    report_type: ReportType | None = None
    test_date: date | None = None


@router.patch("/patients/{patient_id}/uploaded-reports/{report_id}")
def correct_report(report_id: str, body: ReportFix, patient=Depends(patient_for_clinician),
                   user=Depends(current_clinician), conn=Depends(get_conn)):
    try:
        return rl.update_report(conn, patient["id"], report_id, body.model_dump(exclude_unset=True), user, today())
    except repo.RepoError as e:
        raise _fail(e) from None


@router.post("/patients/{patient_id}/uploaded-reports", status_code=201)
async def care_team_upload(file: UploadFile = File(...), display_name: str = Form(..., max_length=120),
                           report_type: ReportType | None = Form(None), test_date: date | None = Form(None),
                           patient=Depends(patient_for_clinician), user=Depends(current_clinician), conn=Depends(get_conn)):
    """A report the care team uploads for this patient (e.g. brought in on paper)."""
    data = await read_upload(file)
    name = display_name.strip()
    if not name:
        raise HTTPException(422, "Please give the report a name.")
    if test_date and test_date > today():
        raise HTTPException(422, "The test date cannot be in the future.")
    doc_id = repo.create_document(conn, patient["id"], "lab_report", file.filename or "report", file.content_type, data,
                                  "care_team_upload", description=name, uploaded_by_user_id=user["id"])
    try:
        conn.execute("UPDATE patient_documents SET review_status = 'confirmed', reviewed_by = ?, reviewed_at = ? "
                     "WHERE id = ? AND patient_id = ?", (user["id"], repo.now_iso(), doc_id, patient["id"]))
        if report_type or test_date:
            conn.execute("INSERT INTO report_details (report_id, patient_id, display_name, report_type, test_date, updated_by, "
                         "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (doc_id, patient["id"], name, report_type, test_date and test_date.isoformat(), user["id"],
                          repo.now_iso()))
        repo.audit(conn, repo.clinician_actor(user), "DOCUMENT_UPLOADED", patient_id=patient["id"],
                   resource_type="document", resource_id=doc_id, new_value=name)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return rl.get_report(conn, patient["id"], doc_id)


@router.post("/patients/{patient_id}/uploaded-reports/{report_id}/file-link")
def report_file_link(report_id: str, patient=Depends(patient_for_clinician), user=Depends(current_clinician),
                     conn=Depends(get_conn)):
    """A link to the file that works for 5 minutes, for this clinician only."""
    if rl.file_of(conn, patient["id"], report_id) is None:
        raise HTTPException(404, "Report not found.")
    return rl.signed_path(report_id, patient["id"], user["id"])


@router.get("/report-files/{report_id}")
def report_file(report_id: str, patient: str, expires: int, sig: str, user=Depends(current_clinician),
                conn=Depends(get_conn)):
    """Serves the file only with a valid, unexpired signature made for THIS signed-in clinician, who must still be
    on the patient's care team. Never cached."""
    if not rl.check_signature(report_id, patient, user["id"], expires, sig) or not repo.has_access(conn, user["id"], patient):
        raise HTTPException(404, "Link expired or not valid.")
    f = rl.file_of(conn, patient, report_id)
    if f is None:
        raise HTTPException(404, "Report not found.")
    repo.audit(conn, repo.clinician_actor(user), "DOCUMENT_VIEWED", patient_id=patient, resource_type="report",
               resource_id=report_id)
    conn.commit()
    return Response(storage.read(f["storage_key"]), media_type=f["content_type"],
                    headers={"Content-Disposition": f'inline; filename="report{storage.EXTENSIONS[f["content_type"]]}"',
                             "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


class ValueEdit(BaseModel):
    value: float | None = None
    unit: str | None = Field(default=None, max_length=20)
    effective_date: date | None = None
    report_id: str | None = Field(default=None, max_length=64)      # sent as null = unlink


@router.patch("/patients/{patient_id}/manual-values/{event_id}")
def edit_value(event_id: int, body: ValueEdit, patient=Depends(patient_for_clinician), user=Depends(current_clinician),
               conn=Depends(get_conn)):
    """Edit a care-team value and/or its linked report: one transaction, audited with old and new."""
    changes = body.model_dump(exclude_unset=True)
    if "effective_date" in changes and changes["effective_date"] is not None:
        changes["effective_date"] = changes["effective_date"].isoformat()
    if not changes:
        raise HTTPException(422, "Nothing to change.")
    try:
        rl.edit_value(conn, patient["id"], event_id, changes, user, today())
    except repo.RepoError as e:
        raise _fail(e) from None
    return next(v for v in rl.list_values(conn, patient["id"]) if v["id"] == event_id)
