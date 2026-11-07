"""Deterministic checks on what was read. No AI here - and a high confidence is never treated as correctness.

Never changes a value: a unit conversion is added BESIDE the printed value (with the factor used), a conflict is
reported (never merged), an implausible value is flagged (never corrected).
"""

from dataclasses import dataclass
from datetime import date, timedelta

from . import parse
from .parse import DateRead, Found


@dataclass
class Issue:
    code: str
    severity: str                  # error | warning | info
    message: str
    field: int | None = None       # index into the fields list; None = about the whole document


@dataclass
class Normalized:
    value: float | None = None
    unit: str | None = None
    conversion: str | None = None


# The test the patient chose -> the values that should appear in its report.
EXPECTED = {"hba1c": {"hba1c"}, "fpg": {"fpg"}, "ppbs": {"ppbs"}, "ogtt": {"ogtt", "fpg", "ppbs"},
            "kft": {"creatinine", "egfr", "uacr"}, "lipid": {"total_cholesterol", "ldl", "hdl", "triglycerides"},
            "urine_ketone": {"urine_ketone"}}


def _round(v: float) -> float:
    return round(v, 1) if abs(v) >= 100 else round(v, 2)


def _num(v: float) -> str:
    return f"{_round(v):g}"


def normalize(f: Found) -> tuple[Normalized, list[tuple[str, str, str]]]:
    """The value in the standard unit, when a supported conversion applies - plus what to say about it."""
    a = parse.analyte(f.analyte) if f.analyte else None
    notes: list[tuple[str, str, str]] = []
    if a is None or a.get("qualitative") or f.value_num is None:
        if "comparator" in f.flags:
            notes.append(("comparator", "info", f"Printed as “{f.value_text}” - not a single number."))
        return Normalized(), notes
    if f.unit is None:
        notes.append(("missing_unit", "warning", "No unit printed next to the value. Check the report."))
        lo, hi = a["plausible"]
        if not lo <= f.value_num <= hi:
            notes.append(("implausible_value", "error",
                          f"{_num(f.value_num)} is outside {lo:g}–{hi:g} {a['standard_unit']} and no unit is printed."))
        return Normalized(), notes
    rule = a["units"].get(f.unit)
    if rule is None:
        notes.append(("unexpected_unit", "error", f"“{f.unit}” is not a unit used for {a['label']}."))
        return Normalized(), notes
    std = f.value_num * rule.get("factor", 1) + rule.get("offset", 0)
    norm = Normalized()
    if f.unit != a["standard_unit"]:
        how = f"× {rule['factor']:g}" + (f" + {rule['offset']:g}" if rule.get("offset") else "")
        norm = Normalized(_round(std), a["standard_unit"], f"{f.unit} {how} = {a['standard_unit']}")
        notes.append(("unit_converted", "info",
                      f"Printed in {f.unit}; also shown as {_num(std)} {a['standard_unit']} ({how}). The printed value is kept."))
    lo, hi = a["plausible"]
    if not lo <= std <= hi:
        notes.append(("implausible_value", "error",
                      f"{a['label']} {_num(std)} {a['standard_unit']} is outside {lo:g}–{hi:g}. Check the report."))
    return norm, notes


def check_date(d: DateRead | None, today: date, required: bool) -> list[Issue]:
    if d is None:
        return [Issue("missing_date", "warning", "No test date found on the report.")] if required else []
    if d.value is None:
        return [Issue("unreadable_date", "warning", f"The date “{d.text}” could not be read as a date.")]
    out = []
    if d.value > today:
        out.append(Issue("future_date", "error", f"The date {d.value:%d %b %Y} is in the future."))
    years = parse.rules()["dates"]["not_older_than_years"]
    if d.value < today - timedelta(days=365 * years):
        out.append(Issue("old_date", "warning", f"The date {d.value:%d %b %Y} is more than {years} years old."))
    if d.ambiguous:
        other = date(d.value.year, d.value.day, d.value.month)
        out.append(Issue("ambiguous_date", "warning",
                         f"“{d.text}” could be {d.value:%d %b %Y} or {other:%d %b %Y}. Read as day first - check it."))
    return out


