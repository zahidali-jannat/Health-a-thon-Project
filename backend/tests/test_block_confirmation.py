"""Marking a doctor unavailable goes through a preview (everything the confirmation pop-up shows) and a confirm that
re-checks the same plan: AM/PM mistakes are caught, the schedule can't change unnoticed, times never shift."""

import json
import os
import sqlite3
from datetime import timedelta

import pytest

from app import appointments as appt
from app import db
from tests.conftest import TODAY, login_patient

DAY = (TODAY + timedelta(days=1)).isoformat()          # 26 Sep 2026, a Saturday
NEXT = (TODAY + timedelta(days=2)).isoformat()
AFTER = (TODAY + timedelta(days=3)).isoformat()


@pytest.fixture()
def doctor(priya):
    """Dr. Priya Nair, working 9:00 AM - 6:00 PM (15-minute slots, 5-minute buffer)."""
    r = priya.put("/api/doctor-profile", json={"working_start_time": "09:00", "working_end_time": "18:00",
                                               "slot_duration_minutes": 15, "buffer_minutes": 5})
    assert r.status_code == 200
    return r.json()["doctor"]["id"]


def preview(client, doctor, **block):
    r = client.post(f"/api/doctors/{doctor}/unavailability/preview", json={"start_date": DAY, **block})
    assert r.status_code == 200, r.text
    return r.json()


def confirm(client, doctor, plan_input, fingerprint, checked=True, **extra):
    return client.post(f"/api/doctors/{doctor}/unavailability",
                       json={"start_date": DAY, **plan_input, "fingerprint": fingerprint, "checked": checked, **extra})


def book(make_client, code, doctor, day, hm):
    c = make_client()
    login_patient(c, code)
    slot = next(s for s in c.get(f"/api/me/appointments/slots?doctor_id={doctor}&date={day}").json() if s["start_time"] == hm)
    assert c.post("/api/me/appointments", json={"slot_id": slot["id"]}).status_code == 201


def codes(items):
    return [x["code"] for x in items]


# ------------------------------------------------------------------ AM / PM mistakes

def test_2am_instead_of_2pm_is_caught_and_the_fix_updates_everything(priya, doctor, make_client):
    book(make_client, "P-1001", doctor, DAY, "14:20")                 # a booking at 2:20 PM
    wrong = preview(priya, doctor, start_time="02:00", end_time="05:30")
    outside = next(w for w in wrong["warnings"] if w["code"] == "outside_hours")
    assert outside["message"] == ("2:00 AM and 5:30 AM are outside Dr. Priya Nair's working hours (9:00 AM – 6:00 PM). "
                                  "Did you mean 2:00 PM – 5:30 PM?")
    assert outside["fix"] == {"start_time": "14:00", "end_time": "17:30", "label": "Change to 2:00 PM – 5:30 PM"}
    assert wrong["affected"] == [] and wrong["free_slots"] == 0 and wrong["duration_text"] == "3 hours 30 minutes"

    fixed = preview(priya, doctor, start_time=outside["fix"]["start_time"], end_time=outside["fix"]["end_time"])
    assert (fixed["start"]["time"], fixed["end"]["time"]) == ("14:00", "17:30")
    assert "outside_hours" not in codes(fixed["warnings"])
    assert [(a["start_time"], a["patient_name"]) for a in fixed["affected"]] == [("14:20", "Ramesh Kumar")]
    assert fixed["free_slots"] == 10                                  # 14:00 ... 17:20, less the booked one
    day = fixed["days"][0]
    assert day["working"] == [540, 1080] and day["block"] == [840, 1050]
    assert {"minute": 860, "affected": True} in day["appointments"]


def test_end_before_start_is_an_error_with_an_am_pm_swap(priya, doctor):
    plan = preview(priya, doctor, start_time="14:00", end_time="05:30")
    assert plan["errors"] == [{"code": "end_before_start", "message": "End time is before start time. Check AM/PM.",
                               "fix": {"end_time": "17:30", "label": "Change end to 5:30 PM"}}]
    r = confirm(priya, doctor, {"start_time": "14:00", "end_time": "05:30"}, "anything")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "end_before_start"


