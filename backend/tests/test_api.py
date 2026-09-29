"""Functional API tests: clinician accounts, dashboard, manual entry, consent flow, patient portal uploads."""

import pytest

from tests.conftest import login_clinician, login_patient, patient_ids

ER = {"date": "2026-09-20", "problem": "Chest pain and high sugar (412 mg/dL)",
      "doctor": "Dr. Anil Rao (Surgeon)", "hospital": "UC2 Hospital — Emergency Department"}


@pytest.fixture()
def ids(priya):
    return patient_ids(priya)


def level(client, pid):
    return client.get(f"/api/patients/{pid}").json()["assessment"]["level"]


# ------------------------------------------------ clinician accounts

def test_signup_generates_unique_clinician_id(make_client):
    codes = set()
    for i in range(5):
        c = make_client()
        r = c.post("/api/auth/clinician/signup", json={"full_name": f"Dr. Test {i}", "phone": f"97000000{i:02d}",
                                                       "password": "longenough"})
        assert r.status_code == 200, r.text
        code = r.json()["clinician_code"]
        assert code.startswith("CLN-") and len(code) == 10
        codes.add(code)
        assert c.get("/api/auth/clinician/me").json()["clinician_code"] == code     # signed in straight away
    assert len(codes) == 5


def test_signup_validation(make_client):
    c = make_client()
    body = {"full_name": "Dr. X", "phone": "9700000000", "password": "longenough"}
    assert c.post("/api/auth/clinician/signup", json={**body, "phone": "123"}).status_code == 422
    assert c.post("/api/auth/clinician/signup", json={**body, "password": "short"}).status_code == 422
    r = c.post("/api/auth/clinician/signup", json={**body, "phone": "9800000001"})          # Priya's phone
    assert r.status_code == 422 and "already exists" in r.json()["detail"]


def test_login_by_code_or_phone_and_logout(make_client):
    c = make_client()
    assert login_clinician(c, "cln-prya27")["full_name"] == "Dr. Priya Nair"                 # case-insensitive ID
    c.post("/api/auth/clinician/logout")
    assert c.get("/api/patients").status_code == 401
    assert login_clinician(c, "98000 00001")["clinician_code"] == "CLN-PRYA27"               # by phone


def test_wrong_password_and_lockout(make_client):
    c = make_client()
    for _ in range(5):
        assert c.post("/api/auth/clinician/login", json={"identifier": "CLN-KRNB58", "password": "nope"}).status_code == 401
    assert c.post("/api/auth/clinician/login", json={"identifier": "CLN-KRNB58", "password": "demo1234"}).status_code == 429


def test_password_is_hashed(seeded_conn):
    conn, _ = seeded_conn
    stored = conn.execute("SELECT password_hash FROM clinical_users WHERE clinician_code = 'CLN-PRYA27'").fetchone()[0]
    assert stored.startswith("scrypt$") and "demo1234" not in stored


# ------------------------------------------------ dashboard

def test_patient_list_levels_and_filter(priya):
    patients = priya.get("/api/patients").json()["patients"]
    assert [(p["full_name"], p["assessment"]["level"]) for p in patients] == [
        ("Meena Rao", "high_priority"), ("Ramesh Kumar", "quietly_worse"), ("Lakshmi Iyer", "none")]
    flagged = priya.get("/api/patients", params={"flagged_only": True}).json()["patients"]
    assert len(flagged) == 2 and all(len(p["assessment"]["signals"]) == 7 for p in flagged)


def test_create_patient_assigns_creator(make_client):
    c = make_client()
    c.post("/api/auth/clinician/signup", json={"full_name": "Dr. New", "phone": "9711111111", "password": "longenough"})
    assert c.get("/api/patients").json()["patients"] == []
    r = c.post("/api/patients", json={"full_name": "Sunita Das", "phone": "9123456780", "date_of_birth": "1970-05-02", "sex": "F"})
    assert r.status_code == 200 and r.json()["patient_code"] == "P-1004"
    [p] = c.get("/api/patients").json()["patients"]
    assert p["full_name"] == "Sunita Das" and p["age"] == 56
    dup = c.post("/api/patients", json={"full_name": "Someone", "phone": "9123456780"})
    assert dup.status_code == 422 and "already registered" in dup.json()["detail"] and "Existing patient" in dup.json()["detail"]


