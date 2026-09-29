"""Pending reports = ONE inbox: everything a patient sends from the app that is still waiting for a decision."""

import pytest

from tests.conftest import login_clinician, login_patient, patient_ids

IMG = ("p.jpg", b"\xff\xd8 photo", "image/jpeg")
PDF = ("r.pdf", b"%PDF-1.4 report", "application/pdf")


@pytest.fixture()
def ids(priya):
    return patient_ids(priya)


@pytest.fixture()
def meena(make_client):
    c = make_client()
    login_patient(c, "P-1003")
    return c


def meena_items(priya):
    return [i for i in priya.get("/api/pending-items").json() if i["patient_code"] == "P-1003"]


def send_everything(meena):
    """One of every kind of upload the patient app offers."""
    assert meena.post("/api/me/sugar", data={"value": "140"}).status_code == 200                        # typed sugar
    assert meena.post("/api/me/sugar", data={"value": "131"}, files={"photo": IMG}).status_code == 200  # sugar + meter photo
    assert meena.post("/api/me/documents", data={"kind": "prescription"}, files={"file": IMG}).status_code == 200
    assert meena.post("/api/me/documents", data={"kind": "lab_report", "description": "Kidney test"},
                      files={"file": PDF}).status_code == 200
    assert meena.post("/api/me/external-reports", data={"test_type": "HbA1c", "test_date": "2026-09-20"},
                      files={"file": PDF}).status_code == 200
    assert meena.post("/api/me/medicine-bills", files={"file": IMG}).status_code == 200
    assert meena.post("/api/me/refill", json={"taken": False}).status_code == 200                       # "not collected yet"


def test_every_kind_of_upload_waits_in_one_place(priya, meena):
    assert meena_items(priya) == []
    send_everything(meena)
    items = meena_items(priya)
    got = sorted((i["kind"], i["label"], i["detail"]) for i in items)
    assert got == sorted([
        ("entry", "Sugar reading", "140 mg/dL"),
        ("entry", "Medicine refill", "Says the medicine is not collected yet"),
        ("document", "Sugar reading with meter photo", "Reading 131 mg/dL"),
        ("document", "Prescription", None),
        ("document", "Lab report: Kidney test", "Values not entered yet"),
        ("external", "HbA1c report from an outside lab", "Test on 20 Sep 2026"),
        ("bill", "Medicine bill", "Diabetes medicine"),
    ])
    assert {i["group"] for i in items} == {"reading", "photo", "test_report", "medicine_bill"}
    assert all(i["file"] for i in items if i["kind"] != "entry")               # everything with a file can be viewed
    assert [i["at"] for i in items] == sorted(i["at"] for i in items)          # oldest first
    # the Overview shows exactly the same items
    overview = [r for r in priya.get("/api/worklist").json()["reviews"] if r["patient_code"] == "P-1003"]
    assert sorted((r["kind"], r["id"]) for r in overview) == sorted((i["kind"], i["id"]) for i in items)


def test_each_item_leaves_the_inbox_once_decided(priya, meena, ids):
    pid = ids["P-1003"]
    send_everything(meena)
    items = {(i["kind"], i["label"]): i for i in meena_items(priya)}
    ok = lambda r: r.status_code == 200 or pytest.fail(r.text)           # noqa: E731
    ok(priya.post(f"/api/patients/{pid}/events/{items[('entry', 'Sugar reading')]['id']}/review", json={"status": "confirmed"}))
    ok(priya.post(f"/api/patients/{pid}/events/{items[('entry', 'Medicine refill')]['id']}/review", json={"status": "rejected"}))
    ok(priya.post(f"/api/patients/{pid}/documents/{items[('document', 'Sugar reading with meter photo')]['id']}/review",
                  json={"status": "confirmed"}))
    ok(priya.post(f"/api/patients/{pid}/documents/{items[('document', 'Prescription')]['id']}/review", json={"status": "rejected"}))
    lab = items[("document", "Lab report: Kidney test")]
    ok(priya.post(f"/api/patients/{pid}/reports/{lab['report_id']}/results",
                  json={"results": [{"test_name": "Creatinine", "value": "1.1", "unit": "mg/dL", "reference": "0.7 - 1.3"}]}))
    ok(priya.post(f"/api/patients/{pid}/external-reports/{items[('external', 'HbA1c report from an outside lab')]['id']}/review",
                  json={"value": 7.2}))
    ok(priya.post(f"/api/patients/{pid}/medicine-bills/{items[('bill', 'Medicine bill')]['id']}/approve", json={}))
    assert meena_items(priya) == []


