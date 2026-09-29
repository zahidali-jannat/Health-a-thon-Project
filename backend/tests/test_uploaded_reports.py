"""Patient-uploaded lab reports are listed straight away; the care team enters their values."""

import pytest

from app.repo import parse_reference
from tests.conftest import login_clinician, login_patient, patient_ids


@pytest.fixture()
def uploaded(priya, make_client):
    ids = patient_ids(priya)
    meena = make_client()
    login_patient(meena, "P-1003")
    r = meena.post("/api/me/documents", data={"kind": "lab_report", "description": "hba1c report"},
                   files={"file": ("h.pdf", b"%PDF-1.4 x", "application/pdf")})
    pid = ids["P-1003"]
    report = next(x for x in priya.get(f"/api/patients/{pid}/reports").json() if x["document_id"] == r.json()["id"])
    return pid, report


def test_uploaded_lab_report_is_listed_immediately(uploaded):
    _, report = uploaded
    assert report["report_type"] == "hba1c report" and report["source"] == "patient_upload"
    assert report["results"] == [] and report["document_status"] == "pending"


def test_prescriptions_are_not_lab_reports(priya, make_client):
    pid = patient_ids(priya)["P-1003"]
    before = len(priya.get(f"/api/patients/{pid}/reports").json())
    c = make_client()
    login_patient(c, "P-1003")
    c.post("/api/me/documents", data={"kind": "prescription"}, files={"file": ("rx.jpg", b"\xff\xd8", "image/jpeg")})
    assert len(priya.get(f"/api/patients/{pid}/reports").json()) == before


def test_entering_results_feeds_graphs_and_confirms_the_file(priya, uploaded):
    pid, report = uploaded
    r = priya.post(f"/api/patients/{pid}/reports/{report['id']}/results", json={
        "report_date": "2026-09-20",
        "results": [{"test_name": "HbA1c", "value": "7.40", "unit": "%", "reference": "< 5.7 %"},
                    {"test_name": "Urine protein", "value": "Negative", "reference": "Negative"}]})
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["report_date"] == "2026-09-20" and saved["document_status"] == "confirmed"
    assert {t["test_name"]: t["value_text"] for t in saved["results"]} == {"HbA1c": "7.40", "Urine protein": "Negative"}

    panels = {p["name"]: p for p in priya.get(f"/api/patients/{pid}/labs").json()}
    hba1c = panels["HbA1c"]["latest"]
    assert hba1c["value"] == 7.4 and hba1c["reference"]["text"] == "< 5.7 %" and hba1c["status"] == "above"
    assert hba1c["date"] == "2026-09-20" and hba1c["report_id"] == report["id"]
    assert panels["Urine protein"]["latest"]["status"] == "normal"


def test_rejected_report_takes_no_results_and_never_counts(priya, uploaded):
    pid, report = uploaded
    priya.post(f"/api/patients/{pid}/reports/{report['id']}/results",
               json={"results": [{"test_name": "HbA1c", "value": "12.5", "unit": "%"}]})
    assert next(p for p in priya.get(f"/api/patients/{pid}/labs").json() if p["name"] == "HbA1c")["latest"]["value"] == 12.5
    priya.post(f"/api/patients/{pid}/documents/{report['document_id']}/review", json={"status": "rejected"})
    latest = next(p for p in priya.get(f"/api/patients/{pid}/labs").json() if p["name"] == "HbA1c")["latest"]
    assert latest["value"] != 12.5                                               # rejected report no longer counts
    r = priya.post(f"/api/patients/{pid}/reports/{report['id']}/results",
                   json={"results": [{"test_name": "HbA1c", "value": "7.0", "unit": "%"}]})
    assert r.status_code == 409


def test_results_validation_and_scoping(priya, uploaded, make_client):
    pid, report = uploaded
    url = f"/api/patients/{pid}/reports/{report['id']}/results"
    assert priya.post(url, json={"results": []}).status_code == 422
    assert priya.post(url, json={"results": [{"test_name": " ", "value": "7"}]}).status_code == 422
    assert priya.post(url, json={"report_date": "2030-01-01", "results": [{"test_name": "HbA1c", "value": "7"}]}).status_code == 422
    other = patient_ids(priya)["P-1001"]
    assert priya.post(f"/api/patients/{other}/reports/{report['id']}/results",
                      json={"results": [{"test_name": "HbA1c", "value": "7"}]}).status_code == 404   # wrong patient path
    karan = make_client()
    login_clinician(karan, "CLN-KRNB58")
    assert karan.post(url, json={"results": [{"test_name": "HbA1c", "value": "7"}]}).status_code == 404


@pytest.mark.parametrize("text, numeric, expected", [
    ("< 5.7 %", True, {"ref_kind": "upper", "ref_high": 5.7}),
    ("> 60 mL/min", True, {"ref_kind": "lower", "ref_low": 60.0}),
    ("12.0 – 16.0 g/dL", True, {"ref_kind": "range", "ref_low": 12.0, "ref_high": 16.0}),
    ("Negative", False, {"ref_kind": "qualitative"}),
    ("see note", True, {}),
    ("", True, None),
])
def test_parse_reference_never_invents(text, numeric, expected):
    out = parse_reference(text, numeric)
    if expected is None:
        assert out == {}
        return
    for k, v in expected.items():
        assert out[k] == v
    assert out.get("ref_text") == text
    if not expected:
        assert "ref_kind" not in out
