"""CareCue Phase 1 - automatic reading of uploaded reports. Values are SUGGESTIONS with their page and source line;
deterministic checks flag problems (never fix them); handwritten or unclear documents get no suggestions at all; the
team's existing save marks each suggestion verified / corrected / rejected."""

import io
import json
import os
import sqlite3
import zipfile
from datetime import date

import httpx
import pytest

from app.extraction import parse, pipeline, sarvam, validate
from tests.conftest import PRIYA, TODAY, login_clinician, login_patient


def text_pdf(pages: list[list[str]]) -> bytes:
    """A real PDF with a text layer (Helvetica), one list of lines per page."""
    def esc(t):
        return t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    objs = {1: "<< /Type /Catalog /Pages 2 0 R >>", 3: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"}
    kids = []
    for i, lines in enumerate(pages):
        page_id, content_id = 4 + 2 * i, 5 + 2 * i
        stream = "BT /F1 11 Tf 50 780 Td 14 TL " + " ".join(f"({esc(line)}) Tj T*" for line in lines) + " ET"
        objs[content_id] = f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"
        objs[page_id] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> "
                         f"/Contents {content_id} 0 R >>")
        kids.append(f"{page_id} 0 R")
    objs[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>"
    out, offsets = b"%PDF-1.4\n", {}
    for k in sorted(objs):
        offsets[k] = len(out)
        out += f"{k} 0 obj\n{objs[k]}\nendobj\n".encode("latin-1")
    size, xref = max(objs) + 1, len(out)
    out += f"xref\n0 {size}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offsets[k]:010d} 00000 n \n".encode() if k in offsets else b"0000000000 65535 f \n" for k in range(1, size))
    out += f"trailer << /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def png() -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (900, 1200), (250, 250, 250)).save(out, format="PNG")
    return out.getvalue()


HBA1C = [["CITY DIAGNOSTIC LABORATORY", "Patient: Ramesh Kumar   DOB: 15/03/1967",
          "Sample collected: 20/09/2026   Reported: 21/09/2026",
          "HbA1c (NGSP) 7.2 % 4.0 - 5.6",
          "Interpretation: Normal < 5.7, Prediabetes 5.7 - 6.4, Diabetes >= 6.5"]]
LIPID = [["SUNRISE PATHOLOGY LAB", "Date of collection: 22-Sep-2026"],
         ["Total Cholesterol 182 mg/dL < 200", "Triglycerides 1.9 mmol/L < 1.7", "HDL Cholesterol 46 mg/dL > 40",
          "LDL Cholesterol 104 mg/dL < 100", "Desirable: Total cholesterol < 200"]]


def db():
    return sqlite3.connect(os.environ["UC2_DB_PATH"])


def send(client, files, **form):
    r = client.post("/api/me/reports", data=form, files=[("files", f) for f in files])
    assert r.status_code == 200, r.text
    return r


