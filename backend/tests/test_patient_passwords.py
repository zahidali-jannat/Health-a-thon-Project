"""Patient ID + password sign-in, clinic-issued temporary passwords, and password changes."""

from tests.conftest import login_clinician

NEW_PATIENT = {"full_name": "Ravi Shankar", "phone": "9876501234", "date_of_birth": "1965-01-20", "sex": "M"}


def create_by_clinician(client):
    r = client.post("/api/patients", json=NEW_PATIENT)
    assert r.status_code == 200, r.text
    return r.json()


def test_clinician_created_patient_gets_id_and_temporary_password(priya, make_client, seeded_conn):
    conn, _ = seeded_conn
    created = create_by_clinician(priya)
    assert created["patient_code"] == "P-1004"
    temp = created["temporary_password"]
    assert len(temp) == 9 and temp[4] == "-"
    stored = conn.execute("SELECT password_hash, must_change_password FROM patients WHERE patient_code = 'P-1004'").fetchone()
    assert stored["password_hash"].startswith("scrypt$") and temp not in stored["password_hash"] and stored["must_change_password"] == 1


def test_first_sign_in_forces_a_new_password(priya, make_client):
    temp = create_by_clinician(priya)["temporary_password"]
    ravi = make_client()
    r = ravi.post("/api/auth/patient/login", json={"identifier": "p-1004", "password": temp.lower()})
    assert r.status_code == 401                                              # passwords are case-sensitive
    r = ravi.post("/api/auth/patient/login", json={"identifier": "P-1004", "password": temp})
    assert r.status_code == 200 and r.json()["needs_password"] is True
    # nothing else works until they choose their own password
    assert ravi.get("/api/me/consents").status_code == 403
    assert ravi.post("/api/me/sugar", data={"value": "120"}).status_code == 403
    assert ravi.get("/api/auth/patient/me").json()["needs_password"] is True
    assert ravi.post("/api/auth/patient/set-password", json={"new_password": temp}).status_code == 422   # not the temp one
    assert ravi.post("/api/auth/patient/set-password", json={"new_password": "123"}).status_code == 422
    r = ravi.post("/api/auth/patient/set-password", json={"new_password": "ravi-own-pw"})
    assert r.status_code == 200 and r.json()["needs_password"] is False
    assert ravi.post("/api/me/sugar", data={"value": "120"}).status_code == 200

    other = make_client()
    assert other.post("/api/auth/patient/login", json={"identifier": "P-1004", "password": temp}).status_code == 401
    assert other.post("/api/auth/patient/login", json={"identifier": "98765 01234", "password": "ravi-own-pw"}).status_code == 200


def test_changing_password_requires_the_current_one(make_client):
    c = make_client()
    assert c.post("/api/auth/patient/login", json={"identifier": "P-1002", "password": "demo1234"}).status_code == 200
    assert c.post("/api/auth/patient/set-password", json={"new_password": "brand-new-1"}).status_code == 401
    assert c.post("/api/auth/patient/set-password", json={"new_password": "brand-new-1", "current_password": "wrong"}).status_code == 401
    assert c.post("/api/auth/patient/set-password", json={"new_password": "brand-new-1", "current_password": "demo1234"}).status_code == 200
    assert make_client().post("/api/auth/patient/login", json={"identifier": "P-1002", "password": "brand-new-1"}).status_code == 200


def test_clinician_reset_issues_new_temp_password_and_signs_patient_out(priya, make_client):
    created = create_by_clinician(priya)
    ravi = make_client()
    ravi.post("/api/auth/patient/login", json={"identifier": "P-1004", "password": created["temporary_password"]})
    ravi.post("/api/auth/patient/set-password", json={"new_password": "ravi-own-pw"})
    assert ravi.get("/api/me/consents").status_code == 200

    reset = priya.post(f"/api/patients/{created['id']}/reset-password").json()
    assert reset["temporary_password"] != created["temporary_password"]
    assert ravi.get("/api/me/consents").status_code == 401                   # old session ended
    again = make_client()
    assert again.post("/api/auth/patient/login", json={"identifier": "P-1004", "password": "ravi-own-pw"}).status_code == 401
    r = again.post("/api/auth/patient/login", json={"identifier": "P-1004", "password": reset["temporary_password"]})
    assert r.json()["needs_password"] is True


def test_only_the_care_team_can_reset_a_password(priya, make_client):
    created = create_by_clinician(priya)
    karan = make_client()
    login_clinician(karan, "CLN-KRNB58")
    assert karan.post(f"/api/patients/{created['id']}/reset-password").status_code == 404


def test_password_login_lockout_and_unknown_patient(make_client):
    c = make_client()
    assert c.post("/api/auth/patient/login", json={"identifier": "P-9999", "password": "whatever"}).status_code == 401
    for _ in range(5):
        c.post("/api/auth/patient/login", json={"identifier": "P-1001", "password": "wrong-pw"})
    assert c.post("/api/auth/patient/login", json={"identifier": "P-1001", "password": "demo1234"}).status_code == 429


def test_self_signup_password_works_for_sign_in(make_client):
    c = make_client()
    code = c.post("/api/auth/patient/signup", json={"full_name": "Meera J", "phone": "9123400000", "password": "meera-pw"}).json()["dev_code"]
    assert c.post("/api/auth/patient/signup/verify", json={"phone": "9123400000", "code": code}).json()["needs_password"] is False
    assert make_client().post("/api/auth/patient/login", json={"identifier": "9123400000", "password": "meera-pw"}).status_code == 200


def test_code_login_without_password_must_set_one(priya, make_client):
    created = create_by_clinician(priya)             # has only a temporary password
    c = make_client()
    code = c.post("/api/auth/patient/request-code", json={"identifier": "P-1004"}).json()["dev_code"]
    assert c.post("/api/auth/patient/verify", json={"identifier": "P-1004", "code": code}).json()["needs_password"] is True
    assert c.get("/api/me/consents").status_code == 403
    assert created["patient_code"] == "P-1004"


def test_forgot_password_code_sign_in_lets_patient_choose_a_new_one(make_client):
    c = make_client()
    code = c.post("/api/auth/patient/request-code", json={"identifier": "P-1002"}).json()["dev_code"]
    r = c.post("/api/auth/patient/verify", json={"identifier": "P-1002", "code": code, "forgot_password": True})
    assert r.json()["needs_password"] is True
    assert c.post("/api/auth/patient/set-password", json={"new_password": "fresh-pw-9"}).status_code == 200   # no current needed
    assert make_client().post("/api/auth/patient/login", json={"identifier": "P-1002", "password": "fresh-pw-9"}).status_code == 200
    assert make_client().post("/api/auth/patient/login", json={"identifier": "P-1002", "password": "demo1234"}).status_code == 401
