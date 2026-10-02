"""Data access. Every patient-specific function takes patient_id as a REQUIRED argument and filters
on it, so a query can never reach another patient's rows by accident. Callers commit."""

import hashlib
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from . import security, storage
from .db import now_iso
from .detection import Event, PatientRecord


class RepoError(ValueError):
    """A user-facing validation problem (duplicate phone, bad input...)."""


def new_id() -> str:
    return str(uuid.uuid4())


# ================================================================== actors & audit

@dataclass(frozen=True)
class Actor:
    type: str          # "patient" | "clinical_user" | "system"
    id: str | None
    label: str


SYSTEM = Actor("system", None, "System")


def audit(conn, actor: Actor, action: str, patient_id: str | None = None, resource_type: str | None = None,
          resource_id: str | int | None = None, detail: str | None = None, at: str | None = None,
          old_value: str | None = None, new_value: str | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_log (actor_type, actor_id, actor_label, patient_id, action, resource_type, resource_id, detail, at, "
        "old_value, new_value) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (actor.type, actor.id, actor.label, patient_id, action, resource_type,
         None if resource_id is None else str(resource_id), detail, at or now_iso(), old_value, new_value))


def patient_audit(conn, patient_id: str, limit: int = 50) -> list[dict]:
    rows = conn.execute("SELECT actor_label, action, resource_type, detail, at FROM audit_log "
                        "WHERE patient_id = ? ORDER BY id DESC LIMIT ?", (patient_id, limit))
    return [dict(r) for r in rows]


# ================================================================== patients

def _next_patient_code(conn) -> str:
    row = conn.execute("SELECT MAX(CAST(SUBSTR(patient_code, 3) AS INTEGER)) FROM patients "
                       "WHERE patient_code LIKE 'P-%'").fetchone()
    return f"P-{max(row[0] or 1000, 1000) + 1}"


def create_patient(conn, full_name: str, phone: str, date_of_birth: str | None = None, sex: str | None = None,
                   email: str | None = None, abha_number: str | None = None, patient_code: str | None = None,
                   password_hash: str | None = None, must_change_password: bool = False) -> dict:
    phone_n = security.normalize_phone(phone)
    if not phone_n:
        raise RepoError("Please enter a 10-digit phone number.")
    if len(full_name.strip()) < 2:
        raise RepoError("Please enter the patient's full name.")
    abha = "".join(ch for ch in (abha_number or "") if ch.isdigit()) or None
    if abha and len(abha) != 14:
        raise RepoError("An ABHA number has 14 digits.")
    if conn.execute("SELECT 1 FROM patients WHERE phone = ?", (phone_n,)).fetchone():
        raise RepoError("A patient with this phone number already exists.")
    if abha and conn.execute("SELECT 1 FROM patients WHERE abha_number = ?", (abha,)).fetchone():
        raise RepoError("A patient with this ABHA number already exists.")
    pid, now = new_id(), now_iso()
    conn.execute(
        "INSERT INTO patients (id, patient_code, full_name, phone, email, date_of_birth, sex, abha_number, created_at, "
        "updated_at, password_hash, must_change_password) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (pid, patient_code or _next_patient_code(conn), full_name.strip(), phone_n, email or None, date_of_birth,
         sex, abha, now, now, password_hash, int(must_change_password)))
    return get_patient(conn, pid)


def get_patient(conn, patient_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    return dict(row) if row else None


def find_patient(conn, identifier: str) -> dict | None:
    """By patient code (P-1001) or phone number."""
    code = identifier.strip().upper()
    phone = security.normalize_phone(identifier)
    row = conn.execute("SELECT * FROM patients WHERE patient_code = ? OR (? != '' AND phone = ?)",
                       (code, phone, phone)).fetchone()
    return dict(row) if row else None


def set_patient_password(conn, patient_id: str, password: str, must_change: bool = False) -> None:
    conn.execute("UPDATE patients SET password_hash = ?, must_change_password = ?, updated_at = ? WHERE id = ?",
                 (security.hash_password(password), int(must_change), now_iso(), patient_id))


def revoke_patient_sessions(conn, patient_id: str) -> None:
    conn.execute("UPDATE auth_sessions SET revoked_at = ? WHERE patient_id = ? AND revoked_at IS NULL", (now_iso(), patient_id))


def patient_needs_password(patient: dict) -> bool:
    return not patient.get("password_hash") or bool(patient.get("must_change_password"))


def _whole_years(since: date, on: date) -> int:
    return on.year - since.year - ((on.month, on.day) < (since.month, since.day))


def age(date_of_birth: str | None, on: date, reported_age: int | None = None,
        reported_age_on: str | None = None) -> int | None:
    """From the date of birth if known; otherwise from the age the patient gave, plus the years since they gave it."""
    if date_of_birth:
        return _whole_years(date.fromisoformat(date_of_birth), on)
    if reported_age is not None and reported_age_on:
        return reported_age + max(0, _whole_years(date.fromisoformat(reported_age_on), on))
    return None


def patient_needs_age(patient: dict) -> bool:
    return not patient.get("date_of_birth") and patient.get("reported_age") is None


def set_reported_age(conn, patient_id: str, years: int, on: date) -> None:
    conn.execute("UPDATE patients SET reported_age = ?, reported_age_on = ? WHERE id = ?", (years, on.isoformat(), patient_id))


# ================================================================== clinicians & access

ROLES = ("doctor", "care_team")


def create_clinician(conn, full_name: str, phone: str | None, password: str, clinician_code: str | None = None,
                     role: str = "doctor") -> dict:
    """clinician_code is only passed by dev fixtures; real sign-ups always get a generated one.
    A clinic-team login may have no phone (a shared desk account); a doctor always has one."""
    if role not in ROLES:
        raise RepoError("Unknown account type.")
    phone_n = security.normalize_phone(phone) if phone else ""
    if len(full_name.strip()) < 2:
        raise RepoError("Please enter your full name.")
    if not phone_n and (phone or role == "doctor"):
        raise RepoError("Please enter a 10-digit phone number.")
    if len(password) < 8:
        raise RepoError("Password must be at least 8 characters.")
    if phone_n and conn.execute("SELECT 1 FROM clinical_users WHERE phone = ?", (phone_n,)).fetchone():
        raise RepoError("An account with this phone number already exists. Please sign in.")
    uid, now, pw = new_id(), now_iso(), security.hash_password(password)
    for _ in range(10):   # the UNIQUE index guarantees no duplicates; retry on the (rare) random clash
        try:
            conn.execute("INSERT INTO clinical_users (id, clinician_code, full_name, phone, password_hash, role, created_at, updated_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (uid, clinician_code or security.new_clinician_code(), full_name.strip(), phone_n or None, pw, role,
                          now, now))
            return get_clinician(conn, uid)
        except sqlite3.IntegrityError as e:
            if "clinician_code" not in str(e):
                raise
    raise RepoError("Could not create the account. Please try again.")


