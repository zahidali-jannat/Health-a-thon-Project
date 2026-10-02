"""Sign-up / sign-in for clinicians (Clinician ID or phone + password) and patients (phone + one-time code)."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import ratelimit, repo, security
from ..deps import CLINICIAN_COOKIE, PATIENT_COOKIE, current_clinician, current_patient_session, get_conn
from ..settings import get_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

def _set_cookie(response: Response, name: str, token: str, hours: int) -> None:
    response.set_cookie(name, token, max_age=hours * 3600, httponly=True, samesite="lax",
                        secure=not get_settings().is_dev, path="/")


def _public(user: dict, conn) -> dict:
    return {"clinician_code": user["clinician_code"], "full_name": user["full_name"], "phone": user["phone"],
            "theme": user.get("theme") or "light", "role": user.get("role") or "doctor", "clinic_name": repo.clinic_name(conn)}


# ------------------------------------------------------------------ clinicians

class SignupIn(BaseModel):
    full_name: str = Field(max_length=120)
    phone: str = Field(max_length=20)
    password: str = Field(max_length=200)
    role: Literal["doctor", "care_team"] = "doctor"     # a doctor, or a member of the clinic team


class LoginIn(BaseModel):
    identifier: str = Field(max_length=40)
    password: str = Field(max_length=200)


@router.post("/clinician/signup")
def clinician_signup(body: SignupIn, response: Response, conn=Depends(get_conn)):
    try:
        user = repo.create_clinician(conn, body.full_name, body.phone, body.password, role=body.role)
    except repo.RepoError as e:
        raise HTTPException(422, str(e))
    repo.audit(conn, repo.clinician_actor(user), "ACCOUNT_CREATED", resource_type="clinical_user", resource_id=user["id"])
    hours = get_settings().clinician_session_hours
    _set_cookie(response, CLINICIAN_COOKIE, repo.create_session(conn, hours, clinical_user_id=user["id"]), hours)
    conn.commit()
    return _public(user, conn)


@router.post("/clinician/login")
def clinician_login(body: LoginIn, response: Response, conn=Depends(get_conn)):
    key = f"clin:{body.identifier.strip().upper()}"
    ratelimit.check(key)
    user = repo.find_clinician_for_login(conn, body.identifier)
    if not security.verify_password(body.password, user["password_hash"] if user else None) or not user["is_active"]:
        ratelimit.fail(key)
        raise HTTPException(401, "Clinician ID / phone or password is incorrect.")
    ratelimit.clear(key)
    repo.audit(conn, repo.clinician_actor(user), "LOGIN", resource_type="clinical_user", resource_id=user["id"])
    hours = get_settings().clinician_session_hours
    _set_cookie(response, CLINICIAN_COOKIE, repo.create_session(conn, hours, clinical_user_id=user["id"]), hours)
    conn.commit()
    return _public(user, conn)


@router.post("/clinician/logout")
def clinician_logout(request: Request, response: Response, conn=Depends(get_conn)):
    if token := request.cookies.get(CLINICIAN_COOKIE):
        repo.revoke_session(conn, token)
        conn.commit()
    response.delete_cookie(CLINICIAN_COOKIE, path="/")
    return {"ok": True}


@router.get("/clinician/me")
def clinician_me(user: dict = Depends(current_clinician), conn=Depends(get_conn)):
    return _public(user, conn)


class AccountIn(BaseModel):
    full_name: str | None = Field(None, max_length=120)
    phone: str | None = Field(None, max_length=20)
    theme: Literal["light", "dark", "pleasant"] | None = None


@router.patch("/clinician/me")
def update_account(body: AccountIn, user: dict = Depends(current_clinician), conn=Depends(get_conn)):
    try:
        updated = repo.update_clinician(conn, user["id"], body.full_name, body.phone, body.theme)
    except repo.RepoError as e:
        raise HTTPException(422, str(e))
    changed = [k for k in ("full_name", "phone") if getattr(body, k) is not None]
    if changed:
        repo.audit(conn, repo.clinician_actor(updated), "ACCOUNT_UPDATED", resource_type="clinical_user",
                   resource_id=user["id"], detail="Changed " + ", ".join(c.replace("_", " ") for c in changed))
    conn.commit()
    return _public(updated, conn)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


@router.post("/clinician/change-password")
def change_password(body: PasswordChangeIn, request: Request, user: dict = Depends(current_clinician),
                    conn=Depends(get_conn)):
    key = f"pw:{user['id']}"
    ratelimit.check(key)
    try:
        repo.change_clinician_password(conn, user["id"], body.current_password, body.new_password,
                                       request.cookies.get(CLINICIAN_COOKIE))
    except repo.RepoError as e:
        if "current password" in str(e):
            ratelimit.fail(key)
        raise HTTPException(422, str(e))
    ratelimit.clear(key)
    repo.audit(conn, repo.clinician_actor(user), "PASSWORD_CHANGED", resource_type="clinical_user", resource_id=user["id"])
    conn.commit()
    return {"ok": True}


# ------------------------------------------------------------------ patients

class PatientSignupIn(BaseModel):
    full_name: str = Field(max_length=120)
    phone: str = Field(max_length=20)
    password: str = Field(max_length=200)
    date_of_birth: date | None = None
    sex: Literal["M", "F", "O"] | None = None


class PatientSignupVerifyIn(BaseModel):
    phone: str = Field(max_length=20)
    code: str = Field(min_length=4, max_length=10)


def _patient_public(patient: dict) -> dict:
    return {"patient_code": patient["patient_code"], "full_name": patient["full_name"],
            "first_name": patient["full_name"].split()[0], "needs_password": repo.patient_needs_password(patient),
            "needs_age": repo.patient_needs_age(patient)}


class PatientLoginIn(BaseModel):
    identifier: str = Field(min_length=3, max_length=20)
    password: str = Field(max_length=200)


class SetPasswordIn(BaseModel):
    new_password: str = Field(max_length=200)
    current_password: str | None = Field(None, max_length=200)


@router.post("/patient/login")
def patient_login(body: PatientLoginIn, response: Response, conn=Depends(get_conn)):
    """Patient ID (or mobile number) + password. A clinic-issued temporary password signs in, but the
    patient must choose their own before anything else works (needs_password = true)."""
    key = f"pat:{body.identifier.strip().upper()}"
    ratelimit.check(key)
    patient = repo.find_patient(conn, body.identifier)
    if not security.verify_password(body.password, patient["password_hash"] if patient else None):
        ratelimit.fail(key)
        raise HTTPException(401, "Patient ID / mobile number or password is not right. Please try again.")
    ratelimit.clear(key)
    repo.audit(conn, repo.Actor("patient", patient["id"], f"{patient['full_name']} (patient)"), "LOGIN",
               patient_id=patient["id"], resource_type="patient", resource_id=patient["id"])
    hours = get_settings().patient_session_hours
    _set_cookie(response, PATIENT_COOKIE, repo.create_session(conn, hours, patient_id=patient["id"]), hours)
    conn.commit()
    return _patient_public(patient)


@router.post("/patient/set-password")
def patient_set_password(body: SetPasswordIn, patient: dict = Depends(current_patient_session), conn=Depends(get_conn)):
    """Choose a password. The current one is required unless the patient is on a temporary (or no) password."""
    if not repo.patient_needs_password(patient):
        if not body.current_password or not security.verify_password(body.current_password, patient["password_hash"]):
            raise HTTPException(401, "Your current password is not right.")
    elif patient["password_hash"] and security.verify_password(body.new_password, patient["password_hash"]):
        raise HTTPException(422, "Please choose a new password, not the temporary one.")
    if problem := security.check_patient_password(body.new_password):
        raise HTTPException(422, problem)
    repo.set_patient_password(conn, patient["id"], body.new_password)
    repo.audit(conn, repo.Actor("patient", patient["id"], f"{patient['full_name']} (patient)"), "PASSWORD_CHANGED",
               patient_id=patient["id"], resource_type="patient", resource_id=patient["id"])
    conn.commit()
    return _patient_public(repo.get_patient(conn, patient["id"]))


@router.post("/patient/signup")
def patient_signup(body: PatientSignupIn, conn=Depends(get_conn)):
    """Step 1: check the details and text a code to the phone. No account exists yet."""
    if body.date_of_birth and body.date_of_birth > date.today():
        raise HTTPException(422, "Date of birth cannot be in the future.")
    settings = get_settings()
    try:
        phone, code = repo.start_patient_signup(conn, body.full_name, body.phone,
                                                body.date_of_birth.isoformat() if body.date_of_birth else None,
                                                body.sex, settings.login_code_minutes, body.password)
    except repo.RepoError as e:
        raise HTTPException(409 if "already exists" in str(e) else 422, str(e))
    conn.commit()
    out = {"sent": True, "phone_hint": "•••••" + phone[-4:]}
    if settings.is_dev:
        out["dev_code"] = code
    return out


@router.post("/patient/signup/verify")
def patient_signup_verify(body: PatientSignupVerifyIn, response: Response, conn=Depends(get_conn)):
    """Step 2: the code proves the phone belongs to them - only now is the patient account created."""
    key = f"signup:{security.normalize_phone(body.phone)}"
    ratelimit.check(key)
    try:
        patient = repo.finish_patient_signup(conn, body.phone, body.code)
    except repo.RepoError as e:
        raise HTTPException(409, str(e))
    if patient is None:
        conn.commit()
        ratelimit.fail(key)
        raise HTTPException(401, "That code is not right or has expired. Please try again.")
    ratelimit.clear(key)
    repo.audit(conn, repo.Actor("patient", patient["id"], f"{patient['full_name']} (patient)"), "ACCOUNT_CREATED",
               patient_id=patient["id"], resource_type="patient", resource_id=patient["id"])
    hours = get_settings().patient_session_hours
    _set_cookie(response, PATIENT_COOKIE, repo.create_session(conn, hours, patient_id=patient["id"]), hours)
    conn.commit()
    return _patient_public(patient)


class CodeRequestIn(BaseModel):
    identifier: str = Field(min_length=3, max_length=20)


class CodeVerifyIn(BaseModel):
    identifier: str = Field(min_length=3, max_length=20)
    code: str = Field(min_length=4, max_length=10)
    forgot_password: bool = False      # the "Forgot password?" route: they must choose a new password next


@router.post("/patient/request-code")
def patient_request_code(body: CodeRequestIn, conn=Depends(get_conn)):
    patient = repo.find_patient(conn, body.identifier)
    if patient is None:
        raise HTTPException(404, "We could not find that phone number or patient ID. Please check and try again.")
    settings = get_settings()
    code = repo.issue_login_code(conn, patient["id"], settings.login_code_minutes)
    conn.commit()
    # Real deployment: send `code` by SMS here. In dev the code is returned so the demo works without SMS.
    out = {"sent": True, "phone_hint": "•••••" + patient["phone"][-4:]}
    if settings.is_dev:
        out["dev_code"] = code
    return out


@router.post("/patient/verify")
def patient_verify(body: CodeVerifyIn, response: Response, conn=Depends(get_conn)):
    key = f"pat:{body.identifier.strip().upper()}"
    ratelimit.check(key)
    patient = repo.find_patient(conn, body.identifier)
    if patient is None or not repo.verify_login_code(conn, patient["id"], body.code):
        conn.commit()
        ratelimit.fail(key)
        raise HTTPException(401, "That code is not right or has expired. Please try again.")
    ratelimit.clear(key)
    if body.forgot_password:
        conn.execute("UPDATE patients SET must_change_password = 1 WHERE id = ?", (patient["id"],))
        patient = repo.get_patient(conn, patient["id"])
    repo.audit(conn, repo.Actor("patient", patient["id"], f"{patient['full_name']} (patient)"), "LOGIN",
               patient_id=patient["id"], resource_type="patient", resource_id=patient["id"],
               detail="Signed in with a code (forgot password)" if body.forgot_password else "Signed in with a code")
    hours = get_settings().patient_session_hours
    _set_cookie(response, PATIENT_COOKIE, repo.create_session(conn, hours, patient_id=patient["id"]), hours)
    conn.commit()
    return _patient_public(patient)


@router.post("/patient/logout")
def patient_logout(request: Request, response: Response, conn=Depends(get_conn)):
    if token := request.cookies.get(PATIENT_COOKIE):
        repo.revoke_session(conn, token)
        conn.commit()
    response.delete_cookie(PATIENT_COOKIE, path="/")
    return {"ok": True}


@router.get("/patient/me")
def patient_me(patient: dict = Depends(current_patient_session)):
    return _patient_public(patient)
