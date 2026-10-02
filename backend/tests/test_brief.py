"""Consultation Brief: rules and limits, verified-only data, immutable versions, "updated since sent", the identity
gate, authorization, idempotent send, empty states, the mixed-lab notice and the medicines "possibly outdated" label."""

import copy
import json
import os
import sqlite3
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import brief, db
from app.seed import seed_database
from tests.conftest import KARAN, PRIYA, TEAM, TODAY, login_clinician, login_patient


@pytest.fixture()
def world():
    """The demo seed WITH the brief demo: appointments today for Ramesh (worsening), Lakshmi (stable), Arjun (new)."""
    conn = db.reset(os.environ["UC2_DB_PATH"])
    ids = seed_database(conn, TODAY, brief_demo=True)
    conn.close()
    from app.main import app
    clients = []

    def client(code=None, patient=None):
        c = TestClient(app)
        c.__enter__()
        clients.append(c)
        if code:
            login_clinician(c, code)
        if patient:
            login_patient(c, patient)
        return c

    yield ids, client
    for c in clients:
        c.__exit__(None, None, None)


def appointments(team):
    return {a["patient_code"]: a for a in team.get("/api/clinic/briefs/today").json()["appointments"]}


def draft(team, code):
    r = team.post(f"/api/appointments/{appointments(team)[code]['appointment_id']}/brief/draft")
    assert r.status_code == 200, r.text
    return r.json()


def send(team, brief_id, **body):
    return team.post(f"/api/briefs/{brief_id}/send", json=body)


def open_as_doctor(doctor, brief_id, code, dob):
    assert doctor.post(f"/api/doctor/briefs/{brief_id}/call").status_code == 200
    r = doctor.post(f"/api/doctor/briefs/{brief_id}/confirm-identity", json={"patient_code": code, "date_of_birth": dob})
    assert r.status_code == 200, r.text
    return doctor.get(f"/api/doctor/briefs/{brief_id}").json()


def rules_with(monkeypatch, **changes):
    cfg = copy.deepcopy(brief.rules())
    for path, value in changes.items():
        section, key = path.split("__")
        cfg[section][key] = value
    monkeypatch.setattr(brief, "rules", lambda: cfg)
    return cfg


# ------------------------------------------------------------------ content and rules

def test_the_day_list_shows_readiness(world):
    _, client = world
    team = client(TEAM)
    day = appointments(team)
    assert [(c, a["start_time"]) for c, a in sorted(day.items(), key=lambda x: x[1]["start_time"])] == \
        [("P-1001", "10:00"), ("P-1002", "10:20"), ("P-1004", "10:40")]
    assert day["P-1004"]["vitals_today"] is True and day["P-1001"]["vitals_today"] is False
    assert day["P-1001"]["awaiting_verification"] == 1                     # the lipid profile upload
    assert day["P-1004"]["medicines_last_confirmed"] is None and day["P-1001"]["brief"] is None


def test_worsening_patient_brief_follows_the_section_order_and_limits(world):
    _, client = world
    team = client(TEAM)
    view = draft(team, "P-1001")
    c = view["content"]
    assert list(c) == ["schema_version", "data_cutoff_at", "identity", "since_last_visit", "key_numbers", "medicines",
                       "tests", "attention", "team_note", "footer"]
    assert c["schema_version"] == "1.0" and c["footer"]["verified_only"] is True
    ident = c["identity"]
    assert (ident["name"], ident["patient_code"], ident["age"], ident["sex"]) == ("Ramesh Kumar", "P-1001", 59, "Male")
    assert ident["priority"]["label"] == "Worsening since last visit" and ident["priority"]["rule"]
    assert ident["appointment"]["time"] == "10:00" and ident["visit_type"] == "Diabetes follow-up"
    b = c["since_last_visit"]
    assert b["basis"] == "last_visit" and b["since"] == (TODAY - timedelta(days=90)).isoformat()
    assert len(b["items"]) <= 5 and all(i["rule"] and i["rule_text"] for i in b["items"])
    ranks = [brief._rank(i["rule"]) for i in b["items"]]
    assert ranks == sorted(ranks)                                           # ranked by the configured order
    assert any(i["rule"] == "worsening_value" and i["key"] == "value:fasting_glucose" for i in b["items"])
    assert any(i["rule"] == "appointment_issue" for i in b["items"])        # the missed visit 21 days ago
    rows = [r["key"] for r in c["key_numbers"]["rows"]]
    assert rows == ["hba1c", "fasting_glucose", "egfr", "bp_systolic", "weight"]        # the fixed order, with data only
    assert c["key_numbers"]["not_on_file"] == ["Post-meal glucose"]
    hba1c = c["key_numbers"]["rows"][0]
    assert hba1c["unit"] == "%" and len(hba1c["trend"]) == 6 and hba1c["previous"] and hba1c["change"]["direction"] == "rising"
    assert all(m["dose"] == "Dose and schedule not recorded" for m in c["medicines"]["items"])     # never invented
    assert all(m["status"] in ("As recorded", "Stopped (as recorded)") for m in c["medicines"]["items"])
    assert len(c["attention"]["items"]) <= 3
    assert {i["severity"] for i in c["attention"]["items"] + c["attention"]["more"]} <= {"Attention", "Info"}
    assert view["warnings"][:2] == ["1 item awaiting verification - not included as values", "No vitals recorded today"]