def get_clinician(conn, user_id: str) -> dict | None:
    row = conn.execute("SELECT id, clinician_code, full_name, phone, is_active, theme, role, created_at FROM clinical_users "
                       "WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def clinic_name(conn) -> str:
    row = conn.execute("SELECT name FROM clinic_profile WHERE id = 1").fetchone()
    return row[0] if row else "Clinic team"


def find_clinician_for_login(conn, identifier: str) -> dict | None:
    """By Clinician ID (CLN-XXXXXX) or phone. Includes the password hash - never return this to a client."""
    code = identifier.strip().upper()
    phone = security.normalize_phone(identifier)
    row = conn.execute("SELECT * FROM clinical_users WHERE clinician_code = ? OR (? != '' AND phone = ?)",
                       (code, phone, phone)).fetchone()
    return dict(row) if row else None


def update_clinician(conn, user_id: str, full_name: str | None = None, phone: str | None = None,
                     theme: str | None = None) -> dict:
    if full_name is not None and len(full_name.strip()) < 2:
        raise RepoError("Please enter your full name.")
    if phone is not None:
        phone = security.normalize_phone(phone)
        if not phone:
            raise RepoError("Please enter a 10-digit phone number.")
        if conn.execute("SELECT 1 FROM clinical_users WHERE phone = ? AND id != ?", (phone, user_id)).fetchone():
            raise RepoError("Another account already uses this phone number.")
    if theme is not None and theme not in ("light", "dark", "pleasant"):
        raise RepoError("Unknown theme.")
    for column, value in (("full_name", full_name and full_name.strip()), ("phone", phone), ("theme", theme)):
        if value is not None:
            conn.execute(f"UPDATE clinical_users SET {column} = ?, updated_at = ? WHERE id = ?", (value, now_iso(), user_id))
    return get_clinician(conn, user_id)


def change_clinician_password(conn, user_id: str, current: str, new: str, keep_token: str | None) -> None:
    row = conn.execute("SELECT password_hash FROM clinical_users WHERE id = ?", (user_id,)).fetchone()
    if not security.verify_password(current, row["password_hash"] if row else None):
        raise RepoError("Your current password is not right.")
    if len(new) < 8:
        raise RepoError("New password must be at least 8 characters.")
    if security.verify_password(new, row["password_hash"]):
        raise RepoError("Please choose a password you haven't used just now.")
    conn.execute("UPDATE clinical_users SET password_hash = ?, updated_at = ? WHERE id = ?",
                 (security.hash_password(new), now_iso(), user_id))
    # Sign out every OTHER session of this clinician (e.g. a forgotten computer); keep this one.
    conn.execute("UPDATE auth_sessions SET revoked_at = ? WHERE clinical_user_id = ? AND revoked_at IS NULL "
                 "AND token_hash != ?", (now_iso(), user_id, security.token_hash(keep_token or "")))


def access_log_for_clinician(conn, user_id: str, patient_id: str | None = None, limit: int = 200) -> list[dict]:
    """Activity on the clinician's OWN patients only (optionally one of them)."""
    ids = patient_ids_for_clinician(conn, user_id)
    if patient_id is not None:
        ids = [p for p in ids if p == patient_id]
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT a.at, a.actor_label, a.action, a.detail, p.full_name AS patient_name, p.patient_code "
        f"FROM audit_log a JOIN patients p ON p.id = a.patient_id WHERE a.patient_id IN ({marks}) "
        f"ORDER BY a.id DESC LIMIT ?", (*ids, limit))
    return [dict(r) for r in rows]


def clinician_actor(user: dict) -> Actor:
    return Actor("clinical_user", user["id"], f"{user['full_name']} ({user['clinician_code']})")


# The clinic team sees every patient of the clinic: anyone with at least one active care-team assignment.
# A doctor sees only the patients on their own care team. VISIBLE_SQL needs the viewer's id twice.
VISIBLE_SQL = ("(EXISTS (SELECT 1 FROM care_team_assignments c WHERE c.patient_id = {p} AND c.clinical_user_id = ? "
               "AND c.revoked_at IS NULL) OR (EXISTS (SELECT 1 FROM clinical_users v WHERE v.id = ? AND v.role = 'care_team') "
               "AND EXISTS (SELECT 1 FROM care_team_assignments c2 WHERE c2.patient_id = {p} AND c2.revoked_at IS NULL)))")


def visible_sql(patient_column: str) -> str:
    return VISIBLE_SQL.format(p=patient_column)


def has_access(conn, user_id: str, patient_id: str) -> bool:
    return conn.execute(f"SELECT {visible_sql('?')}", (patient_id, user_id, user_id, patient_id)).fetchone()[0] == 1


def grant_access(conn, user_id: str, patient_id: str, granted_by: str | None) -> None:
    conn.execute("INSERT INTO care_team_assignments (clinical_user_id, patient_id, granted_by, granted_at) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT (clinical_user_id, patient_id) DO UPDATE SET revoked_at = NULL, granted_by = excluded.granted_by, "
                 "granted_at = excluded.granted_at", (user_id, patient_id, granted_by, now_iso()))


def patient_ids_for_clinician(conn, user_id: str) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT a.patient_id FROM care_team_assignments a WHERE a.revoked_at IS NULL "
                                       "AND (a.clinical_user_id = ? OR EXISTS (SELECT 1 FROM clinical_users v "
                                       "WHERE v.id = ? AND v.role = 'care_team'))", (user_id, user_id))]


def care_team(conn, patient_id: str) -> list[dict]:
    """Who can open this patient's record now. granted_by_name is None when the patient added the doctor themselves."""
    rows = conn.execute(
        "SELECT u.clinician_code, u.full_name, a.granted_at, g.full_name AS granted_by_name FROM care_team_assignments a "
        "JOIN clinical_users u ON u.id = a.clinical_user_id LEFT JOIN clinical_users g ON g.id = a.granted_by "
        "WHERE a.patient_id = ? AND a.revoked_at IS NULL ORDER BY a.granted_at", (patient_id,))
    return [dict(r) for r in rows]


# ================================================================== sessions & login codes

