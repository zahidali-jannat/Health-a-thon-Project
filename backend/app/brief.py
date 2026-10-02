"""Consultation Brief: a short summary of a patient, prepared by the clinical team and sent to the doctor before a visit.

Deterministic only: templates and the rules in backend/config/brief_rules.toml - no generated text, no guessed values.
- Only clinician-verified data gives the numbers. Anything unverified appears only as a count and a link.
- It states facts and the rule that fired. No diagnosis, dose advice or recommendation. Medicines are "as recorded".
- A sent brief is an immutable, versioned snapshot (database trigger). Later data raises "updated since sent".
Content schema "1.0" (stable - the doctor dashboard will reuse it): see build_raw / render.
"""

import hashlib
import json
import sqlite3
import tomllib
from datetime import date, datetime, timedelta, timezone

from . import appointments as appt
from . import orders, repo
from .db import now_iso
from .detection import SOURCE_LABELS, assess_patient, emergency_visits, fmt, last_clinic_visit
from .settings import BACKEND_DIR

RULES_PATH = BACKEND_DIR / "config" / "brief_rules.toml"
VERIFIED_SOURCES = {"hospital_internal", "external_hospital_abdm", "care_team_manual", "patient_upload_reviewed"}
LIVE = ("sent", "in_consultation", "opened", "acknowledged")           # sent and not finished
LEVEL_LABELS = {"high_priority": "High priority", "quietly_worse": "Worsening since last visit",
                "watch": "1 warning sign", "none": "No warning signs"}
LEVEL_RANK = {"high_priority": 0, "quietly_worse": 1, "watch": 2, "none": 3}