def test_share_with_colleague_by_clinician_id(priya, ids, make_client):
    karan = make_client()
    login_clinician(karan, "CLN-KRNB58")
    assert karan.get(f"/api/patients/{ids['P-1001']}").status_code == 404
    team = priya.post(f"/api/patients/{ids['P-1001']}/care-team", json={"clinician_code": "cln-krnb58"}).json()
    assert {m["clinician_code"] for m in team} == {"CLN-PRYA27", "CLN-KRNB58"}
    assert karan.get(f"/api/patients/{ids['P-1001']}").status_code == 200
    assert [p["patient_code"] for p in karan.get("/api/patients").json()["patients"]] == ["P-1001"]
    assert priya.post(f"/api/patients/{ids['P-1001']}/care-team", json={"clinician_code": "CLN-XXXXXX"}).status_code == 422


def test_access_log_records_views(priya, ids):
    priya.get(f"/api/patients/{ids['P-1002']}")
    log = priya.get(f"/api/patients/{ids['P-1002']}/access-log").json()
    assert log[0]["action"] == "PATIENT_VIEWED" and log[0]["actor_label"] == "Dr. Priya Nair (CLN-PRYA27)"


# ------------------------------------------------ manual entry

def test_manual_hypo_raises_hard_flag_immediately(priya, ids):
    pid = ids["P-1002"]
    body = priya.post(f"/api/patients/{pid}/manual", json={"hypoglycemia": True}).json()
    assert body["assessment"]["level"] == "high_priority"
    e = priya.get(f"/api/patients/{pid}/events", params={"source": "care_team_manual"}).json()[0]
    assert e["event_type"] == "hypo_event" and e["asserted_by"] == "Dr. Priya Nair (care team)"


def test_manual_entry_writes_all_filled_fields(priya, ids):
    pid = ids["P-1002"]
    r = priya.post(f"/api/patients/{pid}/manual", json={
        "sugar": {"value": 182, "date": "2026-09-24"}, "hba1c": {"value": 7.4, "date": "2026-09-20"},
        "missed_refill": {"missed": True, "date": "2026-09-10"}, "overdue_screening": {"type": "Eye", "date": "2026-09-01"}})
    assert r.status_code == 200 and len(r.json()["saved"]) == 4
    events = priya.get(f"/api/patients/{pid}/events", params={"source": "care_team_manual"}).json()
    assert sorted(e["event_type"] for e in events) == ["lab", "lab", "refill", "screening"]   # sugar is a graphed value too
    assert r.json()["assessment"]["level"] == "quietly_worse"


def test_manual_entry_validation(priya, ids):
    pid = ids["P-1002"]
    assert priya.post(f"/api/patients/{pid}/manual", json={}).status_code == 422
    assert priya.post(f"/api/patients/{pid}/manual", json={"sugar": {"value": 5000, "date": "2026-09-24"}}).status_code == 422
    assert priya.post(f"/api/patients/{pid}/manual", json={"hba1c": {"value": 7, "date": "2030-01-01"}}).status_code == 422


def test_manual_emergency_visit(priya, ids):
    pid = ids["P-1001"]
    body = priya.post(f"/api/patients/{pid}/manual", json={"emergency": ER}).json()
    assert body["assessment"]["level"] == "high_priority"
    er = next(s for s in body["assessment"]["signals"] if s["key"] == "emergency_visit")
    facts = {f["label"]: f["value"] for f in er["facts"]}
    assert facts["Problem"] == ER["problem"] and facts["Treating doctor"] == ER["doctor"]
    h = priya.get(f"/api/patients/{pid}/emergency-visits").json()
    assert [v["counts_toward_flag"] for v in h["visits"]] == [True, False]


@pytest.mark.parametrize("missing", ["problem", "doctor", "hospital"])
def test_emergency_visit_requires_every_field(priya, ids, missing):
    r = priya.post(f"/api/patients/{ids['P-1001']}/manual", json={"emergency": {**ER, missing: "  "}})
    assert r.status_code == 422 and "Emergency visit: please enter" in r.json()["detail"]


# ------------------------------------------------ consent flow (clinician + patient, separate browsers)

