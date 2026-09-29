"""Reset and seed the demo database. Run: python -m app.seed [--today YYYY-MM-DD]

All dates are relative to "today", so the demo tells the same story whenever it is run.
Seeded data is the hospital's own record (source = hospital_internal), plus a couple of
patient self-uploads for the stable patient.
"""

import argparse
import random
import shutil
import sqlite3
from datetime import date, timedelta

from . import db, repo, security

CLINIC = "UC2 Diabetes Clinic"
PHARMACY = "UC2 Hospital Pharmacy"
LAB = "UC2 Hospital Laboratory"
MONITORING = "UC2 Remote Glucose Monitoring"
ED = "UC2 Hospital — Emergency Department"

# Reference ranges exactly as the UC2 Hospital Laboratory prints them on its reports.
# (Other labs print their own - see abdm_mock.py. A result without a printed range stores none.)
HBA1C_REF = dict(ref_kind="upper", ref_high=5.7, ref_text="< 5.7 %")
HB_MALE_REF = dict(ref_kind="range", ref_low=13.0, ref_high=17.0, ref_text="13.0 – 17.0 g/dL")
HB_FEMALE_REF = dict(ref_kind="range", ref_low=12.0, ref_high=16.0, ref_text="12.0 – 16.0 g/dL")
EGFR_REF = dict(ref_kind="lower", ref_low=60, ref_text="> 60 mL/min/1.73m²")
URINE_PROTEIN_REF = dict(ref_kind="qualitative", ref_text="Negative")
NO_REF = None  # the report did not print a reference range

DEMO_PATIENTS = [
    # 1. CLEARLY DRIFTING - every soft signal planted, no hard signal.
    dict(code="P-1001", name="Ramesh Kumar", phone="9000000001", abha="91123456789012", birth_year=1967, sex="M",
         medicine="Metformin 500 mg", last_refill=52, refill_count=18,
         stopped_medicine=("Glimepiride 1 mg", 300),
         visits=[540, 450, 360, 270, 180, 90], missed_visit=21, next_visit_in=14,
         hba1c=[(547, 9.1), (457, 8.4), (367, 7.9), (277, 7.8), (187, 7.9), (97, 8.2)],
         screenings={"Eye": 480, "Foot": 200, "Kidney": 190},
         er_history=[(410, "Low sugar (48 mg/dL) after skipping lunch; treated with IV glucose and sent home",
                      "Dr. Kavita Menon (Emergency Medicine)")],
         labs=[("Hemoglobin", "g/dL", HB_MALE_REF, [(277, 14.1), (187, 13.6), (97, 12.6)]),
               ("eGFR", "mL/min/1.73m²", EGFR_REF, [(367, 82), (187, 69), (97, 58)]),
               ("Urine protein", None, URINE_PROTEIN_REF, [(367, "Negative"), (187, "Negative"), (97, "Positive (1+)")]),
               ("LDL cholesterol", "mg/dL", NO_REF, [(187, 132), (97, 141)])],
         log_every_before=2, log_every_recent=7, glucose_before=(120, 160), glucose_recent=(165, 230)),
    # 2. CLEARLY STABLE - no signals at all.
    dict(code="P-1002", name="Lakshmi Iyer", phone="9000000002", abha="91234567890123", birth_year=1963, sex="F",
         medicine="Metformin 1000 mg", last_refill=12, refill_count=17,
         visits=[510, 420, 330, 240, 150, 60], next_visit_in=30,
         hba1c=[(517, 8.6), (427, 8.0), (337, 7.6), (247, 7.1), (157, 6.9), (67, 6.8)],
         screenings={"Eye": 150, "Foot": 150, "Kidney": 60},
         labs=[("Hemoglobin", "g/dL", HB_FEMALE_REF, [(247, 13.1), (157, 13.4), (67, 13.2)]),
               ("eGFR", "mL/min/1.73m²", EGFR_REF, [(337, 88), (157, 90), (67, 86)]),
               ("Urine protein", None, URINE_PROTEIN_REF, [(157, "Negative"), (67, "Negative")]),
               ("LDL cholesterol", "mg/dL", NO_REF, [(157, 102), (67, 96)])],
         log_every_before=2, log_every_recent=2, glucose_before=(100, 140), glucose_recent=(95, 130),
         self_uploads=True),
    # 3. HARD SIGNAL ONLY - one hypoglycaemia episode, otherwise stable.
    dict(code="P-1003", name="Meena Rao", phone="9000000003", abha="91345678901234", birth_year=1960, sex="F",
         medicine="Glimepiride 2 mg", last_refill=15, refill_count=14,
         visits=[420, 330, 240, 150, 60], next_visit_in=30,
         hba1c=[(425, 8.1), (335, 7.6), (245, 7.2), (155, 6.9), (65, 6.8)],
         screenings={"Eye": 100, "Foot": 100, "Kidney": 65},
         er_history=[(230, "Fall at home with minor wrist injury; sugar 142 mg/dL",
                      "Dr. Anil Rao (Orthopaedic Surgeon)")],
         labs=[("Hemoglobin", "g/dL", HB_FEMALE_REF, [(245, 12.8), (155, 12.6), (65, 12.9)]),
               ("eGFR", "mL/min/1.73m²", EGFR_REF, [(245, 71), (155, 69), (65, 70)]),
               ("Urine protein", None, URINE_PROTEIN_REF, [(155, "Negative"), (65, "Negative")])],
         log_every_before=2, log_every_recent=2, glucose_before=(100, 145), glucose_recent=(90, 135),
         hypo=(9, 52, "Felt shaky and sweaty at home; glucose 52 mg/dL; recovered with oral glucose.")),
]


