"""Patient self sign-up, sign-in, profile, and patient-controlled doctor access."""

import pytest

from tests.conftest import login_clinician, login_patient, patient_ids

NEW = {"full_name": "Sunita Das", "phone": "91234 56780", "date_of_birth": "1970-05-02", "sex": "F", "password": "sunita-pw"}


def signup(client, details=NEW):
    r = client.post("/api/auth/patient/signup", json=details)
    assert r.status_code == 200, r.text
    return r.json()["dev_code"]


def test_signup_creates_account_only_after_phone_is_verified(make_client, seeded_conn):
    conn, _ = seeded_conn
    c = make_client()
    code = signup(c)
    assert conn.execute("SELECT COUNT(*) FROM patients WHERE phone = '9123456780'").fetchone()[0] == 0   # not yet
    assert c.post("/api/auth/patient/signup/verify", json={"phone": NEW["phone"], "code": "000000"}).status_code == 401
    r = c.post("/api/auth/patient/signup/verify", json={"phone": NEW["phone"], "code": code})
    assert r.status_code == 200
    me = r.json()
    assert me["full_name"] == "Sunita Das" and me["patient_code"] == "P-1004"
    assert c.get("/api/auth/patient/me").json()["patient_code"] == "P-1004"                           # signed in
    profile = c.get("/api/me/profile").json()
    assert profile["date_of_birth"] == "1970-05-02" and profile["sex"] == "F" and profile["phone_hint"].endswith("6780")
    assert conn.execute("SELECT COUNT(*) FROM patient_signups").fetchone()[0] == 0                     # cleaned up


def test_new_patient_can_sign_in_again_with_a_code(make_client):
    c = make_client()
    c.post("/api/auth/patient/signup/verify", json={"phone": NEW["phone"], "code": signup(c)})
    c.post("/api/auth/patient/logout")
    assert c.get("/api/me/profile").status_code == 401
    assert login_patient(make_client(), "P-1004")["first_name"] == "Sunita"


def test_signup_rejects_existing_phone_and_bad_input(make_client):
    c = make_client()
    r = c.post("/api/auth/patient/signup", json={**NEW, "phone": "9000000001"})              # Ramesh
    assert r.status_code == 409 and "Please sign in" in r.json()["detail"]
    assert c.post("/api/auth/patient/signup", json={**NEW, "phone": "123"}).status_code == 422
    assert c.post("/api/auth/patient/signup", json={**NEW, "full_name": " "}).status_code == 422
    assert c.post("/api/auth/patient/signup", json={**NEW, "date_of_birth": "2999-01-01"}).status_code == 422
    r = c.post("/api/auth/patient/signup", json={**NEW, "password": "12345"})
    assert r.status_code == 422 and "at least 6" in r.json()["detail"]


def test_signup_code_is_single_use_and_rate_limited(make_client):
    c = make_client()
    code = signup(c)
    for _ in range(5):
        c.post("/api/auth/patient/signup/verify", json={"phone": NEW["phone"], "code": "111111"})
    assert c.post("/api/auth/patient/signup/verify", json={"phone": NEW["phone"], "code": code}).status_code == 429


def test_self_registered_patient_is_invisible_until_they_add_a_doctor(make_client):
    sunita = make_client()
    sunita.post("/api/auth/patient/signup/verify", json={"phone": NEW["phone"], "code": signup(sunita)})
    priya = make_client()
    login_clinician(priya)
    assert "P-1004" not in patient_ids(priya)

    r = sunita.post("/api/me/care-team", json={"clinician_code": "cln-prya27"})
    assert [d["clinician_code"] for d in r.json()] == ["CLN-PRYA27"]
    ids = patient_ids(priya)
    assert "P-1004" in ids and priya.get(f"/api/patients/{ids['P-1004']}").status_code == 200

    log = priya.get(f"/api/patients/{ids['P-1004']}/access-log").json()
    assert any(a["action"] == "CARE_TEAM_ADDED" and a["actor_label"] == "Sunita Das (patient)" for a in log)

    # patient withdraws access -> doctor loses it on the next request
    assert sunita.delete("/api/me/care-team/CLN-PRYA27").json() == []
    assert priya.get(f"/api/patients/{ids['P-1004']}").status_code == 404
    assert "P-1004" not in patient_ids(priya)


