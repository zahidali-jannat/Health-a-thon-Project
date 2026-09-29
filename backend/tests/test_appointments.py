"""Doctor appointment booking: slot cadence, idempotent slot generation, race-safe booking, instant
confirmation, and "doctor unavailable" -> block / flag / draft / send as separate steps."""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from app import appointments as appt
from app import db
from tests.conftest import KARAN, TODAY, login_clinician, login_patient, patient_ids

DAY = (TODAY + timedelta(days=1)).isoformat()      # tomorrow: nothing here depends on the real time of day
NOW = "08:00"


@pytest.fixture()
def ids(priya):
    return patient_ids(priya)


@pytest.fixture()
def doctors(seeded_conn):
    conn, _ = seeded_conn
    by_name = {d["name"]: d for d in appt.list_doctors(conn)}
    return by_name["Dr. Priya Nair"], by_name["Dr. Karan Bhatia"]


def patient_client(make_client, code):
    c = make_client()
    login_patient(c, code)
    return c


def slots(client, doctor_id, day=DAY):
    r = client.get(f"/api/me/appointments/slots?doctor_id={doctor_id}&date={day}")
    assert r.status_code == 200, r.text
    return r.json()


def priya_id(client):
    return client.get("/api/me/appointments").json()["doctors"][0]["id"]


# ------------------------------------------------------------------ slots

def test_cadence_follows_each_doctors_length_and_buffer(doctors):
    priya, karan = doctors
    p = appt.slot_times(priya)                         # 09:00-13:00, 15 min + 5 min buffer
    assert p[:3] == [("09:00", "09:15"), ("09:20", "09:35"), ("09:40", "09:55")]
    assert p[-1] == ("12:40", "12:55") and len(p) == 12
    k = appt.slot_times(karan)                         # 10:00-14:00, 20 min + 10 min buffer
    assert k[:2] == [("10:00", "10:20"), ("10:30", "10:50")] and k[-1] == ("13:30", "13:50") and len(k) == 8


def test_slot_generation_is_idempotent_even_when_run_concurrently(seeded_conn, doctors):
    conn, _ = seeded_conn
    priya, _ = doctors
    appt.ensure_day(conn, priya, DAY)
    appt.ensure_day(conn, priya, DAY)
    conn.commit()
    path = os.environ["UC2_DB_PATH"]
    gate = threading.Barrier(4)

    def generate(_):
        c = db.connect(path)
        gate.wait()
        c.execute("DELETE FROM appointment_slots WHERE 0")          # no-op; just a fresh connection doing work
        appt.ensure_day(c, priya, "2026-10-10")
        c.commit()
        c.close()

    with ThreadPoolExecutor(4) as pool:
        list(pool.map(generate, range(4)))
    count = lambda d: conn.execute("SELECT COUNT(*) FROM appointment_slots WHERE doctor_id = ? AND date = ?",
                                   (priya["id"], d)).fetchone()[0]
    assert count(DAY) == 12 and count("2026-10-10") == 12


def test_availability_query_is_served_by_the_index(seeded_conn, doctors):
    conn, _ = seeded_conn
    plan = " ".join(r[3] for r in conn.execute(
        "EXPLAIN QUERY PLAN SELECT id FROM appointment_slots WHERE doctor_id = ? AND date = ? AND status = 'available' "
        "AND start_time > ? ORDER BY start_time", (doctors[0]["id"], DAY, "00:00")))
    assert "idx_slots_availability" in plan


# ------------------------------------------------------------------ booking

