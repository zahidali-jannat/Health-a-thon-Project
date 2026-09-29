"""Silent Risk Detector: plain rules, no AI, no hidden weights.

Five SOFT signals (need 2+ together) and two HARD signals (any one alone = HIGH PRIORITY).
Every signal returns a SignalResult that is truthy when it fires and always carries a
plain-language explanation plus the dated facts it was based on. The rules never look at
`source`, so data from any ingestion path is treated the same way.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from .config import DEFAULT_CONFIG, DetectionConfig


@dataclass
class Event:
    event_type: str
    effective_date: date
    source: str
    confidence: str
    status: str
    asserted_by: str = ""
    name: str | None = None
    value: float | None = None
    unit: str | None = None
    note: str | None = None
    id: int | None = None
    value_text: str | None = None
    ref_kind: str | None = None
    ref_low: float | None = None
    ref_high: float | None = None
    ref_text: str | None = None
    crit_low: float | None = None
    crit_high: float | None = None
    facility: str | None = None
    clinician: str | None = None
    report_id: str | None = None       # lab results: the report they came from
    document_id: str | None = None     # the uploaded file behind this fact, if any
    provenance: dict | None = None     # External Report Review: who uploaded / who reviewed, and the document


@dataclass
class PatientRecord:
    id: str
    patient_code: str
    full_name: str
    events: list[Event]
    phone: str | None = None
    abha_number: str | None = None
    date_of_birth: str | None = None
    sex: str | None = None
    reported_age: int | None = None          # asked at sign-in when there is no date of birth
    reported_age_on: str | None = None

    def of_type(self, event_type: str, as_of: date) -> list[Event]:
        return sorted(
            (e for e in self.events if e.event_type == event_type and e.effective_date <= as_of),
            key=lambda e: e.effective_date,
        )


@dataclass
class SignalResult:
    key: str
    label: str
    tier: str                                    # "soft" or "hard"
    fired: bool
    short: str                                   # fragment used in "Flagged because: ..."
    detail: str                                  # full sentence for the expanded view
    evidence: list[str] = field(default_factory=list)
    facts: list[dict] = field(default_factory=list)   # labelled rows for the UI: {label, value, tone}

    def __bool__(self) -> bool:
        return self.fired


@dataclass
class RiskAssessment:
    patient_id: int
    as_of: date
    level: str                                   # "high_priority" | "quietly_worse" | "watch" | "none"
    flagged: bool
    hard_count: int
    soft_count: int
    summary: str
    signals: list[SignalResult]


SOURCE_LABELS = {
    "hospital_internal": "hospital record",
    "patient_upload": "patient upload",
    "external_hospital_abdm": "other hospital via ABHA",
    "care_team_manual": "entered by care team",
    "patient_upload_reviewed": "outside lab report, reviewed by care team",
}


def fmt(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _is_emergency(visit: Event) -> bool:
    return "emergency" in (visit.name or "").lower()


def _where(e: Event) -> str:
    return f"{e.asserted_by} · {SOURCE_LABELS.get(e.source, e.source)}"


def _source_sub(e: Event) -> str | None:
    """Where a fact came from, unless the "recorded by" name already says it (a care-team entry reads "Dr X (care team)")."""
    return None if e.source == "care_team_manual" else SOURCE_LABELS.get(e.source, e.source)


def _fact(label: str, value: str, tone: str | None = None, sub: str | None = None) -> dict:
    """One labelled row for the UI. tone: "bad" (the problem), "ok" (reassuring) or None; sub: small second line."""
    return {"label": label, "value": value, "tone": tone, "sub": sub}


# =============================================================== SOFT signals

def missed_refill(patient: PatientRecord, as_of: date, config: DetectionConfig = DEFAULT_CONFIG) -> SignalResult:
    """Fires if any active medicine's refill is `refill_late_days` or more late.

    Refill rows: active = collected, ordered = due/prescribed but not yet collected,
    stopped = discontinued by the doctor (ignored). "Late" means the next refill is overdue
    now, or the most recent refill was itself collected late.
    """
    key, label, tier = "missed_refill", "Medicine refill late", "soft"
    limit = config.refill_late_days
    refills = patient.of_type("refill", as_of)
    # A medicine bill the care team hasn't checked yet: wait for it - never read it as a missed refill.
    awaiting = [r for r in refills if r.status == "pending_review"]
    if awaiting:
        first = awaiting[0].effective_date
        return SignalResult(key, label, tier, False, "refill bill awaiting review",
                            f"A medicine bill uploaded on {fmt(first)} is waiting for the care team to verify it, "
                            "so this check waits too.",
                            facts=[_fact("Medicine bill", "Awaiting confirmation", sub=f"Uploaded {fmt(first)}"),
                                   _fact("Flag when", f"{limit}+ days late")])
    by_medicine: dict[str, list[Event]] = {}
    for r in refills:
        if r.status == "rejected":          # a rejected bill is not a refill: the clock keeps running as before
            continue
        by_medicine.setdefault(r.name or "Diabetes medicine", []).append(r)
    by_medicine = {m: rows for m, rows in by_medicine.items() if rows[-1].status != "stopped"}
    if not by_medicine:
        return SignalResult(key, label, tier, False, "no refill records",
                            "No active medicine refill records on file, so this check could not run.",
                            facts=[_fact("Refill records", "None on file")])

    evidence: list[str] = []
    facts: list[dict] = []
    worst: tuple[int, str, str] | None = None     # (days late, short, detail)

    def consider(days: int, short: str, detail: str):
        nonlocal worst
        if days > 0 and (worst is None or days > worst[0]):
            worst = (days, short, detail)

    def late_tone(days: int) -> str:
        return "bad" if days >= limit else "ok"

    for med, rows in by_medicine.items():
        collected = [r for r in rows if r.status == "active"]
        last = collected[-1] if collected else None
        pending = [r for r in rows if r.status == "ordered" and (last is None or r.effective_date > last.effective_date)]
        facts.append(_fact("Medicine", med))

        if pending:
            due, who = pending[0].effective_date, pending[0].asserted_by
            overdue = (as_of - due).days
            evidence.append(f"{med}: refill due {fmt(due)} ({who}) — not collected.")
            facts += [_fact("Refill due", fmt(due), sub=who),
                      _fact("Collected", "No", "bad" if overdue >= limit else None)]
            if overdue > 0:
                facts.append(_fact("Days late", _plural(overdue, "day"), late_tone(overdue)))
            consider(overdue, f"{med} refill {overdue} days late",
                     f"{med} refill was due on {fmt(due)} (recorded by {who}) and has not been collected — "
                     f"{overdue} days late.")
        elif last:
            supply = int(last.value or config.default_days_supply)
            runs_out = last.effective_date + timedelta(days=supply)
            overdue = (as_of - runs_out).days
            evidence.append(f"{med}: last refill {fmt(last.effective_date)} ({supply}-day supply), "
                            f"due again {fmt(runs_out)}.")
            facts.append(_fact("Last refill", fmt(last.effective_date), sub=f"{supply}-day supply"))
            if overdue > 0:
                evidence.append(f"{med}: no refill since — {_plural(overdue, 'day')} overdue as of {fmt(as_of)}.")
                facts += [_fact("Supply ran out", fmt(runs_out)),
                          _fact("Days late", _plural(overdue, "day"), late_tone(overdue))]
            else:
                facts.append(_fact("Next refill due", fmt(runs_out), "ok"))
            consider(overdue, f"{med} refill {overdue} days late",
                     f"{med} refill is {overdue} days overdue: the {supply}-day supply from "
                     f"{fmt(last.effective_date)} ran out on {fmt(runs_out)} and no refill has been recorded.")

        if len(collected) >= 2:
            prev = collected[-2]
            expected = prev.effective_date + timedelta(days=int(prev.value or config.default_days_supply))
            late_by = (last.effective_date - expected).days
            if late_by > 0:
                evidence.append(f"{med}: last refill collected {_plural(late_by, 'day')} late "
                                f"(expected {fmt(expected)}, collected {fmt(last.effective_date)}).")
                facts.append(_fact("Last refill collected", f"{_plural(late_by, 'day')} late", late_tone(late_by),
                                   sub=f"Expected {fmt(expected)}"))
            consider(late_by, f"{med} refill collected {late_by} days late",
                     f"{med} was last refilled on {fmt(last.effective_date)}, {late_by} days after the "
                     f"previous supply ran out on {fmt(expected)}.")

    facts.append(_fact("Flag when", f"{limit}+ days late"))
    if worst and worst[0] >= limit:
        return SignalResult(key, label, tier, True, worst[1],
                            f"{worst[2]} Limit is {limit} days.", evidence, facts)
    detail = (f"Refills on track: at most {_plural(worst[0], 'day')} late (limit {limit})."
              if worst else "Refills are on time.")
    return SignalResult(key, label, tier, False, "refills on time", detail, evidence, facts)


def engagement_dropped(patient: PatientRecord, as_of: date, config: DetectionConfig = DEFAULT_CONFIG) -> SignalResult:
    """Fires if sugar logs / check-ins in the last N days fell by at least X% vs the N days before."""
    key, label, tier = "engagement_dropped", "Fewer sugar logs / check-ins", "soft"
    window = config.engagement_window_days
    recent_start = as_of - timedelta(days=window)
    prior_start = recent_start - timedelta(days=window)
    logs = patient.of_type("engagement_log", as_of)

    recent = sum(1 for e in logs if recent_start < e.effective_date <= as_of)
    prior = sum(1 for e in logs if prior_start < e.effective_date <= recent_start)
    prior_span = f"{fmt(prior_start + timedelta(days=1))} – {fmt(recent_start)}"
    recent_span = f"{fmt(recent_start + timedelta(days=1))} – {fmt(as_of)}"
    evidence = [f"{prior_span}: {_plural(prior, 'log')}.", f"{recent_span}: {_plural(recent, 'log')}."]
    facts = [_fact(f"Earlier {window} days", _plural(prior, "log"), sub=prior_span),
             _fact(f"Last {window} days", _plural(recent, "log"), sub=recent_span)]

    if prior < config.engagement_min_baseline:
        facts.append(_fact("Comparison", f"Not possible — needs {config.engagement_min_baseline}+ earlier logs"))
        return SignalResult(key, label, tier, False, "not enough earlier logs",
                            f"Only {_plural(prior, 'log')} in the earlier {window} days — too few to compare "
                            f"fairly (need {config.engagement_min_baseline}).", evidence, facts)

    drop_pct = (prior - recent) / prior * 100
    fired = drop_pct >= config.engagement_drop_pct
    change = f"Down {drop_pct:.0f}%" if drop_pct > 0 else f"Up {-drop_pct:.0f}%" if drop_pct < 0 else "No change"
    facts += [_fact("Change", change, "bad" if fired else "ok"),
              _fact("Flag when", f"Down {config.engagement_drop_pct:.0f}% or more")]
    if fired:
        return SignalResult(key, label, tier, True, f"sugar logs down {drop_pct:.0f}% ({recent} vs {prior})",
                            f"Sugar logs / check-ins fell {drop_pct:.0f}%: {recent} in the last {window} days "
                            f"vs {prior} in the {window} days before. Limit is a {config.engagement_drop_pct:.0f}% drop.",
                            evidence, facts)
    trend = f"down {drop_pct:.0f}%" if drop_pct > 0 else "steady or higher"
    return SignalResult(key, label, tier, False, f"logging {trend}",
                        f"Logging is {trend}: {recent} in the last {window} days vs {prior} before "
                        f"(flag needs a {config.engagement_drop_pct:.0f}% drop).", evidence, facts)


def hba1c_plateaued(patient: PatientRecord, as_of: date, config: DetectionConfig = DEFAULT_CONFIG) -> SignalResult:
    """Fires if the last 2-3 HbA1c results are flat or worse (and, if a target is set, still above it)."""
    key, label, tier = "hba1c_plateaued", "HbA1c not improving", "soft"
    labs = [e for e in patient.of_type("lab", as_of)
            if (e.name or "").lower() == "hba1c" and e.status == "resulted" and e.value is not None]
    if len(labs) < 2:
        return SignalResult(key, label, tier, False, "not enough HbA1c results",
                            f"Only {_plural(len(labs), 'HbA1c result')} on file — need at least 2 to see a trend.",
                            [f"{fmt(e.effective_date)}: {e.value:.1f}%" for e in labs],
                            [_fact(fmt(e.effective_date), f"{e.value:.1f}%", sub=e.asserted_by) for e in labs]
                            + [_fact("Trend", "Needs at least 2 results")])

    window = labs[-config.hba1c_readings_to_compare:]
    first, last = window[0], window[-1]
    change = last.value - first.value
    trail = " → ".join(f"{e.value:.1f}%" for e in window)
    span = f"{fmt(first.effective_date)} to {fmt(last.effective_date)}"
    evidence = [f"{fmt(e.effective_date)}: {e.value:.1f}% ({_where(e)})" for e in window]
    facts = [_fact(fmt(e.effective_date), f"{e.value:.1f}%", sub=e.asserted_by) for e in window]
    change_text = f"{change:+.1f} points"
    at_goal = config.hba1c_target is not None and last.value < config.hba1c_target
    fired = not at_goal and change > -config.hba1c_min_improvement

    facts.append(_fact("Change", change_text, "bad" if fired else "ok" if change < 0 else None,
                       sub=f"Over the last {len(window)} results"))
    if config.hba1c_target is not None:
        facts.append(_fact("Target", f"Below {config.hba1c_target:.1f}% — {'met' if at_goal else 'not met'}",
                           "ok" if at_goal else None))
    facts.append(_fact("Flag when", f"Drop smaller than {config.hba1c_min_improvement:.1f} points"))

    if at_goal:
        return SignalResult(key, label, tier, False, "HbA1c at goal",
                            f"Latest HbA1c {last.value:.1f}% is below the {config.hba1c_target:.1f}% target ({trail}).",
                            evidence, facts)
    if fired:
        direction = "worse" if change > 0 else "flat"
        return SignalResult(key, label, tier, True, f"HbA1c {direction} across last {len(window)} results ({trail})",
                            f"HbA1c is {direction} across the last {len(window)} results ({trail}, {span}). "
                            f"Improvement needed: at least {config.hba1c_min_improvement:.1f} points.", evidence, facts)
    return SignalResult(key, label, tier, False, f"HbA1c improving ({trail})",
                        f"HbA1c is improving: {trail} ({span}).", evidence, facts)


def missed_appointment(patient: PatientRecord, as_of: date, config: DetectionConfig = DEFAULT_CONFIG) -> SignalResult:
    """Fires if the most recent scheduled clinic visit is still 'ordered' (booked, never attended)."""
    key, label, tier = "missed_appointment", "Missed last appointment", "soft"
    cutoff = as_of - timedelta(days=config.missed_visit_grace_days)
    past = [v for v in patient.of_type("visit", cutoff)
            if not _is_emergency(v) and v.status in ("ordered", "resulted")]
    if not past:
        return SignalResult(key, label, tier, False, "no past appointments", "No past scheduled visits on file.",
                            facts=[_fact("Past appointments", "None on file")])

    last = past[-1]
    days = (as_of - last.effective_date).days
    if last.status == "ordered":
        return SignalResult(key, label, tier, True, f"missed visit on {fmt(last.effective_date)}",
                            f"Missed the visit booked for {fmt(last.effective_date)} ({days} days ago) — "
                            f"no attendance recorded.", [f"{fmt(last.effective_date)}: booked, not attended ({_where(last)})."],
                            [_fact("Booked for", fmt(last.effective_date), sub=f"{_plural(days, 'day')} ago"),
                             _fact("Attended", "No", "bad"),
                             _fact("Clinic", last.asserted_by)])
    return SignalResult(key, label, tier, False, "attended last visit",
                        f"Attended the last scheduled visit on {fmt(last.effective_date)}.",
                        [f"{fmt(last.effective_date)}: attended ({_where(last)})."],
                        [_fact("Last booked visit", fmt(last.effective_date), sub=f"{_plural(days, 'day')} ago"),
                         _fact("Attended", "Yes", "ok")])


def overdue_screening(patient: PatientRecord, as_of: date, config: DetectionConfig = DEFAULT_CONFIG) -> SignalResult:
    """Fires if any eye / foot / kidney screening is past its due interval (or was marked overdue)."""
    key, label, tier = "overdue_screening", "Screening overdue", "soft"
    screens = patient.of_type("screening", as_of)
    evidence, overdue, facts = [], [], []

    for kind, interval in config.screening_interval_days.items():
        rows = [s for s in screens if (s.name or "").lower() == kind.lower()]
        done = [s for s in rows if s.status == "resulted"]
        last_done = done[-1] if done else None
        pending = [s for s in rows if s.status == "ordered"
                   and (last_done is None or s.effective_date > last_done.effective_date)]

        if pending:
            due = pending[0].effective_date
            evidence.append(f"{kind}: marked due {fmt(due)} by {pending[0].asserted_by}.")
            basis = f"marked due by {pending[0].asserted_by}"
        elif last_done:
            due = last_done.effective_date + timedelta(days=interval)
            evidence.append(f"{kind}: last done {fmt(last_done.effective_date)}, due every {interval} days → due {fmt(due)}.")
            basis = f"last done {fmt(last_done.effective_date)}"
        else:
            evidence.append(f"{kind}: no screening on record.")
            facts.append(_fact(kind, "Never done", "bad"))           # "never done" already means overdue
            overdue.append((kind, None))
            continue
        if due <= as_of:
            days = (as_of - due).days
            overdue.append((kind, days))
            facts.append(_fact(kind, f"Overdue {_plural(days, 'day')}", "bad", sub=f"Due {fmt(due)} · {basis}"))
        else:
            facts.append(_fact(kind, "Up to date", "ok", sub=f"Next due {fmt(due)}"))

    facts.append(_fact("Flag when", "Any screening is past its due date"))
    if overdue:
        parts = [f"{k.lower()} (no record)" if d is None else f"{k.lower()} ({d} days)" for k, d in overdue]
        return SignalResult(key, label, tier, True, f"screening overdue: {', '.join(parts)}",
                            f"Screening overdue: {', '.join(parts)}.", evidence, facts)
    return SignalResult(key, label, tier, False, "screenings up to date", "Eye, foot and kidney screenings are up to date.",
                        evidence, facts)


# =============================================================== HARD signals

def hypoglycemia_event(patient: PatientRecord, as_of: date, config: DetectionConfig = DEFAULT_CONFIG) -> SignalResult:
    """Fires if any hypoglycaemia event is recorded in the lookback window."""
    key, label, tier = "hypoglycemia_event", "Hypoglycaemia event", "hard"
    since = as_of - timedelta(days=config.hypo_lookback_days)
    events = [e for e in patient.of_type("hypo_event", as_of) if e.effective_date >= since]
    if not events:
        return SignalResult(key, label, tier, False, "no hypoglycaemia",
                            f"No hypoglycaemia recorded in the last {config.hypo_lookback_days} days.",
                            facts=[_fact(f"Last {config.hypo_lookback_days} days", "None recorded", "ok")])

    latest = events[-1]
    reading = f" ({latest.value:g} {latest.unit})" if latest.value is not None else ""
    evidence = [f"{fmt(e.effective_date)}: hypoglycaemia{f' {e.value:g} {e.unit}' if e.value is not None else ''} "
                f"— {_where(e)}{f'. {e.note}' if e.note else ''}" for e in events]
    facts = [_fact("Date", fmt(latest.effective_date), "bad")]
    if latest.value is not None:
        facts.append(_fact("Glucose", f"{latest.value:g} {latest.unit}", "bad"))
    facts.append(_fact("Recorded by", latest.asserted_by, sub=_source_sub(latest)))
    if latest.note:
        facts.append(_fact("Note", latest.note))
    if len(events) > 1:
        facts.append(_fact(f"Events in last {config.hypo_lookback_days} days", str(len(events)), "bad"))
    return SignalResult(key, label, tier, True, f"hypoglycaemia event recorded on {fmt(latest.effective_date)}{reading}",
                        f"{_plural(len(events), 'hypoglycaemia event')} in the last {config.hypo_lookback_days} days, "
                        f"most recently on {fmt(latest.effective_date)}{reading}.", evidence, facts)


def emergency_visits(patient: PatientRecord, as_of: date) -> list[Event]:
    """Every recorded emergency visit up to as_of, oldest first."""
    return [v for v in patient.of_type("visit", as_of) if _is_emergency(v) and v.status == "resulted"]


def last_clinic_visit(patient: PatientRecord, as_of: date) -> date | None:
    attended = [v for v in patient.of_type("visit", as_of) if not _is_emergency(v) and v.status == "resulted"]
    return attended[-1].effective_date if attended else None


def emergency_window_start(patient: PatientRecord, as_of: date, config: DetectionConfig = DEFAULT_CONFIG) -> date:
    """Emergency visits on or after this date count: since the last clinic visit, capped by the lookback."""
    since = as_of - timedelta(days=config.emergency_lookback_days)
    last = last_clinic_visit(patient, as_of)
    return last if last and last > since else since


def emergency_visit(patient: PatientRecord, as_of: date, config: DetectionConfig = DEFAULT_CONFIG) -> SignalResult:
    """Fires if an emergency visit is recorded since the last clinic check-in."""
    key, label, tier = "emergency_visit", "Emergency visit", "hard"
    since = emergency_window_start(patient, as_of, config)
    er = [v for v in emergency_visits(patient, as_of) if v.effective_date >= since]
    if not er:
        return SignalResult(key, label, tier, False, "no emergency visits",
                            f"No emergency visits since the last clinic visit ({fmt(since)}).",
                            facts=[_fact(f"Since {fmt(since)}", "No emergency visits", "ok")])

    latest = er[-1]
    hospital = latest.facility or latest.asserted_by
    facts = [_fact("Date", fmt(latest.effective_date), "bad"),
             _fact("Hospital", hospital)]
    if latest.note:
        facts.append(_fact("Problem", latest.note))
    if latest.clinician:
        facts.append(_fact("Treating doctor", latest.clinician))
    facts += [_fact("Recorded by", latest.asserted_by, sub=_source_sub(latest)),
              _fact("Last clinic visit", fmt(since))]
    if len(er) > 1:
        facts.append(_fact("Emergency visits since then", str(len(er)), "bad"))
    return SignalResult(key, label, tier, True,
                        f"emergency visit on {fmt(latest.effective_date)} at {hospital}",
                        f"Emergency visit on {fmt(latest.effective_date)} at {hospital}, after the last "
                        f"clinic visit on {fmt(since)}.",
                        [f"{fmt(v.effective_date)}: {v.name} — {_where(v)}{f'. {v.note}' if v.note else ''}" for v in er],
                        facts)


# =============================================================== Combine rule

SOFT_SIGNALS = (missed_refill, engagement_dropped, hba1c_plateaued, missed_appointment, overdue_screening)
HARD_SIGNALS = (hypoglycemia_event, emergency_visit)


def assess_patient(patient: PatientRecord, as_of: date, config: DetectionConfig = DEFAULT_CONFIG) -> RiskAssessment:
    hard = [check(patient, as_of, config) for check in HARD_SIGNALS]
    soft = [check(patient, as_of, config) for check in SOFT_SIGNALS]
    hard_fired = [s for s in hard if s.fired]
    soft_fired = [s for s in soft if s.fired]

    if hard_fired:
        level = "high_priority"
        summary = "HIGH PRIORITY — " + " + ".join(s.short for s in hard_fired) + "."
        if soft_fired:
            summary += " Also: " + " + ".join(s.short for s in soft_fired) + "."
    elif len(soft_fired) >= config.soft_signals_needed:
        level = "quietly_worse"
        summary = "Flagged because: " + " + ".join(s.short for s in soft_fired) + "."
    elif soft_fired:
        level = "watch"
        summary = (f"Not flagged: only 1 of {len(soft)} warning signs ({soft_fired[0].short}). "
                   f"A flag needs {config.soft_signals_needed}.")
    else:
        level = "none"
        summary = "Not flagged: no warning signs."

    return RiskAssessment(patient.id, as_of, level, level in ("high_priority", "quietly_worse"),
                          len(hard_fired), len(soft_fired), summary, hard + soft)