def test_a_start_in_the_past_is_an_error(priya, doctor):
    plan = preview(priya, doctor, start_date=(TODAY - timedelta(days=1)).isoformat(), start_time="10:00", end_time="11:00")
    assert codes(plan["errors"]) == ["in_past"]


def test_confirm_needs_the_checkbox(priya, doctor):
    block = {"start_time": "10:00", "end_time": "11:00"}
    plan = preview(priya, doctor, **block)
    r = confirm(priya, doctor, block, plan["fingerprint"], checked=False)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "not_checked"
    assert confirm(priya, doctor, block, plan["fingerprint"]).status_code == 200


# ------------------------------------------------------------------ durations and shapes of block

@pytest.mark.parametrize("block, text, warning", [
    ({"start_time": "10:00", "end_time": "10:30"}, "30 minutes", None),
    ({"start_time": "14:00", "end_time": "17:30"}, "3 hours 30 minutes", None),
    ({"start_time": "22:00", "end_date": NEXT, "end_time": "01:30"}, "3 hours 30 minutes", "crosses_midnight"),
    ({"start_time": "10:00", "end_date": AFTER, "end_time": "13:30"}, "2 days 3 hours 30 minutes", "crosses_midnight"),
    ({"start_time": "10:00", "end_time": "10:10"}, "10 minutes", "short_block"),
    ({"start_time": "05:00", "end_time": "19:00"}, "14 hours", "long_block"),
])
def test_duration_text(priya, doctor, block, text, warning):
    plan = preview(priya, doctor, **block)
    assert plan["duration_text"] == text and plan["errors"] == []
    if warning:
        assert warning in codes(plan["warnings"])


def test_cross_midnight_warning_names_the_next_day(priya, doctor):
    plan = preview(priya, doctor, start_time="22:00", end_date=NEXT, end_time="01:30")
    w = next(w for w in plan["warnings"] if w["code"] == "crosses_midnight")
    assert w["message"] == "This block continues past midnight into Sunday, 27 September 2026."
    assert [d["block"] for d in plan["days"]] == [[1320, 1440], [0, 90]]


def test_full_day_uses_the_doctors_hours(priya, doctor):
    plan = preview(priya, doctor, full_day=True)
    assert (plan["start"]["time"], plan["end"]["time"]) == ("09:00", "18:00")
    assert plan["duration_text"] == "Full day (9:00 AM – 6:00 PM, the doctor's working hours)" and plan["warnings"] == []


def test_a_multi_day_block_blocks_every_day_and_shows_one_row_per_day(priya, doctor):
    block = {"start_time": "09:00", "end_date": AFTER, "end_time": "18:00"}
    plan = preview(priya, doctor, **block)
    assert [d["date"] for d in plan["days"]] == [DAY, NEXT, AFTER] and plan["free_slots"] == 27 * 3
    assert confirm(priya, doctor, block, plan["fingerprint"]).json()["blocked"] == 81


# ------------------------------------------------------------------ the schedule changed meanwhile