def test_two_simultaneous_bookings_one_wins_one_gets_slot_taken(seeded_conn, doctors):
    """Database level, real threads, separate connections - 25 rounds, one slot each."""
    conn, ids = seeded_conn
    priya, _ = doctors
    path = os.environ["UC2_DB_PATH"]
    racers = [conn.execute("SELECT * FROM patients WHERE patient_code = ?", (c,)).fetchone() for c in ("P-1001", "P-1002")]
    days = [(TODAY + timedelta(days=i)).isoformat() for i in range(2, 4)]
    for d in days:
        appt.ensure_day(conn, priya, d)
    conn.commit()
    targets = [r[0] for d in days for r in conn.execute(
        "SELECT id FROM appointment_slots WHERE doctor_id = ? AND date = ? ORDER BY start_time", (priya["id"], d))][:25]

    for slot_id in targets:
        gate, results = threading.Barrier(2), []

        def attempt(patient):
            c = db.connect(path)
            try:
                gate.wait()
                appt.book(c, dict(patient), slot_id, TODAY, NOW)
                results.append("ok")
            except appt.BookingError as e:
                results.append(e.code)
            finally:
                c.close()

        threads = [threading.Thread(target=attempt, args=(p,)) for p in racers]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert sorted(results) == ["ok", "slot_taken"], results
        # the winner is free to book again next round
        conn.execute("UPDATE appointments SET status = 'cancelled', cancel_reason = 'test' WHERE slot_id = ?", (slot_id,))
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM appointments WHERE slot_id = ?", (slot_id,)).fetchone()[0] == 1


def test_two_patients_book_the_same_slot_at_once_over_the_api(make_client):
    a, b = patient_client(make_client, "P-1001"), patient_client(make_client, "P-1002")
    doctor = priya_id(a)
    slot = slots(a, doctor)[3]["id"]
    gate = threading.Barrier(2)

    def go(client):
        gate.wait()
        return client.post("/api/me/appointments", json={"slot_id": slot})

    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(go, (a, b)))
    codes = sorted(r.status_code for r in responses)
    assert codes == [201, 409]
    loser = next(r for r in responses if r.status_code == 409).json()["detail"]
    assert loser == {"code": "slot_taken", "message": "This slot was just booked by someone else. Please choose another time."}
    assert slot not in [s["id"] for s in slots(b, doctor)]        # the refreshed list no longer offers it


def test_booking_is_confirmed_at_once_and_shows_for_the_care_team(make_client, priya, ids):
    ramesh = patient_client(make_client, "P-1001")
    doctor = priya_id(ramesh)
    first = slots(ramesh, doctor)[0]
    r = ramesh.post("/api/me/appointments", json={"slot_id": first["id"]})
    assert r.status_code == 201 and r.json()["status"] == "confirmed"
    mine = ramesh.get("/api/me/appointments").json()["appointments"]
    assert [(x["date"], x["start_time"], x["status"]) for x in mine] == [(DAY, "09:00", "confirmed")]
    seen = priya.get(f"/api/patients/{ids['P-1001']}/appointments").json()
    assert seen[0]["doctor_name"] == "Dr. Priya Nair" and seen[0]["start_time"] == "09:00"
    upcoming = priya.get("/api/worklist").json()["upcoming_visits"]
    assert any(v["patient_code"] == "P-1001" and v["name"] == "9:00 AM · Dr. Priya Nair" for v in upcoming)
    day = priya.get(f"/api/doctors/{doctor}/schedule?date={DAY}").json()
    assert day["slots"][0]["status"] == "booked" and day["slots"][0]["full_name"] == "Ramesh Kumar"


def test_booking_rules(make_client, doctors):
    ramesh = patient_client(make_client, "P-1001")
    doctor = priya_id(ramesh)
    free = slots(ramesh, doctor)
    assert ramesh.post("/api/me/appointments", json={"slot_id": free[0]["id"]}).status_code == 201
    again = ramesh.post("/api/me/appointments", json={"slot_id": free[1]["id"]})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "already_booked"
    # a doctor who is not on the patient's care team cannot be booked, or even looked at
    assert ramesh.get(f"/api/me/appointments/slots?doctor_id={doctors[1]['id']}&date={DAY}").status_code == 404
    # no dates in the past, none beyond the booking window
    assert ramesh.get(f"/api/me/appointments/slots?doctor_id={doctor}&date={TODAY - timedelta(days=1)}").status_code == 422
    assert ramesh.get(f"/api/me/appointments/slots?doctor_id={doctor}&date={TODAY + timedelta(days=61)}").status_code == 422