def latest(kind: str):
    c = db()
    c.row_factory = sqlite3.Row
    return c.execute("SELECT * FROM document_processing WHERE source_kind = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                     (kind,)).fetchone()


# ------------------------------------------------------------------ reading text: line by line, never invented

def test_values_come_from_their_own_line_and_guides_are_ignored():
    found = parse.observations("\n".join(HBA1C[0]), 1)
    assert [(f.analyte, f.value_text, f.unit, f.reference_range) for f in found] == [("hba1c", "7.2", "%", "4.0 - 5.6")]
    assert found[0].source_text == "HbA1c (NGSP) 7.2 % 4.0 - 5.6" and found[0].page_no == 1
    lines = ["| Glucose Fasting (FBS) | 6.9 | mmol/L | 3.9-5.5 |", "Haemoglobin 12.1 g/dL", "Urine Ketones: Negative  Reference: Negative",
             "Albumin Creatinine Ratio 45 mg/g", "Serum Creatinine 1.1 mg/dl", "eGFR (CKD-EPI 2021) 78 mL/min/1.73m2",
             "Microalbumin < 30 mg/g"]
    got = {f.analyte: f for f in parse.observations("\n".join(lines), 2)}
    assert got["fpg"].value_num == 6.9 and got["fpg"].unit == "mmol/L"
    assert got["hemoglobin"].value_num == 12.1                     # not confused with HbA1c
    assert got["urine_ketone"].value_text == "Negative" and got["urine_ketone"].value_num is None
    assert got["uacr"].value_text == "<30" and got["creatinine"].value_num == 1.1          # the ratio line is not creatinine
    assert got["egfr"].value_num == 78 and got["egfr"].unit == "mL/min/1.73m²"             # "(CKD-EPI 2021)" skipped


SARVAM_SAMPLE = ("SAMPLE - GREEN VALLEY DIAGNOSTIC LABORATORY\nPatient: Test Patient Age: 40\nSample collected: 10/10/2026\n"
                 "<table><tr><td>Test</td><td>Result</td><td>Unit</td><td>Reference</td></tr><tr><td>HbA1c</td><td>7.4</td>"
                 "<td>%</td><td>4.0 - 5.6</td></tr><tr><td>Fasting Glucose</td><td>132</td><td>mg/dL</td><td>70 - 100</td></tr>"
                 "<tr><td>Serum Creatinine</td><td>1.1</td><td>mg/dL</td><td>&lt; 1.2</td></tr></table>")


def test_ocr_html_tables_are_read_row_by_row():
    """Sarvam returns a table as HTML on one line (seen with a made-up sample report): each row must stay a row."""
    text = parse.normalize_text(SARVAM_SAMPLE)
    got = {f.analyte: (f.value_text, f.unit, f.reference_range) for f in parse.observations(text, 1)}
    assert got == {"hba1c": ("7.4", "%", "4.0 - 5.6"), "fpg": ("132", "mg/dL", "70 - 100"), "creatinine": ("1.1", "mg/dL", "< 1.2")}
    assert parse.report_date([text]).value == date(2026, 10, 10) and parse.readable_share(text) > 0.8


def test_missing_unit_stays_missing_and_comparators_are_not_numbers():
    f = {x.analyte: x for x in parse.observations("Fasting Blood Sugar 126\nMicroalbumin < 30 mg/g", 1)}
    assert f["fpg"].unit is None                                    # never guessed
    assert f["uacr"].value_text == "<30" and f["uacr"].value_num is None
    _, notes = validate.normalize(f["fpg"])
    assert [c for c, _, _ in notes] == ["missing_unit"]


def test_dates_lab_medicines_and_allergies():
    d = parse.report_date(["Name: X  DOB: 01/02/1970", "Reported: 21/09/2026", "Sample collected: 20/09/2026"])
    assert d.value == date(2026, 9, 20) and d.ambiguous is False    # the collection date wins; a birth date is never used
    amb = parse.report_date(["Date: 03/04/2026"])
    assert amb.ambiguous and validate.check_date(amb, TODAY, True)[0].code == "ambiguous_date"
    assert [i.code for i in validate.check_date(parse.report_date(["Collected: 25/12/2026"]), TODAY, True)] == ["future_date"]
    assert parse.readable_share("| Test | Result | Unit |\n|---|---|---|\n| HbA1c | 7.2 | % |\n| Fasting Glucose | 126 | mg/dL |") > 0.8
    assert parse.lab_name("CITY DIAGNOSTIC LABORATORY\nPatient: X") == "CITY DIAGNOSTIC LABORATORY"
    meds = parse.medications("1. Tab Metformin 500 mg 1-0-1\nTab. Glimepiride 2mg OD\nCap Vitamin D3", 1)
    assert [(m.name, m.value_text) for m in meds] == [("Tab Metformin", "500mg"), ("Tab Glimepiride", "2mg"), ("Cap Vitamin D3", None)]
    assert [a.value_text for a in parse.allergies("Allergies: Penicillin\nNKDA", 1)] == ["Penicillin", "None known"]


def test_conversions_keep_the_printed_value_and_impossible_values_are_only_flagged():
    f = parse.observations("HbA1c 53 mmol/mol", 1)[0]
    n, notes = validate.normalize(f)
    assert f.value_num == 53 and f.unit == "mmol/mol"             # printed value kept
    assert n.value == 7.0 and n.unit == "%" and "0.09148" in n.conversion and notes[0][0] == "unit_converted"
    bad = parse.observations("HbA1c 45 %", 1)[0]
    _, notes = validate.normalize(bad)
    assert bad.value_num == 45 and notes[0][:2] == ("implausible_value", "error")
    twice = parse.observations("HbA1c 7.2 %\nHbA1c 8.1 %", 1)
    issues = validate.within_document(twice, [validate.normalize(x)[0] for x in twice])
    assert issues[0].code == "conflicting_result" and issues[0].severity == "error"


# ------------------------------------------------------------------ the pipeline, through the app

def test_a_text_pdf_is_read_with_page_and_source_and_the_team_verifies_it(make_client):
    patient = make_client()
    login_patient(patient, "P-1001")
    send(patient, [("hba1c.pdf", text_pdf(HBA1C), "application/pdf")], kind="lab_report", test="hba1c")
    row = latest("external_report")
    assert row["status"] == "extraction_completed" and row["expected_test"] == "hba1c"   # read during the upload
    team = make_client()
    login_clinician(team, PRIYA)
    pid = db().execute("SELECT id FROM patients WHERE patient_code = 'P-1001'").fetchone()[0]
    v = team.get(f"/api/patients/{pid}/extraction/external_report/{row['source_id']}").json()
    assert v["status"] == "extraction_completed" and v["method"] == "pdf_text" and v["page_count"] == 1
    [f] = v["fields"]
    assert (f["analyte"], f["value_text"], f["unit"], f["page_no"], f["observed_on"]) == ("hba1c", "7.2", "%", 1, "2026-09-20")
    assert f["source_text"] == "HbA1c (NGSP) 7.2 % 4.0 - 5.6" and f["confidence"] == "high" and f["status"] == "needs_review"
    assert f["lab_name"] == "CITY DIAGNOSTIC LABORATORY"
    pending = next(i for i in team.get("/api/pending-items").json() if i["id"] == row["source_id"])
    assert pending["processing"]["status"] == "extraction_completed" and pending["processing"]["fields"] == 1
    r = team.post(f"/api/patients/{pid}/external-reports/{row['source_id']}/review", json={"value": 7.2, "test_date": "2026-09-20"})
    assert r.status_code == 200, r.text
    v = team.get(f"/api/patients/{pid}/extraction/external_report/{row['source_id']}").json()
    assert v["status"] == "verified" and v["fields"][0]["status"] == "verified" and v["fields"][0]["reviewed_by_name"] == "Dr. Priya Nair"


def test_a_corrected_value_is_recorded_as_corrected(make_client):
    patient = make_client()
    login_patient(patient, "P-1001")
    send(patient, [("hba1c.pdf", text_pdf(HBA1C), "application/pdf")], kind="lab_report", test="hba1c")
    pipeline.run_pending()
    row = latest("external_report")
    team = make_client()
    login_clinician(team, PRIYA)
    team.post(f"/api/patients/{row['patient_id']}/external-reports/{row['source_id']}/review", json={"value": 7.4})
    v = team.get(f"/api/patients/{row['patient_id']}/extraction/external_report/{row['source_id']}").json()
    assert v["status"] == "corrected" and v["fields"][0]["status"] == "corrected" and v["fields"][0]["final_value_text"] == "7.4"


def test_a_lab_report_with_several_values_and_unit_conversion(make_client):
    patient = make_client()
    login_patient(patient, "P-1001")
    send(patient, [("lipid.pdf", text_pdf(LIPID), "application/pdf")], kind="lab_report", test="lipid")
    pipeline.run_pending()
    row = latest("document")
    team = make_client()
    login_clinician(team, PRIYA)
    v = team.get(f"/api/patients/{row['patient_id']}/extraction/document/{row['source_id']}").json()
    got = {f["analyte"]: f for f in v["fields"]}
    assert set(got) == {"total_cholesterol", "triglycerides", "hdl", "ldl"} and all(f["page_no"] == 2 for f in got.values())
    tg = got["triglycerides"]
    assert (tg["value_text"], tg["unit"], tg["normalized_value"], tg["normalized_unit"]) == ("1.9", "mmol/L", 168.3, "mg/dL")
    assert any(i["code"] == "unit_converted" for i in tg["issues"]) and tg["observed_on"] == "2026-09-22"
    report_id = db().execute("SELECT id FROM lab_reports WHERE document_id = ?", (row["source_id"],)).fetchone()[0]
    r = team.post(f"/api/patients/{row['patient_id']}/reports/{report_id}/results", json={"report_date": "2026-09-22", "results": [
        {"test_name": "Total cholesterol", "value": "182", "unit": "mg/dL"},
        {"test_name": "LDL cholesterol", "value": "140", "unit": "mg/dL"},
        {"test_name": "HDL cholesterol", "value": "46", "unit": "mg/dL"}]})
    assert r.status_code == 200, r.text
    v = team.get(f"/api/patients/{row['patient_id']}/extraction/document/{row['source_id']}").json()
    status = {f["analyte"]: f["status"] for f in v["fields"]}
    assert status == {"total_cholesterol": "verified", "ldl": "corrected", "hdl": "verified", "triglycerides": "rejected"}
    assert v["status"] == "corrected"


def test_photos_without_ocr_go_to_a_person_and_nothing_is_suggested(make_client):
    patient = make_client()
    login_patient(patient, "P-1001")
    send(patient, [("rx.png", png(), "image/png")], kind="prescription")
    pipeline.run_pending()
    row = latest("document")
    assert (row["status"], row["review_reason"]) == ("needs_review", "ocr_not_configured")
    assert db().execute("SELECT COUNT(*) FROM extracted_fields WHERE processing_id = ?", (row["id"],)).fetchone()[0] == 0


def test_handwriting_or_unclear_text_gives_no_suggestions(make_client, monkeypatch):
    monkeypatch.setattr(sarvam, "configured", lambda: True)
    monkeypatch.setattr(sarvam, "ocr", lambda data, ct, **k: (["~ r. lo 1i // a ,, Hb 7 ~ ~ x' ;; mg"], "job-1"))
    patient = make_client()
    login_patient(patient, "P-1001")
    send(patient, [("note.png", png(), "image/png")], kind="lab_report", test="hba1c")
    pipeline.run_pending()
    row = latest("external_report")
    assert (row["status"], row["review_reason"]) == ("needs_review", "handwritten_or_unclear")
    assert db().execute("SELECT COUNT(*) FROM extracted_fields WHERE processing_id = ?", (row["id"],)).fetchone()[0] == 0
    team = make_client()
    login_clinician(team, PRIYA)
    v = team.get(f"/api/patients/{row['patient_id']}/extraction/external_report/{row['source_id']}").json()
    assert v["reason_text"] == "Handwritten or unclear - clinical team verification required" and v["fields"] == []


def test_ocr_failure_is_recorded_and_a_person_can_retry(make_client, monkeypatch):
    monkeypatch.setattr(sarvam, "configured", lambda: True)

    def down(*a, **k):
        raise sarvam.OcrError("The OCR service took too long.")
    monkeypatch.setattr(sarvam, "ocr", down)
    patient = make_client()
    login_patient(patient, "P-1001")
    send(patient, [("scan.png", png(), "image/png")], kind="lab_report", test="hba1c")
    pipeline.run_pending()
    row = latest("external_report")
    assert (row["status"], row["error"], row["attempts"]) == ("failed", "The OCR service took too long.", 1)
    monkeypatch.setattr(sarvam, "ocr", lambda data, ct, **k: (["SAMPLE LAB\nCollected: 20/09/2026\nHbA1c 6.8 % 4.0-5.6"], "job-2"))
    team = make_client()
    login_clinician(team, PRIYA)
    v = team.post(f"/api/patients/{row['patient_id']}/extraction/{row['id']}/retry").json()
    assert v["status"] == "extraction_completed" and v["attempts"] == 2 and v["fields"][0]["method"] == "ocr_sarvam"
    assert v["fields"][0]["confidence"] == "medium"                # OCR is never "high"


def test_conflicts_with_the_record_and_repeated_files_are_flagged(make_client, seeded_conn):
    conn, ids = seeded_conn
    from app import repo
    repo.insert_event(conn, ids["P-1001"], "lab", date(2026, 9, 20), "hospital_internal", "high", "resulted", "Clinic",
                      name="HbA1c", value=8.0, unit="%")
    conn.commit()
    patient = make_client()
    login_patient(patient, "P-1001")
    pdf = text_pdf(HBA1C)
    send(patient, [("a.pdf", pdf, "application/pdf")], kind="lab_report", test="hba1c")
    again = patient.post("/api/me/reports", data={"kind": "lab_report", "test": "hba1c"},
                         files=[("files", ("a-again.pdf", pdf, "application/pdf"))])
    assert again.status_code == 409 and again.json()["detail"]["code"] == "already_sent"     # ask, don't resend
    send(patient, [("a-again.pdf", pdf, "application/pdf")], kind="lab_report", test="fpg", confirm_mismatch="true")
    pipeline.run_pending()
    row = latest("external_report")
    codes = {r[0]: r[1] for r in db().execute("SELECT code, severity FROM validation_issues WHERE processing_id = ?", (row["id"],))}
    assert codes["conflicting_result"] == "error" and codes["duplicate_document"] == "warning"
    assert (row["status"], row["review_reason"]) == ("needs_review", "issues_found")


def test_states_only_move_forward_and_a_decision_is_final(make_client):
    patient = make_client()
    login_patient(patient, "P-1001")
    send(patient, [("rx.png", png(), "image/png")], kind="prescription")
    row = latest("document")
    c = db()
    with pytest.raises(sqlite3.IntegrityError, match="not allowed"):
        c.execute("UPDATE document_processing SET status = 'extraction_completed' WHERE id = ?", (row["id"],))
    c.rollback()
    team = make_client()
    login_clinician(team, PRIYA)
    assert team.post(f"/api/patients/{row['patient_id']}/documents/{row['source_id']}/review", json={"status": "confirmed"}).status_code == 200
    assert latest("document")["status"] == "verified"                # the team decided before the machine read it
    with pytest.raises(sqlite3.IntegrityError, match="not allowed"):
        c.execute("UPDATE document_processing SET status = 'processing' WHERE id = ?", (row["id"],))
    c.rollback()
    assert pipeline.process(row["id"]) == "verified"                # a decided upload is never read again


def test_uploads_from_before_are_picked_up(seeded_conn):
    conn, _ = seeded_conn
    conn.execute("DELETE FROM document_processing")
    conn.commit()
    assert pipeline.backfill() == 1                                 # the demo's pending lipid PDF
    assert pipeline.backfill() == 0


# ------------------------------------------------------------------ the Sarvam client (no real network)

def test_sarvam_flow_and_the_key_never_reaches_the_download_host(monkeypatch):
    monkeypatch.setenv("SARVAM_API_KEY", "test-key")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("document.json", "{}")
        z.writestr("metadata/page_002.json", json.dumps({"blocks": [{"text": "LDL 104 mg/dL"}]}))
        z.writestr("metadata/page_001.json", json.dumps({"blocks": [{"text": "HbA1c 7.2 %", "bbox": [1, 2, 3, 4]},
                                                                     {"children": [{"content": "Collected: 20/09/2026"}]}]}))
    seen = []

    def handler(request: httpx.Request):
        seen.append((request.method, request.url.host, request.url.path, request.headers.get("api-subscription-key")))
        if request.url.path == "/doc-ai/v1/job/digitise":
            return httpx.Response(200, json={"job_id": "J1", "status": "pending"})
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={"job_id": "J1", "status": "completed"})
        if request.url.path.endswith("/download-url"):
            return httpx.Response(200, json={"method": "GET", "url": "https://storage.example.com/out.zip?sig=1"})
        return httpx.Response(200, content=archive.getvalue())
    pages, job = sarvam.ocr(b"%PDF-1.4", "application/pdf", transport=httpx.MockTransport(handler), poll_seconds=0)
    assert job == "J1" and pages == ["HbA1c 7.2 %\nCollected: 20/09/2026", "LDL 104 mg/dL"]
    assert [s[3] for s in seen if s[1] == "api.sarvam.ai"] == ["test-key"] * 3
    assert [s for s in seen if s[1] == "storage.example.com"][0][3] is None


