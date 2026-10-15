"""Send report: one way for a patient to send a paper. Prescription, or a lab report of a chosen type; one or several
pages merged into one file; the date recorded at upload. Each type lands where the care team reviews it."""

import io
import os
import sqlite3

from tests.conftest import PRIYA, TODAY, login_clinician, login_patient

PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n" \
      b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\nxref\n0 4\n0000000000 65535 f \n" \
      b"0000000009 00000 n \n0000000052 00000 n \n0000000101 00000 n \ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n164\n%%EOF\n"


def _png() -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (40, 60), (230, 230, 230)).save(out, format="PNG")
    return out.getvalue()


def _db():
    return sqlite3.connect(os.environ["UC2_DB_PATH"])


def send(client, files, **form):
    return client.post("/api/me/reports", data=form, files=[("files", f) for f in files])


def test_the_list_is_the_same_for_everyone(make_client):
    c = make_client()
    data = c.get("/api/report-tests").json()
    keys = [t["key"] for t in data["tests"]]
    assert keys == ["hba1c", "fpg", "ppbs", "ogtt", "kft", "lipid", "eye_foot", "urine_ketone", "other"]
    assert all(t["short"] and t["full"] and t["why"] for t in data["tests"])
    assert data["aliases"]["Fasting Sugar"] == "fpg"


def test_a_lab_report_needs_its_test(make_client):
    c = make_client()
    login_patient(c, "P-1001")
    r = send(c, [("r.pdf", PDF, "application/pdf")], kind="lab_report")
    assert r.status_code == 422 and "choose the test" in r.json()["detail"]
    assert send(c, [("r.pdf", PDF, "application/pdf")], kind="lab_report", test="other").status_code == 422
    assert send(c, [("r.pdf", PDF, "application/pdf")], kind="lab_report", test="nonsense").status_code == 422


def test_single_value_tests_go_to_the_split_screen_review_dated_today(make_client):
    c = make_client()
    login_patient(c, "P-1001")
    r = send(c, [("hba1c.pdf", PDF, "application/pdf")], kind="lab_report", test="hba1c")
    assert r.status_code == 200 and r.json()["message"] == "Sent. Your care team will check it."
    row = _db().execute("SELECT test_type, test_date, status FROM external_reports ORDER BY upload_date DESC LIMIT 1").fetchone()
    assert row == ("HbA1c", TODAY.isoformat(), "pending_review")          # the date is recorded, never typed
    team = make_client()
    login_clinician(team, PRIYA)
    pending = [i for i in team.get("/api/pending-items").json() if i["kind"] == "external"]
    assert any(i["label"].startswith("HbA1c") for i in pending)


def test_other_tests_become_lab_reports_and_several_pages_become_one_pdf(make_client):
    c = make_client()
    login_patient(c, "P-1001")
    r = send(c, [("p1.png", _png(), "image/png"), ("p2.pdf", PDF, "application/pdf"), ("p3.png", _png(), "image/png")],
             kind="lab_report", test="lipid")
    assert r.status_code == 200 and r.json()["pages"] == 3
    doc = _db().execute("SELECT id, description, content_type, file_name, storage_key FROM patient_documents "
                        "WHERE file_name = 'report-3-pages.pdf'").fetchone()
    assert doc[1:4] == ("Lipid", "application/pdf", "report-3-pages.pdf")
    from pypdf import PdfReader
    from app import storage
    assert len(PdfReader(io.BytesIO(storage.read(doc[4]))).pages) == 3
    assert _db().execute("SELECT report_type, report_date FROM lab_reports WHERE document_id = ?", (doc[0],)).fetchone() == \
        ("Lipid", TODAY.isoformat())
    named = send(c, [("v.pdf", PDF, "application/pdf")], kind="lab_report", test="other", test_name="Vitamin D")
    assert named.status_code == 200
    assert _db().execute("SELECT description FROM patient_documents WHERE file_name = 'v.pdf'").fetchone()[0] == "Vitamin D"


def test_prescription_and_the_real_file_type(make_client):
    c = make_client()
    login_patient(c, "P-1001")
    assert send(c, [("rx.png", _png(), "image/png")], kind="prescription").status_code == 200
    fake = send(c, [("rx.pdf", b"not really a pdf", "application/pdf")], kind="prescription")
    assert fake.status_code == 415                                   # the bytes decide, not the name
    too_many = send(c, [("p.pdf", PDF, "application/pdf")] * 11, kind="prescription")
    assert too_many.status_code == 422
    stranger = make_client()
    assert send(stranger, [("rx.png", _png(), "image/png")], kind="prescription").status_code == 401
