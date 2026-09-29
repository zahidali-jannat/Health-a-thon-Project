"""External Report Review through the API: patient upload -> clinician queue -> one-step review -> graph reference."""

import pytest

from tests.conftest import TODAY, login_patient, patient_ids

PDF = ("hba1c.pdf", b"%PDF-1.4 outside lab report", "application/pdf")


@pytest.fixture()
def ids(priya):
    return patient_ids(priya)


def upload(client, test_type="HbA1c", test_date="2026-09-20", test_name=None, file=PDF):
    data = {"test_type": test_type, "test_date": test_date} | ({"test_name": test_name} if test_name else {})
    return client.post("/api/me/external-reports", data=data, files={"file": file})


# ------------------------------------------------ step 2: patient upload

def test_patient_uploads_report_without_any_value(make_client):
    ramesh = make_client()
    login_patient(ramesh, "P-1001")
    r = upload(ramesh)
    assert r.status_code == 200, r.text
    assert r.json()["message"] == "Report uploaded. Your care team will review it shortly."
    mine = ramesh.get("/api/me/external-reports").json()
    assert len(mine) == 1
    x = mine[0]
    assert (x["test_type"], x["test_date"], x["status"], x["result"]) == ("HbA1c", "2026-09-20", "pending_review", None)
    assert x["upload_date"][:2] == "20" and "T" in x["upload_date"]   # captured by the server, not sent by the patient
    f = ramesh.get(f"/api/me/external-reports/{x['id']}/file")
    assert f.status_code == 200 and f.content == PDF[1]


def test_upload_validation(make_client):
    c = make_client()
    login_patient(c, "P-1001")
    future = TODAY.replace(year=TODAY.year + 1).isoformat()
    assert upload(c, test_date=future).status_code == 422
    assert upload(c, test_type="Cholesterol").status_code == 422             # not one of the four types
    assert upload(c, test_type="Other").status_code == 422                   # "Other" needs the test's name
    assert upload(c, test_type="Other", test_name="Vitamin D").status_code == 200
    assert upload(c, file=("x.txt", b"hello", "text/plain")).status_code == 415
    assert c.post("/api/me/external-reports", data={"test_type": "HbA1c", "test_date": "2026-09-20"}).status_code == 422
    assert [x["test_label"] for x in c.get("/api/me/external-reports").json()] == ["Vitamin D"]


def test_patients_only_see_their_own_reports(make_client):
    ramesh, meena = make_client(), make_client()
    login_patient(ramesh, "P-1001")
    login_patient(meena, "P-1003")
    rid = upload(ramesh).json()["id"]
    assert meena.get("/api/me/external-reports").json() == []
    assert meena.get(f"/api/me/external-reports/{rid}/file").status_code == 404


# ------------------------------------------------ step 3: clinician queue + one-step review

def test_queue_shows_only_own_patients_pending_reports(priya, make_client, ids):
    ramesh = make_client()
    login_patient(ramesh, "P-1001")
    rid = upload(ramesh).json()["id"]
    queue = priya.get("/api/external-reports/pending").json()
    assert [(q["id"], q["patient_code"], q["test_type"], q["test_date"]) for q in queue] == [(rid, "P-1001", "HbA1c", "2026-09-20")]
    assert "storage_key" not in queue[0]
    assert any(r["kind"] == "external" and r["id"] == rid for r in priya.get("/api/worklist").json()["reviews"])

    karan = make_client()                                               # a doctor with no patients
    from tests.conftest import login_clinician
    login_clinician(karan, "CLN-KRNB58")
    assert karan.get("/api/external-reports/pending").json() == []
    assert karan.get(f"/api/patients/{ids['P-1001']}/external-reports/{rid}/file").status_code == 404


def test_one_save_links_value_to_document_and_clears_queue(priya, make_client, ids):
    pid = ids["P-1001"]
    ramesh = make_client()
    login_patient(ramesh, "P-1001")
    rid = upload(ramesh).json()["id"]
    detail = priya.get(f"/api/patients/{pid}/external-reports/{rid}").json()
    assert (detail["expected_unit"], detail["status"], detail["result"]) == ("%", "pending_review", None)
    assert priya.get(f"/api/patients/{pid}/external-reports/{rid}/file").content == PDF[1]

    r = priya.post(f"/api/patients/{pid}/external-reports/{rid}/review", json={"value": 7.9})
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["status"] == "reviewed" and done["reviewed_by_name"] == "Dr. Priya Nair"
    assert done["result"]["value"] == 7.9 and done["result"]["unit"] == "%" and done["result"]["date"] == "2026-09-20"
    assert priya.get("/api/external-reports/pending").json() == []                       # gone from the queue
    assert priya.post(f"/api/patients/{pid}/external-reports/{rid}/review", json={"value": 8.1}).status_code == 409
    mine = ramesh.get("/api/me/external-reports").json()[0]
    assert mine["status"] == "reviewed" and mine["result"] == {"value": 7.9, "unit": "%"}