def test_add_doctor_validation(make_client):
    c = make_client()
    login_patient(c, "9000000002")
    assert c.post("/api/me/care-team", json={"clinician_code": "CLN-NOPE22"}).status_code == 422
    assert c.delete("/api/me/care-team/CLN-KRNB58").status_code == 404          # not on her list


def test_patient_sees_their_existing_doctors(make_client):
    c = make_client()
    login_patient(c, "9000000001")
    assert [d["full_name"] for d in c.get("/api/me/care-team").json()] == ["Dr. Priya Nair"]


def test_clinician_add_patient_with_registered_phone_explains_what_to_do(make_client):
    c = make_client()
    login_clinician(c, "CLN-KRNB58")
    r = c.post("/api/patients", json={"full_name": "Ramesh K", "phone": "9000000001"})
    assert r.status_code == 422 and "CLN-KRNB58" in r.json()["detail"]


# ------------------------------------------------ fresh system + linking an existing patient

def test_fresh_start_is_completely_empty(empty_client):
    import os
    from app import db
    conn = db.connect(os.environ["UC2_DB_PATH"])
    for table in ("patients", "clinical_users", "care_team_assignments", "patient_documents", "lab_reports",
                  "lab_test_results", "clinical_events", "consent_requests", "audit_log"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert empty_client.post("/api/auth/clinician/login", json={"identifier": "CLN-PRYA27", "password": "demo1234"}).status_code == 401


def test_brand_new_system_clinician_and_patient_meet_by_patient_id(empty_client):
    """Clinician signs up, patient signs up separately, clinician links the patient by Patient ID + mobile."""
    from fastapi.testclient import TestClient
    from app.main import app
    doctor = empty_client
    me = doctor.post("/api/auth/clinician/signup", json={"full_name": "Dr. Asha Verma", "phone": "9812345678",
                                                         "password": "asha-secure-1"}).json()
    assert doctor.get("/api/patients").json()["patients"] == []

    with TestClient(app) as patient:
        code = patient.post("/api/auth/patient/signup", json={"full_name": "Sunita Das", "phone": "9123456780", "password": "sunita-pw"}).json()["dev_code"]
        pc = patient.post("/api/auth/patient/signup/verify", json={"phone": "9123456780", "code": code}).json()["patient_code"]
        assert pc == "P-1001"                                                 # first patient in an empty system
        patient.post("/api/me/sugar", data={"value": "142"})

        wrong = doctor.post("/api/patients/link", json={"patient_code": pc, "phone": "9999999999"})
        assert wrong.status_code == 404 and "Patient ID and mobile number" in wrong.json()["detail"]
        assert doctor.get("/api/patients").json()["patients"] == []

        r = doctor.post("/api/patients/link", json={"patient_code": pc.lower(), "phone": "+91 91234 56780"})
        assert r.status_code == 200 and r.json()["full_name"] == "Sunita Das" and r.json()["already_linked"] is False
        pid = r.json()["id"]
        # the clinician now sees everything the patient has sent
        up = doctor.get(f"/api/patients/{pid}/uploads").json()
        assert [e["value"] for e in up["events"]] == [142]
        # and the patient sees the clinician in "My doctors"
        assert [d["clinician_code"] for d in patient.get("/api/me/care-team").json()] == [me["clinician_code"]]
        # linking again is harmless
        assert doctor.post("/api/patients/link", json={"patient_code": pc, "phone": "9123456780"}).json()["already_linked"]


def test_link_is_rate_limited(empty_client):
    empty_client.post("/api/auth/clinician/signup", json={"full_name": "Dr. X", "phone": "9812345670", "password": "longenough"})
    for i in range(5):
        assert empty_client.post("/api/patients/link", json={"patient_code": f"P-{1001 + i}", "phone": "9000000000"}).status_code == 404
    assert empty_client.post("/api/patients/link", json={"patient_code": "P-1001", "phone": "9000000000"}).status_code == 429


def test_link_requires_sign_in(empty_client):
    assert empty_client.post("/api/patients/link", json={"patient_code": "P-1001", "phone": "9000000001"}).status_code == 401
