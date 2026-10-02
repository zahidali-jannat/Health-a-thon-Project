"""Doctor appointment booking.

Guarantees, and where each one lives:
- No double booking: a booking is ONE transaction (BEGIN IMMEDIATE) that flips the slot
  'available' -> 'booked' with a conditional UPDATE and inserts the appointment. If the UPDATE touches
  0 rows, someone else got there first. Behind that, the database itself refuses a second live
  appointment on a slot (partial unique index) and any appointment on a slot that isn't booked (trigger).
- Idempotent slot generation: slots are real rows with UNIQUE (doctor_id, date, start_time), inserted
  with INSERT OR IGNORE - running it twice (or concurrently) cannot create duplicates.
- Marking a doctor away and telling patients are separate steps: blocking commits on its own; the
  "please rebook" messages are saved as drafts and sent later, one by one or all together.

Slots are clinic-local wall-clock strings (dates YYYY-MM-DD, times HH:MM, Asia/Kolkata). A "doctor unavailable"
block is a real time range stored as UTC instants; it is converted to clinic-local days when it touches slots.
"""

import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import repo
from .db import now_iso
from .detection import fmt

BOOKING_DAYS = 60          # patients can book up to this many days ahead
DEFAULT_HOURS = ("09:00", "17:00", 15, 5)   # start, end, slot minutes, buffer minutes - until the doctor changes them
REBOOK_SEARCH_DAYS = 30    # how far ahead to look for a replacement slot
CLINIC_TZ = ZoneInfo("Asia/Kolkata")        # every time shown to people; stored instants are UTC
MAX_BLOCK_DAYS = 31


class BookingError(Exception):
    """A booking/scheduling request that cannot go ahead. `code` lets the app react (e.g. refresh slots);
    `payload` carries extra data for it (e.g. a fresh preview when the schedule changed)."""

    def __init__(self, code: str, message: str, status: int = 409, payload: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.payload = code, message, status, payload


# ------------------------------------------------------------------ small helpers

def _mins(hm: str) -> int:
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def _hm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def clock(hm: str) -> str:
    """'09:20' -> '9:20 AM' (how the clinic writes times to patients)."""
    h, m = divmod(_mins(hm), 60)
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def doctor_title(name: str) -> str:
    return name if name.lower().startswith("dr") else f"Dr. {name}"


def valid_hm(hm: str) -> bool:
    try:
        h, m = hm.split(":")
        return len(h) == 2 and len(m) == 2 and 0 <= int(h) <= 23 and 0 <= int(m) <= 59
    except ValueError:
        return False


def _begin(conn: sqlite3.Connection) -> None:
    """Start a write transaction now, so everything read below is what we write against."""
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")


def _stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="microseconds")


def local_dt(day: str, hm: str) -> datetime:
    """A clinic wall-clock time (Asia/Kolkata) as an aware datetime."""
    return datetime.fromisoformat(f"{day}T{hm}").replace(tzinfo=CLINIC_TZ)