def test_consent_approve_flow(priya, ids, make_client):
    pid = ids["P-1001"]
    req = priya.post(f"/api/patients/{pid}/consent-requests", json={
        "abha_number": "91-1234-5678-9012", "hi_types": ["Prescription", "DiagnosticReport", "DischargeSummary"]}).json()
    assert req["status"] == "REQUESTED" and req["requester"] == "Dr. Priya Nair, UC2 Diabetes Clinic"

    ramesh = make_client()
    login_patient(ramesh, "9000000001")
    assert [c["id"] for c in ramesh.get("/api/me/consents").json()] == [req["id"]]
    r = ramesh.post(f"/api/me/consents/{req['id']}", json={"approve": True})
    assert r.json()["status"] == "GRANTED" and r.json()["records_received"] == 3
    assert level(priya, pid) == "high_priority"
    assert ramesh.post(f"/api/me/consents/{req['id']}", json={"approve": True}).status_code == 409
    log = priya.get("/api/consent-log").json()
    assert [row["action"] for row in log] == ["DATA_RECEIVED", "GRANTED", "REQUESTED"]


def test_consent_expiry(priya, ids):
    pid = ids["P-1002"]
    req = priya.post(f"/api/patients/{pid}/consent-requests", json={"abha_number": "91234567890123", "hi_types": ["Prescription"]}).json()
    assert priya.post(f"/api/patients/{pid}/consent-requests/{req['id']}/simulate-expiry").json()["status"] == "EXPIRED"


# ------------------------------------------------ patient portal uploads

def test_patient_uploads_arrive_as_pending_and_are_reviewed_once(priya, ids, make_client):
    pid = ids["P-1003"]
    meena = make_client()
    login_patient(meena, "P-1003")
    meena.post("/api/me/sugar", data={"value": "131"}, files={"photo": ("m.jpg", b"\xff\xd8 fake", "image/jpeg")})
    meena.post("/api/me/sugar", data={"value": "118"})                                   # typed only, no photo
    meena.post("/api/me/documents", data={"kind": "lab_report", "description": "Kidney test"},
               files={"file": ("r.pdf", b"%PDF-1.4 fake", "application/pdf")})
    up = priya.get(f"/api/patients/{pid}/uploads").json()
    with_photo = next(e for e in up["events"] if e["value"] == 131)
    typed = next(e for e in up["events"] if e["value"] == 118)
    assert with_photo["review_status"] == typed["review_status"] == "pending"
    assert [d["description"] for d in up["documents"]] == ["Kidney test"]

    # a reading with a photo can't be reviewed on its own - only through its photo
    assert priya.post(f"/api/patients/{pid}/events/{with_photo['id']}/review", json={"status": "confirmed"}).status_code == 404
    assert priya.post(f"/api/patients/{pid}/documents/{with_photo['photo_id']}/review", json={"status": "confirmed"}).status_code == 200
    up = priya.get(f"/api/patients/{pid}/uploads").json()
    with_photo = next(e for e in up["events"] if e["value"] == 131)
    assert with_photo["review_status"] == "confirmed" and with_photo["confidence"] == "high"      # photo + reading agree
    assert with_photo["reviewed_by_name"] == "Dr. Priya Nair"

    # a typed value is reviewed once, in Reports & documents; rejecting marks it low confidence
    assert priya.post(f"/api/patients/{pid}/events/{typed['id']}/review", json={"status": "rejected"}).status_code == 200
    assert priya.post(f"/api/patients/{pid}/events/{typed['id']}/review", json={"status": "confirmed"}).status_code == 404
    typed = next(e for e in priya.get(f"/api/patients/{pid}/uploads").json()["events"] if e["value"] == 118)
    assert typed["review_status"] == "rejected" and typed["confidence"] == "low"
    assert typed["reviewed_by_name"] == "Dr. Priya Nair" and "Rejected" not in (typed["note"] or "")

    # rejecting a photo updates its reading too
    meena.post("/api/me/sugar", data={"value": "500"}, files={"photo": ("m.jpg", b"\xff\xd8 x", "image/jpeg")})
    bad = next(e for e in priya.get(f"/api/patients/{pid}/uploads").json()["events"] if e["value"] == 500)
    priya.post(f"/api/patients/{pid}/documents/{bad['photo_id']}/review", json={"status": "rejected"})
    bad = next(e for e in priya.get(f"/api/patients/{pid}/uploads").json()["events"] if e["value"] == 500)
    assert bad["review_status"] == "rejected" and bad["confidence"] == "low"