# ------------------------------------------------------------------ HbA1c is recorded in % only

def test_hba1c_is_saved_in_percent_only(make_client):
    patient = make_client()
    login_patient(patient, "P-1001")
    send(patient, [("kidney.pdf", text_pdf([["SAMPLE LAB", "Collected: 20/09/2026"]]), "application/pdf")], kind="lab_report", test="kft")
    row = latest("document")
    team = make_client()
    login_clinician(team, PRIYA)
    report_id = db().execute("SELECT id FROM lab_reports WHERE document_id = ?", (row["source_id"],)).fetchone()[0]
    url = f"/api/patients/{row['patient_id']}/reports/{report_id}/results"
    wrong = team.post(url, json={"results": [{"test_name": "HbA1c", "value": "24", "unit": "mmol/L"}]})
    assert wrong.status_code == 422 and "% only" in wrong.json()["detail"]
    ok = team.post(url, json={"results": [{"test_name": "HbA1c", "value": "7.1", "unit": ""}, {"test_name": "Creatinine", "value": "1.1", "unit": "mg/dL"}]})
    assert ok.status_code == 200, ok.text
    units = dict(db().execute("SELECT test_name, unit FROM lab_test_results WHERE report_id = ?", (report_id,)).fetchall())
    assert units == {"HbA1c": "%", "Creatinine": "mg/dL"}            # no unit given -> %; other tests untouched


