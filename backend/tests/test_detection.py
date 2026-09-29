from datetime import date, timedelta

import pytest

from app import repo
from app.config import DetectionConfig
from app.detection import (Event, PatientRecord, assess_patient, emergency_visit, engagement_dropped,
                           hba1c_plateaued, hypoglycemia_event, missed_appointment, missed_refill,
                           overdue_screening)

TODAY = date(2026, 9, 25)


def ago(days: int) -> date:
    return TODAY - timedelta(days=days)


def ev(event_type: str, days_ago: int, **kw) -> Event:
    defaults = dict(source="hospital_internal", confidence="high", status="resulted", asserted_by="Test Clinic")
    if event_type == "refill":
        defaults.update(status="active", name="Metformin 500 mg", value=30, unit="days")
    if event_type == "lab":
        defaults.update(name="HbA1c", unit="%")
    if event_type == "visit":
        defaults.update(name="Diabetes follow-up")
    defaults.update(kw)
    return Event(event_type=event_type, effective_date=ago(days_ago), **defaults)


def patient(*events: Event) -> PatientRecord:
    return PatientRecord(id=1, patient_code="T-1", full_name="Test Patient", events=list(events))


def logs(earlier: int, recent: int) -> list[Event]:
    """`earlier` logs 61-120 days ago and `recent` logs in the last 60 days."""
    days = [70 + i for i in range(earlier)] + [5 + i for i in range(recent)]
    return [ev("engagement_log", d, value=120, unit="mg/dL") for d in days]


# ================================================= The three seeded patients (end-to-end through SQLite)

@pytest.fixture()
def seeded(seeded_conn):
    conn, ids = seeded_conn
    return {code: repo.load_patient_record(conn, pid) for code, pid in ids.items()}


def test_drifting_patient_raises_soft_flag(seeded):
    p = seeded["P-1001"]
    for check in (missed_refill, engagement_dropped, hba1c_plateaued, missed_appointment, overdue_screening):
        assert check(p, TODAY), check.__name__
    assert not hypoglycemia_event(p, TODAY) and not emergency_visit(p, TODAY)

    a = assess_patient(p, TODAY)
    assert a.level == "quietly_worse" and a.flagged and a.soft_count == 5 and a.hard_count == 0
    assert a.summary.startswith("Flagged because: ")
    assert "22 days late" in a.summary and "7.8% → 7.9% → 8.2%" in a.summary


def test_hypo_patient_raises_hard_flag_alone(seeded):
    a = assess_patient(seeded["P-1003"], TODAY)
    assert a.soft_count == 0, "hypo patient must be otherwise stable"
    assert a.hard_count == 1 and a.level == "high_priority" and a.flagged
    assert a.summary == "HIGH PRIORITY — hypoglycaemia event recorded on 16 Sep 2026 (52 mg/dL)."


def test_stable_patient_triggers_nothing(seeded):
    a = assess_patient(seeded["P-1002"], TODAY)
    assert a.level == "none" and not a.flagged
    assert a.soft_count == 0 and a.hard_count == 0
    assert a.summary == "Not flagged: no warning signs."


def test_every_signal_always_explains_itself(seeded):
    for p in seeded.values():
        for s in assess_patient(p, TODAY).signals:
            assert s.short and s.detail, s.key


def test_stopped_medicine_is_not_a_missed_refill(seeded):
    assert "Glimepiride" not in missed_refill(seeded["P-1001"], TODAY).detail


# ================================================= Combine rule

SCREENED = [ev("screening", 100, name=k) for k in ("Eye", "Foot", "Kidney")]


def test_one_soft_signal_is_watch_not_flag():
    a = assess_patient(patient(ev("refill", 45), *SCREENED), TODAY)
    assert a.soft_count == 1 and a.level == "watch" and not a.flagged


def test_hard_signal_wins_and_still_lists_soft_signals():
    a = assess_patient(patient(ev("refill", 45), ev("hypo_event", 2, value=50, unit="mg/dL")), TODAY)
    assert a.level == "high_priority"
    assert a.summary.startswith("HIGH PRIORITY — hypoglycaemia") and "Also: Metformin 500 mg refill" in a.summary


