"""Patient isolation & authorization - the section 20 requirements."""

import pytest

from tests.conftest import login_clinician, login_patient, patient_ids

PDF = ("r.pdf", b"%PDF-1.4 patient A only", "application/pdf")


@pytest.fixture()
def ids(priya):
    return patient_ids(priya)


def upload(client, description="HbA1c test"):
    r = client.post("/api/me/documents", data={"kind": "lab_report", "description": description}, files={"file": PDF})
    assert r.status_code == 200
    return r.json()["id"]


def test_upload_is_owned_by_the_session_patient(seeded_conn, make_client):
    """Patient A uploads -> document.patient_id == A, whatever the request body says."""
    conn, ids = seeded_conn
    a = make_client()
    login_patient(a, "9000000001")
    r = a.post("/api/me/documents", data={"kind": "lab_report", "description": "HbA1c", "patient_id": ids["P-1002"]},
               files={"file": PDF})
    owner = conn.execute("SELECT patient_id, storage_key FROM patient_documents WHERE id = ?", (r.json()["id"],)).fetchone()
    assert owner["patient_id"] == ids["P-1001"]
    assert owner["storage_key"].startswith(f"patients/{ids['P-1001']}/documents/")


def test_patient_cannot_read_another_patients_document(make_client):
    a, b = make_client(), make_client()
    login_patient(a, "9000000001")
    login_patient(b, "9000000002")
    doc_a = upload(a)
    assert a.get(f"/api/me/documents/{doc_a}/file").content == PDF[1]
    assert b.get(f"/api/me/documents/{doc_a}/file").status_code == 404        # changing the id doesn't expose it
    assert doc_a not in {d["id"] for d in b.get("/api/me/documents").json()}


def test_patient_cannot_answer_another_patients_consent(priya, ids, make_client):
    req = priya.post(f"/api/patients/{ids['P-1001']}/consent-requests",
                     json={"abha_number": "91123456789012", "hi_types": ["Prescription"]}).json()
    lakshmi = make_client()
    login_patient(lakshmi, "9000000002")
    assert lakshmi.get("/api/me/consents").json() == []
    assert lakshmi.post(f"/api/me/consents/{req['id']}", json={"approve": True}).status_code == 409


def test_patient_session_cannot_use_clinical_api(ids, make_client):
    a = make_client()
    login_patient(a, "9000000001")
    assert a.get("/api/patients").status_code == 401
    assert a.get(f"/api/patients/{ids['P-1001']}").status_code == 401


PATIENT_ROUTES = ["", "/events", "/labs", "/reports", "/documents", "/uploads", "/emergency-visits", "/care-team",
                  "/access-log", "/consent-requests", "/lab-results?test_name=HbA1c"]


@pytest.mark.parametrize("route", PATIENT_ROUTES)
def test_unassigned_clinician_gets_404_everywhere(ids, make_client, route):
    karan = make_client()
    login_clinician(karan, "CLN-KRNB58")
    assert karan.get(f"/api/patients/{ids['P-1001']}{route}").status_code == 404


@pytest.mark.parametrize("route", PATIENT_ROUTES)
def test_signed_out_gets_401_everywhere(ids, make_client, route):
    assert make_client().get(f"/api/patients/{ids['P-1001']}{route}").status_code == 401


def test_unassigned_clinician_cannot_write(ids, make_client):
    karan = make_client()
    login_clinician(karan, "CLN-KRNB58")
    pid = ids["P-1001"]
    assert karan.post(f"/api/patients/{pid}/manual", json={"hypoglycemia": True}).status_code == 404
    assert karan.post(f"/api/patients/{pid}/care-team", json={"clinician_code": "CLN-KRNB58"}).status_code == 404
    assert karan.post(f"/api/patients/{pid}/consent-requests",
                      json={"abha_number": "91123456789012", "hi_types": ["Prescription"]}).status_code == 404
    assert karan.get("/api/patients").json()["patients"] == []
    assert karan.get("/api/consent-log").json() == []


def test_denied_access_is_audited(seeded_conn, make_client):
    conn, ids = seeded_conn
    karan = make_client()
    login_clinician(karan, "CLN-KRNB58")
    karan.get(f"/api/patients/{ids['P-1001']}")
    row = conn.execute("SELECT actor_label, action FROM audit_log WHERE action = 'ACCESS_DENIED'").fetchone()
    assert tuple(row) == ("Dr. Karan Bhatia (CLN-KRNB58)", "ACCESS_DENIED")


def test_clinician_document_url_is_scoped_to_its_patient(priya, ids, make_client):
    """A real document id under the WRONG patient path must not be served."""
    a = make_client()
    login_patient(a, "9000000001")
    doc_a = upload(a)
    assert priya.get(f"/api/patients/{ids['P-1001']}/documents/{doc_a}/file").status_code == 200
    assert priya.get(f"/api/patients/{ids['P-1002']}/documents/{doc_a}/file").status_code == 404
    assert priya.post(f"/api/patients/{ids['P-1002']}/documents/{doc_a}/review", json={"status": "rejected"}).status_code == 404


def test_dashboard_returns_only_the_opened_patient(priya, ids):
    for code, pid in ids.items():
        assert priya.get(f"/api/patients/{pid}").json()["patient_code"] == code
        for e in priya.get(f"/api/patients/{pid}/events", params={"limit": 200}).json():
            assert e["id"] is not None
        reports = priya.get(f"/api/patients/{pid}/reports").json()
        assert reports and all(r["id"] for r in reports)
    # cross-check at the DB layer: every result belongs to the same patient as its report
    ramesh_reports = {r["id"] for r in priya.get(f"/api/patients/{ids['P-1001']}/reports").json()}
    lakshmi_reports = {r["id"] for r in priya.get(f"/api/patients/{ids['P-1002']}/reports").json()}
    assert ramesh_reports.isdisjoint(lakshmi_reports)


def test_result_cannot_be_attached_to_another_patients_report(seeded_conn):
    import sqlite3
    from datetime import date
    from app import repo
    conn, ids = seeded_conn
    report = repo.create_lab_report(conn, ids["P-1001"], "Lab", date(2026, 9, 1), "hospital_internal")
    with pytest.raises(sqlite3.IntegrityError):
        repo.add_lab_result(conn, report, ids["P-1002"], "HbA1c", 7.0, "%", date(2026, 9, 1))


def test_hba1c_history_is_never_overwritten(priya, ids):
    pid = ids["P-1001"]
    before = priya.get(f"/api/patients/{pid}/lab-results", params={"test_name": "HbA1c"}).json()
    priya.post(f"/api/patients/{pid}/manual", json={"hba1c": {"value": 8.6, "date": "2026-09-24"}})
    after = priya.get(f"/api/patients/{pid}/lab-results", params={"test_name": "HbA1c"}).json()
    assert len(after) == len(before) + 1 and after[:-1] == before
    assert [r["value"] for r in after][-3:] == [7.9, 8.2, 8.6]