def test_patient_upload_view_all_includes_older_items(priya, ids, make_client):
    import os
    import sqlite3
    pid = ids["P-1003"]
    meena = make_client()
    login_patient(meena, "P-1003")
    meena.post("/api/me/sugar", data={"value": "140"})
    meena.post("/api/me/documents", data={"kind": "prescription"},
               files={"file": ("rx.pdf", b"%PDF-1.4 fake", "application/pdf")})
    # backdate both to 90 days ago - outside the default 60-day window
    db = sqlite3.connect(os.environ["UC2_DB_PATH"])
    db.execute("UPDATE clinical_events SET effective_date = date(effective_date, '-90 days') "
               "WHERE patient_id = ? AND source = 'patient_upload' AND value = 140", (pid,))
    db.execute("UPDATE patient_documents SET uploaded_at = datetime(uploaded_at, '-90 days') "
               "WHERE patient_id = ? AND document_type = 'prescription'", (pid,))
    db.commit()
    db.close()

    recent = priya.get(f"/api/patients/{pid}/uploads").json()
    assert 140 not in [e["value"] for e in recent["events"]]
    assert "prescription" not in [d["document_type"] for d in recent["documents"]]
    everything = priya.get(f"/api/patients/{pid}/uploads?all=true").json()
    assert 140 in [e["value"] for e in everything["events"]]
    assert "prescription" in [d["document_type"] for d in everything["documents"]]