def within_document(fields: list[Found], norms: list[Normalized]) -> list[Issue]:
    """The same test printed twice: the same value is a repeat; different values are a conflict, never merged."""
    out, seen = [], {}
    for i, f in enumerate(fields):
        if f.kind != "observation" or not f.analyte:
            continue
        v = norms[i].value if norms[i].value is not None else f.value_num
        if f.analyte in seen:
            j, w = seen[f.analyte]
            label = parse.analyte(f.analyte)["label"]
            if v is not None and w is not None and abs(v - w) > 1e-9:
                out.append(Issue("conflicting_result", "error",
                                 f"{label} is printed twice with different values ({fields[j].value_text} and {f.value_text}).", i))
            else:
                out.append(Issue("duplicate_in_document", "info", f"{label} is printed more than once.", i))
        else:
            seen[f.analyte] = (i, v)
    return out


def against_record(conn, patient_id: str, fields: list[Found], norms: list[Normalized], on: date | None,
                   exclude_document: str | None) -> list[Issue]:
    """Compare with what is already ON RECORD for the same test and the same date: a repeat, or a conflict."""
    if on is None:
        return []
    existing: dict[str, list[tuple[float, str]]] = {}
    rows = conn.execute(
        "SELECT name, value, unit, linked_document_id AS doc FROM clinical_events WHERE patient_id = ? AND event_type = 'lab' "
        "AND effective_date = ? AND value IS NOT NULL AND status != 'rejected' "
        "UNION ALL SELECT t.test_name, t.numeric_value, t.unit, r.document_id FROM lab_test_results t "
        "JOIN lab_reports r ON r.id = t.report_id WHERE t.patient_id = ? AND t.test_date = ? AND t.numeric_value IS NOT NULL",
        (patient_id, on.isoformat(), patient_id, on.isoformat())).fetchall()
    for name, value, unit, doc in rows:
        if exclude_document and doc == exclude_document:
            continue
        key = analyte_of(name)
        if key:
            existing.setdefault(key, []).append((float(value), f"{value:g} {unit or ''}".strip()))
    out = []
    for i, f in enumerate(fields):
        if f.kind != "observation" or f.analyte not in existing:
            continue
        v = norms[i].value if norms[i].value is not None else f.value_num
        if v is None:
            continue
        label = parse.analyte(f.analyte)["label"]
        same = [shown for val, shown in existing[f.analyte] if abs(val - v) < 1e-6]
        if same:
            out.append(Issue("duplicate_observation", "warning", f"{label} {same[0]} is already on record for {on:%d %b %Y}.", i))
        else:
            shown = ", ".join(s for _, s in existing[f.analyte])
            out.append(Issue("conflicting_result", "error",
                             f"On record for {on:%d %b %Y}: {label} {shown} - different from this report.", i))
    return out


def analyte_of(name: str) -> str | None:
    """Which standard test a printed or stored name is (the same rules the reader uses)."""
    low = (name or "").lower()
    for a in parse.rules()["analytes"]:
        if any(rx.search(name or "") for rx in a["_names"]) and not any(x in low for x in a["_exclude"]):
            return a["key"]
    return None


def expected_found(expected_test: str | None, fields: list[Found], label: str | None) -> list[Issue]:
    want = EXPECTED.get(expected_test or "")
    if not want or any(f.analyte in want for f in fields):
        return []
    return [Issue("expected_test_not_found", "warning", f"No {label or expected_test} value was found, but the patient sent it as {label or expected_test}.")]


def confidence(method: str, f: Found, field_issues: list[Issue]) -> str:
    if any(i.severity in ("error", "warning") for i in field_issues):
        return "low"
    if f.kind == "observation" and f.value_num is None and not f.value_text:
        return "low"
    return "high" if method == "pdf_text" else "medium"