def create_session(conn, hours: int, patient_id: str | None = None, clinical_user_id: str | None = None) -> str:
    token = security.new_token()
    now = datetime.now(timezone.utc)
    conn.execute("INSERT INTO auth_sessions (token_hash, patient_id, clinical_user_id, created_at, expires_at) "
                 "VALUES (?, ?, ?, ?, ?)",
                 (security.token_hash(token), patient_id, clinical_user_id, now.isoformat(timespec="seconds"),
                  (now + timedelta(hours=hours)).isoformat(timespec="seconds")))
    return token


def session_for_token(conn, token: str) -> dict | None:
    row = conn.execute("SELECT * FROM auth_sessions WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > ?",
                       (security.token_hash(token), now_iso())).fetchone()
    return dict(row) if row else None


def revoke_session(conn, token: str) -> None:
    conn.execute("UPDATE auth_sessions SET revoked_at = ? WHERE token_hash = ?", (now_iso(), security.token_hash(token)))


def issue_login_code(conn, patient_id: str, minutes: int) -> str:
    now = datetime.now(timezone.utc)
    conn.execute("UPDATE patient_login_codes SET used_at = ? WHERE patient_id = ? AND used_at IS NULL",
                 (now_iso(), patient_id))
    code = security.new_login_code()
    conn.execute("INSERT INTO patient_login_codes (patient_id, code_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
                 (patient_id, security.login_code_hash(patient_id, code),
                  (now + timedelta(minutes=minutes)).isoformat(timespec="seconds"), now_iso()))
    return code


def verify_login_code(conn, patient_id: str, code: str, max_attempts: int = 5) -> bool:
    row = conn.execute("SELECT * FROM patient_login_codes WHERE patient_id = ? AND used_at IS NULL AND expires_at > ? "
                       "ORDER BY id DESC LIMIT 1", (patient_id, now_iso())).fetchone()
    if row is None or row["attempts"] >= max_attempts:
        return False
    conn.execute("UPDATE patient_login_codes SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
    if security.login_code_hash(patient_id, code.strip()) != row["code_hash"]:
        return False
    conn.execute("UPDATE patient_login_codes SET used_at = ? WHERE id = ?", (now_iso(), row["id"]))
    return True


def revoke_access(conn, user_id: str, patient_id: str) -> bool:
    cur = conn.execute("UPDATE care_team_assignments SET revoked_at = ? WHERE clinical_user_id = ? AND patient_id = ? "
                       "AND revoked_at IS NULL", (now_iso(), user_id, patient_id))
    return cur.rowcount == 1


def find_active_clinician_by_code(conn, clinician_code: str) -> dict | None:
    row = conn.execute("SELECT id, full_name, clinician_code FROM clinical_users WHERE clinician_code = ? AND is_active = 1",
                       (clinician_code.strip().upper(),)).fetchone()
    return dict(row) if row else None


# ================================================================== patient self sign-up

def start_patient_signup(conn, full_name: str, phone: str, date_of_birth: str | None, sex: str | None,
                         minutes: int, password: str = "") -> tuple[str, str]:
    """Validates the details and stores them with a one-time code. Returns (normalised phone, code)."""
    phone_n = security.normalize_phone(phone)
    if len(full_name.strip()) < 2:
        raise RepoError("Please enter your full name.")
    if not phone_n:
        raise RepoError("Please enter your 10-digit mobile number.")
    if problem := security.check_patient_password(password):
        raise RepoError(problem)
    if conn.execute("SELECT 1 FROM patients WHERE phone = ?", (phone_n,)).fetchone():
        raise RepoError("An account with this phone number already exists. Please sign in instead.")
    conn.execute("DELETE FROM patient_signups WHERE phone = ?", (phone_n,))
    code = security.new_login_code()
    now = datetime.now(timezone.utc)
    conn.execute("INSERT INTO patient_signups (phone, full_name, date_of_birth, sex, code_hash, expires_at, created_at, "
                 "password_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (phone_n, full_name.strip(), date_of_birth, sex, security.login_code_hash(phone_n, code),
                  (now + timedelta(minutes=minutes)).isoformat(timespec="seconds"), now_iso(),
                  security.hash_password(password)))
    return phone_n, code


def finish_patient_signup(conn, phone: str, code: str, max_attempts: int = 5) -> dict | None:
    """Checks the code; on success creates the patient and removes the pending sign-up. None if the code is wrong."""
    phone_n = security.normalize_phone(phone)
    row = conn.execute("SELECT * FROM patient_signups WHERE phone = ? AND expires_at > ? ORDER BY id DESC LIMIT 1",
                       (phone_n, now_iso())).fetchone()
    if row is None or row["attempts"] >= max_attempts:
        return None
    conn.execute("UPDATE patient_signups SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
    if security.login_code_hash(phone_n, code.strip()) != row["code_hash"]:
        return None
    patient = create_patient(conn, row["full_name"], phone_n, row["date_of_birth"], row["sex"],
                             password_hash=row["password_hash"])
    conn.execute("DELETE FROM patient_signups WHERE phone = ?", (phone_n,))
    return patient


# ================================================================== documents

def create_document(conn, patient_id: str, document_type: str, file_name: str, content_type: str, data: bytes,
                    source: str, description: str | None = None, uploaded_by_patient_id: str | None = None,
                    uploaded_by_user_id: str | None = None) -> str:
    """Stores the file, then its metadata. If the metadata insert fails, the stored file is removed."""
    doc_id = new_id()
    key = storage.storage_key(patient_id, doc_id, content_type)
    storage.save(key, data)
    try:
        conn.execute(
            "INSERT INTO patient_documents (id, patient_id, document_type, description, file_name, content_type, size_bytes, "
            "sha256, storage_key, source, uploaded_by_patient_id, uploaded_by_user_id, uploaded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (doc_id, patient_id, document_type, description, (file_name or "upload")[:200], content_type, len(data),
             hashlib.sha256(data).hexdigest(), key, source, uploaded_by_patient_id, uploaded_by_user_id, now_iso()))
    except Exception:
        storage.delete(key)
        raise
    return doc_id


_DOC_COLUMNS = ("d.id, d.document_type, d.description, d.file_name, d.content_type, d.size_bytes, d.source, "
                "d.uploaded_at, d.review_status, d.reviewed_at, "
                "COALESCE(u.full_name, p.full_name) AS uploaded_by, r.full_name AS reviewed_by_name")