# Development fixtures only. Never loaded outside APP_ENV=dev.
DEMO_PASSWORD = "demo1234"
DEMO_CLINICIANS = [
    # Care team for all three demo patients. Takes appointments 09:00-13:00: 15-minute slots, 5-minute buffer.
    dict(code="CLN-PRYA27", name="Dr. Priya Nair", phone="9800000001", patients=["P-1001", "P-1002", "P-1003"],
         hours=("09:00", "13:00", 15, 5)),
    # A real account with NO patients assigned - demonstrates isolation. Different hours, slot length and buffer.
    dict(code="CLN-KRNB58", name="Dr. Karan Bhatia", phone="9800000002", patients=[],
         hours=("10:00", "14:00", 20, 10)),
]

# A tiny valid PDF so the demo has a real patient-uploaded file to open.
_DEMO_PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
             b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 144]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
             b"4 0 obj<</Length 56>>stream\nBT /F1 14 Tf 20 90 Td (Demo lipid profile - sample) Tj ET\nendstream endobj\n"
             b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")


def _insert_patient(conn: sqlite3.Connection, spec: dict, today: date, rng: random.Random) -> str:
    pid = repo.create_patient(conn, spec["name"], spec["phone"], date_of_birth=f"{spec['birth_year']}-03-15",
                              sex=spec["sex"], abha_number=spec["abha"], patient_code=spec["code"],
                              password_hash=security.hash_password(DEMO_PASSWORD))["id"]

    def ago(days: int) -> date:
        return today - timedelta(days=days)

    def add(event_type, days_ago, status, asserted_by, source="hospital_internal", confidence="high", **kw):
        repo.insert_event(conn, pid, event_type, ago(days_ago), source, confidence, status, asserted_by, **kw)

    # Visits: attended = resulted; a booked visit that never happened stays "ordered" (= missed).
    for d in spec["visits"]:
        add("visit", d, "resulted", CLINIC, name="Diabetes follow-up", facility=CLINIC)
        add("vital", d, "resulted", CLINIC, name="Blood pressure (systolic)", value=rng.randint(122, 142), unit="mmHg")
        add("vital", d, "resulted", CLINIC, name="Weight", value=round(rng.uniform(64, 82), 1), unit="kg")
    if spec.get("missed_visit"):
        add("visit", spec["missed_visit"], "ordered", CLINIC, name="Diabetes follow-up", note="Patient did not attend")
    add("visit", -spec["next_visit_in"], "ordered", CLINIC, name="Diabetes follow-up")

    # Lab results: one report per lab visit date, each result carrying the range printed on that report.
    reports: dict[int, str] = {}

    def result(days_ago, name, value, unit, ref):
        if days_ago not in reports:
            reports[days_ago] = repo.create_lab_report(conn, pid, LAB, ago(days_ago), "hospital_internal")
        repo.add_lab_result(conn, reports[days_ago], pid, name, value, unit, ago(days_ago), **(ref or {}))

    for d, value in spec["hba1c"]:
        result(d, "HbA1c", value, "%", HBA1C_REF)
    for name, unit, ref, results in spec["labs"]:
        for d, value in results:
            result(d, name, value, unit, ref)

    for kind, d in spec["screenings"].items():
        add("screening", d, "resulted", CLINIC, name=kind, note=f"{kind} screening done")

    # Refills: 30-day supply, each collected 0-3 days early, walking backwards from the latest one.
    days_ago, refill_days = spec["last_refill"], []
    for _ in range(spec["refill_count"]):
        refill_days.append(days_ago)
        days_ago += 30 - rng.randint(0, 3)
    for d in sorted(refill_days, reverse=True):
        add("refill", d, "active", PHARMACY, name=spec["medicine"], value=30, unit="days")

    if spec.get("stopped_medicine"):
        med, stopped_ago = spec["stopped_medicine"]
        for d in (stopped_ago + 60, stopped_ago + 30):
            add("refill", d, "active", PHARMACY, name=med, value=30, unit="days")
        add("refill", stopped_ago, "stopped", CLINIC, name=med, note="Stopped by doctor at visit")

    # Engagement: fasting glucose logs from the remote monitoring programme, on a fixed cadence.
    for d in range(spec["visits"][0], 0, -1):
        recent = d <= 60
        every = spec["log_every_recent"] if recent else spec["log_every_before"]
        if d % every == 0:
            lo, hi = spec["glucose_recent"] if recent else spec["glucose_before"]
            add("engagement_log", d, "resulted", MONITORING, name="Fasting glucose", value=rng.randint(lo, hi), unit="mg/dL")

    # Older emergency visits - all before the latest clinic visit, so history only (no flag).
    for d, problem, doctor in spec.get("er_history", []):
        add("visit", d, "resulted", ED, name="Emergency visit", note=problem, facility=ED, clinician=doctor)

    if spec.get("self_uploads"):
        for d, v in ((1, 118), (3, 124)):
            add("engagement_log", d, "resulted", "Patient (self-reported)", source="patient_upload",
                confidence="medium", name="Fasting glucose", value=v, unit="mg/dL")

    if spec.get("hypo"):
        d, glucose, note = spec["hypo"]
        add("hypo_event", d, "resulted", f"{CLINIC} — nurse phone check-in", name="Hypoglycaemia",
            value=glucose, unit="mg/dL", note=note)
    return pid


