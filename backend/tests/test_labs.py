from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.detection import Event, PatientRecord
from app.labs import classify, lab_panels, reference_text, trend

TODAY = date(2026, 9, 25)


def lab(value=None, days_ago=10, text=None, **ref) -> Event:
    return Event(event_type="lab", effective_date=TODAY - timedelta(days=days_ago), source="hospital_internal",
                 confidence="high", status="resulted", asserted_by="Test Lab", name="Test",
                 value=value, value_text=text, unit="u", **ref)


RANGE = dict(ref_kind="range", ref_low=12.0, ref_high=16.0)
UPPER = dict(ref_kind="upper", ref_high=5.7)
LOWER = dict(ref_kind="lower", ref_low=60)


@pytest.mark.parametrize("value, ref, expected", [
    (13.2, RANGE, "within"), (12.0, RANGE, "within"), (16.0, RANGE, "within"),     # both ends inclusive
    (11.9, RANGE, "below"), (16.1, RANGE, "above"),
    (5.6, UPPER, "within"), (5.7, UPPER, "above"), (8.4, UPPER, "above"),           # "< 5.7" is strict
    (61, LOWER, "within"), (60, LOWER, "below"), (58, LOWER, "below"),              # "> 60" is strict
    (8.4, {}, "unavailable"),                                                       # no range on the report
])
def test_classify_numeric(value, ref, expected):
    assert classify(lab(value, **ref)) == expected


def test_critical_only_when_lab_reports_it():
    assert classify(lab(3.1, ref_kind="range", ref_low=3.5, ref_high=5.1)) == "below"
    assert classify(lab(2.4, ref_kind="range", ref_low=3.5, ref_high=5.1, crit_low=2.5)) == "critical_low"
    assert classify(lab(6.8, ref_kind="range", ref_low=3.5, ref_high=5.1, crit_high=6.5)) == "critical_high"


def test_qualitative():
    q = dict(ref_kind="qualitative", ref_text="Negative")
    assert classify(lab(text="negative", **q)) == "normal"
    assert classify(lab(text="Positive (1+)", **q)) == "abnormal"
    assert classify(lab(text="Positive")) == "unavailable"


def test_reference_text_prefers_the_printed_text():
    assert reference_text(lab(1, ref_kind="upper", ref_high=5.7, ref_text="< 5.7 %")) == "< 5.7 %"
    assert reference_text(lab(1, **RANGE)) == "12 – 16 u"
    assert reference_text(lab(1)) is None


def test_trend_direction():
    assert trend([lab(7.8, 60, **UPPER), lab(8.4, 10, **UPPER)]).direction == "away"
    assert trend([lab(8.4, 60, **UPPER), lab(7.6, 10, **UPPER)]).direction == "toward"
    assert trend([lab(13, 60, **RANGE), lab(14, 10, **RANGE)]).direction == "within"
    t = trend([lab(132, 60), lab(141, 10)])
    assert t.direction is None and t.change_text.startswith("+9\u00a0u since")
    assert trend([lab(8.4, 10, **UPPER)]) is None


def test_trend_uses_each_results_own_range():
    # 6.0 is outside "< 5.7" by 0.3; 6.2 is outside "4.0 – 5.6" by 0.6 -> away
    prev = lab(6.0, 60, **UPPER)
    last = lab(6.2, 10, ref_kind="range", ref_low=4.0, ref_high=5.6)
    assert trend([prev, last]).direction == "away"


def test_units_are_never_mixed():
    a = lab(100, 60)
    b = Event(**{**a.__dict__, "unit": "mmol/L", "value": 5.5})
    panels = lab_panels(PatientRecord(1, "T", "T", [a, b]), TODAY)
    assert sorted(p["unit"] for p in panels) == ["mmol/L", "u"]


# ------------------------------------------------ API over the seeded patients

from tests.conftest import patient_ids  # noqa: E402


def panel(panels, name):
    return next(p for p in panels if p["name"] == name)


@pytest.fixture()
def ids(priya):
    return patient_ids(priya)


def test_seeded_panels(priya, ids):
    panels = priya.get(f"/api/patients/{ids['P-1001']}/labs").json()
    assert [p["name"] for p in panels] == ["HbA1c", "eGFR", "Hemoglobin", "Urine protein", "LDL cholesterol"]

    hba1c = panel(panels, "HbA1c")
    assert hba1c["latest"]["value"] == 8.2 and hba1c["latest"]["reference"]["text"] == "< 5.7 %"
    assert hba1c["latest"]["status_label"] == "Above range ↑"
    assert hba1c["trend"]["direction"] == "away" and hba1c["reference_consistent"]
    assert hba1c["latest"]["report_id"]                                       # every result traces to a report

    assert panel(panels, "Hemoglobin")["latest"]["status"] == "below"
    assert panel(panels, "eGFR")["latest"]["status"] == "below"
    up = panel(panels, "Urine protein")
    assert up["kind"] == "qualitative" and up["latest"]["status"] == "abnormal"
    ldl = panel(panels, "LDL cholesterol")
    assert ldl["latest"]["status"] == "unavailable" and ldl["latest"]["reference"] is None

    lakshmi_hb = panel(priya.get(f"/api/patients/{ids['P-1002']}/labs").json(), "Hemoglobin")["latest"]
    assert (lakshmi_hb["value"], lakshmi_hb["reference"]["text"], lakshmi_hb["status"]) == (13.2, "12.0 – 16.0 g/dL", "within")


