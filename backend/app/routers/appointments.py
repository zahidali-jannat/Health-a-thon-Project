"""Appointment booking API.

Care team (/api/...): consultation hours, a doctor's day, marking a doctor away, sending rebooking messages.
Patient (/api/me/...): the patient is always taken from the session; they book only with doctors on their
own care team. A booking that loses a race answers 409 with code "slot_taken" - never a generic error.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import appointments as appt
from ..deps import current_clinician, current_doctor, current_patient, get_conn, now, patient_for_clinician, today

clinical = APIRouter(prefix="/api", tags=["appointments (care team)"])
portal = APIRouter(prefix="/api/me", tags=["appointments (patient)"])


def _fail(e: appt.BookingError) -> JSONResponse:
    return JSONResponse({"detail": {"code": e.code, "message": e.message, **(e.payload or {})}}, status_code=e.status)


def _now_hm() -> str:
    return now().strftime("%H:%M")


def _doctor(conn, doctor_id: str) -> dict:
    d = appt.get_doctor(conn, doctor_id)
    if d is None:
        raise HTTPException(404, "Doctor not found.")
    return d


class HoursIn(BaseModel):
    working_start_time: str = Field(max_length=5)
    working_end_time: str = Field(max_length=5)
    slot_duration_minutes: int = 15
    buffer_minutes: int = 5


class BlockIn(BaseModel):
    """Clinic-local (Asia/Kolkata) dates and 24-hour times; the app converts the 12-hour + AM/PM input."""
    start_date: date
    start_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end_date: date | None = None
    end_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    full_day: bool = False
    reason: str | None = Field(default=None, max_length=200)

    def as_block(self) -> dict:
        return {"start_date": self.start_date.isoformat(), "start_time": self.start_time,
                "end_date": (self.end_date or self.start_date).isoformat(), "end_time": self.end_time,
                "full_day": self.full_day, "reason": self.reason}


class BlockConfirmIn(BlockIn):
    fingerprint: str = Field(max_length=64)                       # from the preview the pop-up showed
    checked: bool = False                                         # "I have checked the doctor, date and time (AM/PM)"
    warnings_shown: list[str] = Field(default_factory=list, max_length=10)
    changes: list[str] = Field(default_factory=list, max_length=5)   # e.g. "start changed from 2:00 AM to 2:00 PM"


class BookIn(BaseModel):
    slot_id: int


# ================================================================== care team

@clinical.get("/doctor-profile")
def my_doctor_profile(user=Depends(current_doctor), conn=Depends(get_conn)):
    doctor = appt.doctor_for_user(conn, user["id"])
    conn.commit()
    return {"doctor": doctor}


@clinical.put("/doctor-profile")
def save_my_doctor_profile(body: HoursIn, user=Depends(current_doctor), conn=Depends(get_conn)):
    try:
        return {"doctor": appt.save_doctor_profile(conn, user, body.working_start_time, body.working_end_time,
                                                   body.slot_duration_minutes, body.buffer_minutes, today())}
    except appt.BookingError as e:
        return _fail(e)


@clinical.get("/doctors")
def doctors(user=Depends(current_clinician), conn=Depends(get_conn)):
    out = appt.list_doctors(conn)
    conn.commit()
    return out


@clinical.get("/doctors/{doctor_id}/schedule")
def doctor_schedule(doctor_id: str, date: str, user=Depends(current_clinician), conn=Depends(get_conn)):
    try:
        return appt.schedule(conn, _doctor(conn, doctor_id), date, user["id"], today())
    except appt.BookingError as e:
        return _fail(e)


@clinical.post("/doctors/{doctor_id}/unavailability/preview")
def doctor_unavailable_preview(doctor_id: str, body: BlockIn, user=Depends(current_clinician), conn=Depends(get_conn)):
    """What blocking this time would do - everything the confirmation pop-up shows, plus a fingerprint of it."""
    plan = appt.plan_block(conn, _doctor(conn, doctor_id), body.as_block(), user["id"], today(), _now_hm())
    conn.commit()                    # only the days' slots were materialised; nothing is blocked
    return plan


@clinical.post("/doctors/{doctor_id}/unavailability")
def doctor_unavailable(doctor_id: str, body: BlockConfirmIn, user=Depends(current_clinician), conn=Depends(get_conn)):
    """Confirm: block + flag + draft, only if the schedule still matches the preview (else 409 schedule_changed with
    a fresh preview). Messages are sent by the separate endpoints below."""
    try:
        return appt.mark_unavailable(conn, _doctor(conn, doctor_id), user, body.as_block(), body.fingerprint, body.checked,
                                     body.warnings_shown, body.changes, today(), _now_hm())
    except appt.BookingError as e:
        return _fail(e)


@clinical.get("/doctors/{doctor_id}/reschedule-notices")
def doctor_notices(doctor_id: str, user=Depends(current_clinician), conn=Depends(get_conn)):
    _doctor(conn, doctor_id)
    return appt.notices(conn, doctor_id, user["id"])


@clinical.post("/reschedule-notices/{notice_id}/send")
def send_notice(notice_id: int, user=Depends(current_clinician), conn=Depends(get_conn)):
    try:
        return appt.send_notice(conn, notice_id, user, today(), _now_hm())
    except appt.BookingError as e:
        return _fail(e)


@clinical.post("/doctors/{doctor_id}/reschedule-notices/send-all")
def send_all_notices(doctor_id: str, user=Depends(current_clinician), conn=Depends(get_conn)):
    """Each message is its own transaction: one that cannot be sent never holds back the others."""
    _doctor(conn, doctor_id)
    sent, failed = [], []
    for n in appt.notices(conn, doctor_id, user["id"]):
        if not n["can_send"]:
            continue
        try:
            sent.append(appt.send_notice(conn, n["id"], user, today(), _now_hm()))
        except appt.BookingError as e:
            failed.append({"id": n["id"], "message": e.message})
    return {"sent": sent, "failed": failed}


@clinical.get("/patients/{patient_id}/appointments")
def patient_appointments(patient=Depends(patient_for_clinician), conn=Depends(get_conn)):
    return appt.upcoming_for_patient(conn, patient["id"], today())


# ================================================================== patient

@portal.get("/appointments")
def my_appointments(patient=Depends(current_patient), conn=Depends(get_conn)):
    doctors = appt.bookable_doctors(conn, patient["id"])
    conn.commit()
    return {"appointments": appt.upcoming_for_patient(conn, patient["id"], today()),
            "doctors": [{"id": d["id"], "name": d["name"], "slot_duration_minutes": d["slot_duration_minutes"]}
                        for d in doctors],
            "today": today(), "last_day": today() + timedelta(days=appt.BOOKING_DAYS)}


@portal.get("/appointments/slots")
def my_bookable_slots(doctor_id: str, date: str, patient=Depends(current_patient), conn=Depends(get_conn)):
    if not appt.can_book_with(conn, patient["id"], doctor_id):
        raise HTTPException(404, "Doctor not found.")
    try:
        return appt.available_slots(conn, _doctor(conn, doctor_id), date, today(), _now_hm())
    except appt.BookingError as e:
        return _fail(e)


@portal.post("/appointments", status_code=201)
def book_appointment(body: BookIn, patient=Depends(current_patient), conn=Depends(get_conn)):
    try:
        return appt.book(conn, patient, body.slot_id, today(), _now_hm())
    except appt.BookingError as e:
        return _fail(e)