def list_documents(conn, patient_id: str) -> list[dict]:
    rows = conn.execute(
        f"SELECT {_DOC_COLUMNS} FROM patient_documents d "
        "LEFT JOIN clinical_users u ON u.id = d.uploaded_by_user_id LEFT JOIN patients p ON p.id = d.uploaded_by_patient_id "
        "LEFT JOIN clinical_users r ON r.id = d.reviewed_by "
        "WHERE d.patient_id = ? ORDER BY d.uploaded_at DESC", (patient_id,))
    return [dict(r) for r in rows]


def get_document(conn, patient_id: str, document_id: str) -> dict | None:
    """Scoped: returns None unless the document belongs to this patient."""
    row = conn.execute("SELECT * FROM patient_documents WHERE id = ? AND patient_id = ?",
                       (document_id, patient_id)).fetchone()
    return dict(row) if row else None


_CONFIDENCE_FOR = {"confirmed": "high", "rejected": "low"}


def review_document(conn, patient_id: str, document_id: str, status: str, user_id: str) -> bool:
    """One review decision for a file AND any reading it backs up (e.g. a glucometer photo and its sugar value)."""
    cur = conn.execute("UPDATE patient_documents SET review_status = ?, reviewed_by = ?, reviewed_at = ? "
                       "WHERE id = ? AND patient_id = ?", (status, user_id, now_iso(), document_id, patient_id))
    if cur.rowcount != 1:
        return False
    conn.execute("UPDATE clinical_events SET confidence = ? WHERE document_id = ? AND patient_id = ? AND source = 'patient_upload'",
                 (_CONFIDENCE_FOR[status], document_id, patient_id))
    return True


# ================================================================== lab reports & results

TEST_CODES = {"hba1c": "4548-4", "hemoglobin": "718-7", "egfr": "33914-3",
              "urine protein": "20454-5", "ldl cholesterol": "13457-7"}


def create_lab_report(conn, patient_id: str, laboratory_name: str, report_date: date, source: str,
                      document_id: str | None = None, consent_id: int | None = None,
                      entered_by_user_id: str | None = None, report_type: str | None = None) -> str:
    if document_id and not get_document(conn, patient_id, document_id):
        raise RepoError("That document does not belong to this patient.")
    rid = new_id()
    conn.execute("INSERT INTO lab_reports (id, patient_id, document_id, laboratory_name, report_date, source, consent_id, "
                 "entered_by_user_id, created_at, report_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (rid, patient_id, document_id, laboratory_name, report_date.isoformat(), source, consent_id,
                  entered_by_user_id, now_iso(), report_type))
    return rid


def get_lab_report(conn, patient_id: str, report_id: str) -> dict | None:
    """Scoped to the patient. Includes the review status of the file it came from, if any."""
    row = conn.execute("SELECT r.*, d.review_status AS document_status FROM lab_reports r "
                       "LEFT JOIN patient_documents d ON d.id = r.document_id AND d.patient_id = r.patient_id "
                       "WHERE r.id = ? AND r.patient_id = ?", (report_id, patient_id)).fetchone()
    return dict(row) if row else None


def parse_reference(text: str | None, value_is_numeric: bool) -> dict:
    """Turn a reference range typed exactly as printed into structured fields. Never invents one:
    '< 5.7' -> upper, '> 60' -> lower, '12 - 16' / '12–16' -> range, 'Negative' -> qualitative.
    Anything else is kept as printed text only."""
    import re
    t = (text or "").strip()
    if not t:
        return {}
    num = r"(\d+(?:\.\d+)?)"
    if not value_is_numeric:
        return {"ref_kind": "qualitative", "ref_text": t}
    if m := re.fullmatch(rf"(?:<|≤|<=|up to|below)\s*{num}\s*\S*.*", t, re.I):
        return {"ref_kind": "upper", "ref_high": float(m[1]), "ref_text": t}
    if m := re.fullmatch(rf"(?:>|≥|>=|above)\s*{num}\s*\S*.*", t, re.I):
        return {"ref_kind": "lower", "ref_low": float(m[1]), "ref_text": t}
    if m := re.fullmatch(rf"{num}\s*(?:-|–|—|to)\s*{num}\s*\S*.*", t, re.I):
        lo, hi = float(m[1]), float(m[2])
        if lo <= hi:
            return {"ref_kind": "range", "ref_low": lo, "ref_high": hi, "ref_text": t}
    return {"ref_text": t}


def add_lab_result(conn, report_id: str, patient_id: str, test_name: str, value: float | str, unit: str | None,
                   test_date: date, ref_kind: str | None = None, ref_low: float | None = None,
                   ref_high: float | None = None, ref_text: str | None = None, crit_low: float | None = None,
                   crit_high: float | None = None, reported_flag: str | None = None,
                   value_text: str | None = None) -> str:
    """value_text keeps the result exactly as reported (e.g. "8.40"); numeric_value is for graphs and rules."""
    numeric = value if isinstance(value, (int, float)) else None
    value_text = value_text or (f"{value:g}" if numeric is not None else str(value))
    rid = new_id()
    conn.execute(
        "INSERT INTO lab_test_results (id, report_id, patient_id, test_name, test_code, value_text, numeric_value, unit, "
        "reference_kind, reference_low, reference_high, reference_text, critical_low, critical_high, reported_flag, "
        "test_date, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (rid, report_id, patient_id, test_name, TEST_CODES.get(test_name.lower()), value_text, numeric, unit,
         ref_kind, ref_low, ref_high, ref_text, crit_low, crit_high, reported_flag, test_date.isoformat(), now_iso()))
    return rid


def list_reports(conn, patient_id: str) -> list[dict]:
    reports = [dict(r) for r in conn.execute(
        "SELECT r.id, r.laboratory_name, r.report_date, r.source, r.document_id, r.report_type, "
        "d.file_name AS document_name, d.review_status AS document_status, "
        "u.full_name AS entered_by FROM lab_reports r "
        "LEFT JOIN patient_documents d ON d.id = r.document_id AND d.patient_id = r.patient_id "
        "LEFT JOIN clinical_users u ON u.id = r.entered_by_user_id "
        "WHERE r.patient_id = ? ORDER BY r.report_date DESC, r.created_at DESC", (patient_id,))]
    results: dict[str, list] = {}
    for row in conn.execute("SELECT id, report_id, test_name, test_code, value_text, numeric_value, unit, reference_kind, "
                            "reference_low, reference_high, reference_text, critical_low, critical_high, reported_flag, "
                            "test_date FROM lab_test_results WHERE patient_id = ? ORDER BY test_name", (patient_id,)):
        results.setdefault(row["report_id"], []).append(dict(row))
    for r in reports:
        r["results"] = results.get(r["id"], [])
    return reports


# ================================================================== clinical events