def test_database_refuses_a_second_live_appointment_on_a_slot(seeded_conn, doctors):
    import sqlite3
    conn, ids = seeded_conn
    priya, _ = doctors
    appt.ensure_day(conn, priya, DAY)
    slot = conn.execute("SELECT id FROM appointment_slots WHERE doctor_id = ? AND date = ? LIMIT 1", (priya["id"], DAY)).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):         # an appointment on a slot that isn't booked
        conn.execute("INSERT INTO appointments (id, slot_id, patient_id, doctor_id, status, booked_at, updated_at) "
                     "VALUES ('x1', ?, ?, ?, 'confirmed', 't', 't')", (slot, ids["P-1001"], priya["id"]))
    conn.execute("UPDATE appointment_slots SET status = 'booked' WHERE id = ?", (slot,))
    conn.execute("INSERT INTO appointments (id, slot_id, patient_id, doctor_id, status, booked_at, updated_at) "
                 "VALUES ('x1', ?, ?, ?, 'confirmed', 't', 't')", (slot, ids["P-1001"], priya["id"]))
    with pytest.raises(sqlite3.IntegrityError):         # a second live appointment on the same slot
        conn.execute("INSERT INTO appointments (id, slot_id, patient_id, doctor_id, status, booked_at, updated_at) "
                     "VALUES ('x2', ?, ?, ?, 'confirmed', 't', 't')", (slot, ids["P-1002"], priya["id"]))


# ------------------------------------------------------------------ doctor unavailable

def mark(client, doctor, start, end, reason="Emergency surgery"):
    """Preview, then confirm exactly what the preview showed (as the confirmation pop-up does)."""
    block = {"start_date": DAY, "start_time": start, "end_time": end, "reason": reason}
    preview = client.post(f"/api/doctors/{doctor}/unavailability/preview", json=block).json()
    r = client.post(f"/api/doctors/{doctor}/unavailability",
                    json={**block, "fingerprint": preview["fingerprint"], "checked": True})
    assert r.status_code == 200, r.text
    return r.json()


def test_unavailable_blocks_flags_and_drafts_but_sends_nothing(make_client, priya):
    ramesh, lakshmi = patient_client(make_client, "P-1001"), patient_client(make_client, "P-1002")
    doctor = priya_id(ramesh)
    free = {s["start_time"]: s["id"] for s in slots(ramesh, doctor)}
    assert ramesh.post("/api/me/appointments", json={"slot_id": free["09:20"]}).status_code == 201
    assert lakshmi.post("/api/me/appointments", json={"slot_id": free["09:40"]}).status_code == 201

    result = mark(priya, doctor, "09:10", "10:00")
    assert result["blocked"] == 1 and result["needs_reschedule"] == 2      # 09:00-09:15 overlaps the window

    day = {s["start_time"]: s for s in priya.get(f"/api/doctors/{doctor}/schedule?date={DAY}").json()["slots"]}
    assert day["09:00"]["status"] == "blocked"
    assert day["09:20"]["status"] == "booked" and day["09:20"]["appointment_status"] == "needs_reschedule"
    assert day["10:00"]["status"] == "available"
    assert [s["start_time"] for s in slots(ramesh, doctor)][0] == "10:00"   # blocked times are gone for patients

    drafts = priya.get(f"/api/doctors/{doctor}/reschedule-notices").json()
    assert [(n["full_name"], n["status"]) for n in drafts] == [("Ramesh Kumar", "draft"), ("Lakshmi Iyer", "draft")]
    assert drafts[0]["message"] == ("Dr. Priya Nair is unavailable for your scheduled slot on 26 Sep 2026 at 9:20 AM. "
                                    "The next available slot is 26 Sep 2026, 10:00 AM.")
    assert drafts[1]["message"].endswith("The next available slot is 26 Sep 2026, 10:20 AM.")   # never the same slot
    # nothing reaches the patient until someone presses Send; the appointment is still there, flagged
    assert ramesh.get("/api/me/notifications").json() == []
    mine = ramesh.get("/api/me/appointments").json()["appointments"]
    assert mine[0]["status"] == "needs_reschedule" and mine[0]["notice"] is None


