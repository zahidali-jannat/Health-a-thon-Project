"""Request dependencies: DB connection, clock, and the authentication/authorization gates.

Every patient-specific clinical route depends on `patient_for_clinician`, which returns the patient
ONLY if the logged-in clinician has an active care-team assignment. Otherwise it answers 404 (not 403)
so it doesn't reveal whether the patient exists, and records ACCESS_DENIED.
Patient-portal routes depend on `current_patient` and never take a patient id from the request.
"""

import os
from datetime import date, datetime

from fastapi import Depends, HTTPException, Request, UploadFile

from . import db, repo
from .settings import get_settings
from .storage import EXTENSIONS

CLINICIAN_COOKIE = "uc2_clinician"
PATIENT_COOKIE = "uc2_patient"


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def today() -> date:
    override = os.environ.get("DEMO_TODAY")
    return date.fromisoformat(override) if override else date.today()


def now() -> datetime:
    return datetime.now()


def current_clinician(request: Request, conn=Depends(get_conn)) -> dict:
    token = request.cookies.get(CLINICIAN_COOKIE)
    session = repo.session_for_token(conn, token) if token else None
    user = repo.get_clinician(conn, session["clinical_user_id"]) if session and session["clinical_user_id"] else None
    if user is None or not user["is_active"]:
        raise HTTPException(401, "Please sign in.")
    return user


def current_care_team(user: dict = Depends(current_clinician)) -> dict:
    """Clinic-team work (preparing and sending briefs). Doctors have their own dashboard."""
    if user.get("role") != "care_team":
        raise HTTPException(403, "This is for the clinic team. Doctors see briefs on their own dashboard.")
    return user


def current_doctor(user: dict = Depends(current_clinician)) -> dict:
    """The doctor dashboard and a doctor's own consultation hours."""
    if user.get("role") != "doctor":
        raise HTTPException(403, "This is for doctors only.")
    return user


def current_patient_session(request: Request, conn=Depends(get_conn)) -> dict:
    """Signed-in patient, even if they still have to choose a password (used by /me and set-password)."""
    token = request.cookies.get(PATIENT_COOKIE)
    session = repo.session_for_token(conn, token) if token else None
    patient = repo.get_patient(conn, session["patient_id"]) if session and session["patient_id"] else None
    if patient is None:
        raise HTTPException(401, "Please sign in again.")
    return patient


def current_patient(patient: dict = Depends(current_patient_session)) -> dict:
    """Signed-in patient who has their own password. A temporary (clinic-issued) password gets nothing else."""
    if repo.patient_needs_password(patient):
        raise HTTPException(403, "Please choose your own password first.")
    return patient


def patient_for_clinician(patient_id: str, user: dict = Depends(current_clinician), conn=Depends(get_conn)) -> dict:
    patient = repo.get_patient(conn, patient_id)
    if patient is None or not repo.has_access(conn, user["id"], patient_id):
        repo.audit(conn, repo.clinician_actor(user), "ACCESS_DENIED", patient_id=patient["id"] if patient else None,
                   resource_type="patient", resource_id=patient_id)
        conn.commit()
        raise HTTPException(404, "Patient not found.")
    return patient


async def read_upload(file: UploadFile) -> bytes:
    if file.content_type not in EXTENSIONS:
        raise HTTPException(415, "Please upload a photo (JPG, PNG, HEIC) or a PDF.")
    limit = get_settings().max_upload_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, "This file is too large. Please use a file under 10 MB.")
    if not data:
        raise HTTPException(422, "The file is empty.")
    return data