class BriefError(Exception):
    def __init__(self, code: str, message: str, status: int = 422, payload: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.payload = code, message, status, payload


_rules_cache: dict = {}


def rules() -> dict:
    """backend/config/brief_rules.toml, re-read when the file changes."""
    mtime = RULES_PATH.stat().st_mtime
    if _rules_cache.get("mtime") != mtime:
        _rules_cache.update(mtime=mtime, data=tomllib.loads(RULES_PATH.read_text()))
    return _rules_cache["data"]


def _rank(rule: str) -> int:
    order = rules()["ranking"]["order"]
    return order.index(rule) if rule in order else len(order)


def _verified(e) -> bool:
    if e.source in VERIFIED_SOURCES:
        return True
    # a reading the patient sent counts once the care team confirmed it; a bill-backed refill once the bill is approved
    return e.source == "patient_upload" and e.confidence == "high" and e.status not in ("pending_review", "rejected")


def _num(v: float) -> str:
    return f"{v:g}"


def _begin(conn) -> None:
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")


# ------------------------------------------------------------------ stubs for features that do not exist yet

def patient_targets(conn, patient_id: str) -> dict:
    """TODO(targets): per-patient targets (e.g. an individual HbA1c goal) are not recorded anywhere yet.
    Until they are, no target exists and the "above target" rule never fires."""
    return {}


def conflict_cards(conn, patient_id: str) -> list[dict]:
    """TODO(conflicts): a contradictions / conflict-card feature does not exist yet. Returns nothing until it does."""
    return []


# ------------------------------------------------------------------ building the brief (fixed number of queries)

def _appointment(conn, appointment_id: str) -> dict | None:
    row = conn.execute(
        "SELECT a.id, a.patient_id, a.doctor_id, a.status, s.date, s.start_time, s.end_time, d.name AS doctor_name, "
        "d.clinical_user_id AS doctor_user_id FROM appointments a JOIN appointment_slots s ON s.id = a.slot_id "
        "JOIN doctors d ON d.id = a.doctor_id WHERE a.id = ?", (appointment_id,)).fetchone()
    return dict(row) if row else None


def _unverified(conn, patient_id: str) -> int:
    """Everything still waiting for the care team: counted, never shown as values."""
    return conn.execute(
        "SELECT (SELECT COUNT(*) FROM external_reports WHERE patient_id = :p AND status = 'pending_review') "
        "+ (SELECT COUNT(*) FROM patient_documents WHERE patient_id = :p AND source = 'patient_upload' AND review_status = 'pending') "
        "+ (SELECT COUNT(*) FROM clinical_events WHERE patient_id = :p AND source = 'patient_upload' AND confidence = 'medium' "
        "   AND event_type != 'refill' AND document_id IS NULL AND linked_document_id IS NULL) "
        "+ (SELECT COUNT(*) FROM lab_result_ingestions WHERE patient_id = :p AND status IN ('needs_review', 'unlinked'))",
        {"p": patient_id}).fetchone()[0]


def _baseline(conn, record, patient_id: str, today: date) -> dict:
    """What "since last visit" compares against: the snapshot saved at the last completed consultation if there is
    one, otherwise the last attended clinic visit."""
    snap = conn.execute("SELECT visit_date, numbers_json FROM consultation_snapshots WHERE patient_id = ? "
                        "ORDER BY visit_date DESC, id DESC LIMIT 1", (patient_id,)).fetchone()
    visit = last_clinic_visit(record, today)
    if snap and (visit is None or snap["visit_date"] >= visit.isoformat()):
        return {"date": date.fromisoformat(snap["visit_date"]), "basis": "snapshot", "numbers": json.loads(snap["numbers_json"])}
    if visit:
        return {"date": visit, "basis": "last_visit", "numbers": None}
    return {"date": None, "basis": "none", "numbers": None}


def _series(record, names: list[str], today: date) -> list:
    wanted = {n.lower() for n in names}
    return sorted((e for e in record.events if e.event_type in ("lab", "vital", "engagement_log") and e.value is not None
                   and (e.name or "").lower() in wanted and e.effective_date <= today and _verified(e)),
                  key=lambda e: (e.effective_date, str(e.id or '')))


def _point(e) -> dict:
    return {"value": e.value, "unit": e.unit, "date": e.effective_date.isoformat(), "lab": e.asserted_by,
            "source": SOURCE_LABELS.get(e.source, e.source)}


def build_raw(conn, appointment: dict, today: date) -> dict:
    """Every candidate line, ranked and keyed, before the team's hide / pin edits and the per-section limits."""
    cfg = rules()
    pid = appointment["patient_id"]
    patient = repo.get_patient(conn, pid)
    record = repo.load_patient_record(conn, pid)
    base = _baseline(conn, record, pid, today)
    since = base["date"]
    assessment = assess_patient(record, today)
    link = lambda tab: f"/care-team/patients/{pid}?tab={tab}"     # noqa: E731

    # -- A. identity strip
    fired = sorted((s for s in assessment.signals if s.fired), key=lambda s: s.tier != "hard")
    priority = {"level": assessment.level, "label": LEVEL_LABELS[assessment.level],
                "reason": (fired[0].short[0].upper() + fired[0].short[1:]) if fired else "No warning signs on the record",
                "rule": fired[0].key if fired else "none"}
    identity = {"patient_id": pid, "name": patient["full_name"], "patient_code": patient["patient_code"],
                "age": repo.age(patient["date_of_birth"], today, patient.get("reported_age"), patient.get("reported_age_on")),
                "sex": {"M": "Male", "F": "Female", "O": "Other"}.get(patient["sex"]),
                "appointment": {"id": appointment["id"], "date": appointment["date"], "time": appointment["start_time"],
                                "doctor": appointment["doctor_name"], "status": appointment["status"]},
                "visit_type": cfg["visit_type"], "priority": priority}

    # -- C. key numbers (and the value changes for B)
    rows, missing, b_items, stable = [], [], [], []
    for k in cfg["key_numbers"]:
        series = _series(record, k["names"], today)
        if not series:
            missing.append(k["label"])
            continue
        latest, prev = series[-1], (series[-2] if len(series) > 1 else None)
        trend = series[-cfg["limits"]["trend_points"]:]
        labs = {e.asserted_by for e in ([latest, prev] if prev else [latest])}
        change = None
        if prev:
            delta = round(latest.value - prev.value, 3)
            change = {"value": delta, "direction": "rising" if delta > 0 else "falling" if delta < 0 else "no change"}
        rows.append({"key": k["key"], "label": k["label"], "unit": latest.unit or k["unit"], "latest": _point(latest),
                     "previous": _point(prev) if prev else None, "change": change,
                     "trend": [{"date": e.effective_date.isoformat(), "value": e.value} for e in trend],
                     "mixed_labs": len(labs) > 1, "labs": sorted(labs)})
        # since last visit: latest value vs the value at the baseline
        if since is None:
            continue
        if base["numbers"] is not None:
            snap = base["numbers"].get(k["key"])
            before = snap and {"value": snap["value"], "date": snap["date"]}
        else:
            earlier = [e for e in series if e.effective_date <= since]
            before = earlier and {"value": earlier[-1].value, "date": earlier[-1].effective_date.isoformat()}
        if not before or latest.effective_date.isoformat() <= before["date"]:
            continue
        delta = round(latest.value - before["value"], 3)
        meaningful = abs(delta) >= k["change_size"]
        worse = (k["worse"] == "either" and delta != 0) or (k["worse"] == "up" and delta > 0) or (k["worse"] == "down" and delta < 0)
        if not meaningful:
            stable.append({"key": f"value:{k['key']}", "label": k["label"]})
            continue
        rule = "worsening_value" if worse else "improving_value"
        unit = latest.unit or k["unit"]
        b_items.append({"key": f"value:{k['key']}", "rule": rule, "date": latest.effective_date.isoformat(),
                        "direction": "rising" if delta > 0 else "falling",
                        "text": f"{k['label']} {'rose' if delta > 0 else 'fell'} {_num(abs(delta))} {unit} "
                                f"({_num(before['value'])} → {_num(latest.value)} {unit})",
                        "rule_text": f"{rule}: change of {_num(k['change_size'])} {unit} or more since the last visit",
                        "link": link("labs")})

    # -- emergency visits and low glucose since the last visit (or in the last 90 days for a new patient)
    window = since or (today - timedelta(days=90))
    att = []
    for v in emergency_visits(record, today):
        if v.effective_date > window and _verified(v):
            item = {"key": f"emergency:{v.effective_date}:{v.id}", "rule": "emergency_visit", "date": v.effective_date.isoformat(),
                    "text": f"Emergency visit on {fmt(v.effective_date)}" + (f" at {v.facility}" if v.facility else "")
                            + (f" - {v.note[:90]}" if v.note else ""),
                    "rule_text": "emergency_visit: an emergency visit after the last clinic visit", "link": link("summary")}
            b_items.append(item)
            att.append({**item, "severity": "Attention", "label": item["text"]})
    low = cfg["glucose"]["low_mg_dl"]
    glucose_names = {n.lower() for n in cfg["glucose"]["names"]}
    for e in record.events:
        if not (e.effective_date > window and e.effective_date <= today and _verified(e)):
            continue
        if (e.name or "").lower() in glucose_names and e.value is not None and e.value < low:
            item = {"key": f"low_glucose:{e.effective_date}:{e.id}", "rule": "low_glucose", "date": e.effective_date.isoformat(),
                    "text": f"Glucose {_num(e.value)} {e.unit or 'mg/dL'} on {fmt(e.effective_date)} ({e.name})",
                    "rule_text": f"low_glucose: a verified glucose value below {_num(low)} mg/dL", "link": link("labs")}
            b_items.append(item)
            att.append({**item, "severity": "Attention", "label": f"Low glucose: {_num(e.value)} {e.unit or 'mg/dL'} on {fmt(e.effective_date)}"})

    # -- D. medicines, as recorded (from verified refill / bill records - no dose or schedule is recorded)
    meds_cfg = cfg["medicines"]
    refills = sorted((e for e in record.events if e.event_type == "refill" and e.effective_date <= today and _verified(e)),
                     key=lambda e: (e.effective_date, str(e.id or '')))
    by_name: dict[str, list] = {}
    for e in refills:
        by_name.setdefault(e.name or "Medicine (name not recorded)", []).append(e)
    medicines = []
    for name, recs in by_name.items():
        last, first = recs[-1], recs[0]
        stopped = last.status == "stopped"
        changed = None
        if since and stopped and last.effective_date > since:
            changed = "Stopped since last visit"
        elif since and first.effective_date > since:
            changed = "New since last visit"
        medicines.append({"key": f"med:{name}", "name": name, "dose": "Dose and schedule not recorded",
                          "status": "Stopped (as recorded)" if stopped else "As recorded", "source": SOURCE_LABELS.get(last.source, last.source),
                          "recorded_by": last.asserted_by, "date": last.effective_date.isoformat(), "changed": changed})
        if changed:
            b_items.append({"key": f"med:{name}", "rule": "medicine_change", "date": last.effective_date.isoformat(),
                            "text": f"{name}: {changed.lower()} ({fmt(last.effective_date)}, as recorded)",
                            "rule_text": "medicine_change: a medicine first recorded or stopped after the last visit",
                            "link": link("sources")})
    medicines.sort(key=lambda m: m["date"], reverse=True)                  # most recently recorded first
    medicines.sort(key=lambda m: m["status"] != "As recorded")              # ... and stopped ones last
    last_confirmed = max((e.effective_date for e in refills), default=None)
    outdated = last_confirmed is None or (today - last_confirmed).days > meds_cfg["outdated_after_days"]
    if medicines and outdated:
        att.append({"key": "medicines_outdated", "rule": "medicines_outdated", "severity": "Info", "date": last_confirmed.isoformat(),
                    "label": f"Medicines list possibly outdated - last confirmed {fmt(last_confirmed)}",
                    "rule_text": f"medicines_outdated: no medicine record in the last {meds_cfg['outdated_after_days']} days",
                    "link": link("sources")})

    # -- E. tests ordered at (or since) the last visit
    nxt = orders.next_appointment(conn, pid, today)
    test_rows = [orders._view(r, today, nxt) for r in conn.execute(orders._ITEM_SELECT + "WHERE i.patient_id = ? ORDER BY i.due_by", (pid,))]
    if since:
        test_rows = [t for t in test_rows if t["ordered_at"][:10] >= since.isoformat() or t["section"] != "completed"]
    tests = []
    for t in test_rows:
        state = ("done" if t["status"] in orders.DONE else "awaiting verification" if t["section"] == "awaiting_verification"
                 else "not done" if t["status"] in orders.STOPPED else "overdue" if t["overdue"] else "open")
        tests.append({"key": f"test:{t['id']}", "name": t["name"], "due_by": t["due_by"], "state": state,
                      "result": t["result"] and {"value": t["result"]["value"], "unit": t["result"]["unit"], "date": t["result"]["test_date"]}})
    overdue = [t for t in tests if t["state"] == "overdue"]
    if overdue:
        item = {"key": "overdue_tests", "rule": "overdue_tests", "date": min(t["due_by"] for t in overdue),
                "text": f"{len(overdue)} test{'s' if len(overdue) > 1 else ''} overdue: "
                        + ", ".join(f"{t['name']} (due {fmt(date.fromisoformat(t['due_by']))})" for t in overdue),
                "rule_text": "overdue_tests: an ordered test is past its due date without a result", "link": link("tests")}
        b_items.append(item)
        att.append({**item, "severity": "Attention", "label": item["text"]})

    # -- appointment issues: missed clinic visits, and booked appointments cancelled or needing a new time
    lookback = today - timedelta(days=cfg["appointments"]["lookback_days"])
    missed = [v.effective_date for v in record.events if v.event_type == "visit" and v.status == "ordered"
              and "emergency" not in (v.name or "").lower() and lookback <= v.effective_date < today]
    for r in conn.execute("SELECT s.date, a.status FROM appointments a JOIN appointment_slots s ON s.id = a.slot_id "
                          "WHERE a.patient_id = ? AND a.status IN ('cancelled', 'needs_reschedule') AND s.date >= ? AND a.id != ?",
                          (pid, lookback.isoformat(), appointment["id"])):
        missed.append(date.fromisoformat(r["date"]))
    for d in sorted(set(missed)):
        if since is None or d > since:
            b_items.append({"key": f"appt:{d}", "rule": "appointment_issue", "date": d.isoformat(),
                            "text": f"Appointment on {fmt(d)} missed or cancelled",
                            "rule_text": "appointment_issue: a booked visit that did not happen", "link": link("summary")})
    if len(set(missed)) >= cfg["appointments"]["repeated_missed_count"]:
        att.append({"key": "repeated_missed", "rule": "appointment_issue", "severity": "Attention", "date": max(missed).isoformat(),
                    "label": f"{len(set(missed))} missed or cancelled appointments in the last {cfg['appointments']['lookback_days']} days",
                    "rule_text": f"appointment_issue: {cfg['appointments']['repeated_missed_count']} or more within "
                                 f"{cfg['appointments']['lookback_days']} days", "link": link("summary")})

    # -- verification queue, targets, conflicts
    waiting = _unverified(conn, pid)
    if waiting:
        att.append({"key": "awaiting_verification", "rule": "awaiting_verification", "severity": "Info", "date": today.isoformat(),
                    "label": f"{waiting} item{'s' if waiting > 1 else ''} awaiting verification (unverified - not used in this brief)",
                    "rule_text": "awaiting_verification: uploads or results not yet checked by the care team",
                    "link": "/care-team/reports"})
    for key, target in patient_targets(conn, pid).items():     # TODO(targets): never fires until targets exist
        row = next((r for r in rows if r["key"] == key), None)
        if row and row["latest"]["value"] > target:
            att.append({"key": f"above_target:{key}", "rule": "above_target", "severity": "Attention", "date": row["latest"]["date"],
                        "label": f"{row['label']} {_num(row['latest']['value'])} {row['unit']} is above the patient's target "
                                 f"of {_num(target)} {row['unit']}", "rule_text": "above_target: latest value above the individual target",
                        "link": link("labs")})
    for c in conflict_cards(conn, pid):                          # TODO(conflicts)
        att.append(c)

    return {"schema_version": cfg["schema_version"], "identity": identity,
            "since": {"date": since.isoformat() if since else None, "basis": base["basis"]},
            "candidates": b_items, "stable": stable,
            "key_numbers": {"rows": rows, "not_on_file": missing,
                            "notice": ("Values compared here come from different labs or sources - see the lab names."
                                       if any(r["mixed_labs"] for r in rows) else None)},
            "medicines": {"items": medicines, "last_confirmed_on": last_confirmed.isoformat() if last_confirmed else None,
                          "possibly_outdated": bool(medicines) and outdated},
            "tests": tests, "attention": att, "awaiting_verification": waiting}


def render(raw: dict, hidden: set, pinned: set, note: dict | None, cutoff: str) -> dict:
    """The brief as the doctor sees it: team edits applied, ranked, cut to the per-section limits."""
    lim = rules()["limits"]

    def ranked(items):
        items = [i for i in items if i["key"] not in hidden]
        items.sort(key=lambda i: i["date"] or "", reverse=True)                       # ties: most recent first
        items.sort(key=lambda i: (i["key"] not in pinned, _rank(i["rule"])))
        return [{**i, "pinned": i["key"] in pinned} for i in items]

    b = ranked(raw["candidates"])
    f = ranked(raw["attention"])
    stable = [s for s in raw["stable"] if s["key"] not in hidden]
    meds = [m for m in raw["medicines"]["items"] if m["key"] not in hidden]
    meds.sort(key=lambda m: m["key"] not in pinned)
    return {
        "schema_version": raw["schema_version"], "data_cutoff_at": cutoff, "identity": raw["identity"],
        "since_last_visit": {"since": raw["since"]["date"], "basis": raw["since"]["basis"],
                             "items": b[:lim["since_last_visit_max"]], "more": b[lim["since_last_visit_max"]:],
                             "stable_line": ("No meaningful change: " + ", ".join(s["label"] for s in stable)) if stable else None},
        "key_numbers": raw["key_numbers"],
        "medicines": {**raw["medicines"], "items": meds[:lim["medicines_max"]], "more": meds[lim["medicines_max"]:]},
        "tests": raw["tests"],
        "attention": {"items": f[:lim["attention_max"]], "more": f[lim["attention_max"]:]},
        "team_note": note,
        "footer": {"data_cutoff_at": cutoff, "verified_only": True, "awaiting_verification": raw["awaiting_verification"],
                   "link": "/care-team/reports"},
    }


# ------------------------------------------------------------------ edits

def _edits(conn, brief_id: str) -> tuple[set, set]:
    hidden, pinned = set(), set()
    for e in conn.execute("SELECT item_key, action FROM brief_edits WHERE brief_id = ? ORDER BY id", (brief_id,)):
        if e["action"] == "hide":
            hidden.add(e["item_key"])
        elif e["action"] == "unhide":
            hidden.discard(e["item_key"])
        elif e["action"] == "pin":
            pinned.add(e["item_key"])
        elif e["action"] == "unpin":
            pinned.discard(e["item_key"])
    return hidden, pinned


def _row(conn, brief_id: str) -> dict | None:
    r = conn.execute("SELECT b.*, u.full_name AS note_by_name FROM consultation_briefs b "
                     "LEFT JOIN clinical_users u ON u.id = b.team_note_by WHERE b.id = ?", (brief_id,)).fetchone()
    return dict(r) if r else None


def _compose(conn, brief: dict, appointment: dict, today: date) -> tuple[dict, dict, str]:
    raw = build_raw(conn, appointment, today)
    hidden, pinned = _edits(conn, brief["id"])
    cutoff = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    note = {"text": brief["team_note"], "by": brief.get("note_by_name"), "at": brief["team_note_at"]} if brief.get("team_note") else None
    return raw, render(raw, hidden, pinned, note, cutoff), cutoff


def candidates(raw: dict, hidden: set, pinned: set) -> list[dict]:
    """For the composer: every line that can be hidden or pinned, with its current state."""
    out = []
    for section, items in (("since_last_visit", raw["candidates"]), ("attention", raw["attention"]),
                           ("medicines", raw["medicines"]["items"])):
        for i in items:
            out.append({"section": section, "key": i["key"], "text": i.get("text") or i.get("label") or i.get("name"),
                        "rule": i.get("rule"), "hidden": i["key"] in hidden, "pinned": i["key"] in pinned})
    for s in raw["stable"]:
        out.append({"section": "stable", "key": s["key"], "text": f"No meaningful change: {s['label']}", "rule": "stable",
                    "hidden": s["key"] in hidden, "pinned": False})
    return out


# ------------------------------------------------------------------ clinical team: draft, edit, send

def draft(conn, appointment_id: str, user: dict, today: date) -> dict:
    """Generate (or regenerate) the draft for an appointment. Idempotent: one draft per appointment; regenerating
    refreshes it in place. After a send, the next draft is the next version and keeps the team's edits and note."""
    appointment = _appointment(conn, appointment_id)
    if appointment is None or not repo.has_access(conn, user["id"], appointment["patient_id"]):
        raise BriefError("not_found", "Appointment not found.", 404)
    _begin(conn)
    try:
        existing = conn.execute("SELECT id FROM consultation_briefs WHERE appointment_id = ? AND status = 'draft'",
                                (appointment_id,)).fetchone()
        latest = conn.execute("SELECT * FROM consultation_briefs WHERE appointment_id = ? ORDER BY version DESC LIMIT 1",
                              (appointment_id,)).fetchone()
        if latest and latest["status"] == "completed":
            raise BriefError("completed", "This consultation is already completed.", 409)
        now = now_iso()
        if existing:
            brief = _row(conn, existing["id"])
            _, content, cutoff = _compose(conn, brief, appointment, today)
            conn.execute("UPDATE consultation_briefs SET content_json = ?, data_cutoff_at = ?, row_version = row_version + 1, "
                         "updated_at = ? WHERE id = ?", (json.dumps(content), cutoff, now, brief["id"]))
            brief_id = brief["id"]
        else:
            brief_id = repo.new_id()
            version = (latest["version"] + 1) if latest else 1
            conn.execute("INSERT INTO consultation_briefs (id, patient_id, appointment_id, doctor_id, version, status, content_json, "
                         "schema_version, data_cutoff_at, composed_by, team_note, team_note_by, team_note_at, created_at, updated_at) "
                         "VALUES (?, ?, ?, ?, ?, 'draft', '{}', ?, ?, ?, ?, ?, ?, ?, ?)",
                         (brief_id, appointment["patient_id"], appointment_id, appointment["doctor_id"], version,
                          rules()["schema_version"], now, user["id"], latest and latest["team_note"],
                          latest and latest["team_note_by"], latest and latest["team_note_at"], now, now))
            if latest:          # carry the team's hide / pin choices into the new version
                hidden, pinned = _edits(conn, latest["id"])
                for key in hidden:
                    conn.execute("INSERT INTO brief_edits (brief_id, item_key, action, edited_by, edited_at) VALUES (?, ?, 'hide', ?, ?)",
                                 (brief_id, key, user["id"], now))
                for key in pinned:
                    conn.execute("INSERT INTO brief_edits (brief_id, item_key, action, edited_by, edited_at) VALUES (?, ?, 'pin', ?, ?)",
                                 (brief_id, key, user["id"], now))
            brief = _row(conn, brief_id)
            _, content, cutoff = _compose(conn, brief, appointment, today)
            conn.execute("UPDATE consultation_briefs SET content_json = ?, data_cutoff_at = ? WHERE id = ?",
                         (json.dumps(content), cutoff, brief_id))
        repo.audit(conn, repo.clinician_actor(user), "BRIEF_DRAFTED", patient_id=appointment["patient_id"],
                   resource_type="consultation_brief", resource_id=brief_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return clinic_view(conn, brief_id, user, today)


def brief_for_clinic(conn, brief_id: str, user: dict) -> dict:
    brief = _row(conn, brief_id)
    if brief is None or not repo.has_access(conn, user["id"], brief["patient_id"]):
        raise BriefError("not_found", "Brief not found.", 404)
    return brief


def clinic_view(conn, brief_id: str, user: dict, today: date) -> dict:
    """The composer's view: the brief as the doctor will see it, plus every line that can be hidden or pinned, and
    completeness warnings."""
    brief = brief_for_clinic(conn, brief_id, user)
    content = json.loads(brief["content_json"])
    appointment = _appointment(conn, brief["appointment_id"])
    raw = build_raw(conn, appointment, today) if brief["status"] == "draft" else None
    hidden, pinned = _edits(conn, brief_id)
    warnings = []
    waiting = content.get("footer", {}).get("awaiting_verification", 0)
    if waiting:
        warnings.append(f"{waiting} item{'s' if waiting > 1 else ''} awaiting verification - not included as values")
    if not _vitals_on(conn, brief["patient_id"], appointment["date"]):
        warnings.append("No vitals recorded today")
    if content.get("medicines", {}).get("possibly_outdated"):
        warnings.append("Medicines list possibly outdated")
    if not content.get("medicines", {}).get("items"):
        warnings.append("No medicines on record")
    if appointment["status"] != "confirmed":
        warnings.append("The appointment needs a new time or was cancelled - the brief cannot be sent")
    if appointment["date"] != today.isoformat():
        warnings.append("The appointment is not today")
    history = [dict(r) for r in conn.execute("SELECT id, version, status, sent_at, opened_at, acknowledged_at FROM consultation_briefs "
                                              "WHERE appointment_id = ? ORDER BY version DESC", (brief["appointment_id"],))]
    return {"brief": _public(brief), "content": content, "candidates": candidates(raw, hidden, pinned) if raw else [],
            "warnings": warnings, "appointment": appointment, "versions": history}


def _public(brief: dict) -> dict:
    keep = ("id", "patient_id", "appointment_id", "doctor_id", "version", "status", "schema_version", "data_cutoff_at",
            "row_version", "sent_at", "called_at", "identity_confirmed_at", "opened_at", "acknowledged_at", "completed_at",
            "superseded_by", "created_at", "updated_at", "team_note")
    return {k: brief.get(k) for k in keep}


def edit(conn, brief_id: str, user: dict, row_version: int, changes: dict, today: date) -> dict:
    """Hide / pin lines and set the team note on a DRAFT (optimistic lock on row_version). Every edit is recorded."""
    brief = brief_for_clinic(conn, brief_id, user)
    _begin(conn)
    try:
        brief = _row(conn, brief_id)
        if brief["status"] != "draft":
            raise BriefError("not_draft", "A sent brief cannot be edited. Prepare a new version instead.", 409)
        if brief["row_version"] != row_version:
            raise BriefError("stale", "Someone else changed this brief a moment ago. Please reload.", 409)
        now = now_iso()
        for action in ("hide", "unhide", "pin", "unpin"):
            for key in changes.get(action) or []:
                conn.execute("INSERT INTO brief_edits (brief_id, item_key, action, edited_by, edited_at) VALUES (?, ?, ?, ?, ?)",
                             (brief_id, key[:200], action, user["id"], now))
        if "note" in changes:
            text = (changes["note"] or "").strip()
            if len(text) > rules()["limits"]["team_note_max"]:
                raise BriefError("note_too_long", f"The note can be at most {rules()['limits']['team_note_max']} characters.")
            conn.execute("UPDATE consultation_briefs SET team_note = ?, team_note_by = ?, team_note_at = ? WHERE id = ?",
                         (text or None, user["id"] if text else None, now if text else None, brief_id))
            conn.execute("INSERT INTO brief_edits (brief_id, item_key, action, edited_by, edited_at) VALUES (?, 'team_note', 'note', ?, ?)",
                         (brief_id, user["id"], now))
        brief = _row(conn, brief_id)
        _, content, cutoff = _compose(conn, brief, _appointment(conn, brief["appointment_id"]), today)
        conn.execute("UPDATE consultation_briefs SET content_json = ?, data_cutoff_at = ?, row_version = row_version + 1, "
                     "updated_at = ? WHERE id = ?", (json.dumps(content), cutoff, now, brief_id))
        repo.audit(conn, repo.clinician_actor(user), "BRIEF_EDITED", patient_id=brief["patient_id"],
                   resource_type="consultation_brief", resource_id=brief_id,
                   detail=", ".join(f"{a}: {len(changes.get(a) or [])}" for a in ("hide", "unhide", "pin", "unpin") if changes.get(a))
                          + ("; note changed" if "note" in changes else ""))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return clinic_view(conn, brief_id, user, today)


def send(conn, brief_id: str, user: dict, today: date, allow_not_today: bool = False) -> dict:
    """Freeze the draft as the version the doctor receives. Idempotent: sending a brief that was already sent returns it
    unchanged. Refuses cancelled / needs-a-new-time appointments, and appointments that are not today (unless allowed)."""
    brief_for_clinic(conn, brief_id, user)
    _begin(conn)
    try:
        brief = _row(conn, brief_id)
        if brief["status"] != "draft":
            conn.commit()
            if brief["status"] in ("superseded", "cancelled"):
                raise BriefError("not_current", "This version is no longer current.", 409)
            return {"id": brief_id, "status": brief["status"], "version": brief["version"], "replayed": True}
        appointment = _appointment(conn, brief["appointment_id"])
        if appointment["status"] != "confirmed":
            raise BriefError("appointment_not_confirmed", "This appointment was cancelled or needs a new time, so the brief "
                                                          "cannot be sent.", 409)
        if appointment["date"] != today.isoformat() and not allow_not_today:
            raise BriefError("not_today", "The appointment is not today. Tick “send early” to send it anyway.", 409)
        _, content, cutoff = _compose(conn, brief, appointment, today)
        now = now_iso()
        previous = conn.execute(f"SELECT id FROM consultation_briefs WHERE appointment_id = ? AND status IN ({','.join('?' * len(LIVE))})",
                                (brief["appointment_id"], *LIVE)).fetchone()
        if previous:
            conn.execute("UPDATE consultation_briefs SET status = 'superseded', superseded_by = ?, updated_at = ? WHERE id = ?",
                         (brief_id, now, previous["id"]))
        conn.execute("UPDATE consultation_briefs SET content_json = ?, data_cutoff_at = ?, status = 'sent', sent_at = ?, sent_by = ?, "
                     "row_version = row_version + 1, updated_at = ? WHERE id = ?",
                     (json.dumps(content), cutoff, now, user["id"], now, brief_id))
        repo.audit(conn, repo.clinician_actor(user), "BRIEF_SENT", patient_id=brief["patient_id"], resource_type="consultation_brief",
                   resource_id=brief_id, detail=f"version {brief['version']}" + (f", supersedes {previous['id']}" if previous else ""))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"id": brief_id, "status": "sent", "version": brief["version"], "replayed": False}


def _vitals_on(conn, patient_id: str, day: str) -> bool:
    return conn.execute("SELECT 1 FROM clinical_events WHERE patient_id = ? AND event_type = 'vital' AND effective_date = ? LIMIT 1",
                        (patient_id, day)).fetchone() is not None


def day_list(conn, user: dict, day: date) -> list[dict]:
    """The clinical team's list for a day: every appointment of their own patients, with a readiness checklist."""
    ids = repo.patient_ids_for_clinician(conn, user["id"])
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = [dict(r) for r in conn.execute(
        "SELECT a.id AS appointment_id, a.status AS appointment_status, a.patient_id, s.date, s.start_time, s.end_time, "
        "d.id AS doctor_id, d.name AS doctor_name, p.full_name, p.patient_code FROM appointments a "
        "JOIN appointment_slots s ON s.id = a.slot_id JOIN doctors d ON d.id = a.doctor_id JOIN patients p ON p.id = a.patient_id "
        f"WHERE s.date = ? AND a.status IN ('confirmed', 'needs_reschedule') AND a.patient_id IN ({marks}) ORDER BY s.start_time",
        (day.isoformat(), *ids))]
    if not rows:
        return []
    appt_ids = [r["appointment_id"] for r in rows]
    am = ",".join("?" * len(appt_ids))
    latest = {}
    for b in conn.execute(f"SELECT appointment_id, id, version, status, sent_at, opened_at, acknowledged_at FROM consultation_briefs "
                          f"WHERE appointment_id IN ({am}) ORDER BY version", appt_ids):
        latest[b["appointment_id"]] = dict(b)                    # highest version wins
    vitals = {r[0] for r in conn.execute(f"SELECT DISTINCT patient_id FROM clinical_events WHERE event_type = 'vital' "
                                         f"AND effective_date = ? AND patient_id IN ({marks})", (day.isoformat(), *ids))}
    meds = {r[0]: r[1] for r in conn.execute(f"SELECT patient_id, MAX(effective_date) FROM clinical_events WHERE event_type = 'refill' "
                                              f"AND (source != 'patient_upload' OR confidence = 'high') AND patient_id IN ({marks}) "
                                              f"GROUP BY patient_id", ids)}
    out = []
    for r in rows:
        last_med = meds.get(r["patient_id"])
        out.append({**r, "brief": latest.get(r["appointment_id"]), "vitals_today": r["patient_id"] in vitals,
                    "awaiting_verification": _unverified(conn, r["patient_id"]),
                    "medicines_last_confirmed": last_med,
                    "medicines_age_days": (day - date.fromisoformat(last_med)).days if last_med else None,
                    "medicines_outdated": last_med is None or (day - date.fromisoformat(last_med)).days > rules()["medicines"]["outdated_after_days"]})
    return out


def send_ready(conn, user: dict, today: date, ids: list[str] | None) -> dict:
    """Bulk send. Without ids: the preview (drafts for today's confirmed appointments). With ids: send those."""
    ready = [r for r in day_list(conn, user, today)
             if r["brief"] and r["brief"]["status"] == "draft" and r["appointment_status"] == "confirmed"]
    if ids is None:
        return {"ready": [{"brief_id": r["brief"]["id"], "patient": r["full_name"], "patient_code": r["patient_code"],
                           "doctor": r["doctor_name"], "time": r["start_time"]} for r in ready]}
    allowed = {r["brief"]["id"] for r in ready}
    sent, skipped = [], []
    for bid in ids:
        if bid not in allowed:
            skipped.append({"id": bid, "reason": "not ready"})
            continue
        try:
            sent.append(send(conn, bid, user, today))
        except BriefError as e:
            skipped.append({"id": bid, "reason": e.message})
    return {"sent": sent, "skipped": skipped}


# ------------------------------------------------------------------ doctor

def _doctor_brief(conn, brief_id: str, user: dict) -> dict:
    doctor = appt.doctor_for_user(conn, user["id"])
    brief = _row(conn, brief_id)
    if brief is None or doctor is None or brief["doctor_id"] != doctor["id"] or brief["status"] in ("draft", "cancelled"):
        raise BriefError("not_found", "Brief not found.", 404)
    if brief["status"] == "superseded":
        raise BriefError("superseded", "A newer version of this brief was sent.", 409, {"superseded_by": brief["superseded_by"]})
    return brief


def queue(conn, user: dict, day: date) -> list[dict]:
    """The doctor's queue: only their own sent briefs for the day. Name, age, short ID, time, priority - nothing clinical."""
    doctor = appt.doctor_for_user(conn, user["id"])
    if doctor is None:
        return []
    out = []
    for r in conn.execute(
            f"SELECT b.id, b.status, b.version, b.content_json, s.start_time, s.date, p.full_name, p.patient_code "
            f"FROM consultation_briefs b JOIN appointments a ON a.id = b.appointment_id JOIN appointment_slots s ON s.id = a.slot_id "
            f"JOIN patients p ON p.id = b.patient_id WHERE b.doctor_id = ? AND s.date = ? "
            f"AND b.status IN ({','.join('?' * (len(LIVE) + 1))})", (doctor["id"], day.isoformat(), *LIVE, "completed")):
        ident = json.loads(r["content_json"])["identity"]
        out.append({"brief_id": r["id"], "status": r["status"], "version": r["version"], "time": r["start_time"], "date": r["date"],
                    "name": r["full_name"], "patient_code": r["patient_code"], "age": ident["age"], "sex": ident["sex"],
                    "priority": ident["priority"]["label"], "priority_level": ident["priority"]["level"]})
    out.sort(key=lambda q: (q["status"] == "completed", q["time"], LEVEL_RANK.get(q["priority_level"], 9)))
    return out


def call(conn, brief_id: str, user: dict) -> dict:
    brief = _doctor_brief(conn, brief_id, user)
    if brief["status"] == "sent":
        conn.execute("UPDATE consultation_briefs SET status = 'in_consultation', called_at = ?, updated_at = ? WHERE id = ?",
                     (now_iso(), now_iso(), brief_id))
    repo.audit(conn, repo.clinician_actor(user), "BRIEF_PATIENT_CALLED", patient_id=brief["patient_id"],
               resource_type="consultation_brief", resource_id=brief_id)
    conn.commit()
    p = repo.get_patient(conn, brief["patient_id"])
    return {"brief_id": brief_id, "name": p["full_name"], "patient_code": p["patient_code"],
            "identity_confirmed": brief["identity_confirmed_by"] == user["id"]}


def confirm_identity(conn, brief_id: str, user: dict, patient_code: str, date_of_birth: date | None, age: int | None,
                     today: date) -> dict:
    """Two identifiers: the patient ID plus the date of birth (or the age, when no date of birth is on file)."""
    brief = _doctor_brief(conn, brief_id, user)
    p = repo.get_patient(conn, brief["patient_id"])
    if date_of_birth is None and age is None:
        raise BriefError("two_identifiers", "Enter the patient ID and the date of birth (or age).")
    real_age = repo.age(p["date_of_birth"], today, p.get("reported_age"), p.get("reported_age_on"))
    ok = (patient_code or "").strip().upper() == p["patient_code"].upper() and (
        (date_of_birth is not None and p["date_of_birth"] == date_of_birth.isoformat())
        or (date_of_birth is None and age is not None and real_age is not None and age == real_age))
    actor = repo.clinician_actor(user)
    if not ok:
        repo.audit(conn, actor, "BRIEF_IDENTITY_FAILED", patient_id=brief["patient_id"], resource_type="consultation_brief",
                   resource_id=brief_id, detail="identifiers did not match")
        conn.commit()
        raise BriefError("identity_mismatch", "The identifiers do not match this patient. Check with the patient and try again.")
    now = now_iso()
    conn.execute("UPDATE consultation_briefs SET identity_confirmed_at = ?, identity_confirmed_by = ?, "
                 "status = CASE WHEN status IN ('sent', 'in_consultation') THEN 'opened' ELSE status END, "
                 "opened_at = COALESCE(opened_at, ?), called_at = COALESCE(called_at, ?), updated_at = ? WHERE id = ?",
                 (now, user["id"], now, now, now, brief_id))
    repo.audit(conn, actor, "BRIEF_IDENTITY_CONFIRMED", patient_id=brief["patient_id"], resource_type="consultation_brief",
               resource_id=brief_id, detail="patient ID + " + ("date of birth" if date_of_birth else "age"))
    conn.commit()
    return {"ok": True}


def for_doctor(conn, brief_id: str, user: dict) -> dict:
    """The full brief - only after this doctor confirmed the patient's identity."""
    brief = _doctor_brief(conn, brief_id, user)
    if brief["identity_confirmed_by"] != user["id"]:
        raise BriefError("identity_required", "Confirm the patient's identity first.", 403)
    repo.audit(conn, repo.clinician_actor(user), "BRIEF_OPENED", patient_id=brief["patient_id"], resource_type="consultation_brief",
               resource_id=brief_id)
    conn.commit()
    return {"brief": _public(brief), "content": json.loads(brief["content_json"]), "sent_by": _sender(conn, brief["sent_by"])}


def _sender(conn, user_id: str | None) -> dict:
    """Who a brief came from, as the doctor sees it: the clinic team's name, plus the team member who sent it."""
    row = conn.execute("SELECT full_name, role FROM clinical_users WHERE id = ?", (user_id,)).fetchone() if user_id else None
    team = repo.clinic_name(conn)
    name = row["full_name"] if row else None
    return {"team": team, "name": None if name is None or name.strip().lower() == team.strip().lower() else name,
            "is_team": bool(row) and row["role"] == "care_team"}


INBOX_DAYS = 7


def inbox(conn, user: dict, today: date) -> dict:
    """The doctor's messages from the clinic team: every brief sent to this doctor for an appointment from
    INBOX_DAYS ago onwards, newest first. "New" until the doctor confirms the patient and opens it."""
    doctor = appt.doctor_for_user(conn, user["id"])
    conn.commit()
    if doctor is None:
        return {"team": repo.clinic_name(conn), "messages": []}
    rows = conn.execute(
        "SELECT b.id, b.status, b.version, b.sent_at, b.sent_by, b.opened_at, b.team_note, s.date, s.start_time, "
        "p.full_name, p.patient_code FROM consultation_briefs b JOIN appointments a ON a.id = b.appointment_id "
        "JOIN appointment_slots s ON s.id = a.slot_id JOIN patients p ON p.id = b.patient_id "
        f"WHERE b.doctor_id = ? AND s.date >= ? AND b.status IN ({','.join('?' * (len(LIVE) + 1))}) "
        "ORDER BY b.sent_at DESC, b.id LIMIT 100",
        (doctor["id"], (today - timedelta(days=INBOX_DAYS)).isoformat(), *LIVE, "completed")).fetchall()
    senders: dict = {}
    out = []
    for r in rows:
        if r["sent_by"] not in senders:
            senders[r["sent_by"]] = _sender(conn, r["sent_by"])
        out.append({"brief_id": r["id"], "status": r["status"], "version": r["version"], "sent_at": r["sent_at"],
                    "sent_by": senders[r["sent_by"]], "patient": r["full_name"], "patient_code": r["patient_code"],
                    "date": r["date"], "time": r["start_time"], "is_today": r["date"] == today.isoformat(),
                    "new": r["status"] in ("sent", "in_consultation"), "has_note": bool(r["team_note"])})
    return {"team": repo.clinic_name(conn), "messages": out}


def acknowledge(conn, brief_id: str, user: dict) -> dict:
    brief = _doctor_brief(conn, brief_id, user)
    if brief["identity_confirmed_by"] != user["id"]:
        raise BriefError("identity_required", "Confirm the patient's identity first.", 403)
    if brief["status"] == "opened":
        conn.execute("UPDATE consultation_briefs SET status = 'acknowledged', acknowledged_at = ?, updated_at = ? WHERE id = ?",
                     (now_iso(), now_iso(), brief_id))
    repo.audit(conn, repo.clinician_actor(user), "BRIEF_ACKNOWLEDGED", patient_id=brief["patient_id"],
               resource_type="consultation_brief", resource_id=brief_id)
    conn.commit()
    return {"ok": True, "status": "acknowledged"}


def complete(conn, brief_id: str, user: dict, today: date) -> dict:
    """The consultation is over: save the key numbers so the next brief compares against them."""
    brief = _doctor_brief(conn, brief_id, user)
    if brief["identity_confirmed_by"] != user["id"]:
        raise BriefError("identity_required", "Confirm the patient's identity first.", 403)
    if brief["status"] == "completed":
        return {"ok": True, "status": "completed"}
    _begin(conn)
    try:
        record = repo.load_patient_record(conn, brief["patient_id"])
        numbers = {}
        for k in rules()["key_numbers"]:
            s = _series(record, k["names"], today)
            if s:
                numbers[k["key"]] = {"value": s[-1].value, "unit": s[-1].unit, "date": s[-1].effective_date.isoformat()}
        appointment = _appointment(conn, brief["appointment_id"])
        conn.execute("INSERT INTO consultation_snapshots (patient_id, brief_id, appointment_id, visit_date, numbers_json, taken_at) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (brief["patient_id"], brief_id, brief["appointment_id"], appointment["date"],
                                                   json.dumps(numbers), now_iso()))
        conn.execute("UPDATE consultation_briefs SET status = 'completed', completed_at = ?, updated_at = ? WHERE id = ?",
                     (now_iso(), now_iso(), brief_id))
        repo.audit(conn, repo.clinician_actor(user), "BRIEF_COMPLETED", patient_id=brief["patient_id"],
                   resource_type="consultation_brief", resource_id=brief_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"ok": True, "status": "completed"}