def test_send_one_then_send_all_and_rebooking_closes_the_old_appointment(make_client, priya):
    ramesh, lakshmi = patient_client(make_client, "P-1001"), patient_client(make_client, "P-1002")
    doctor = priya_id(ramesh)
    free = {s["start_time"]: s["id"] for s in slots(ramesh, doctor)}
    ramesh.post("/api/me/appointments", json={"slot_id": free["09:20"]})
    lakshmi.post("/api/me/appointments", json={"slot_id": free["09:40"]})
    mark(priya, doctor, "09:10", "10:00")
    first, second = priya.get(f"/api/doctors/{doctor}/reschedule-notices").json()

    assert priya.post(f"/api/reschedule-notices/{first['id']}/send").json()["status"] == "sent"
    again = priya.post(f"/api/reschedule-notices/{first['id']}/send")
    assert again.status_code == 409 and again.json()["detail"]["code"] == "already_sent"
    note = ramesh.get("/api/me/notifications").json()[0]
    assert note["kind"] == "appointment_reschedule" and note["message"] == first["message"]

    everything = priya.post(f"/api/doctors/{doctor}/reschedule-notices/send-all").json()
    assert [n["id"] for n in everything["sent"]] == [second["id"]] and everything["failed"] == []
    assert lakshmi.get("/api/me/notifications").json()[0]["message"] == second["message"]

    # Ramesh takes the suggested 10:00 slot: that is the reschedule - the old appointment closes, its slot stays out of use
    assert ramesh.post("/api/me/appointments", json={"slot_id": free["10:00"]}).status_code == 201
    mine = ramesh.get("/api/me/appointments").json()["appointments"]
    assert [(a["start_time"], a["status"]) for a in mine] == [("10:00", "confirmed")]
    day = {s["start_time"]: s for s in priya.get(f"/api/doctors/{doctor}/schedule?date={DAY}").json()["slots"]}
    assert day["09:20"]["status"] == "blocked" and day["09:20"]["appointment_id"] is None
    assert [n["full_name"] for n in priya.get(f"/api/doctors/{doctor}/reschedule-notices").json()] == ["Lakshmi Iyer"]


def test_a_stale_suggestion_is_replaced_before_sending(make_client, priya):
    ramesh, meena = patient_client(make_client, "P-1001"), patient_client(make_client, "P-1003")
    doctor = priya_id(ramesh)
    free = {s["start_time"]: s["id"] for s in slots(ramesh, doctor)}
    ramesh.post("/api/me/appointments", json={"slot_id": free["09:20"]})
    mark(priya, doctor, "09:10", "09:40")
    notice = priya.get(f"/api/doctors/{doctor}/reschedule-notices").json()[0]
    assert notice["message"].endswith("9:40 AM.")
    assert meena.post("/api/me/appointments", json={"slot_id": free["09:40"]}).status_code == 201   # someone takes it
    sent = priya.post(f"/api/reschedule-notices/{notice['id']}/send").json()
    assert sent["message"].endswith("The next available slot is 26 Sep 2026, 10:00 AM.")


def test_blocking_stands_even_if_sending_fails(make_client, priya, monkeypatch):
    ramesh = patient_client(make_client, "P-1001")
    doctor = priya_id(ramesh)
    ramesh.post("/api/me/appointments", json={"slot_id": slots(ramesh, doctor)[1]["id"]})
    mark(priya, doctor, "09:00", "10:00")
    notice = priya.get(f"/api/doctors/{doctor}/reschedule-notices").json()[0]

    from app import repo
    with monkeypatch.context() as m:         # delivery breaks while sending
        m.setattr(repo, "get_patient", lambda *a: (_ for _ in ()).throw(RuntimeError("delivery down")))
        with pytest.raises(RuntimeError):
            priya.post(f"/api/reschedule-notices/{notice['id']}/send")

    day = {s["start_time"]: s for s in priya.get(f"/api/doctors/{doctor}/schedule?date={DAY}").json()["slots"]}
    assert day["09:00"]["status"] == "blocked" and day["09:20"]["appointment_status"] == "needs_reschedule"
    assert priya.get(f"/api/doctors/{doctor}/reschedule-notices").json()[0]["status"] == "draft"   # can simply be retried
    assert priya.post(f"/api/reschedule-notices/{notice['id']}/send").status_code == 200