def insert_event(conn, patient_id: str, event_type: str, effective_date: date, source: str, confidence: str,
                 status: str, asserted_by: str, name: str | None = None, value: float | None = None,
                 unit: str | None = None, note: str | None = None, consent_id: int | None = None,
                 facility: str | None = None, clinician: str | None = None, document_id: str | None = None,
                 recorded_by_user_id: str | None = None, linked_document_id: str | None = None,
                 reviewed_by: str | None = None, reviewed_date: str | None = None,
                 linked_upload_id: str | None = None) -> int:
    return conn.execute(
        "INSERT INTO clinical_events (patient_id, event_type, name, value, unit, effective_date, source, confidence, status, "
        "asserted_by, recorded_by_user_id, facility, clinician, note, consent_id, document_id, linked_document_id, "
        "reviewed_by, reviewed_date, created_at, linked_upload_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (patient_id, event_type, name, value, unit, effective_date.isoformat(), source, confidence, status, asserted_by,
         recorded_by_user_id, facility, clinician, note, consent_id, document_id, linked_document_id, reviewed_by,
         reviewed_date, now_iso(), linked_upload_id)).lastrowid


def review_patient_event(conn, patient_id: str, event_id: int, status: str, reviewer_label: str) -> bool:
    """Review a value the patient typed in with NO file attached (readings with a photo are reviewed via the photo)."""
    cur = conn.execute("UPDATE clinical_events SET confidence = ? WHERE id = ? AND patient_id = ? "
                       "AND source = 'patient_upload' AND confidence = 'medium' AND document_id IS NULL "
                       "AND linked_document_id IS NULL",
                       (_CONFIDENCE_FOR[status], event_id, patient_id))
    return cur.rowcount == 1


def event_reviewer(conn, patient_id: str, event_id: int) -> str | None:
    """Who reviewed a typed patient entry - read from the audit log, the single record of that decision."""
    row = conn.execute("SELECT actor_label FROM audit_log WHERE patient_id = ? AND resource_type = 'clinical_event' "
                       "AND resource_id = ? AND action LIKE 'PATIENT_ENTRY_%' ORDER BY id DESC LIMIT 1",
                       (patient_id, str(event_id))).fetchone()
    return row[0].split(" (CLN-")[0] if row else None


def review_status_for_event(confidence: str) -> str:
    return {"high": "confirmed", "low": "rejected"}.get(confidence, "pending")


# ================================================================== external report review
# A patient uploads a report from an outside lab; a clinician reads it and types ONE value. The value is saved
# with its document link in a single INSERT - the database refuses the value without the link (see migration 0006).

EXTERNAL_TESTS = {   # test type -> (name used on graphs, unit, lowest and highest plausible value)
    "HbA1c": ("HbA1c", "%", 3.0, 20.0),
    "Fasting Sugar": ("Fasting glucose", "mg/dL", 20.0, 600.0),
    "PP Sugar": ("Post-meal glucose (PP)", "mg/dL", 20.0, 800.0),
    "Other": (None, None, None, None),
}


def external_test_label(test_type: str, test_name: str | None) -> str:
    return test_name if test_type == "Other" and test_name else test_type


def create_external_report(conn, patient_id: str, test_type: str, test_date: date, file_name: str,
                           content_type: str, data: bytes, test_name: str | None = None,
                           test_order_item_id: str | None = None, lab_name: str | None = None) -> str:
    """Stores the file, then its row (status pending_review). If the row can't be written, the file is removed."""
    if test_type not in EXTERNAL_TESTS:
        raise RepoError("Please choose the test type.")
    report_id = new_id()
    key = storage.storage_key(patient_id, report_id, content_type)
    storage.save(key, data)
    try:
        conn.execute(
            "INSERT INTO external_reports (id, patient_id, uploaded_by_patient_id, upload_date, test_type, test_name, "
            "test_date, file_name, content_type, size_bytes, sha256, storage_key, test_order_item_id, lab_name) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (report_id, patient_id, patient_id, now_iso(), test_type, test_name if test_type == "Other" else None,
             test_date.isoformat(), (file_name or "report")[:200], content_type, len(data),
             hashlib.sha256(data).hexdigest(), key, test_order_item_id, lab_name))
    except Exception:
        storage.delete(key)
        raise
    return report_id


_EXT_SELECT = (
    "SELECT x.id, x.patient_id, x.document_type, x.disease, x.purchase_date, x.reject_code, "
    "x.test_type, x.test_name, x.test_date, x.upload_date, x.file_name, x.content_type, "
    "x.storage_key, x.status, x.reviewed_at, x.reject_reason, r.full_name AS reviewed_by_name, "
    "p.full_name AS patient_name, p.patient_code, e.id AS event_id, e.name AS result_name, e.value, e.unit, "
    "e.effective_date AS result_date, x.test_order_item_id, x.lab_name, oc.display_name AS order_test_name, "
    "oc.expected_unit AS order_unit, oc.plausible_low AS order_low, oc.plausible_high AS order_high, "
    "oi.due_by AS order_due_by FROM external_reports x JOIN patients p ON p.id = x.patient_id "
    "LEFT JOIN clinical_users r ON r.id = x.reviewed_by LEFT JOIN clinical_events e ON e.linked_document_id = x.id "
    "LEFT JOIN test_order_items oi ON oi.id = x.test_order_item_id LEFT JOIN test_catalog oc ON oc.id = oi.test_catalog_id ")


def _external(row) -> dict:
    d = dict(row)
    if d["document_type"] == "medicine_bill":
        d["test_label"] = "Medicine bill"
        d["expected_unit"] = d["plausible_low"] = d["plausible_high"] = None
        return d
    d["test_label"] = external_test_label(d["test_type"], d["test_name"])
    name, unit, lo, hi = EXTERNAL_TESTS[d["test_type"]]
    if unit is None and d.get("order_unit"):          # an "Other" upload answering a test order: the ordered test's unit
        unit, lo, hi = d["order_unit"], d["order_low"], d["order_high"]
    d["expected_unit"], d["plausible_low"], d["plausible_high"] = unit, lo, hi
    return d


def get_external_report(conn, patient_id: str, report_id: str, document_type: str | None = "lab_report") -> dict | None:
    """Scoped: None unless the document belongs to this patient (and is of that type; None = any type)."""
    row = conn.execute(_EXT_SELECT + "WHERE x.id = ? AND x.patient_id = ? AND (? IS NULL OR x.document_type = ?)",
                       (report_id, patient_id, document_type, document_type)).fetchone()
    return _external(row) if row else None