def test_confirmed_lab_photo_without_values_stays_until_values_are_entered(priya, meena, ids):
    pid = ids["P-1003"]
    meena.post("/api/me/documents", data={"kind": "lab_report", "description": "Lipid test"}, files={"file": PDF})
    doc = meena_items(priya)[0]
    priya.post(f"/api/patients/{pid}/documents/{doc['id']}/review", json={"status": "confirmed"})
    [still] = meena_items(priya)
    assert (still["kind"], still["detail"]) == ("results", "Confirmed · values not entered yet")
    priya.post(f"/api/patients/{pid}/reports/{still['id']}/results", json={"results": [{"test_name": "LDL", "value": "120"}]})
    assert meena_items(priya) == []


def test_inbox_shows_only_your_own_patients(meena, make_client):
    send_everything(meena)
    karan = make_client()
    login_clinician(karan, "CLN-KRNB58")
    assert karan.get("/api/pending-items").json() == []


def test_lab_report_review_screen_data(priya, meena, ids):
    """Review → the file, its approval state and its results, all for this one report."""
    pid = ids["P-1003"]
    meena.post("/api/me/documents", data={"kind": "lab_report", "description": "Kidney test"}, files={"file": PDF})
    item = meena_items(priya)[0]
    r = priya.get(f"/api/patients/{pid}/reports/{item['report_id']}").json()
    assert (r["report_type"], r["patient_code"], r["results"]) == ("Kidney test", "P-1003", [])
    assert r["document"]["id"] == item["id"] and r["document"]["review_status"] == "pending"
    assert r["document"]["content_type"] == "application/pdf"
    # Confirm first: the report stays in the inbox until its values are entered
    priya.post(f"/api/patients/{pid}/documents/{item['id']}/review", json={"status": "confirmed"})
    assert priya.get(f"/api/patients/{pid}/reports/{item['report_id']}").json()["document"]["review_status"] == "confirmed"
    assert [i["kind"] for i in meena_items(priya)] == ["results"]
    priya.post(f"/api/patients/{pid}/reports/{item['report_id']}/results",
               json={"results": [{"test_name": "Creatinine", "value": "1.4", "unit": "mg/dL", "reference": "0.7 - 1.3"}]})
    assert meena_items(priya) == []
    done = priya.get(f"/api/patients/{pid}/reports/{item['report_id']}").json()
    assert [(t["test_name"], t["value_text"], t["reference_text"]) for t in done["results"]] == [("Creatinine", "1.4", "0.7 - 1.3")]
    assert priya.get(f"/api/patients/{pid}/reports/no-such-report").status_code == 404


def test_rejected_lab_report_leaves_inbox_and_takes_no_values(priya, meena, ids):
    pid = ids["P-1003"]
    meena.post("/api/me/documents", data={"kind": "lab_report", "description": "Lipid test"}, files={"file": PDF})
    item = meena_items(priya)[0]
    priya.post(f"/api/patients/{pid}/documents/{item['id']}/review", json={"status": "rejected"})
    assert meena_items(priya) == []
    r = priya.post(f"/api/patients/{pid}/reports/{item['report_id']}/results", json={"results": [{"test_name": "LDL", "value": "120"}]})
    assert r.status_code == 409