def utc_text(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:00Z")


def from_utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(CLINIC_TZ)


def day_segments(start: datetime, end: datetime) -> list[tuple[str, str, str]]:
    """A clinic-local range cut into (date, from HH:MM, to HH:MM) per day; a day's end is '24:00'."""
    out, d = [], start.date()
    while d <= end.date():
        s = start.strftime("%H:%M") if d == start.date() else "00:00"
        e = end.strftime("%H:%M") if d == end.date() else "24:00"
        if s < e:
            out.append((d.isoformat(), s, e))
        d += timedelta(days=1)
    return out


def duration_text(minutes: int) -> str:
    """3 hours 30 minutes / 2 days 3 hours 30 minutes / 30 minutes."""
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    parts = [f"{n} {word}{'' if n == 1 else 's'}" for n, word in ((days, "day"), (hours, "hour"), (mins, "minute")) if n]
    return " ".join(parts) or "0 minutes"


def long_date(d: date) -> str:
    return f"{d:%A}, {d.day} {d:%B %Y}"


def _in_past(day: str, start: str, today: date, now_hm: str) -> bool:
    return day < today.isoformat() or (day == today.isoformat() and start <= now_hm)


# ------------------------------------------------------------------ doctors

def ensure_doctors(conn, user_ids: list[str] | None = None) -> None:
    """Every active doctor takes appointments: one without saved hours gets DEFAULT_HOURS the first time
    they are needed (booking works with no setup step). Clinic-team accounts never do.
    Idempotent - clinical_user_id is UNIQUE. Caller commits."""
    where, args = ("AND u.id IN (%s)" % ",".join("?" * len(user_ids)), user_ids) if user_ids is not None else ("", [])
    if user_ids is not None and not user_ids:
        return
    start, end, length, buffer = DEFAULT_HOURS
    for r in conn.execute(f"SELECT u.id, u.full_name FROM clinical_users u WHERE u.is_active = 1 AND u.role = 'doctor' {where} "
                          "AND NOT EXISTS (SELECT 1 FROM doctors d WHERE d.clinical_user_id = u.id)", args).fetchall():
        conn.execute("INSERT OR IGNORE INTO doctors (id, clinical_user_id, name, working_start_time, working_end_time, "
                     "slot_duration_minutes, buffer_minutes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (repo.new_id(), r["id"], r["full_name"], start, end, length, buffer, now_iso(), now_iso()))


def get_doctor(conn, doctor_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM doctors WHERE id = ?", (doctor_id,)).fetchone()
    return dict(row) if row else None


def doctor_for_user(conn, user_id: str) -> dict | None:
    ensure_doctors(conn, [user_id])
    row = conn.execute("SELECT * FROM doctors WHERE clinical_user_id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def list_doctors(conn) -> list[dict]:
    ensure_doctors(conn)
    return [dict(r) for r in conn.execute("SELECT * FROM doctors ORDER BY name")]


def bookable_doctors(conn, patient_id: str) -> list[dict]:
    """A patient books with the doctors on their own care team. A patient the clinic team looks after
    (registered or linked by the team) may book any of the clinic's doctors."""
    team = conn.execute("SELECT u.id, u.role FROM care_team_assignments a JOIN clinical_users u ON u.id = a.clinical_user_id "
                        "WHERE a.patient_id = ? AND a.revoked_at IS NULL AND u.is_active = 1", (patient_id,)).fetchall()
    if any(r["role"] == "care_team" for r in team):
        return list_doctors(conn)
    ensure_doctors(conn, [r["id"] for r in team])
    return [dict(r) for r in conn.execute(
        "SELECT d.* FROM doctors d JOIN care_team_assignments a ON a.clinical_user_id = d.clinical_user_id "
        "WHERE a.patient_id = ? AND a.revoked_at IS NULL ORDER BY d.name", (patient_id,))]


def can_book_with(conn, patient_id: str, doctor_id: str) -> bool:
    return any(d["id"] == doctor_id for d in bookable_doctors(conn, patient_id))


def save_doctor_profile(conn, user: dict, start: str, end: str, duration: int, buffer: int, today: date) -> dict:
    """Create or change the signed-in clinician's consultation hours.

    Days already generated keep their slots if anything happened on them (a booking or a block); untouched
    days from today on are cleared so they are rebuilt with the new hours the next time someone looks at them."""
    if not (valid_hm(start) and valid_hm(end)) or start >= end:
        raise BookingError("invalid", "Please choose a start time before the end time.", 422)
    if not 5 <= duration <= 240 or not 0 <= buffer <= 120:
        raise BookingError("invalid", "Please choose a slot length of 5–240 minutes and a buffer of 0–120.", 422)
    if _mins(start) + duration > _mins(end):
        raise BookingError("invalid", "The working hours are shorter than one appointment.", 422)
    _begin(conn)
    try:
        existing = doctor_for_user(conn, user["id"])
        if existing:
            conn.execute("UPDATE doctors SET name = ?, working_start_time = ?, working_end_time = ?, slot_duration_minutes = ?, "
                         "buffer_minutes = ?, updated_at = ? WHERE id = ?",
                         (user["full_name"], start, end, duration, buffer, now_iso(), existing["id"]))
            doctor_id = existing["id"]
            conn.execute(   # today included: a day nobody has booked or blocked simply takes the new hours
                "DELETE FROM appointment_slots WHERE doctor_id = ? AND date >= ? AND date IN ("
                "  SELECT date FROM appointment_slots WHERE doctor_id = ? AND date >= ? GROUP BY date"
                "  HAVING SUM(status <> 'available') = 0) "
                "AND id NOT IN (SELECT slot_id FROM appointments)",
                (doctor_id, today.isoformat(), doctor_id, today.isoformat()))
        else:
            doctor_id = repo.new_id()
            conn.execute("INSERT INTO doctors (id, clinical_user_id, name, working_start_time, working_end_time, "
                         "slot_duration_minutes, buffer_minutes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (doctor_id, user["id"], user["full_name"], start, end, duration, buffer, now_iso(), now_iso()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_doctor(conn, doctor_id)


# ------------------------------------------------------------------ slots

def slot_times(doctor: dict) -> list[tuple[str, str]]:
    """The day's slots: each lasts slot_duration_minutes; the next starts after a buffer_minutes gap."""
    start, end = _mins(doctor["working_start_time"]), _mins(doctor["working_end_time"])
    length, step = doctor["slot_duration_minutes"], doctor["slot_duration_minutes"] + doctor["buffer_minutes"]
    out, t = [], start
    while t + length <= end:
        out.append((_hm(t), _hm(t + length)))
        t += step
    return out


def ensure_day(conn, doctor: dict, day: str) -> None:
    """Materialise one doctor-day of slots. Safe to call any number of times (UNIQUE + INSERT OR IGNORE).
    The caller commits."""
    if conn.execute("SELECT 1 FROM appointment_slots WHERE doctor_id = ? AND date = ? LIMIT 1",
                    (doctor["id"], day)).fetchone():
        return
    created = now_iso()
    conn.executemany("INSERT OR IGNORE INTO appointment_slots (doctor_id, date, start_time, end_time, status, created_at) "
                     "VALUES (?, ?, ?, ?, 'available', ?)",
                     [(doctor["id"], day, s, e, created) for s, e in slot_times(doctor)])
    # a block marked before this day's slots existed still applies to them
    day_start = local_dt(day, "00:00")
    for u in conn.execute("SELECT starts_at, ends_at FROM doctor_unavailability WHERE doctor_id = ? AND starts_at < ? "
                          "AND ends_at > ?", (doctor["id"], utc_text(day_start + timedelta(days=1)), utc_text(day_start))).fetchall():
        for d, s, e in day_segments(from_utc(u["starts_at"]), from_utc(u["ends_at"])):
            if d == day:
                conn.execute("UPDATE appointment_slots SET status = 'blocked' WHERE doctor_id = ? AND date = ? "
                             "AND start_time < ? AND end_time > ? AND status = 'available'", (doctor["id"], day, e, s))


def _check_day(day: str, today: date) -> None:
    try:
        d = date.fromisoformat(day)
    except ValueError:
        raise BookingError("invalid", "Please choose a valid date.", 422) from None
    if d < today or d > today + timedelta(days=BOOKING_DAYS):
        raise BookingError("invalid", f"Please choose a date from today up to {BOOKING_DAYS} days ahead.", 422)


def available_slots(conn, doctor: dict, day: str, today: date, now_hm: str) -> list[dict]:
    """Bookable slots only - filtered by the database (indexed on doctor_id, date, status)."""
    _check_day(day, today)
    ensure_day(conn, doctor, day)
    conn.commit()
    after = now_hm if day == today.isoformat() else "00:00"
    return [dict(r) for r in conn.execute(
        "SELECT id, date, start_time, end_time FROM appointment_slots "
        "WHERE doctor_id = ? AND date = ? AND status = 'available' AND start_time > ? ORDER BY start_time",
        (doctor["id"], day, after))]


# ------------------------------------------------------------------ booking

def _upcoming_confirmed(conn, patient_id: str, doctor_id: str, today: date, now_hm: str) -> dict | None:
    row = conn.execute(
        "SELECT s.date, s.start_time FROM appointments a JOIN appointment_slots s ON s.id = a.slot_id "
        "WHERE a.patient_id = ? AND a.doctor_id = ? AND a.status = 'confirmed' "
        "AND (s.date > ? OR (s.date = ? AND s.start_time > ?)) ORDER BY s.date, s.start_time LIMIT 1",
        (patient_id, doctor_id, today.isoformat(), today.isoformat(), now_hm)).fetchone()
    return dict(row) if row else None


def book(conn, patient: dict, slot_id: int, today: date, now_hm: str) -> dict:
    """Book one slot, atomically. Raises BookingError('slot_taken') if another patient got it first."""
    _begin(conn)
    try:
        slot = conn.execute("SELECT * FROM appointment_slots WHERE id = ?", (slot_id,)).fetchone()
        if slot is None or not can_book_with(conn, patient["id"], slot["doctor_id"]):
            raise BookingError("not_found", "This time is not available to book.", 404)
        doctor = get_doctor(conn, slot["doctor_id"])
        if _in_past(slot["date"], slot["start_time"], today, now_hm):
            raise BookingError("slot_past", "This time has already passed. Please choose another time.")
        held = _upcoming_confirmed(conn, patient["id"], doctor["id"], today, now_hm)
        if held:
            raise BookingError("already_booked", f"You already have an appointment with {doctor_title(doctor['name'])} on "
                                                 f"{fmt(date.fromisoformat(held['date']))} at {clock(held['start_time'])}.")

        # The whole guarantee: only one request can move this row from 'available' to 'booked'.
        taken = conn.execute("UPDATE appointment_slots SET status = 'booked' WHERE id = ? AND status = 'available'",
                             (slot_id,)).rowcount
        if taken != 1:
            now_status = conn.execute("SELECT status FROM appointment_slots WHERE id = ?", (slot_id,)).fetchone()[0]
            if now_status == "booked":
                raise BookingError("slot_taken", "This slot was just booked by someone else. Please choose another time.")
            raise BookingError("slot_unavailable", "This time is no longer available. Please choose another time.")

        appointment_id, stamp = repo.new_id(), _stamp()
        try:
            conn.execute("INSERT INTO appointments (id, slot_id, patient_id, doctor_id, status, booked_at, updated_at) "
                         "VALUES (?, ?, ?, ?, 'confirmed', ?, ?)",
                         (appointment_id, slot_id, patient["id"], doctor["id"], stamp, stamp))
        except sqlite3.IntegrityError:        # second line of defence: one live appointment per slot
            raise BookingError("slot_taken", "This slot was just booked by someone else. Please choose another time.") from None

        # Booking again after the doctor became unavailable IS the reschedule: close the old appointment.
        # Its slot stays out of use - it is inside the time the doctor is away.
        old = conn.execute("SELECT id, slot_id FROM appointments WHERE patient_id = ? AND doctor_id = ? "
                           "AND status = 'needs_reschedule'", (patient["id"], doctor["id"])).fetchall()
        for o in old:
            conn.execute("UPDATE appointments SET status = 'cancelled', cancel_reason = 'rescheduled', updated_at = ? "
                         "WHERE id = ?", (stamp, o["id"]))
            conn.execute("UPDATE appointment_slots SET status = 'blocked' WHERE id = ?", (o["slot_id"],))

        actor = repo.Actor("patient", patient["id"], f"{patient['full_name']} (patient)")
        repo.audit(conn, actor, "APPOINTMENT_BOOKED", patient_id=patient["id"], resource_type="appointment",
                   resource_id=appointment_id, detail=f"{doctor['name']} · {slot['date']} {slot['start_time']}"
                   + (" · rescheduled" if old else ""))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return appointment(conn, appointment_id)


def appointment(conn, appointment_id: str) -> dict | None:
    row = conn.execute(
        "SELECT a.id, a.status, a.booked_at, a.patient_id, a.doctor_id, d.name AS doctor_name, "
        "s.id AS slot_id, s.date, s.start_time, s.end_time FROM appointments a "
        "JOIN appointment_slots s ON s.id = a.slot_id JOIN doctors d ON d.id = a.doctor_id WHERE a.id = ?",
        (appointment_id,)).fetchone()
    return dict(row) if row else None


def upcoming_for_patient(conn, patient_id: str, today: date) -> list[dict]:
    """Live appointments from today on (confirmed, or waiting to be rebooked), with the latest message sent."""
    rows = conn.execute(
        "SELECT a.id, a.status, a.booked_at, a.doctor_id, d.name AS doctor_name, s.date, s.start_time, s.end_time "
        "FROM appointments a JOIN appointment_slots s ON s.id = a.slot_id JOIN doctors d ON d.id = a.doctor_id "
        "WHERE a.patient_id = ? AND a.status IN ('confirmed', 'needs_reschedule') "
        "AND (s.date >= ? OR a.status = 'needs_reschedule') ORDER BY s.date, s.start_time",
        (patient_id, today.isoformat())).fetchall()
    out = []
    for r in rows:
        a = dict(r)
        if a["status"] == "needs_reschedule":
            n = conn.execute("SELECT message, sent_at FROM appointment_notices WHERE appointment_id = ? AND status = 'sent' "
                             "ORDER BY sent_at DESC LIMIT 1", (a["id"],)).fetchone()
            a["notice"] = dict(n) if n else None
        out.append(a)
    return out


# ------------------------------------------------------------------ doctor unavailable

def _outstanding_suggestions(conn) -> set[int]:
    return {r[0] for r in conn.execute(
        "SELECT n.suggested_slot_id FROM appointment_notices n JOIN appointments a ON a.id = n.appointment_id "
        "WHERE a.status = 'needs_reschedule' AND n.suggested_slot_id IS NOT NULL")}


def next_available(conn, doctor: dict, day: str, after_hm: str, today: date, now_hm: str,
                   exclude: set[int]) -> dict | None:
    """The doctor's first open slot after `after_hm` on `day`, else on the following days."""
    first = date.fromisoformat(day)
    for i in range(REBOOK_SEARCH_DAYS + 1):
        d = (first + timedelta(days=i)).isoformat()
        if d > (today + timedelta(days=BOOKING_DAYS)).isoformat():
            break
        ensure_day(conn, doctor, d)
        floor = after_hm if i == 0 else "00:00"
        if d == today.isoformat():
            floor = max(floor, now_hm)
        for s in conn.execute("SELECT id, date, start_time, end_time FROM appointment_slots WHERE doctor_id = ? AND date = ? "
                              "AND status = 'available' AND start_time >= ? ORDER BY start_time", (doctor["id"], d, floor)):
            if s["id"] not in exclude:
                return dict(s)
    return None


def notice_message(doctor: dict, appt_day: str, appt_start: str, suggestion: dict | None) -> str:
    text = (f"{doctor_title(doctor['name'])} is unavailable for your scheduled slot on "
            f"{fmt(date.fromisoformat(appt_day))} at {clock(appt_start)}.")
    if suggestion:
        return text + (f" The next available slot is {fmt(date.fromisoformat(suggestion['date']))}, "
                       f"{clock(suggestion['start_time'])}.")
    return text + " Please contact the clinic to choose a new time."


WARNING_CODES = {"outside_hours", "crosses_midnight", "long_block", "short_block"}


def _flip(minutes: int) -> int:
    """Same clock time, other half of the day: 2:00 AM <-> 2:00 PM."""
    return (minutes + 720) % 1440


def _hm_of(minutes: int) -> str:
    return _hm(minutes % 1440)


def _initials(name: str) -> str:
    words = [w for w in name.replace("Dr.", "").replace("Dr ", "").split() if w]
    return "".join(w[0] for w in words[:2]).upper() or "?"


def plan_block(conn, doctor: dict, block: dict, viewer_id: str, today: date, now_hm: str) -> dict:
    """Everything the confirmation pop-up shows - and exactly what confirming will do. The caller commits (slots of
    the days in the range are materialised so the impact is real).
    block: {start_date, start_time, end_date, end_time, full_day, reason} - clinic-local, times HH:MM (24 h)."""
    ws, we = doctor["working_start_time"], doctor["working_end_time"]
    who = doctor_title(doctor["name"])
    errors, warnings = [], []
    start_date = block["start_date"]
    end_date = block.get("end_date") or start_date
    full_day = bool(block.get("full_day"))
    start_hm, end_hm = (ws, we) if full_day else (block.get("start_time"), block.get("end_time"))
    base = {"doctor": {"id": doctor["id"], "name": doctor["name"], "initials": _initials(doctor["name"]),
                       "working_start_time": ws, "working_end_time": we},
            "full_day": full_day, "reason": (block.get("reason") or "").strip()[:200] or None, "timezone": "Asia/Kolkata"}
    try:
        start, end = local_dt(start_date, start_hm), local_dt(end_date, end_hm)
    except (TypeError, ValueError):
        return {**base, "errors": [{"code": "invalid", "message": "Please choose a date and a time (with AM or PM) for the start and the end."}],
                "warnings": []}
    minutes = int((end - start).total_seconds() // 60)
    base.update(start={"date": start.date().isoformat(), "time": start.strftime("%H:%M")},
                end={"date": end.date().isoformat(), "time": end.strftime("%H:%M")},
                starts_at=utc_text(start), ends_at=utc_text(end), duration_minutes=max(minutes, 0))

    s_min, e_min = start.hour * 60 + start.minute, end.hour * 60 + end.minute
    same_day = start.date() == end.date()
    if minutes <= 0:
        fix = None
        if same_day and _flip(e_min) > s_min:
            fix = {"end_time": _hm_of(_flip(e_min)), "label": f"Change end to {clock(_hm_of(_flip(e_min)))}"}
        errors.append({"code": "end_before_start", "fix": fix,
                       "message": "End time is before start time. Check AM/PM." if same_day
                       else "The block ends before it starts. Check the dates and AM/PM."})
        return {**base, "errors": errors, "warnings": []}
    now_local = local_dt(today.isoformat(), now_hm)
    if start < now_local:
        errors.append({"code": "in_past", "fix": None,
                       "message": f"The start, {clock(start.strftime('%H:%M'))} on {long_date(start.date())}, is already in the past."})
    if minutes > MAX_BLOCK_DAYS * 1440:
        errors.append({"code": "too_long", "fix": None, "message": f"A block can be at most {MAX_BLOCK_DAYS} days long."})
        return {**base, "errors": errors, "warnings": []}

    # --- warnings (the confirm button still works; they ask the user to look again)
    if not full_day:
        w_s, w_e = _mins(ws), _mins(we)
        end_clock = 1440 if (e_min == 0 and not same_day) else e_min
        outside = []
        if not w_s <= s_min < w_e:
            outside.append(("start", s_min))
        if not w_s < end_clock <= w_e:
            outside.append(("end", end_clock))
        if outside:
            flipped = {k: _flip(m) for k, m in outside}
            new_s, new_e = flipped.get("start", s_min), flipped.get("end", e_min)
            fits = all(w_s <= v <= w_e for v in flipped.values()) and (not same_day or new_e > new_s)
            times = " and ".join(clock(_hm_of(m)) for _, m in outside)
            verb = "is" if len(outside) == 1 else "are"
            message = f"{times} {verb} outside {who}'s working hours ({clock(ws)} – {clock(we)})."
            fix = None
            if fits:
                label = " – ".join(clock(_hm_of(v)) for v in flipped.values())
                message += f" Did you mean {label}?"
                fix = {**({"start_time": _hm_of(new_s)} if "start" in flipped else {}),
                       **({"end_time": _hm_of(new_e)} if "end" in flipped else {}), "label": f"Change to {label}"}
            warnings.append({"code": "outside_hours", "message": message, "fix": fix})
    if end.date() > start.date() and not (end.date() == start.date() + timedelta(days=1) and e_min == 0):
        warnings.append({"code": "crosses_midnight", "fix": None,
                         "message": f"This block continues past midnight into {long_date(start.date() + timedelta(days=1))}."})
    if same_day and not full_day and minutes > 720:
        warnings.append({"code": "long_block", "fix": None, "message": f"This block is {duration_text(minutes)} long. Is that correct?"})
    if minutes < 15:
        warnings.append({"code": "short_block", "fix": None, "message": f"This block is only {duration_text(minutes)} long."})

    # --- impact: the real slots of every day in the range
    segments = day_segments(start, end)
    for d, _, _ in segments:
        ensure_day(conn, doctor, d)
    affected, free_ids, days = [], [], []
    for d, seg_s, seg_e in segments:
        booked = conn.execute(
            "SELECT s.id, s.start_time, s.end_time, s.status, a.id AS appointment_id, a.status AS appointment_status, "
            f"p.full_name, {repo.visible_sql('a.patient_id')} AS visible "
            "FROM appointment_slots s LEFT JOIN appointments a ON a.slot_id = s.id AND a.status <> 'cancelled' "
            "LEFT JOIN patients p ON p.id = a.patient_id WHERE s.doctor_id = ? AND s.date = ? ORDER BY s.start_time",
            (viewer_id, viewer_id, doctor["id"], d)).fetchall()
        marks = []
        for r in booked:
            inside = r["start_time"] < seg_e and r["end_time"] > seg_s
            if r["appointment_id"]:
                marks.append({"minute": _mins(r["start_time"]), "affected": inside and r["appointment_status"] == "confirmed"})
            if inside and r["appointment_status"] == "confirmed":
                affected.append({"appointment_id": r["appointment_id"], "date": d, "start_time": r["start_time"],
                                 "end_time": r["end_time"], "patient_name": r["full_name"] if r["visible"] else None})
            elif inside and r["status"] == "available":
                free_ids.append(r["id"])
        days.append({"date": d, "working": [_mins(ws), _mins(we)],
                     "block": [_mins(seg_s), 1440 if seg_e == "24:00" else _mins(seg_e)], "appointments": marks})
    fingerprint = hashlib.sha256("|".join([base["starts_at"], base["ends_at"], ",".join(sorted(a["appointment_id"] for a in affected)),
                                           ",".join(str(i) for i in sorted(free_ids))]).encode()).hexdigest()[:24]
    if full_day:
        span = "Full day" if len(segments) == 1 else f"{len(segments)} full days"
        text = f"{span} ({clock(ws)} – {clock(we)}, the doctor's working hours)"
    else:
        text = duration_text(minutes)
    return {**base, "duration_text": text, "days": days[:7], "more_days": max(0, len(days) - 7),
            "affected": affected, "free_slots": len(free_ids), "highlight_affected": len(affected) >= 5,
            "warnings": warnings, "errors": errors, "fingerprint": fingerprint}


def mark_unavailable(conn, doctor: dict, user: dict, block: dict, fingerprint: str, checked: bool,
                     shown_warnings: list[str], changes: list[str], today: date, now_hm: str) -> dict:
    """Confirm a block. The plan is worked out AGAIN inside this transaction: if it no longer matches what the
    pop-up showed (someone booked meanwhile), nothing is saved and the fresh plan is returned instead.
    Blocks the open slots, flags booked ones for rebooking (never deletes them) and drafts one message per
    patient - nothing is sent here."""
    if not checked:
        raise BookingError("not_checked", "Please tick “I have checked the doctor, date and time (AM/PM)”.", 422)
    _begin(conn)
    try:
        plan = plan_block(conn, doctor, block, user["id"], today, now_hm)
        if plan["errors"]:
            raise BookingError(plan["errors"][0]["code"], plan["errors"][0]["message"], 422)
        if plan["fingerprint"] != fingerprint:
            raise BookingError("schedule_changed", "The schedule changed - please review again.", 409, {"preview": plan})
        start, end = from_utc(plan["starts_at"]), from_utc(plan["ends_at"])
        shown = sorted({w for w in shown_warnings if w in WARNING_CODES} | {w["code"] for w in plan["warnings"]})
        changes = [c.strip()[:80] for c in changes[:5] if c and c.strip()]
        record = {"shown_and_accepted": True, "warnings_shown": shown, "changes_in_popup": changes,
                  "affected": len(plan["affected"]), "free_slots": plan["free_slots"]}
        cur = conn.execute("INSERT INTO doctor_unavailability (doctor_id, starts_at, ends_at, reason, created_by, created_at, "
                           "confirmation) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (doctor["id"], plan["starts_at"], plan["ends_at"], plan["reason"], user["id"], now_iso(),
                            json.dumps(record)))
        unavailability_id = cur.lastrowid
        blocked = 0
        for d, seg_s, seg_e in day_segments(start, end):       # a slot is affected if any part of it is inside
            blocked += conn.execute("UPDATE appointment_slots SET status = 'blocked' WHERE doctor_id = ? AND date = ? "
                                    "AND start_time < ? AND end_time > ? AND status = 'available'",
                                    (doctor["id"], d, seg_e, seg_s)).rowcount
        exclude = _outstanding_suggestions(conn)
        stamp = _stamp()
        for a in plan["affected"]:
            row = conn.execute("SELECT patient_id FROM appointments WHERE id = ?", (a["appointment_id"],)).fetchone()
            conn.execute("UPDATE appointments SET status = 'needs_reschedule', updated_at = ? WHERE id = ?", (stamp, a["appointment_id"]))
            suggestion = next_available(conn, doctor, end.date().isoformat(), end.strftime("%H:%M"), today, now_hm, exclude)
            if suggestion:
                exclude.add(suggestion["id"])        # two patients are never offered the same slot
            conn.execute("INSERT INTO appointment_notices (appointment_id, patient_id, unavailability_id, suggested_slot_id, "
                         "message, status, created_at) VALUES (?, ?, ?, ?, ?, 'draft', ?)",
                         (a["appointment_id"], row["patient_id"], unavailability_id, suggestion and suggestion["id"],
                          notice_message(doctor, a["date"], a["start_time"], suggestion), now_iso()))
            repo.audit(conn, repo.clinician_actor(user), "APPOINTMENT_NEEDS_RESCHEDULE", patient_id=row["patient_id"],
                       resource_type="appointment", resource_id=a["appointment_id"], detail=f"{doctor['name']} unavailable")
        when = (f"{start:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M} Asia/Kolkata "
                f"({plan['starts_at']} → {plan['ends_at']} UTC)")
        repo.audit(conn, repo.clinician_actor(user), "DOCTOR_UNAVAILABLE", resource_type="doctor", resource_id=doctor["id"],
                   detail=(f"Confirmation shown and accepted. Warnings shown: {', '.join(shown) or 'none'}. "
                           f"Changed in the pop-up: {'; '.join(changes) or 'nothing'}."),
                   new_value=f"{doctor['name']} · {when} · {blocked} slot(s) blocked, {len(plan['affected'])} to rebook")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"unavailability_id": unavailability_id, "blocked": blocked, "needs_reschedule": len(plan["affected"]),
            "starts_at": plan["starts_at"], "ends_at": plan["ends_at"], "start": plan["start"], "end": plan["end"]}


# ------------------------------------------------------------------ rebooking messages

def notices(conn, doctor_id: str, viewer_id: str) -> list[dict]:
    """Messages for appointments of this doctor that still need rebooking. Patient names only for patients
    the viewer may see; `can_send` likewise."""
    rows = conn.execute(
        "SELECT n.id, n.status, n.message, n.sent_at, n.created_at, a.id AS appointment_id, a.patient_id, "
        "s.date, s.start_time, s.end_time, p.full_name, p.patient_code, "
        f"{repo.visible_sql('a.patient_id')} AS visible "
        "FROM appointment_notices n JOIN appointments a ON a.id = n.appointment_id "
        "JOIN appointment_slots s ON s.id = a.slot_id JOIN patients p ON p.id = a.patient_id "
        "WHERE a.doctor_id = ? AND a.status = 'needs_reschedule' ORDER BY s.date, s.start_time, n.id",
        (viewer_id, viewer_id, doctor_id)).fetchall()
    out = []
    for r in rows:
        n = dict(r)
        visible = bool(n.pop("visible"))
        if not visible:
            n.update(full_name=None, patient_code=None, message=None, patient_id=None)
        n["can_send"] = visible and n["status"] == "draft"
        out.append(n)
    return out


def send_notice(conn, notice_id: int, user: dict, today: date, now_hm: str) -> dict:
    """Deliver one drafted message to the patient app (and simulated e-mail). If the suggested slot has
    been taken since the draft was made, a fresh one is found first so the message is still true."""
    _begin(conn)
    try:
        n = conn.execute("SELECT n.*, a.status AS appt_status, a.doctor_id, s.date, s.start_time FROM appointment_notices n "
                         "JOIN appointments a ON a.id = n.appointment_id JOIN appointment_slots s ON s.id = a.slot_id "
                         "WHERE n.id = ?", (notice_id,)).fetchone()
        if n is None or not repo.has_access(conn, user["id"], n["patient_id"]):
            raise BookingError("not_found", "Message not found.", 404)
        if n["status"] == "sent":
            raise BookingError("already_sent", "This message was already sent.")
        if n["appt_status"] != "needs_reschedule":
            raise BookingError("not_needed", "The patient has already booked a new time.")
        message, suggested = n["message"], n["suggested_slot_id"]
        still_open = suggested and conn.execute("SELECT 1 FROM appointment_slots WHERE id = ? AND status = 'available'",
                                                (suggested,)).fetchone()
        if not still_open:
            doctor = get_doctor(conn, n["doctor_id"])
            ends = from_utc(conn.execute("SELECT ends_at FROM doctor_unavailability WHERE id = ?",
                                         (n["unavailability_id"],)).fetchone()[0])
            exclude = _outstanding_suggestions(conn) - {suggested}
            s = next_available(conn, doctor, ends.date().isoformat(), ends.strftime("%H:%M"), today, now_hm, exclude)
            message, suggested = notice_message(doctor, n["date"], n["start_time"], s), s and s["id"]
        sent_at = now_iso()
        conn.execute("UPDATE appointment_notices SET status = 'sent', sent_at = ?, sent_by = ?, message = ?, "
                     "suggested_slot_id = ? WHERE id = ?", (sent_at, user["id"], message, suggested, notice_id))
        patient = repo.get_patient(conn, n["patient_id"])
        email = patient.get("email")
        conn.execute("INSERT INTO patient_notifications (patient_id, kind, appointment_id, message, email_to, email_status, "
                     "created_at) VALUES (?, 'appointment_reschedule', ?, ?, ?, ?, ?)",
                     (n["patient_id"], n["appointment_id"], message, email, "simulated" if email else "no_email_on_file", sent_at))
        repo.audit(conn, repo.clinician_actor(user), "RESCHEDULE_NOTICE_SENT", patient_id=n["patient_id"],
                   resource_type="appointment", resource_id=n["appointment_id"])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    try:                      # the in-app message is the delivery; the (simulated) e-mail is best effort
        repo.send_email(email, "Your appointment needs a new time", message)
    except Exception:         # noqa: BLE001
        import logging
        logging.getLogger("uc2.email").exception("Appointment e-mail failed for notice %s", notice_id)
    return {"id": notice_id, "status": "sent", "message": message, "sent_at": sent_at}


# ------------------------------------------------------------------ care-team day view

def schedule(conn, doctor: dict, day: str, viewer_id: str, today: date) -> dict:
    """Every slot of the doctor's day with its state. A booked slot shows the patient's name only to
    clinicians on that patient's care team."""
    try:
        d = date.fromisoformat(day)
    except ValueError:
        raise BookingError("invalid", "Please choose a valid date.", 422) from None
    if today <= d <= today + timedelta(days=BOOKING_DAYS):
        ensure_day(conn, doctor, day)
        conn.commit()
    slots = conn.execute(
        "SELECT s.id, s.start_time, s.end_time, s.status, a.id AS appointment_id, a.status AS appointment_status, "
        "a.booked_at, a.patient_id, p.full_name, p.patient_code, "
        f"{repo.visible_sql('a.patient_id')} AS visible "
        "FROM appointment_slots s LEFT JOIN appointments a ON a.slot_id = s.id AND a.status <> 'cancelled' "
        "LEFT JOIN patients p ON p.id = a.patient_id WHERE s.doctor_id = ? AND s.date = ? ORDER BY s.start_time",
        (viewer_id, viewer_id, doctor["id"], day)).fetchall()
    out = []
    for r in slots:
        s = dict(r)
        if not s.pop("visible"):
            s.update(full_name=None, patient_code=None, patient_id=None)
        out.append(s)
    day_start = local_dt(day, "00:00")
    away = []
    for r in conn.execute(
            "SELECT u.id, u.starts_at, u.ends_at, u.reason, u.created_at, c.full_name AS created_by_name "
            "FROM doctor_unavailability u JOIN clinical_users c ON c.id = u.created_by WHERE u.doctor_id = ? "
            "AND u.starts_at < ? AND u.ends_at > ? ORDER BY u.starts_at",
            (doctor["id"], utc_text(day_start + timedelta(days=1)), utc_text(day_start))):
        a, b = from_utc(r["starts_at"]), from_utc(r["ends_at"])
        away.append({**dict(r), "start": {"date": a.date().isoformat(), "time": a.strftime("%H:%M")},
                     "end": {"date": b.date().isoformat(), "time": b.strftime("%H:%M")}})
    return {"doctor": doctor, "date": day, "slots": out, "unavailable": away}