def list_external_reports(conn, patient_id: str, document_type: str | None = "lab_report") -> list[dict]:
    return [_external(r) for r in conn.execute(
        _EXT_SELECT + "WHERE x.patient_id = ? AND (? IS NULL OR x.document_type = ?) ORDER BY x.upload_date DESC",
        (patient_id, document_type, document_type))]


def pending_external_reports(conn, patient_ids: list[str], document_type: str = "lab_report") -> list[dict]:
    """A review queue: waiting documents of the given patients (the caller's own), oldest upload first."""
    if not patient_ids:
        return []
    marks = ",".join("?" * len(patient_ids))
    return [_external(r) for r in conn.execute(
        _EXT_SELECT + f"WHERE x.status = 'pending_review' AND x.document_type = ? AND x.patient_id IN ({marks}) "
                      "ORDER BY x.upload_date", (document_type, *patient_ids))]


def save_reviewed_value(conn, patient_id: str, report_id: str | None, value: float, reviewer: dict,
                        unit: str | None = None, test_date: date | None = None) -> int:
    """THE save of this workflow: one INSERT writes the value together with linked_document_id, reviewed_by and
    reviewed_date; a trigger marks the report reviewed inside that same statement. There is no path that stores
    the value first and attaches the document later. Without a report id this refuses - and so does the database."""
    if not report_id:
        raise RepoError("A value read from an uploaded report must be linked to that report.")
    report = get_external_report(conn, patient_id, report_id, "lab_report")
    if report is None:
        raise RepoError("Report not found.")
    if report["status"] != "pending_review":
        raise RepoError("This report has already been reviewed.")
    graph_name, fixed_unit, _, _ = EXTERNAL_TESTS[report["test_type"]]
    return insert_event(
        conn, patient_id, "lab", test_date or date.fromisoformat(report["test_date"]), "patient_upload_reviewed", "high",
        "resulted", "Outside lab (uploaded by patient)", name=graph_name or report["test_name"], value=value,
        unit=fixed_unit or unit, recorded_by_user_id=reviewer["id"], linked_document_id=report_id,
        reviewed_by=reviewer["id"], reviewed_date=now_iso())


def reject_external_report(conn, patient_id: str, report_id: str, user_id: str, reason: str | None) -> bool:
    cur = conn.execute("UPDATE external_reports SET status = 'rejected', reviewed_by = ?, reviewed_at = ?, reject_reason = ? "
                       "WHERE id = ? AND patient_id = ? AND status = 'pending_review' AND document_type = 'lab_report'",
                       (user_id, now_iso(), reason, report_id, patient_id))
    return cur.rowcount == 1


def _local_date(iso: str) -> date:
    return datetime.fromisoformat(iso).astimezone().date()


def reference_line(patient_name: str, patient_code: str, test_label: str, uploaded_at: str,
                   reviewer: str, reviewed_at: str) -> str:
    """The plain-language source of a reviewed value - the exact wording shown when a graph point is clicked."""
    from .detection import fmt
    return (f"{patient_name} ({patient_code}) uploaded this {test_label} report on {fmt(_local_date(uploaded_at))}. "
            f"Reviewed by {reviewer} on {fmt(_local_date(reviewed_at))}.")


# ================================================================== "Link to Report" for manual values
# A value the care team typed in may say which uploaded report it came from. The link can be changed later; every
# change keeps previous_document_id / link_changed_by / link_changed_date on the row (the database insists).

# Which uploads fit each manual field. "Other" uploads match by the test name the patient wrote.
MANUAL_FIELD_TESTS = {
    "sugar": {"types": ("Fasting Sugar", "PP Sugar"), "words": ("sugar", "glucose")},
    "hba1c": {"types": ("HbA1c",), "words": ("hba1c", "a1c", "glycated")},
    "hemoglobin": {"types": (), "words": ("hemoglobin", "haemoglobin", "hb", "cbc", "blood count")},
    "egfr": {"types": (), "words": ("egfr", "gfr", "kidney", "renal", "creatinine", "kft", "rft")},
}


def report_matches_field(report: dict, field: str) -> bool:
    rule = MANUAL_FIELD_TESTS[field]
    if report["test_type"] in rule["types"]:
        return True
    if report["test_type"] != "Other":
        return False
    import re
    name = (report.get("test_name") or "").lower()
    words = re.findall(r"[a-z0-9]+", name)
    # short keywords ("hb", "gfr") must be whole words; longer ones may be part of a word ("haemoglobin level")
    return any(w in words if len(w) <= 3 else w in name for w in rule["words"])


def linkable_reports(conn, patient_id: str, field: str | None, show_all: bool) -> dict:
    """This patient's usable uploads (never another patient's; never ones marked not usable), newest test first."""
    usable = [x for x in list_external_reports(conn, patient_id) if x["status"] != "rejected"]
    shown = usable if show_all or field is None else [x for x in usable if report_matches_field(x, field)]
    shown.sort(key=lambda x: (x["test_date"], x["upload_date"]), reverse=True)
    return {"total": len(usable), "reports": shown}


def check_linkable(conn, patient_id: str, report_id: str) -> dict:
    report = get_external_report(conn, patient_id, report_id, "lab_report")
    if report is None:
        raise RepoError("That report was not found for this patient.")
    if report["status"] == "rejected":
        raise RepoError("That report was marked as not usable, so a value cannot be linked to it.")
    return report


def set_manual_link(conn, patient_id: str, event_id: int, report_id: str | None, user_id: str) -> dict:
    """Re-link (or unlink) a saved manual value: an UPDATE of the same row, recording what it was before, who
    changed it and when. Returns {"from": old, "to": new}."""
    row = conn.execute("SELECT linked_document_id FROM clinical_events WHERE id = ? AND patient_id = ? "
                       "AND source = 'care_team_manual' AND event_type = 'lab'", (event_id, patient_id)).fetchone()
    if row is None:
        raise RepoError("That value was not found, or it is not a value entered by the care team.")
    if report_id:
        check_linkable(conn, patient_id, report_id)
    if row["linked_document_id"] == report_id:
        raise RepoError("The value is already linked that way.")
    conn.execute("UPDATE clinical_events SET previous_document_id = linked_document_id, linked_document_id = ?, "
                 "previous_upload_id = linked_upload_id, linked_upload_id = NULL, "
                 "link_changed_by = ?, link_changed_date = ? WHERE id = ? AND patient_id = ?",
                 # microseconds: two quick corrections still get distinct, ordered change times
                 (report_id, user_id, datetime.now(timezone.utc).isoformat(timespec="microseconds"), event_id, patient_id))
    return {"from": row["linked_document_id"], "to": report_id}