def test_ranking_limits_pins_hides_and_the_stable_line():
    cand = [{"key": f"k{i}", "rule": rule, "date": f"2026-09-{10 + i:02d}", "text": rule}
            for i, rule in enumerate(["stable_x", "appointment_issue", "worsening_value", "emergency_visit",
                                      "low_glucose", "medicine_change", "overdue_tests", "improving_value"])]
    raw = {"schema_version": "1.0", "identity": {}, "since": {"date": "2026-06-01", "basis": "last_visit"}, "candidates": cand,
           "stable": [{"key": "value:egfr", "label": "eGFR"}, {"key": "value:weight", "label": "Weight"}],
           "key_numbers": {}, "medicines": {"items": [{"key": f"med:{n}", "name": str(n)} for n in range(10)]},
           "tests": [], "attention": [], "awaiting_verification": 0}
    out = brief.render(raw, hidden={"value:weight", "k6"}, pinned={"k1"}, note=None, cutoff="x")
    b = out["since_last_visit"]
    assert [i["rule"] for i in b["items"]] == ["appointment_issue", "emergency_visit", "low_glucose", "medicine_change",
                                               "worsening_value"]                 # pinned first, then the configured order
    assert [i["rule"] for i in b["more"]] == ["improving_value", "stable_x"] and "k6" not in str(b)   # hidden is gone
    assert b["stable_line"] == "No meaningful change: eGFR"
    assert len(out["medicines"]["items"]) == 8 and len(out["medicines"]["more"]) == 2


def test_thresholds_come_from_the_config(world, monkeypatch):
    ids, client = world
    team = client(TEAM)
    meena = client(patient="P-1003")              # hypoglycaemia (52 mg/dL) 9 days ago; book her for tomorrow
    doctor = meena.get("/api/me/appointments").json()["doctors"][0]["id"]
    slot = meena.get(f"/api/me/appointments/slots?doctor_id={doctor}&date={TODAY + timedelta(days=1)}").json()[0]
    appt_id = meena.post("/api/me/appointments", json={"slot_id": slot["id"]}).json()["id"]
    first = team.post(f"/api/appointments/{appt_id}/brief/draft").json()["content"]
    low = [i for i in first["attention"]["items"] if i["rule"] == "low_glucose"]
    assert low and low[0]["severity"] == "Attention" and "below 70 mg/dL" in low[0]["rule_text"]
    assert first["since_last_visit"]["items"][0]["rule"] == "low_glucose"
    rules_with(monkeypatch, glucose__low_mg_dl=50)                     # a stricter threshold, from the config
    again = team.post(f"/api/appointments/{appt_id}/brief/draft").json()["content"]
    assert not [i for i in again["attention"]["items"] + again["attention"]["more"] if i["rule"] == "low_glucose"]