# ------------------------------------------------------------------ the report does not match the chosen test

def test_matching_the_chosen_test():
    from app.extraction import match
    lipid = ["SAMPLE LAB", "Total Cholesterol 182 mg/dL\nHDL Cholesterol 46 mg/dL\nTriglycerides 150 mg/dL"]
    assert match.check("lipid", "lab_report", lipid).result == "match"
    assert (match.check("kft", "lab_report", lipid).result, match.check("kft", "lab_report", lipid).found) == ("mismatch", ["Lipid"])
    assert match.check("kft", "lab_report", ["RENAL FUNCTION TEST", "Blood Urea 28 mg/dL"]).result == "match"   # its words count
    assert match.check("eye_foot", "lab_report", ["Fundus examination: no retinopathy. Monofilament test normal."]).result == "match"
    assert match.check("other", "lab_report", lipid).result == "unknown"          # nothing to compare with
    assert match.check("kft", "lab_report", ["~ r. lo 1i // a ,, ~"]).result == "unknown"   # unreadable: a person decides
    assert match.check("hba1c", "lab_report", ["Rx", "1. Tab Metformin 500 mg 1-0-1", "2. Tab Glimepiride 2 mg OD"]).found == ["Prescription"]
    assert match.check(None, "prescription", lipid).result == "mismatch"


