"""Lab results against their reference ranges.

The range always comes from the result itself (the range printed on that specific report).
This module never supplies a default or "typical" range: no range on the report means
"Reference range unavailable".

Range kinds:
  range        low – high, both inclusive          e.g. 12.0 – 16.0 g/dL
  upper        value must be below the limit       e.g. < 5.7 %
  lower        value must be above the limit       e.g. > 60 mL/min/1.73m²
  qualitative  result text must match the expected text   e.g. Negative
Critical limits are applied only if the lab reported them.
"""

from dataclasses import asdict, dataclass

from . import repo
from .config import EGFR_AGE_AVERAGES, EGFR_AGE_AVERAGES_SOURCE, HBA1C_STAGES, HBA1C_STAGES_SOURCE
from .detection import SOURCE_LABELS, Event, PatientRecord, fmt

# Clinically most relevant first; anything else follows alphabetically.
PREFERRED_ORDER = ["hba1c", "fasting glucose", "post-meal glucose (pp)", "egfr", "hemoglobin", "urine protein",
                   "ldl cholesterol"]

STATUS_LABELS = {
    "within": "In range",
    "above": "Above range ↑",
    "below": "Below range ↓",
    "critical_high": "Critical high ↑",
    "critical_low": "Critical low ↓",
    "normal": "Matches reference",
    "abnormal": "Abnormal",
    "unavailable": "No range",
}


def _decimals(x: float) -> int:
    """Decimal places the lab reported, so 13.6 -> 12.6 reads "-1.0", not "-1"."""
    text = f"{x:g}"
    return len(text.split(".")[1]) if "." in text else 0


def _num(x: float) -> str:
    return f"{x:g}"


def reference_text(e: Event) -> str | None:
    if e.ref_kind is None:
        return None
    if e.ref_text:
        return e.ref_text
    unit = f" {e.unit}" if e.unit else ""
    return {
        "range": f"{_num(e.ref_low)} – {_num(e.ref_high)}{unit}" if e.ref_low is not None and e.ref_high is not None else None,
        "upper": f"< {_num(e.ref_high)}{unit}" if e.ref_high is not None else None,
        "lower": f"> {_num(e.ref_low)}{unit}" if e.ref_low is not None else None,
        "qualitative": None,
    }[e.ref_kind]


def classify(e: Event) -> str:
    """Status of one result against its own reference range."""
    if e.ref_kind is None:
        return "unavailable"

    if e.ref_kind == "qualitative":
        if not e.ref_text or e.value_text is None:
            return "unavailable"
        return "normal" if e.value_text.strip().lower() == e.ref_text.strip().lower() else "abnormal"

    v = e.value
    if v is None:
        return "unavailable"
    if e.crit_high is not None and v > e.crit_high:
        return "critical_high"
    if e.crit_low is not None and v < e.crit_low:
        return "critical_low"

    if e.ref_kind == "range":
        if e.ref_low is None or e.ref_high is None:
            return "unavailable"
        return "below" if v < e.ref_low else "above" if v > e.ref_high else "within"
    if e.ref_kind == "upper":
        if e.ref_high is None:
            return "unavailable"
        return "within" if v < e.ref_high else "above"
    if e.ref_kind == "lower":
        if e.ref_low is None:
            return "unavailable"
        return "within" if v > e.ref_low else "below"
    return "unavailable"


def status_label(e: Event, status: str) -> str:
    """Short on purpose: every place that shows it also shows the reference range itself."""
    return STATUS_LABELS[status]


def distance_from_range(e: Event) -> float | None:
    """0 inside the range, otherwise how far outside it. None if there is no numeric range."""
    status = classify(e)
    if status == "within":
        return 0.0
    if status in ("above", "critical_high") and e.ref_high is not None:
        return e.value - e.ref_high
    if status in ("below", "critical_low") and e.ref_low is not None:
        return e.ref_low - e.value
    return None


@dataclass
class Trend:
    direction: str | None      # "toward" | "away" | "within" | "same" | None (no range to compare with)
    text: str
    change: float
    change_text: str


def trend(results: list[Event]) -> Trend | None:
    numeric = [r for r in results if r.value is not None]
    if len(numeric) < 2:
        return None
    prev, last = numeric[-2], numeric[-1]
    change = round(last.value - prev.value, 3)
    unit = f"\u00a0{last.unit}" if last.unit else ""   # keep "+0.3 %" together when wrapping
    places = max(_decimals(prev.value), _decimals(last.value))
    change_text = (f"{change:+.{places}f}{unit} since {fmt(prev.effective_date)}" if change
                   else f"No change since {fmt(prev.effective_date)}")

    dp, dl = distance_from_range(prev), distance_from_range(last)
    if dp is None or dl is None:
        return Trend(None, "No range to compare", change, change_text)
    if dp == 0 and dl == 0:
        return Trend("within", "Stayed in range", change, change_text)
    if abs(dl - dp) < 1e-9:
        return Trend("same", "No closer to range", change, change_text)
    if dl < dp:
        return Trend("toward", "Moving toward range", change, change_text)
    return Trend("away", "Moving away from range", change, change_text)


# ------------------------------------------------------------------ HbA1c diabetes stages (ADA)

HBA1C_UNITS = ("%", "mmol/mol")


def _is_hba1c(name: str | None, unit: str | None) -> bool:
    return (name or "").strip().lower() == "hba1c" and unit in HBA1C_UNITS


def to_mmol(pct: float) -> float:
    """NGSP % -> IFCC mmol/mol (master equation)."""
    return (pct - 2.15) * 10.929


def to_pct(mmol: float) -> float:
    return mmol / 10.929 + 2.15