def test_unverified_values_are_only_counted(world):
    _, client = world
    team = client(TEAM)
    c = draft(team, "P-1002")["content"]                              # Lakshmi typed 118 and 124 into the app (unreviewed)
    fasting = next(r for r in c["key_numbers"]["rows"] if r["key"] == "fasting_glucose")
    assert fasting["latest"]["date"] == (TODAY - timedelta(days=2)).isoformat()   # the monitoring value, not the typed one
    assert all(p["date"] not in ((TODAY - timedelta(days=1)).isoformat(), (TODAY - timedelta(days=3)).isoformat())
               for p in fasting["trend"])
    assert c["footer"]["awaiting_verification"] == 2
    waiting = next(i for i in c["attention"]["items"] + c["attention"]["more"] if i["rule"] == "awaiting_verification")
    assert waiting["severity"] == "Info" and "unverified" in waiting["label"] and waiting["link"] == "/care-team/reports"


def test_new_patient_with_little_data(world):
    _, client = world
    team = client(TEAM)
    c = draft(team, "P-1004")["content"]
    assert c["since_last_visit"] == {"since": None, "basis": "none", "items": [], "more": [], "stable_line": None}
    assert [r["key"] for r in c["key_numbers"]["rows"]] == ["weight"]
    assert c["key_numbers"]["not_on_file"] == ["HbA1c", "Fasting glucose", "Post-meal glucose", "eGFR", "Blood pressure (systolic)"]
    assert c["medicines"] == {"items": [], "more": [], "last_confirmed_on": None, "possibly_outdated": False}
    assert c["tests"] == [] and c["attention"] == {"items": [], "more": []}


def test_mixed_lab_notice_and_outdated_medicines(world, monkeypatch):
    ids, client = world
    team = client(TEAM)
    team.post(f"/api/patients/{ids['P-1001']}/manual", json={"hba1c": {"value": 8.6, "date": str(TODAY)}})
    c = draft(team, "P-1001")["content"]
    hba1c = c["key_numbers"]["rows"][0]
    assert hba1c["latest"]["value"] == 8.6 and hba1c["mixed_labs"] is True and len(hba1c["labs"]) == 2
    assert c["key_numbers"]["notice"].startswith("Values compared here come from different labs")
    assert c["medicines"]["possibly_outdated"] is False                 # last refill 52 days ago
    rules_with(monkeypatch, medicines__outdated_after_days=30)
    c = draft(team, "P-1001")["content"]
    assert c["medicines"]["possibly_outdated"] is True
    assert any(i["rule"] == "medicines_outdated" and i["severity"] == "Info" for i in c["attention"]["items"] + c["attention"]["more"])


# ------------------------------------------------------------------ editing, sending, versions

def test_edits_need_the_current_version_and_the_note_is_limited(world):
    _, client = world
    team = client(TEAM)
    view = draft(team, "P-1001")
    bid, rv = view["brief"]["id"], view["brief"]["row_version"]
    key = view["candidates"][0]["key"]
    r = team.patch(f"/api/briefs/{bid}", json={"row_version": rv, "hide": [key], "note": "Patient asked about foot pain."})
    assert r.status_code == 200
    after = r.json()
    assert key not in json.dumps(after["content"]["since_last_visit"]) + json.dumps(after["content"]["attention"])
    assert after["content"]["team_note"]["text"] == "Patient asked about foot pain." and after["content"]["team_note"]["by"] == "Tanishq Clinic Management"
    assert team.patch(f"/api/briefs/{bid}", json={"row_version": rv, "unhide": [key]}).json()["detail"]["code"] == "stale"
    long = team.patch(f"/api/briefs/{bid}", json={"row_version": after["brief"]["row_version"], "note": "x" * 281})
    assert long.status_code == 422 and long.json()["detail"]["code"] == "note_too_long"


def test_send_is_idempotent_and_a_sent_brief_is_immutable_and_versioned(world):
    _, client = world
    team = client(TEAM)
    v1 = draft(team, "P-1001")["brief"]
    first = send(team, v1["id"]).json()
    again = send(team, v1["id"]).json()
    assert first["status"] == "sent" and again == {**first, "replayed": True}
    c = sqlite3.connect(os.environ["UC2_DB_PATH"])
    with pytest.raises(sqlite3.IntegrityError, match="cannot be changed"):
        c.execute("UPDATE consultation_briefs SET content_json = '{}' WHERE id = ?", (v1["id"],))
    c.rollback()                                   # release this test connection's lock
    assert team.patch(f"/api/briefs/{v1['id']}", json={"row_version": 9, "note": "x"}).json()["detail"]["code"] == "not_draft"
    v2 = draft(team, "P-1001")["brief"]
    assert v2["version"] == 2 and v2["id"] != v1["id"]
    assert draft(team, "P-1001")["brief"]["id"] == v2["id"]                # regenerating the draft keeps one draft
    send(team, v2["id"])
    rows = dict(c.execute("SELECT version, status FROM consultation_briefs WHERE appointment_id = ? ORDER BY version",
                          (v1["appointment_id"],)).fetchall())
    assert rows == {1: "superseded", 2: "sent"}
    assert c.execute("SELECT superseded_by FROM consultation_briefs WHERE id = ?", (v1["id"],)).fetchone()[0] == v2["id"]