def updates(conn, brief_id: str, user: dict, today: date) -> dict:
    """What changed in the VERIFIED data after the brief's cut-off: the sent brief compared with the brief as it would be
    built now. The sent version itself never changes."""
    brief = _doctor_brief(conn, brief_id, user)
    if brief["identity_confirmed_by"] != user["id"]:
        raise BriefError("identity_required", "Confirm the patient's identity first.", 403)
    sent = json.loads(brief["content_json"])
    raw = build_raw(conn, _appointment(conn, brief["appointment_id"]), today)
    hidden, pinned = _edits(conn, brief_id)
    now = render(raw, hidden, pinned, sent.get("team_note"), datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"))
    changes = []
    old_rows = {r["key"]: r for r in sent["key_numbers"]["rows"]}
    for r in now["key_numbers"]["rows"]:
        o = old_rows.get(r["key"])
        if o is None or (o["latest"]["date"], o["latest"]["value"]) != (r["latest"]["date"], r["latest"]["value"]):
            changes.append(f"New verified {r['label']}: {_num(r['latest']['value'])} {r['unit']} "
                           f"({fmt(date.fromisoformat(r['latest']['date']))})")
    old_att = {i["key"] for i in sent["attention"]["items"] + sent["attention"]["more"]}
    for i in now["attention"]["items"] + now["attention"]["more"]:
        if i["key"] not in old_att and i["rule"] != "awaiting_verification":
            changes.append(f"New attention item: {i['label']}")
    old_tests = {t["key"]: t["state"] for t in sent["tests"]}
    for t in now["tests"]:
        if old_tests.get(t["key"]) not in (None, t["state"]):
            changes.append(f"{t['name']}: now {t['state']}")
    if now["footer"]["awaiting_verification"] != sent["footer"]["awaiting_verification"]:
        changes.append(f"Items awaiting verification: {now['footer']['awaiting_verification']}")
    return {"changed": bool(changes), "changes": changes, "data_cutoff_at": sent["data_cutoff_at"],
            "live": now if changes else None}


def etag(payload) -> str:
    return 'W/"' + hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:32] + '"'