def test_other_hospitals_range_is_kept_not_replaced(priya, ids, make_client):
    pid = ids["P-1001"]
    req = priya.post(f"/api/patients/{pid}/consent-requests",
                     json={"abha_number": "91123456789012", "hi_types": ["DiagnosticReport"]}).json()
    from tests.conftest import login_patient
    ramesh = make_client()
    login_patient(ramesh, "9000000001")
    ramesh.post(f"/api/me/consents/{req['id']}", json={"approve": True})
    hba1c = panel(priya.get(f"/api/patients/{pid}/labs").json(), "HbA1c")
    assert hba1c["latest"]["value"] == 8.4
    assert hba1c["latest"]["reference"]["text"] == "4.0 – 5.6 %"
    assert hba1c["results"][-2]["reference"]["text"] == "< 5.7 %"
    assert hba1c["reference_consistent"] is False


def test_manual_entry_without_range_is_unavailable(priya, ids):
    pid = ids["P-1002"]
    priya.post(f"/api/patients/{pid}/manual", json={"hba1c": {"value": 7.1, "date": "2026-09-24"}})
    latest = panel(priya.get(f"/api/patients/{pid}/labs").json(), "HbA1c")["latest"]
    assert latest["value"] == 7.1 and latest["status"] == "unavailable" and latest["reference"] is None
    assert latest["source"] == "care_team_manual"


def test_manual_hemoglobin_and_egfr_join_their_graphs(priya, ids):
    pid = ids["P-1001"]
    before = {p["name"]: len(p["results"]) for p in priya.get(f"/api/patients/{pid}/labs").json()}
    r = priya.post(f"/api/patients/{pid}/manual", json={"hemoglobin": {"value": 12.9, "date": "2026-09-24"},
                                                         "egfr": {"value": 61, "date": "2026-09-24"}})
    assert r.status_code == 200 and r.json()["saved"] == ["Hemoglobin 12.9 g/dL", "eGFR 61 mL/min/1.73m²"]
    panels = priya.get(f"/api/patients/{pid}/labs").json()
    for name, value in (("Hemoglobin", 12.9), ("eGFR", 61)):
        p = panel(panels, name)
        assert len(p["results"]) == before[name] + 1
        assert p["latest"]["value"] == value and p["latest"]["status"] == "unavailable"
        assert p["results"][-2]["reference"] is not None
    # each typed value is its own care-team row (so each can be linked to its own report)
    values = priya.get(f"/api/patients/{pid}/manual-values").json()
    assert {(v["name"], v["value"]) for v in values} >= {("Hemoglobin", 12.9), ("eGFR", 61)}


@pytest.mark.parametrize("field, value", [("hemoglobin", 40), ("egfr", 500), ("egfr", 0)])
def test_manual_lab_validation(priya, ids, field, value):
    r = priya.post(f"/api/patients/{ids['P-1001']}/manual", json={field: {"value": value, "date": "2026-09-24"}})
    assert r.status_code == 422


# ------------------------------------------------ HbA1c diabetes stages (ADA): Normal < 5.7 · Prediabetes 5.7-6.4 · Diabetes >= 6.5

def hba1c(value, unit="%", days_ago=10):
    return Event(event_type="lab", effective_date=TODAY - timedelta(days=days_ago), source="hospital_internal",
                 confidence="high", status="resulted", asserted_by="Test Lab", name="HbA1c", value=value, unit=unit)


@pytest.mark.parametrize("value, unit, stage", [
    (5.6, "%", "normal"), (5.7, "%", "prediabetes"), (6.4, "%", "prediabetes"), (6.49, "%", "prediabetes"),
    (6.5, "%", "diabetes"), (7.9, "%", "diabetes"),
    (38, "mmol/mol", "normal"), (39, "mmol/mol", "prediabetes"), (47, "mmol/mol", "prediabetes"), (48, "mmol/mol", "diabetes"),
])
def test_hba1c_stage_boundaries(value, unit, stage):
    from app.labs import hba1c_stage
    assert hba1c_stage(hba1c(value, unit))["key"] == stage


def test_hba1c_stage_texts_and_units():
    from app.labs import hba1c_other_unit, hba1c_stage, hba1c_stages
    assert [(s["range_text"], s["alt_range_text"]) for s in hba1c_stages("%")] == [
        ("< 5.7 %", "< 39 mmol/mol"), ("5.7–6.4 %", "39–47 mmol/mol"), ("≥ 6.5 %", "≥ 48 mmol/mol")]
    assert hba1c_other_unit(hba1c(7.9)) == "63 mmol/mol" and hba1c_other_unit(hba1c(63, "mmol/mol")) == "7.9 %"
    assert hba1c_stage(hba1c(7.9))["range_text"] == "≥ 6.5 %"


def test_stages_only_for_hba1c_and_never_replace_the_report_range():
    other = lab(14.0, **RANGE)
    from app.labs import hba1c_stage
    assert hba1c_stage(other) is None
    a1c = hba1c(7.9)
    a1c.ref_kind, a1c.ref_high = "upper", 5.7
    panels = lab_panels(PatientRecord(id="p", patient_code="P", full_name="X", events=[a1c, other]), TODAY)
    p = next(p for p in panels if p["name"] == "HbA1c")
    assert [s["key"] for s in p["stages"]] == ["normal", "prediabetes", "diabetes"]
    assert p["latest"]["stage"]["label"] == "Diabetes"
    assert p["latest"]["reference"]["text"] == "< 5.7 %"            # the printed range is untouched
    assert next(p for p in panels if p["name"] == "Test")["stages"] is None