def _limits(stage, unit):
    return (stage.low_pct, stage.high_pct) if unit == "%" else (stage.low_mmol, stage.high_mmol)


def _range_text(low, high, unit) -> str:
    u = " %" if unit == "%" else f" {unit}"
    if low is None:
        return f"< {_num(high)}{u}"
    if high is None:
        return f"≥ {_num(low)}{u}"
    # upper limit is exclusive: 5.7 - <6.5 reads "5.7–6.4 %", 39 - <48 reads "39–47 mmol/mol"
    step = 0.1 if unit == "%" else 1
    return f"{_num(low)}–{_num(round(high - step, 1))}{u}"


def hba1c_stages(unit: str | None) -> list[dict] | None:
    """The three stages in the panel's own unit, each with the equivalent in the other unit."""
    if unit not in HBA1C_UNITS:
        return None
    other = "mmol/mol" if unit == "%" else "%"
    return [{"key": s.key, "label": s.label, "tone": s.tone, "low": _limits(s, unit)[0], "high": _limits(s, unit)[1],
             "range_text": _range_text(*_limits(s, unit), unit), "alt_range_text": _range_text(*_limits(s, other), other)}
            for s in HBA1C_STAGES]


def hba1c_stage(e: Event) -> dict | None:
    """Which stage one HbA1c result falls in. None for any other test, or a result without a number."""
    if e.value is None or not _is_hba1c(e.name, e.unit):
        return None
    for s in HBA1C_STAGES:
        low, high = _limits(s, e.unit)
        if (low is None or e.value >= low) and (high is None or e.value < high):
            return {"key": s.key, "label": s.label, "tone": s.tone, "range_text": _range_text(low, high, e.unit)}
    return None


def hba1c_other_unit(e: Event) -> str | None:
    """7.9 % -> "63 mmol/mol", 63 mmol/mol -> "7.9 %"."""
    if e.value is None or not _is_hba1c(e.name, e.unit):
        return None
    return f"{round(to_mmol(e.value))} mmol/mol" if e.unit == "%" else f"{to_pct(e.value):.1f} %"


# ------------------------------------------------------------------ eGFR: average for the patient's age

def egfr_age_reference(age: int | None, unit: str | None) -> dict | None:
    """The patient's age group and its average eGFR, chosen automatically from their age."""
    if age is None or not unit or not unit.lower().startswith("ml/min"):
        return None
    for youngest, oldest, average in EGFR_AGE_AVERAGES:
        if age >= youngest and (oldest is None or age <= oldest):
            group = f"{youngest}–{oldest}" if oldest is not None else f"{youngest}+"
            return {"age": age, "group": group, "average": average, "unit": unit,
                    "text": f"Average for age {group}: {average} {unit}", "source": EGFR_AGE_AVERAGES_SOURCE}
    return None                                    # under 20: no average in the table


def _result_dict(e: Event) -> dict:
    status = classify(e)
    return {
        "id": e.id,
        "date": e.effective_date.isoformat(),
        "value": e.value,
        "value_text": e.value_text,
        "unit": e.unit,
        "status": status,
        "status_label": status_label(e, status),
        "reference": None if status == "unavailable" and e.ref_kind is None else {
            "kind": e.ref_kind, "low": e.ref_low, "high": e.ref_high, "text": reference_text(e),
            "crit_low": e.crit_low, "crit_high": e.crit_high,
        },
        "source": e.source,
        "source_label": SOURCE_LABELS.get(e.source, e.source),
        "asserted_by": e.asserted_by,
        "report_id": e.report_id,
        "document_id": e.document_id,
        # External Report Review: who uploaded / who reviewed + the exact document. Always set for those values.
        "provenance": e.provenance,
        "stage": hba1c_stage(e),                 # HbA1c only: Normal / Prediabetes / Diabetes (ADA)
        "other_unit": hba1c_other_unit(e),
    }


def lab_panels(patient: PatientRecord, as_of) -> list[dict]:
    """One panel per test (name + unit). Results with different units are never mixed on one graph."""
    groups: dict[tuple[str, str | None], list[Event]] = {}
    for e in patient.of_type("lab", as_of):
        if e.status != "resulted" or (e.value is None and e.value_text is None):
            continue
        groups.setdefault(((e.name or "Lab test").strip().lower(), e.unit), []).append(e)

    def order(key):
        name = key[0]
        return (PREFERRED_ORDER.index(name) if name in PREFERRED_ORDER else len(PREFERRED_ORDER), name, key[1] or "")

    age = repo.age(patient.date_of_birth, as_of, patient.reported_age, patient.reported_age_on)
    panels = []
    for key in sorted(groups, key=order):
        results = groups[key]
        latest = results[-1]
        qualitative = all(r.value is None for r in results)
        ref_texts = {reference_text(r) for r in results}
        t = trend(results)
        panels.append({
            "name": latest.name,
            "unit": latest.unit,
            "kind": "qualitative" if qualitative else "numeric",
            "latest": _result_dict(latest),
            "results": [_result_dict(r) for r in results],
            # False when labs printed different ranges (or some printed none) - the UI must say so.
            "reference_consistent": len(ref_texts) == 1 and None not in ref_texts,
            "trend": asdict(t) if t else None,
            # HbA1c only: stage bands the clinician can switch on in the graph
            "stages": hba1c_stages(latest.unit) if _is_hba1c(latest.name, latest.unit) else None,
            "stages_source": HBA1C_STAGES_SOURCE if _is_hba1c(latest.name, latest.unit) else None,
            # eGFR only: the average for the patient's age group (from their date of birth or the age they gave)
            "age_reference": egfr_age_reference(age, latest.unit) if key[0] == "egfr" else None,
        })
    return panels
