"""Consultation Brief API.

Clinical team: every brief route checks the patient's care team (only their own patients).
Doctor: only briefs sent to this doctor; the content only after the doctor confirmed the patient's identity.
Polling: the queue and the update check answer 304 when nothing changed (ETag).
Errors: {"detail": {"code": ..., "message": ..., ...}}.
"""

from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .. import brief as briefs
from ..deps import current_care_team, current_doctor, get_conn, today

router = APIRouter(prefix="/api", tags=["consultation brief"])


def _fail(e: briefs.BriefError) -> JSONResponse:
    return JSONResponse({"detail": {"code": e.code, "message": e.message, **(e.payload or {})}}, status_code=e.status)


def _cached(request: Request, payload) -> Response:
    tag = briefs.etag(payload)
    headers = {"ETag": tag, "Cache-Control": "private, no-cache"}
    if request.headers.get("if-none-match") == tag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(payload, headers=headers)


# ================================================================== clinical team

@router.get("/clinic/briefs/today")
def briefs_for_day(day: date | None = None, user=Depends(current_care_team), conn=Depends(get_conn)):
    """Today's (or another day's) appointments of the clinician's own patients, with readiness and brief status."""
    d = day or today()
    return {"date": d, "today": today(), "appointments": briefs.day_list(conn, user, d)}


@router.post("/appointments/{appointment_id}/brief/draft")
def draft_brief(appointment_id: str, user=Depends(current_care_team), conn=Depends(get_conn)):
    try:
        return briefs.draft(conn, appointment_id, user, today())
    except briefs.BriefError as e:
        return _fail(e)


@router.get("/briefs/{brief_id}")
def get_brief(brief_id: str, user=Depends(current_care_team), conn=Depends(get_conn)):
    try:
        return briefs.clinic_view(conn, brief_id, user, today())
    except briefs.BriefError as e:
        return _fail(e)


class BriefEditIn(BaseModel):
    row_version: int
    hide: list[str] = Field(default_factory=list, max_length=50)
    unhide: list[str] = Field(default_factory=list, max_length=50)
    pin: list[str] = Field(default_factory=list, max_length=50)
    unpin: list[str] = Field(default_factory=list, max_length=50)
    note: str | None = Field(default=None, max_length=400)


@router.patch("/briefs/{brief_id}")
def edit_brief(brief_id: str, body: BriefEditIn, user=Depends(current_care_team), conn=Depends(get_conn)):
    changes = body.model_dump(exclude_unset=True, exclude={"row_version"})
    try:
        return briefs.edit(conn, brief_id, user, body.row_version, changes, today())
    except briefs.BriefError as e:
        return _fail(e)


class SendIn(BaseModel):
    allow_not_today: bool = False


@router.post("/briefs/{brief_id}/send")
def send_brief(brief_id: str, body: SendIn | None = None, user=Depends(current_care_team), conn=Depends(get_conn)):
    try:
        return briefs.send(conn, brief_id, user, today(), bool(body and body.allow_not_today))
    except briefs.BriefError as e:
        return _fail(e)


class SendReadyIn(BaseModel):
    brief_ids: list[str] | None = Field(default=None, max_length=200)     # leave out for the preview list


@router.post("/clinic/briefs/send-ready")
def send_ready(body: SendReadyIn, user=Depends(current_care_team), conn=Depends(get_conn)):
    return briefs.send_ready(conn, user, today(), body.brief_ids)


# ================================================================== doctor

@router.get("/doctor/queue")
def doctor_queue(request: Request, day: date | None = None, user=Depends(current_doctor), conn=Depends(get_conn)):
    return _cached(request, {"date": (day or today()).isoformat(), "queue": briefs.queue(conn, user, day or today())})


@router.get("/doctor/inbox")
def doctor_inbox(request: Request, user=Depends(current_doctor), conn=Depends(get_conn)):
    """Messages from the clinic team: "<clinic> sent a report about <patient>"."""
    return _cached(request, briefs.inbox(conn, user, today()))


@router.post("/doctor/briefs/{brief_id}/call")
def call_patient(brief_id: str, user=Depends(current_doctor), conn=Depends(get_conn)):
    try:
        return briefs.call(conn, brief_id, user)
    except briefs.BriefError as e:
        return _fail(e)


class IdentityIn(BaseModel):
    patient_code: str = Field(max_length=20)
    date_of_birth: date | None = None
    age: int | None = Field(default=None, ge=0, le=130)


@router.post("/doctor/briefs/{brief_id}/confirm-identity")
def confirm_identity(brief_id: str, body: IdentityIn, user=Depends(current_doctor), conn=Depends(get_conn)):
    try:
        return briefs.confirm_identity(conn, brief_id, user, body.patient_code, body.date_of_birth, body.age, today())
    except briefs.BriefError as e:
        return _fail(e)


@router.get("/doctor/briefs/{brief_id}")
def doctor_brief(brief_id: str, user=Depends(current_doctor), conn=Depends(get_conn)):
    try:
        return briefs.for_doctor(conn, brief_id, user)
    except briefs.BriefError as e:
        return _fail(e)


@router.post("/doctor/briefs/{brief_id}/acknowledge")
def acknowledge(brief_id: str, user=Depends(current_doctor), conn=Depends(get_conn)):
    try:
        return briefs.acknowledge(conn, brief_id, user)
    except briefs.BriefError as e:
        return _fail(e)


@router.post("/doctor/briefs/{brief_id}/complete")
def complete(brief_id: str, user=Depends(current_doctor), conn=Depends(get_conn)):
    try:
        return briefs.complete(conn, brief_id, user, today())
    except briefs.BriefError as e:
        return _fail(e)


@router.get("/doctor/briefs/{brief_id}/updates")
def brief_updates(brief_id: str, request: Request, user=Depends(current_doctor), conn=Depends(get_conn)):
    try:
        return _cached(request, briefs.updates(conn, brief_id, user, today()))
    except briefs.BriefError as e:
        return _fail(e)