def manual_line(enterer: str | None, created_at: str) -> str:
    from .detection import fmt
    return f"Entered manually by {enterer or 'the care team'} on {fmt(_local_date(created_at))} — no report attached."


def manual_linked_line(patient_name: str, test_label: str, uploaded_at: str, reviewer: str | None,
                       named: bool = False) -> str:
    from .detection import fmt
    what = f"“{test_label}”" if named else f"this {test_label} report"
    return (f"{patient_name} uploaded {what} on {fmt(_local_date(uploaded_at))}. "
            f"Reviewed by {reviewer or 'the care team'}.")


# ================================================================== medicine purchase verification
# The patient uploads the bill (a file is compulsory) - no date is ever typed: the upload time is the server's.
# The care team approves or rejects it; the bill's status drives its refill row (trigger, migration 0009).

BILL_REJECT_REASONS = {
    "unclear": "Bill image unclear or unreadable",
    "medicine_mismatch": "Bill does not match the prescribed medicine",
    "date_mismatch": "Bill date does not match upload",
    "other": "Other",
}
BILL_STATUS_TEXT = {"pending_review": "Pending Review", "reviewed_approved": "Approved", "reviewed_rejected": "Rejected"}


def create_medicine_bill(conn, patient_id: str, file_name: str, content_type: str, data: bytes, medicine: str,
                         supply_days: int, asserted_by: str, on: date) -> str:
    """The bill row and its pending refill row, in the caller's transaction. The file is removed if either fails."""
    if not data:
        raise RepoError("Please attach a photo or PDF of your bill.")
    bill_id = new_id()
    key = storage.storage_key(patient_id, bill_id, content_type)
    storage.save(key, data)
    try:
        uploaded = now_iso()
        conn.execute(
            "INSERT INTO external_reports (id, patient_id, document_type, uploaded_by_patient_id, upload_date, disease, "
            "file_name, content_type, size_bytes, sha256, storage_key) VALUES (?, ?, 'medicine_bill', ?, ?, 'Diabetes', ?, ?, ?, ?, ?)",
            (bill_id, patient_id, patient_id, uploaded, (file_name or "bill")[:200], content_type, len(data),
             hashlib.sha256(data).hexdigest(), key))
        # until the clinician reads a purchase date off the bill, the refill is dated on the upload day
        insert_event(conn, patient_id, "refill", on, "patient_upload", "medium", "pending_review",
                     asserted_by, name=medicine, value=supply_days, unit="days", note="Medicine bill uploaded",
                     linked_document_id=bill_id)
    except Exception:
        storage.delete(key)
        raise
    return bill_id


def all_bills_for_patients(conn, patient_ids: list[str]) -> list[dict]:
    """Every medicine bill (pending, approved, rejected) of the given patients - the caller's own - newest first."""
    if not patient_ids:
        return []
    marks = ",".join("?" * len(patient_ids))
    return [_external(r) for r in conn.execute(
        _EXT_SELECT + f"WHERE x.document_type = 'medicine_bill' AND x.patient_id IN ({marks}) ORDER BY x.upload_date DESC",
        patient_ids)]


def list_medicine_bills(conn, patient_id: str) -> list[dict]:
    return list_external_reports(conn, patient_id, "medicine_bill")


def bill_message(approved: bool, uploaded_at: str, reason: str | None) -> str:
    """The exact text of both the e-mail and the in-app message."""
    from .detection import fmt
    when = fmt(_local_date(uploaded_at))
    if approved:
        return f"Your medicine bill from {when} has been verified. Thank you for keeping your record up to date."
    return (f"Your medicine bill from {when} could not be verified. Reason: {reason}. "
            "Please upload a clearer bill or contact your care team.")


def decide_medicine_bill(conn, patient_id: str, bill_id: str, user_id: str, approve: bool,
                         purchase_date: date | None = None, reject_code: str | None = None,
                         reject_note: str | None = None) -> dict:
    """Approve or reject in ONE update of the bill (its refill row follows via trigger), then notify the patient
    by e-mail and in the app with the same message - all in the caller's transaction."""
    bill = get_external_report(conn, patient_id, bill_id, "medicine_bill")
    if bill is None:
        raise RepoError("Bill not found.")
    if bill["status"] != "pending_review":
        raise RepoError("This bill has already been reviewed.")
    if approve:
        reason = None
        conn.execute("UPDATE external_reports SET status = 'reviewed_approved', reviewed_by = ?, reviewed_at = ?, "
                     "purchase_date = ? WHERE id = ? AND patient_id = ?",
                     (user_id, now_iso(), purchase_date.isoformat() if purchase_date else None, bill_id, patient_id))
    else:
        if reject_code not in BILL_REJECT_REASONS:
            raise RepoError("Please choose why the bill is rejected.")
        note = (reject_note or "").strip()
        if reject_code == "other" and len(note) < 3:
            raise RepoError("Please write a short note explaining the reason.")
        reason = note if reject_code == "other" else BILL_REJECT_REASONS[reject_code]
        conn.execute("UPDATE external_reports SET status = 'reviewed_rejected', reviewed_by = ?, reviewed_at = ?, "
                     "reject_code = ?, reject_reason = ? WHERE id = ? AND patient_id = ?",
                     (user_id, now_iso(), reject_code, reason, bill_id, patient_id))
    message = bill_message(approve, bill["upload_date"], reason)
    patient = get_patient(conn, patient_id)
    email = patient.get("email")
    conn.execute("INSERT INTO patient_notifications (patient_id, kind, document_id, message, email_to, email_status, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (patient_id, "bill_approved" if approve else "bill_rejected", bill_id, message, email,
                  "simulated" if email else "no_email_on_file", now_iso()))
    send_email(email, "Your medicine bill" + (" has been verified" if approve else " could not be verified"), message)
    return {"message": message, "email_to": email}


def send_email(to: str | None, subject: str, body: str) -> None:
    """Prototype: no mail service is connected, so the e-mail is logged (and recorded in patient_notifications)."""
    import logging
    logging.getLogger("uc2.email").warning("EMAIL (simulated) to %s | %s | %s", to or "<no email on file>", subject, body)


def list_notifications(conn, patient_id: str, limit: int = 20) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, kind, document_id, appointment_id, test_order_item_id, message, email_to, email_status, created_at, "
        "read_at FROM patient_notifications "
        "WHERE patient_id = ? ORDER BY created_at DESC, id DESC LIMIT ?", (patient_id, limit))]