def test_a_booking_made_after_the_popup_opened_stops_the_save(priya, doctor, make_client):
    block = {"start_time": "14:00", "end_time": "17:30"}
    plan = preview(priya, doctor, **block)
    assert plan["affected"] == []
    book(make_client, "P-1002", doctor, DAY, "15:00")                 # someone books while the pop-up is open
    r = confirm(priya, doctor, block, plan["fingerprint"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "schedule_changed"
    assert r.json()["detail"]["message"] == "The schedule changed - please review again."
    fresh = r.json()["detail"]["preview"]
    assert [a["start_time"] for a in fresh["affected"]] == ["15:00"]
    c = sqlite3.connect(os.environ["UC2_DB_PATH"])
    assert c.execute("SELECT COUNT(*) FROM doctor_unavailability").fetchone()[0] == 0          # nothing saved
    assert c.execute("SELECT COUNT(*) FROM appointment_slots WHERE status = 'blocked'").fetchone()[0] == 0
    assert confirm(priya, doctor, block, fresh["fingerprint"]).json()["needs_reschedule"] == 1  # after a re-check: saved


# ------------------------------------------------------------------ times never shift; the audit says what was seen

def test_saved_times_match_the_popup_and_are_stored_in_utc(priya, doctor):
    block = {"start_time": "14:00", "end_time": "17:30"}
    plan = preview(priya, doctor, **block)
    saved = confirm(priya, doctor, block, plan["fingerprint"],
                    warnings_shown=["outside_hours"], changes=["start and end changed from AM to PM"]).json()
    assert (saved["starts_at"], saved["ends_at"]) == ("2026-09-26T08:30:00Z", "2026-09-26T12:00:00Z")
    away = priya.get(f"/api/doctors/{doctor}/schedule?date={DAY}").json()["unavailable"][0]
    assert (away["start"], away["end"]) == (plan["start"], plan["end"]) == \
        ({"date": DAY, "time": "14:00"}, {"date": DAY, "time": "17:30"})
    c = sqlite3.connect(os.environ["UC2_DB_PATH"])
    detail, new_value = c.execute("SELECT detail, new_value FROM audit_log WHERE action = 'DOCTOR_UNAVAILABLE'").fetchone()
    assert detail == ("Confirmation shown and accepted. Warnings shown: outside_hours. "
                      "Changed in the pop-up: start and end changed from AM to PM.")
    assert "2026-09-26 14:00 → 2026-09-26 17:30 Asia/Kolkata" in new_value
    record = json.loads(c.execute("SELECT confirmation FROM doctor_unavailability").fetchone()[0])
    assert record["shown_and_accepted"] is True and record["warnings_shown"] == ["outside_hours"]


def test_days_made_later_still_respect_an_earlier_block(seeded_conn):
    conn, _ = seeded_conn
    doctor = next(d for d in appt.list_doctors(conn) if d["name"] == "Dr. Priya Nair")
    later = (TODAY + timedelta(days=10)).isoformat()
    user = conn.execute("SELECT id FROM clinical_users LIMIT 1").fetchone()[0]
    conn.execute("INSERT INTO doctor_unavailability (doctor_id, starts_at, ends_at, created_by, created_at) VALUES (?, ?, ?, ?, 'x')",
                 (doctor["id"], appt.utc_text(appt.local_dt(later, "10:00")), appt.utc_text(appt.local_dt(later, "11:00")), user))
    appt.ensure_day(conn, doctor, later)                            # this day's slots are made only now
    blocked = [r[0] for r in conn.execute("SELECT start_time FROM appointment_slots WHERE doctor_id = ? AND date = ? "
                                          "AND status = 'blocked' ORDER BY start_time", (doctor["id"], later))]
    assert blocked == ["10:00", "10:20", "10:40"]


def test_old_single_day_blocks_convert_exactly(tmp_path, monkeypatch):
    """Migration 0012: a block saved as '2026-09-30 09:10-10:00' (clinic time) becomes 03:40-04:30 UTC."""
    import shutil
    older = tmp_path / "migrations"
    older.mkdir()
    for f in sorted(db.MIGRATIONS_DIR.glob("*.sql")):
        if f.stem < "0012":
            shutil.copy(f, older / f.name)
    real = db.MIGRATIONS_DIR
    conn = db.connect(tmp_path / "old.db")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", older)
    db.migrate(conn)                                                   # the schema as it was before 0012
    conn.execute("INSERT INTO clinical_users (id, clinician_code, full_name, phone, password_hash, created_at, updated_at) "
                 "VALUES ('u', 'CLN-X', 'Dr X', '1', 'h', 'x', 'x')")
    conn.execute("INSERT INTO doctors (id, clinical_user_id, name, working_start_time, working_end_time, created_at, updated_at) "
                 "VALUES ('d', 'u', 'Dr X', '09:00', '17:00', 'x', 'x')")
    conn.execute("INSERT INTO doctor_unavailability (doctor_id, date, start_time, end_time, created_by, created_at) "
                 "VALUES ('d', '2026-09-30', '09:10', '10:00', 'u', 'x')")
    conn.commit()
    monkeypatch.setattr(db, "MIGRATIONS_DIR", real)
    assert db.migrate(conn)[0] == "0012_unavailability_utc_range"                # later migrations may follow
    assert tuple(conn.execute("SELECT starts_at, ends_at FROM doctor_unavailability").fetchone()) == \
        ("2026-09-30T03:40:00Z", "2026-09-30T04:30:00Z")