def seed_database(conn: sqlite3.Connection, today: date) -> dict[str, str]:
    """Load the development fixtures into an EMPTY, migrated database. Returns {patient_code: id}."""
    if conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0]:
        raise RuntimeError("Refusing to seed: the database already has patients.")
    ids = {spec["code"]: _insert_patient(conn, spec, today, random.Random(1000 + i))
           for i, spec in enumerate(DEMO_PATIENTS)}
    for c in DEMO_CLINICIANS:
        user = repo.create_clinician(conn, c["name"], c["phone"], DEMO_PASSWORD, clinician_code=c["code"])
        for code in c["patients"]:
            repo.grant_access(conn, user["id"], ids[code], granted_by=None)
        if c.get("hours"):
            start, end, length, buffer = c["hours"]
            conn.execute("INSERT INTO doctors (id, clinical_user_id, name, working_start_time, working_end_time, "
                         "slot_duration_minutes, buffer_minutes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (repo.new_id(), user["id"], c["name"], start, end, length, buffer, db.now_iso(), db.now_iso()))
    # One patient-uploaded PDF awaiting review, so the Documents section has a real file.
    doc = repo.create_document(conn, ids["P-1001"], "lab_report", "lipid_profile.pdf", "application/pdf", _DEMO_PDF,
                               "patient_upload", description="Lipid profile", uploaded_by_patient_id=ids["P-1001"])
    repo.create_lab_report(conn, ids["P-1001"], "Uploaded by patient", today, "patient_upload", document_id=doc,
                           report_type="Lipid profile")
    conn.commit()
    return ids


def main() -> None:
    from .detection import assess_patient
    from .settings import get_settings

    parser = argparse.ArgumentParser(description="Reset the DEVELOPMENT database and load demo fixtures.")
    parser.add_argument("--today", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    settings = get_settings()
    if not settings.is_dev:
        raise SystemExit("Refusing to reset: APP_ENV is not 'dev'. Demo fixtures never touch a real database.")

    shutil.rmtree(settings.storage_dir, ignore_errors=True)
    conn = db.reset()
    ids = seed_database(conn, args.today)
    print(f"Reset {settings.db_path} and loaded demo fixtures (today = {args.today})\n")
    for table in ("patients", "clinical_users", "care_team_assignments", "clinical_events", "lab_reports",
                  "lab_test_results", "patient_documents"):
        print(f"  {table:<22} {conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]:>5}")
    print("\n  Clinician logins (password: demo1234):")
    for c in DEMO_CLINICIANS:
        print(f"    {c['code']}  {c['name']:<18} phone {c['phone']}  patients: {', '.join(c['patients']) or 'none'}")
    print()
    for code, pid in ids.items():
        a = assess_patient(repo.load_patient_record(conn, pid), args.today)
        print(f"  [{a.level:^13}] {code} {a.summary}")


if __name__ == "__main__":
    main()
