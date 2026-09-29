"""Age asked at sign-in (when there is no date of birth), and the eGFR average for the patient's age."""

from datetime import date

import pytest

from app import repo
from app.labs import egfr_age_reference
from tests.conftest import login_clinician, login_patient, patient_ids

NO_DOB = {"full_name": "Ravi Menon", "phone": "91234 55555", "password": "ravi-pass"}


def signed_up_without_dob(make_client):
    c = make_client()
    code = c.post("/api/auth/patient/signup", json=NO_DOB).json()["dev_code"]
    me = c.post("/api/auth/patient/signup/verify", json={"phone": NO_DOB["phone"], "code": code}).json()
    return c, me


def test_patient_without_date_of_birth_is_asked_once(make_client):
    c, me = signed_up_without_dob(make_client)
    assert me["needs_age"] is True
    assert c.post("/api/me/age", json={"age": 0}).status_code == 422
    assert c.post("/api/me/age", json={"age": 121}).status_code == 422
    assert c.post("/api/me/age", json={"age": 54}).json()["age"] == 54
    assert c.get("/api/auth/patient/me").json()["needs_age"] is False


def test_patient_with_date_of_birth_is_never_asked(make_client):
    c = make_client()
    assert login_patient(c, "P-1001")["needs_age"] is False           # the demo patients have a date of birth


def test_clinician_sees_the_age_and_where_it_came_from(make_client):
    c, me = signed_up_without_dob(make_client)
    c.post("/api/me/age", json={"age": 54})
    c.post("/api/me/care-team", json={"clinician_code": "CLN-PRYA27"})
    priya = make_client()
    login_clinician(priya)
    p = priya.get(f"/api/patients/{patient_ids(priya)[me['patient_code']]}").json()
    assert (p["age"], p["age_source"]) == (54, "told by patient")


def test_reported_age_keeps_counting_and_date_of_birth_wins():
    assert repo.age(None, date(2026, 9, 25), 54, "2026-09-25") == 54
    assert repo.age(None, date(2028, 9, 24), 54, "2026-09-25") == 55        # one full year later, not two
    assert repo.age(None, date(2028, 9, 25), 54, "2026-09-25") == 56
    assert repo.age("1960-03-15", date(2026, 9, 25), 30, "2026-01-01") == 66
    assert repo.age(None, date(2026, 9, 25)) is None


@pytest.mark.parametrize("age, group, average", [
    (20, "20–29", 116), (29, "20–29", 116), (30, "30–39", 107), (45, "40–49", 99), (59, "50–59", 93),
    (60, "60–69", 85), (69, "60–69", 85), (70, "70+", 75), (92, "70+", 75),
])
def test_egfr_average_follows_the_age_group(age, group, average):
    ref = egfr_age_reference(age, "mL/min/1.73m²")
    assert (ref["group"], ref["average"]) == (group, average)
    assert ref["text"] == f"Average for age {group}: {average} mL/min/1.73m²"


def test_no_egfr_average_without_age_under_20_or_other_units():
    assert egfr_age_reference(None, "mL/min/1.73m²") is None
    assert egfr_age_reference(15, "mL/min/1.73m²") is None
    assert egfr_age_reference(50, "mg/dL") is None


def test_egfr_panel_gets_the_average_automatically(priya):
    pid = patient_ids(priya)["P-1001"]                         # Ramesh: born 1967, so 59 on the demo date
    panels = priya.get(f"/api/patients/{pid}/labs").json()
    egfr = next(p for p in panels if p["name"] == "eGFR")
    assert egfr["age_reference"]["group"] == "50–59" and egfr["age_reference"]["average"] == 93
    assert all(p["age_reference"] is None for p in panels if p["name"] != "eGFR")
    assert egfr["latest"]["reference"]["text"] == "> 60 mL/min/1.73m²"      # the printed range is untouched
