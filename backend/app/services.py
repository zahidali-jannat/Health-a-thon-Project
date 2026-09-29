"""Read models shared by the clinical and patient APIs."""

from dataclasses import asdict
from datetime import date

from . import repo
from .detection import PatientRecord, assess_patient, emergency_visits


def patient_summary(p: PatientRecord, as_of: date) -> dict:
    visits = [e for e in p.events if e.event_type == "visit" and "emergency" not in (e.name or "").lower()]
    past = [e for e in visits if e.status == "resulted" and e.effective_date <= as_of]
    upcoming = [e for e in visits if e.status == "ordered" and e.effective_date >= as_of]
    hba1c = sorted((e for e in p.events if e.event_type == "lab" and (e.name or "").lower() == "hba1c"
                    and e.value is not None and e.effective_date <= as_of), key=lambda e: e.effective_date)
    a = assess_patient(p, as_of)
    return {
        "id": p.id, "patient_code": p.patient_code, "full_name": p.full_name, "phone": p.phone,
        "abha_number": p.abha_number, "date_of_birth": p.date_of_birth,
        "age": repo.age(p.date_of_birth, as_of, p.reported_age, p.reported_age_on), "sex": p.sex,
        "age_source": "date of birth" if p.date_of_birth else "told by patient" if p.reported_age is not None else None,
        "last_visit": past[-1].effective_date if past else None,
        "next_visit": upcoming[0].effective_date if upcoming else None,
        "latest_hba1c": {"value": hba1c[-1].value, "date": hba1c[-1].effective_date} if hba1c else None,
        "emergency_visit_count": len(emergency_visits(p, as_of)),
        "assessment": {
            "level": a.level, "flagged": a.flagged, "hard_count": a.hard_count, "soft_count": a.soft_count,
            "summary": a.summary, "signals": [asdict(s) for s in a.signals],
        },
    }


def current_medicine(p: PatientRecord) -> str:
    """The medicine the patient most recently collected, ignoring medicines the doctor stopped."""
    latest_status: dict[str, str] = {}
    last_collected: dict[str, date] = {}
    for e in sorted((e for e in p.events if e.event_type == "refill"), key=lambda e: e.effective_date):
        latest_status[e.name] = e.status
        if e.status == "active":
            last_collected[e.name] = e.effective_date
    candidates = [m for m in last_collected if latest_status[m] != "stopped"]
    return max(candidates, key=last_collected.get) if candidates else "Diabetes medicine"


def event_dict(e) -> dict:
    d = asdict(e)
    d["effective_date"] = e.effective_date.isoformat()
    return d