def mark_notification_read(conn, patient_id: str, notification_id: int) -> bool:
    return conn.execute("UPDATE patient_notifications SET read_at = ? WHERE id = ? AND patient_id = ? AND read_at IS NULL",
                        (now_iso(), notification_id, patient_id)).rowcount == 1


def bill_line(patient_name: str, uploaded_at: str, status: str, reviewer: str | None, reviewed_at: str | None) -> str:
    from .detection import fmt
    uploaded = fmt(_local_date(uploaded_at))
    if status == "active" and reviewer:
        return f"{patient_name} uploaded this medicine bill on {uploaded}. Verified by {reviewer} on {fmt(_local_date(reviewed_at))}."
    if status == "rejected":
        return f"{patient_name} uploaded a medicine bill on {uploaded}. It was not accepted."
    return f"{patient_name} uploaded a medicine bill on {uploaded} — waiting for the care team to verify it."


# ================================================================== assembled record (for detection & dashboards)

def load_patient_record(conn, patient_id: str) -> PatientRecord | None:
    p = get_patient(conn, patient_id)
    if p is None:
        return None
    def provenance(r) -> dict | None:
        """Where a value came from, built from the row's CURRENT linked_document_id every time it is read."""
        manual_value = r["source"] == "care_team_manual" and r["event_type"] == "lab"
        if r["linked_document_id"] and r["x_document_type"] == "medicine_bill":
            return {"kind": "medicine_bill", "document_id": r["linked_document_id"], "uploaded_at": r["x_upload_date"],
                    "content_type": r["x_content_type"], "test_type": "Medicine bill", "reviewed_by": r["x_reviewer"],
                    "text": bill_line(p["full_name"], r["x_upload_date"], r["status"], r["x_reviewer"], r["reviewed_date"])}
        if r["linked_upload_id"]:
            label = r["u_description"] or r["u_file_name"]
            reviewer = r["m_linker"] or r["m_enterer"]
            uploader = p["full_name"] if r["u_by_user"] is None else r["u_uploader"]
            return {"kind": "manual_linked", "origin": "document", "document_id": r["linked_upload_id"], "test_type": label,
                    "uploaded_at": r["u_uploaded_at"], "reviewed_by": reviewer, "content_type": r["u_content_type"],
                    "text": manual_linked_line(uploader, label, r["u_uploaded_at"], reviewer, named=True)}
        if not r["linked_document_id"]:
            if not manual_value:
                return None
            return {"kind": "manual", "document_id": None, "entered_by": r["m_enterer"],
                    "text": manual_line(r["m_enterer"], r["created_at"])}
        label = external_test_label(r["x_test_type"], r["x_test_name"])
        if manual_value:
            reviewer = r["m_linker"] or r["m_enterer"]
            return {"kind": "manual_linked", "document_id": r["linked_document_id"], "test_type": label,
                    "uploaded_at": r["x_upload_date"], "reviewed_by": reviewer, "content_type": r["x_content_type"],
                    "text": manual_linked_line(p["full_name"], label, r["x_upload_date"], reviewer)}
        return {"kind": "external_report", "document_id": r["linked_document_id"], "test_type": label,
                "uploaded_at": r["x_upload_date"], "reviewed_by": r["x_reviewer"], "reviewed_at": r["reviewed_date"],
                "content_type": r["x_content_type"],
                "text": reference_line(p["full_name"], p["patient_code"], label, r["x_upload_date"], r["x_reviewer"],
                                       r["reviewed_date"])}

    events = [Event(
        id=r["id"], event_type=r["event_type"], name=r["name"], value=r["value"], unit=r["unit"],
        effective_date=date.fromisoformat(r["effective_date"]), source=r["source"], confidence=r["confidence"],
        status=r["status"], asserted_by=r["asserted_by"], note=r["note"], facility=r["facility"],
        clinician=r["clinician"], document_id=r["document_id"], provenance=provenance(r),
    ) for r in conn.execute(
        "SELECT e.*, x.upload_date AS x_upload_date, x.test_type AS x_test_type, x.test_name AS x_test_name, "
        "x.content_type AS x_content_type, x.document_type AS x_document_type, "
        "u.full_name AS x_reviewer, m.full_name AS m_enterer, c.full_name AS m_linker, "
        "pd.description AS u_description, pd.file_name AS u_file_name, pd.uploaded_at AS u_uploaded_at, "
        "pd.content_type AS u_content_type, pd.uploaded_by_user_id AS u_by_user, pu.full_name AS u_uploader "
        "FROM clinical_events e "
        "LEFT JOIN external_reports x ON x.id = e.linked_document_id AND x.patient_id = e.patient_id "
        "LEFT JOIN patient_documents pd ON pd.id = e.linked_upload_id AND pd.patient_id = e.patient_id "
        "LEFT JOIN clinical_users pu ON pu.id = pd.uploaded_by_user_id "
        "LEFT JOIN clinical_users u ON u.id = e.reviewed_by "
        "LEFT JOIN clinical_users m ON m.id = e.recorded_by_user_id "
        "LEFT JOIN clinical_users c ON c.id = e.link_changed_by "
        "WHERE e.patient_id = ? ORDER BY e.effective_date, e.id", (patient_id,))]

    for r in conn.execute(
            "SELECT t.*, r.laboratory_name, r.source AS report_source, r.document_id FROM lab_test_results t "
            "JOIN lab_reports r ON r.id = t.report_id AND r.patient_id = t.patient_id "
            "LEFT JOIN patient_documents d ON d.id = r.document_id "
            "WHERE t.patient_id = ? AND COALESCE(d.review_status, '') != 'rejected' "
            "ORDER BY t.test_date, t.created_at", (patient_id,)):
        events.append(Event(
            id=r["id"], event_type="lab", name=r["test_name"], value=r["numeric_value"],
            value_text=None if r["numeric_value"] is not None else r["value_text"], unit=r["unit"],
            effective_date=date.fromisoformat(r["test_date"]), source=r["report_source"], confidence="high",
            status="resulted", asserted_by=r["laboratory_name"], ref_kind=r["reference_kind"], ref_low=r["reference_low"],
            ref_high=r["reference_high"], ref_text=r["reference_text"], crit_low=r["critical_low"],
            crit_high=r["critical_high"], report_id=r["report_id"], document_id=r["document_id"],
        ))
    return PatientRecord(id=p["id"], patient_code=p["patient_code"], full_name=p["full_name"], events=events,
                         phone=p["phone"], abha_number=p["abha_number"], date_of_birth=p["date_of_birth"], sex=p["sex"],
                         reported_age=p["reported_age"], reported_age_on=p["reported_age_on"])