def test_other_care_teams_see_the_load_but_not_the_patient(make_client, priya):
    ramesh = patient_client(make_client, "P-1001")
    doctor = priya_id(ramesh)
    ramesh.post("/api/me/appointments", json={"slot_id": slots(ramesh, doctor)[0]["id"]})
    karan = make_client()
    login_clinician(karan, KARAN)
    first = karan.get(f"/api/doctors/{doctor}/schedule?date={DAY}").json()["slots"][0]
    assert first["status"] == "booked" and first["full_name"] is None and first["patient_id"] is None
    mark(karan, doctor, "09:00", "09:30")
    notice = karan.get(f"/api/doctors/{doctor}/reschedule-notices").json()[0]
    assert notice["full_name"] is None and notice["message"] is None and notice["can_send"] is False
    assert karan.post(f"/api/reschedule-notices/{notice['id']}/send").status_code == 404
    assert priya.get(f"/api/doctors/{doctor}/reschedule-notices").json()[0]["can_send"] is True


def test_changing_hours_rebuilds_only_untouched_days(make_client, priya):
    ramesh = patient_client(make_client, "P-1001")
    doctor = priya_id(ramesh)
    later = (TODAY + timedelta(days=5)).isoformat()
    slots(ramesh, doctor, later)                                   # generated, nothing happened on it
    appt_today = TODAY.isoformat()
    ramesh.get(f"/api/me/appointments/slots?doctor_id={doctor}&date={appt_today}")    # today generated too, untouched
    ramesh.post("/api/me/appointments", json={"slot_id": slots(ramesh, doctor)[0]["id"]})   # DAY has a booking
    r = priya.put("/api/doctor-profile", json={"working_start_time": "14:00", "working_end_time": "16:00",
                                               "slot_duration_minutes": 30, "buffer_minutes": 0})
    assert r.status_code == 200
    assert [s["start_time"] for s in slots(ramesh, doctor, later)] == ["14:00", "14:30", "15:00", "15:30"]
    assert slots(ramesh, doctor)[0]["start_time"] == "09:20"       # the booked day kept its slots
    ramesh.get(f"/api/me/appointments/slots?doctor_id={doctor}&date={appt_today}")    # rebuilt when next opened
    from app import db
    c = db.connect(os.environ["UC2_DB_PATH"])
    assert [r[0] for r in c.execute("SELECT start_time FROM appointment_slots WHERE doctor_id = ? AND date = ? "
                                    "ORDER BY start_time", (doctor, appt_today))] == ["14:00", "14:30", "15:00", "15:30"]
    bad = priya.put("/api/doctor-profile", json={"working_start_time": "16:00", "working_end_time": "14:00"})
    assert bad.status_code == 422


def test_patient_can_book_with_a_doctor_who_never_set_hours(make_client, seeded_conn):
    """The live case: the care team's doctor never opened Settings. Booking must still just work (default hours)."""
    conn, _ = seeded_conn
    conn.execute("DELETE FROM doctors")
    conn.commit()
    ramesh = patient_client(make_client, "P-1001")
    mine = ramesh.get("/api/me/appointments").json()
    assert [d["name"] for d in mine["doctors"]] == ["Dr. Priya Nair"]
    free = slots(ramesh, mine["doctors"][0]["id"])
    assert (free[0]["start_time"], free[0]["end_time"], free[1]["start_time"]) == ("09:00", "09:15", "09:20")
    assert free[-1]["start_time"] == "16:40"                    # 09:00-17:00 by default
    r = ramesh.post("/api/me/appointments", json={"slot_id": free[0]["id"]})
    assert r.status_code == 201 and r.json()["status"] == "confirmed"