def test_a_wrong_report_is_caught_at_upload_and_checked_by_the_team(make_client):
    patient = make_client()
    login_patient(patient, "P-1001")
    files = [("files", ("kidney.pdf", text_pdf(LIPID), "application/pdf"))]
    first = patient.post("/api/me/reports", data={"kind": "lab_report", "test": "kft"}, files=files)
    assert first.status_code == 409
    assert first.json()["detail"] == {"code": "test_mismatch", "found": ["Lipid"], "message": "Not a KFT report · Lipid found"}
    assert latest("document") is None or latest("document")["expected_test"] != "kft"          # nothing was saved
    anyway = patient.post("/api/me/reports", data={"kind": "lab_report", "test": "kft", "confirm_mismatch": "true"}, files=files)
    assert anyway.status_code == 200
    row = latest("document")
    assert (row["status"], row["review_reason"], row["patient_override"]) == ("needs_review", "issues_found", "test_mismatch")
    team = make_client()
    login_clinician(team, PRIYA)
    v = team.get(f"/api/patients/{row['patient_id']}/extraction/document/{row['source_id']}").json()
    assert v["mismatch"] == "Not a KFT report · Lipid found · sent anyway" and v["patient_override"] == "test_mismatch"
    item = next(i for i in team.get("/api/pending-items").json() if i.get("file") and i["file"]["id"] == row["source_id"])
    assert item["processing"]["mismatch"].startswith("Not a KFT report")
    r = team.post(f"/api/patients/{row['patient_id']}/extraction/{row['id']}/request-reupload")
    assert r.status_code == 200 and r.json()["message"] == "The KFT report you sent does not match. Please upload the correct KFT report."
    assert latest("document")["status"] == "rejected"
    assert db().execute("SELECT review_status FROM patient_documents WHERE id = ?", (row["source_id"],)).fetchone()[0] == "rejected"
    note = next(n for n in patient.get("/api/me/notifications").json() if n["kind"] == "reupload_request")
    assert "upload the correct KFT report" in note["message"]
    assert team.post(f"/api/patients/{row['patient_id']}/extraction/{row['id']}/request-reupload").status_code == 409