def test_cancelled_or_rescheduled_and_not_today_cannot_be_sent(world):
    ids, client = world
    team = client(TEAM)
    lakshmi = draft(team, "P-1002")["brief"]
    c = sqlite3.connect(os.environ["UC2_DB_PATH"])
    c.execute("UPDATE appointments SET status = 'needs_reschedule' WHERE id = ?", (lakshmi["appointment_id"],))
    c.commit()
    r = send(team, lakshmi["id"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "appointment_not_confirmed"
    meena = client(patient="P-1003")
    doctor = meena.get("/api/me/appointments").json()["doctors"][0]["id"]
    slot = meena.get(f"/api/me/appointments/slots?doctor_id={doctor}&date={TODAY + timedelta(days=2)}").json()[0]
    appt_id = meena.post("/api/me/appointments", json={"slot_id": slot["id"]}).json()["id"]
    later = team.post(f"/api/appointments/{appt_id}/brief/draft").json()["brief"]
    assert send(team, later["id"]).json()["detail"]["code"] == "not_today"
    assert send(team, later["id"], allow_not_today=True).json()["status"] == "sent"


def test_bulk_send_previews_then_sends(world):
    _, client = world
    team = client(TEAM)
    for code in ("P-1001", "P-1002", "P-1004"):
        draft(team, code)
    preview = team.post("/api/clinic/briefs/send-ready", json={}).json()["ready"]
    assert [p["patient_code"] for p in preview] == ["P-1001", "P-1002", "P-1004"] and preview[0]["time"] == "10:00"
    done = team.post("/api/clinic/briefs/send-ready", json={"brief_ids": [p["brief_id"] for p in preview]}).json()
    assert len(done["sent"]) == 3 and done["skipped"] == []
    assert team.post("/api/clinic/briefs/send-ready", json={}).json()["ready"] == []


# ------------------------------------------------------------------ the doctor

def test_queue_identity_gate_and_status_flow(world):
    ids, client = world
    team = client(TEAM)
    for code in ("P-1004", "P-1001"):
        send(team, draft(team, code)["brief"]["id"])
    doctor = client(PRIYA)                         # Dr. Priya Nair is the doctor for these appointments
    q = doctor.get("/api/doctor/queue")
    queue = q.json()["queue"]
    assert [x["patient_code"] for x in queue] == ["P-1001", "P-1004"]          # by appointment time
    assert set(queue[0]) == {"brief_id", "status", "version", "time", "date", "name", "patient_code", "age", "sex",
                             "priority", "priority_level"}                     # nothing clinical
    assert doctor.get("/api/doctor/queue", headers={"If-None-Match": q.headers["etag"]}).status_code == 304
    bid = queue[0]["brief_id"]
    gate = doctor.get(f"/api/doctor/briefs/{bid}")
    assert gate.status_code == 403 and gate.json()["detail"]["code"] == "identity_required"
    called = doctor.post(f"/api/doctor/briefs/{bid}/call").json()
    assert called == {"brief_id": bid, "name": "Ramesh Kumar", "patient_code": "P-1001", "identity_confirmed": False}
    one = doctor.post(f"/api/doctor/briefs/{bid}/confirm-identity", json={"patient_code": "P-1001"})
    assert one.json()["detail"]["code"] == "two_identifiers"
    wrong = doctor.post(f"/api/doctor/briefs/{bid}/confirm-identity", json={"patient_code": "P-1001", "date_of_birth": "1970-01-01"})
    assert wrong.status_code == 422 and wrong.json()["detail"]["code"] == "identity_mismatch"
    assert doctor.get(f"/api/doctor/briefs/{bid}").status_code == 403
    by_age = doctor.post(f"/api/doctor/briefs/{bid}/confirm-identity", json={"patient_code": "p-1001", "age": 59})
    assert by_age.status_code == 200
    full = doctor.get(f"/api/doctor/briefs/{bid}").json()
    assert full["content"]["identity"]["name"] == "Ramesh Kumar" and full["brief"]["status"] == "opened"
    assert doctor.post(f"/api/doctor/briefs/{bid}/acknowledge").json()["status"] == "acknowledged"
    c = sqlite3.connect(os.environ["UC2_DB_PATH"])
    actions = [r[0] for r in c.execute("SELECT action FROM audit_log WHERE resource_id = ? ORDER BY id", (bid,))]
    assert actions == ["BRIEF_DRAFTED", "BRIEF_SENT", "BRIEF_PATIENT_CALLED", "BRIEF_IDENTITY_FAILED", "BRIEF_IDENTITY_CONFIRMED",
                       "BRIEF_OPENED", "BRIEF_ACKNOWLEDGED"]


def test_updated_since_sent_keeps_the_sent_version(world):
    ids, client = world
    team = client(TEAM)
    bid = draft(team, "P-1001")["brief"]["id"]
    send(team, bid)
    doctor = client(PRIYA)
    sent = open_as_doctor(doctor, bid, "P-1001", "1967-03-15")["content"]
    assert doctor.get(f"/api/doctor/briefs/{bid}/updates").json()["changed"] is False
    team.post(f"/api/patients/{ids['P-1001']}/manual", json={"hba1c": {"value": 8.9, "date": str(TODAY)}})   # new verified data
    upd = doctor.get(f"/api/doctor/briefs/{bid}/updates").json()
    assert upd["changed"] is True and any(c.startswith("New verified HbA1c: 8.9 %") for c in upd["changes"])
    assert upd["live"]["key_numbers"]["rows"][0]["latest"]["value"] == 8.9
    assert doctor.get(f"/api/doctor/briefs/{bid}").json()["content"] == sent             # the sent version never changes


def test_completing_saves_the_snapshot_the_next_brief_compares_with(world):
    ids, client = world
    team = client(TEAM)
    bid = draft(team, "P-1004")["brief"]["id"]
    send(team, bid)
    doctor = client(PRIYA)
    open_as_doctor(doctor, bid, "P-1004", "1985-07-02")
    assert doctor.post(f"/api/doctor/briefs/{bid}/complete").json()["status"] == "completed"
    c = sqlite3.connect(os.environ["UC2_DB_PATH"])
    snap = json.loads(c.execute("SELECT numbers_json FROM consultation_snapshots WHERE brief_id = ?", (bid,)).fetchone()[0])
    assert snap == {"weight": {"value": 71.5, "unit": "kg", "date": TODAY.isoformat()}}
    assert team.post("/api/appointments/no-such-appointment/brief/draft").status_code == 404
    arjun = client(patient="P-1004")
    doctor_id = arjun.get("/api/me/appointments").json()["doctors"][0]["id"]
    slot = arjun.get(f"/api/me/appointments/slots?doctor_id={doctor_id}&date={TODAY + timedelta(days=20)}").json()[0]
    appt_id = arjun.post("/api/me/appointments", json={"slot_id": slot["id"]}).json()["id"]
    nxt = team.post(f"/api/appointments/{appt_id}/brief/draft").json()["content"]
    assert nxt["since_last_visit"]["basis"] == "snapshot" and nxt["since_last_visit"]["since"] == TODAY.isoformat()


# ------------------------------------------------------------------ authorization

def test_other_doctors_and_other_teams_cannot_reach_a_brief(world):
    ids, client = world
    team = client(TEAM)
    bid = draft(team, "P-1001")["brief"]["id"]
    send(team, bid)
    karan = client(KARAN)                          # a doctor with none of these patients
    assert karan.get("/api/doctor/queue").json()["queue"] == []
    assert karan.get("/api/doctor/inbox").json()["messages"] == []
    for method, url, body in [("post", f"/api/doctor/briefs/{bid}/call", None),
                              ("post", f"/api/doctor/briefs/{bid}/confirm-identity", {"patient_code": "P-1001", "age": 59}),
                              ("get", f"/api/doctor/briefs/{bid}", None), ("get", f"/api/doctor/briefs/{bid}/updates", None)]:
        r = getattr(karan, method)(url, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (url, r.status_code)
    appt_id = appointments(team)["P-1001"]["appointment_id"]
    for doctor in (karan, client(PRIYA)):          # doctors - even this brief's own doctor - never use the clinic team's side
        for method, url, body in [("get", "/api/clinic/briefs/today", None), ("get", f"/api/briefs/{bid}", None),
                                  ("patch", f"/api/briefs/{bid}", {"row_version": 1}), ("post", f"/api/briefs/{bid}/send", {}),
                                  ("post", f"/api/appointments/{appt_id}/brief/draft", None),
                                  ("post", "/api/clinic/briefs/send-ready", {})]:
            r = getattr(doctor, method)(url, **({"json": body} if body is not None else {}))
            assert r.status_code == 403, (url, r.status_code)
    for url in ("/api/doctor/queue", "/api/doctor/inbox", f"/api/doctor/briefs/{bid}"):   # and the team never the doctor's
        assert team.get(url).status_code == 403
    patient = client(patient="P-1001")
    assert patient.get(f"/api/doctor/briefs/{bid}").status_code == 401
    assert patient.get("/api/clinic/briefs/today").status_code == 401


def test_a_lab_result_and_a_care_team_value_on_the_same_day(world):
    """Regression (found on live data): lab results have text ids and care-team values numeric ids - same date must sort."""
    ids, client = world
    team = client(TEAM)
    same_day = str(TODAY - timedelta(days=97))                         # Ramesh's last lab HbA1c is on this day
    assert team.post(f"/api/patients/{ids['P-1001']}/manual", json={"hba1c": {"value": 8.1, "date": same_day}}).status_code == 200
    rows = {r["key"]: r for r in draft(team, "P-1001")["content"]["key_numbers"]["rows"]}
    assert rows["hba1c"]["latest"]["date"] == same_day


# ------------------------------------------------------------------ the doctor's dashboard: who sent what

def test_the_doctor_sees_which_clinic_team_sent_a_report(world):
    _, client = world
    team = client(TEAM)
    me = team.get("/api/auth/clinician/me").json()
    assert me["role"] == "care_team" and me["clinic_name"] == "Tanishq Clinic Management"
    doctor = client(PRIYA)
    assert doctor.get("/api/auth/clinician/me").json()["role"] == "doctor"
    assert doctor.get("/api/doctor/inbox").json() == {"team": "Tanishq Clinic Management", "messages": []}
    bid = draft(team, "P-1001")["brief"]["id"]
    send(team, bid)
    inbox = doctor.get("/api/doctor/inbox")
    [m] = inbox.json()["messages"]
    assert m["brief_id"] == bid and m["patient"] == "Ramesh Kumar" and m["patient_code"] == "P-1001"
    assert m["sent_by"] == {"team": "Tanishq Clinic Management", "name": None, "is_team": True}
    assert m["new"] is True and m["is_today"] is True and m["time"] == "10:00"
    assert doctor.get("/api/doctor/inbox", headers={"If-None-Match": inbox.headers["etag"]}).status_code == 304
    full = open_as_doctor(doctor, bid, "P-1001", "1967-03-15")
    assert full["sent_by"]["team"] == "Tanishq Clinic Management"
    assert doctor.get("/api/doctor/inbox").json()["messages"][0]["new"] is False      # opened


def test_the_clinic_team_sees_every_clinic_patient_but_takes_no_appointments(world):
    ids, client = world
    team = client(TEAM)
    codes = {p["patient_code"] for p in team.get("/api/patients").json()["patients"]}
    assert {"P-1001", "P-1002", "P-1003", "P-1004"} <= codes
    assert all(d["name"] != "Tanishq Clinic Management" for d in team.get("/api/doctors").json())
    assert team.get("/api/doctor-profile").status_code == 403
    signup = client().post("/api/auth/clinician/signup", json={"full_name": "Front Desk", "phone": "9811111111",
                                                               "password": "longenough1", "role": "care_team"})
    assert signup.status_code == 200 and signup.json()["role"] == "care_team"