def test_detection_ignores_source():
    """Same facts from any ingestion path must give the same answer."""
    for source in ("hospital_internal", "patient_upload", "external_hospital_abdm", "care_team_manual"):
        a = assess_patient(patient(ev("hypo_event", 3, source=source)), TODAY)
        assert a.level == "high_priority", source


# ================================================= Soft signal boundaries

def test_refill_9_days_late_does_not_fire_10_does():
    assert not missed_refill(patient(ev("refill", 39)), TODAY)
    r = missed_refill(patient(ev("refill", 40)), TODAY)
    assert r and "10 days overdue" in r.detail and "15 Sep 2026" in r.detail


def test_refill_collected_late_fires():
    assert "collected 40 days late" in missed_refill(patient(ev("refill", 75), ev("refill", 5)), TODAY).short


def test_refill_marked_missed_by_care_team():
    p = patient(ev("refill", 40), ev("refill", 12, status="ordered", source="care_team_manual", asserted_by="Dr. Nair"))
    r = missed_refill(p, TODAY)
    assert r and "12 days late" in r.short and "Dr. Nair" in r.detail


def test_engagement_40_percent_drop_fires_30_does_not():
    assert "40%" in engagement_dropped(patient(*logs(10, 6)), TODAY).short
    assert not engagement_dropped(patient(*logs(10, 7)), TODAY)


def test_engagement_needs_baseline():
    r = engagement_dropped(patient(*logs(2, 0)), TODAY)
    assert not r and "too few" in r.detail


@pytest.mark.parametrize("values, fires", [
    ([8.0, 8.0, 8.1], True),    # worse
    ([7.9, 7.8, 7.8], True),    # flat
    ([8.4, 8.0, 7.6], False),   # improving
    ([6.8, 6.8, 6.9], False),   # flat but at goal
    ([8.0, 8.2], True),         # only 2 readings
    ([8.0], False),             # not enough data
])
def test_hba1c_plateau(values, fires):
    events = [ev("lab", 300 - 90 * i, value=v) for i, v in enumerate(values)]
    assert bool(hba1c_plateaued(patient(*events), TODAY)) is fires


def test_missed_appointment():
    assert missed_appointment(patient(ev("visit", 100), ev("visit", 10, status="ordered")), TODAY)
    assert not missed_appointment(patient(ev("visit", 100), ev("visit", 10)), TODAY)             # attended
    assert not missed_appointment(patient(ev("visit", 100), ev("visit", 1, status="ordered")), TODAY)  # grace period
    assert not missed_appointment(patient(ev("visit", 100), ev("visit", -20, status="ordered")), TODAY)  # future


def test_overdue_screening():
    up_to_date = [ev("screening", 100, name=k) for k in ("Eye", "Foot", "Kidney")]
    assert not overdue_screening(patient(*up_to_date), TODAY)
    r = overdue_screening(patient(ev("screening", 400, name="Eye"), *up_to_date[1:]), TODAY)
    assert r and "eye (35 days)" in r.short
    marked = overdue_screening(patient(*up_to_date, ev("screening", 2, name="Foot", status="ordered")), TODAY)
    assert marked and "foot" in marked.short
    assert "no record" in overdue_screening(patient(*up_to_date[:2]), TODAY).short


# ================================================= Hard signal boundaries

def test_hypo_lookback_window():
    assert hypoglycemia_event(patient(ev("hypo_event", 90)), TODAY)
    assert not hypoglycemia_event(patient(ev("hypo_event", 91)), TODAY)


def test_emergency_visit_since_last_clinic_visit():
    er = ev("visit", 20, name="Emergency visit", asserted_by="City Care Hospital")
    assert "City Care Hospital" in emergency_visit(patient(ev("visit", 60), er), TODAY).short
    assert not emergency_visit(patient(er, ev("visit", 5)), TODAY)       # seen in clinic after the ER visit


def test_thresholds_come_from_config():
    p = patient(ev("refill", 36))                                        # 6 days overdue
    assert not missed_refill(p, TODAY)
    assert missed_refill(p, TODAY, DetectionConfig(refill_late_days=5))