def test_a_matching_report_is_read_during_the_upload(make_client):
    patient = make_client()
    login_patient(patient, "P-1001")
    send(patient, [("lipid.pdf", text_pdf(LIPID), "application/pdf")], kind="lab_report", test="lipid")
    assert latest("document")["status"] == "extraction_completed"            # no second read in the background
    send(patient, [("hba1c.pdf", text_pdf(LIPID), "application/pdf")], kind="lab_report", test="hba1c", confirm_mismatch="true")
    row = latest("external_report")
    team = make_client()
    login_clinician(team, PRIYA)
    team.post(f"/api/patients/{row['patient_id']}/extraction/{row['id']}/request-reupload")
    assert db().execute("SELECT status FROM external_reports WHERE id = ?", (row["source_id"],)).fetchone()[0] == "rejected"


# ------------------------------------------------------------------ the patient asks to send a report again

def test_a_patient_asks_to_resend_and_the_team_decides(make_client):
    patient = make_client()
    login_patient(patient, "P-1001")
    send(patient, [("hba1c.pdf", text_pdf(HBA1C), "application/pdf")], kind="lab_report", test="hba1c")
    blocked = patient.post("/api/me/reports", data={"kind": "lab_report", "test": "hba1c"},
                           files=[("files", ("new.pdf", text_pdf(HBA1C), "application/pdf"))])
    assert blocked.status_code == 409
    detail = blocked.json()["detail"]
    assert (detail["code"], detail["message"]) == ("already_sent", "HbA1c report already sent · Checking")
    mine = patient.get("/api/me/reupload-requests").json()
    hba1c = next(u for u in mine["waiting"] if u["label"] == "HbA1c")
    assert hba1c["source_id"] == detail["source_id"] and hba1c["requested"] is False
    r = patient.post("/api/me/reupload-requests", json={"source_kind": hba1c["source_kind"], "source_id": hba1c["source_id"],
                                                        "reason": "Sent the old report by mistake"})
    assert r.status_code == 201 and r.json()["status"] == "requested"
    assert patient.post("/api/me/reupload-requests", json={"source_kind": hba1c["source_kind"],
                                                           "source_id": hba1c["source_id"]}).status_code == 409   # one at a time
    team = make_client()
    login_clinician(team, PRIYA)
    item = next(i for i in team.get("/api/pending-items").json() if i["kind"] == "reupload")
    assert item["label"] == "Re-upload request · HbA1c" and item["detail"] == "Sent the old report by mistake" and item["group"] == "request"
    pid = item["patient_id"]
    ok = team.post(f"/api/patients/{pid}/reupload-requests/{item['id']}/accept")
    assert ok.status_code == 200 and ok.json()["status"] == "accepted" and ok.json()["decided_by_name"] == "Dr. Priya Nair"
    assert db().execute("SELECT status FROM external_reports WHERE id = ?", (hba1c["source_id"],)).fetchone()[0] == "rejected"
    assert any(n["kind"] == "reupload_accepted" for n in patient.get("/api/me/notifications").json())
    send(patient, [("new.pdf", text_pdf(HBA1C), "application/pdf")], kind="lab_report", test="hba1c")   # allowed again
    assert team.post(f"/api/patients/{pid}/reupload-requests/{item['id']}/accept").status_code == 409


def test_a_request_can_be_declined_with_a_note(make_client):
    patient = make_client()
    login_patient(patient, "P-1001")
    send(patient, [("lipid.pdf", text_pdf(LIPID), "application/pdf")], kind="lab_report", test="lipid")
    lipid = next(u for u in patient.get("/api/me/reupload-requests").json()["waiting"] if u["label"] == "Lipid")
    rid = patient.post("/api/me/reupload-requests", json={"source_kind": lipid["source_kind"], "source_id": lipid["source_id"]}).json()["id"]
    team = make_client()
    login_clinician(team, PRIYA)
    pid = db().execute("SELECT id FROM patients WHERE patient_code = 'P-1001'").fetchone()[0]
    r = team.post(f"/api/patients/{pid}/reupload-requests/{rid}/decline", json={"note": "This report is fine."})
    assert r.status_code == 200 and r.json()["status"] == "declined"
    note = next(n for n in patient.get("/api/me/notifications").json() if n["kind"] == "reupload_declined")
    assert note["message"] == "Your request to re-send the Lipid report was declined. This report is fine."
    assert db().execute("SELECT review_status FROM patient_documents WHERE id = ?", (lipid["source_id"],)).fetchone()[0] == "pending"