def test_review_validation(priya, make_client, ids):
    pid = ids["P-1001"]
    ramesh = make_client()
    login_patient(ramesh, "P-1001")
    rid = upload(ramesh).json()["id"]
    url = f"/api/patients/{pid}/external-reports/{rid}/review"
    assert priya.post(url, json={"value": 79}).status_code == 422                          # 79 % is not an HbA1c
    assert priya.post(url, json={"value": 7.9, "test_date": "2099-01-01"}).status_code == 422
    assert priya.get("/api/external-reports/pending").json()[0]["id"] == rid              # nothing saved
    other = upload(ramesh, test_type="Other", test_name="Vitamin D").json()["id"]
    assert priya.post(f"/api/patients/{pid}/external-reports/{other}/review", json={"value": 21}).status_code == 422
    ok = priya.post(f"/api/patients/{pid}/external-reports/{other}/review", json={"value": 21, "unit": "ng/mL"}).json()
    assert ok["result"]["name"] == "Vitamin D" and ok["result"]["unit"] == "ng/mL"


def test_corrected_test_date_is_used_and_audited(priya, make_client, ids):
    pid = ids["P-1001"]
    ramesh = make_client()
    login_patient(ramesh, "P-1001")
    rid = upload(ramesh, test_type="Fasting Sugar").json()["id"]
    r = priya.post(f"/api/patients/{pid}/external-reports/{rid}/review", json={"value": 142, "test_date": "2026-09-18"}).json()
    assert r["result"]["date"] == "2026-09-18" and r["result"]["name"] == "Fasting glucose" and r["result"]["unit"] == "mg/dL"
    log = priya.get(f"/api/patients/{pid}/access-log").json()
    assert any("corrected from 2026-09-20 to 2026-09-18" in (e.get("detail") or "") for e in log)


def test_reject_removes_from_queue_without_a_value(priya, make_client, ids):
    pid = ids["P-1001"]
    ramesh = make_client()
    login_patient(ramesh, "P-1001")
    rid = upload(ramesh).json()["id"]
    r = priya.post(f"/api/patients/{pid}/external-reports/{rid}/reject", json={"reason": "Photo is blurred"}).json()
    assert r["status"] == "rejected" and r["result"] is None and r["reject_reason"] == "Photo is blurred"
    assert priya.get("/api/external-reports/pending").json() == []
    assert ramesh.get("/api/me/external-reports").json()[0]["status_text"] == "Not accepted"
    assert priya.post(f"/api/patients/{pid}/external-reports/{rid}/review", json={"value": 7.9}).status_code == 409


# ------------------------------------------------ step 4 (backend): the graph point carries its reference line

def test_graph_point_traces_back_to_the_exact_document(priya, make_client, ids):
    pid = ids["P-1001"]
    ramesh = make_client()
    login_patient(ramesh, "P-1001")
    rid = upload(ramesh).json()["id"]
    priya.post(f"/api/patients/{pid}/external-reports/{rid}/review", json={"value": 7.9})
    hba1c = next(p for p in priya.get(f"/api/patients/{pid}/labs").json() if p["name"] == "HbA1c")
    point = next(r for r in hba1c["results"] if r["provenance"])
    assert point["value"] == 7.9 and point["date"] == "2026-09-20" and point["source"] == "patient_upload_reviewed"
    prov = point["provenance"]
    assert prov["document_id"] == rid
    assert prov["text"].startswith("Ramesh Kumar (P-1001) uploaded this HbA1c report on ")
    assert ". Reviewed by Dr. Priya Nair on " in prov["text"] and prov["text"].endswith(".")
    # the reference opens the very file that was reviewed
    assert priya.get(f"/api/patients/{pid}/external-reports/{prov['document_id']}/file").content == PDF[1]
    # every point from this workflow has a reference - never "none"
    assert all(r["provenance"] for r in hba1c["results"] if r["source"] == "patient_upload_reviewed")
    # and the summary / detection see the value like any other HbA1c
    assert priya.get(f"/api/patients/{pid}").json()["latest_hba1c"]["value"] in (7.9, hba1c["latest"]["value"])