@pytest.mark.parametrize("description", [None, "", "   ", "x"])
def test_lab_report_requires_report_type(make_client, description):
    c = make_client()
    login_patient(c, "P-1003")
    data = {"kind": "lab_report"} | ({"description": description} if description is not None else {})
    r = c.post("/api/me/documents", data=data, files={"file": ("r.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 422
    assert c.get("/api/me/documents").json() == []


def test_upload_validation(make_client):
    c = make_client()
    login_patient(c, "P-1003")
    assert c.post("/api/me/sugar", data={"value": "5000"}).status_code == 422
    assert c.post("/api/me/documents", data={"kind": "prescription"},
                  files={"file": ("x.exe", b"MZ", "application/octet-stream")}).status_code == 415


def test_patient_login_code(make_client):
    c = make_client()
    r = c.post("/api/auth/patient/request-code", json={"identifier": "+91 90000 00002"})
    assert r.json()["phone_hint"].endswith("0002") and len(r.json()["dev_code"]) == 6
    assert c.post("/api/auth/patient/verify", json={"identifier": "9000000002", "code": "000000"}).status_code == 401
    ok = c.post("/api/auth/patient/verify", json={"identifier": "9000000002", "code": r.json()["dev_code"]})
    assert ok.json()["first_name"] == "Lakshmi"
    assert c.post("/api/auth/patient/verify", json={"identifier": "9000000002", "code": r.json()["dev_code"]}).status_code == 401
    assert c.post("/api/auth/patient/request-code", json={"identifier": "99999"}).status_code == 404


# ------------------------------------------------ Settings

def test_update_account_and_theme(priya):
    r = priya.patch("/api/auth/clinician/me", json={"full_name": "Dr. Priya N. Nair", "theme": "dark"})
    assert r.status_code == 200 and r.json()["full_name"] == "Dr. Priya N. Nair" and r.json()["theme"] == "dark"
    assert priya.get("/api/auth/clinician/me").json()["theme"] == "dark"             # saved with the account
    assert priya.patch("/api/auth/clinician/me", json={"theme": "neon"}).status_code == 422
    assert priya.patch("/api/auth/clinician/me", json={"phone": "9800000002"}).status_code == 422   # Karan's phone
    assert priya.patch("/api/auth/clinician/me", json={"full_name": " "}).status_code == 422


def test_change_password_signs_out_other_sessions(priya, make_client):
    laptop = make_client()
    login_clinician(laptop)
    assert priya.post("/api/auth/clinician/change-password",
                      json={"current_password": "wrong", "new_password": "new-pass-123"}).status_code == 422
    assert priya.post("/api/auth/clinician/change-password",
                      json={"current_password": "demo1234", "new_password": "short"}).status_code == 422
    r = priya.post("/api/auth/clinician/change-password", json={"current_password": "demo1234", "new_password": "new-pass-123"})
    assert r.status_code == 200
    assert priya.get("/api/patients").status_code == 200          # this session stays
    assert laptop.get("/api/patients").status_code == 401         # other sessions signed out
    assert make_client().post("/api/auth/clinician/login", json={"identifier": "CLN-PRYA27", "password": "demo1234"}).status_code == 401
    login_clinician(make_client(), "CLN-PRYA27", "new-pass-123")


def test_access_log_only_covers_my_patients(priya, ids, make_client):
    priya.get(f"/api/patients/{ids['P-1001']}")
    priya.get(f"/api/patients/{ids['P-1002']}")
    all_rows = priya.get("/api/access-log").json()
    assert {r["patient_code"] for r in all_rows} >= {"P-1001", "P-1002"}
    one = priya.get("/api/access-log", params={"patient_id": ids["P-1002"]}).json()
    assert one and {r["patient_code"] for r in one} == {"P-1002"}
    karan = make_client()
    login_clinician(karan, "CLN-KRNB58")
    assert karan.get("/api/access-log").json() == []
    assert karan.get("/api/access-log", params={"patient_id": ids["P-1001"]}).json() == []    # can't peek



def test_worklist_is_real_and_scoped(priya, ids, make_client):
    w = priya.get("/api/worklist").json()
    assert {v["patient_code"] for v in w["upcoming_visits"]} == {"P-1001", "P-1002", "P-1003"}      # next visits in 30 days
    lipid = [r for r in w["reviews"] if r["kind"] == "document"]
    assert [(r["label"], r["detail"]) for r in lipid] == [("Lab report: Lipid profile", "Values not entered yet")]
    assert not any(r["kind"] == "results" for r in w["reviews"])          # one item per report, not two
    priya.post(f"/api/patients/{ids['P-1001']}/documents/{lipid[0]['id']}/review", json={"status": "confirmed"})
    after = priya.get("/api/worklist").json()["reviews"]
    assert [(r["kind"], r["patient_code"]) for r in after if r["kind"] in ("document", "results")] == [("results", "P-1001")]
    priya.post(f"/api/patients/{ids['P-1002']}/consent-requests", json={"abha_number": "91234567890123", "hi_types": ["Prescription"]})
    assert [c["patient_code"] for c in priya.get("/api/worklist").json()["consents"]] == ["P-1002"]
    assert {a["patient_code"] for a in w["activity"]} == {"P-1001", "P-1002"}     # Ramesh sent a report, Lakshmi logged sugar
    karan = make_client()
    login_clinician(karan, "CLN-KRNB58")
    assert karan.get("/api/worklist").json()["reviews"] == []


def test_care_team_access_can_be_revoked_but_never_the_last(priya, ids, make_client):
    pid = ids["P-1001"]
    team = priya.post(f"/api/patients/{pid}/care-team", json={"clinician_code": "CLN-KRNB58"}).json()
    karan_row = next(m for m in team if m["clinician_code"] == "CLN-KRNB58")
    assert karan_row["granted_by_name"] == "Dr. Priya Nair"
    karan = make_client()
    login_clinician(karan, "CLN-KRNB58")
    assert karan.get(f"/api/patients/{pid}").status_code == 200
    after = priya.delete(f"/api/patients/{pid}/care-team/CLN-KRNB58")
    assert after.status_code == 200 and [m["clinician_code"] for m in after.json()] == ["CLN-PRYA27"]
    assert karan.get(f"/api/patients/{pid}").status_code == 404                     # access gone at once
    assert priya.delete(f"/api/patients/{pid}/care-team/CLN-KRNB58").status_code == 404
    assert priya.delete(f"/api/patients/{pid}/care-team/CLN-PRYA27").status_code == 409   # the last one stays
    log = priya.get(f"/api/patients/{pid}/access-log").json()
    assert any(e["action"] == "CARE_TEAM_REMOVED" for e in log)
    assert karan.delete(f"/api/patients/{pid}/care-team/CLN-PRYA27").status_code == 404    # outsiders can't touch it
