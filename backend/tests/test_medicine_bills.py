"""Medicine Purchase Verification: the patient uploads the bill (compulsory), the care team approves or rejects it,
the refill row follows the bill, the missed-refill check waits while a bill is pending, and the patient is told."""

import os
import sqlite3
from datetime import date

import pytest

from app import repo
from app.detection import missed_refill
from tests.conftest import TODAY, login_patient, patient_ids

BILL = ("bill.jpg", b"\xff\xd8 pharmacy bill", "image/jpeg")


@pytest.fixture()
def ids(priya):
    return patient_ids(priya)


@pytest.fixture()
def ramesh(make_client):
    c = make_client()
    login_patient(c, "P-1001")
    return c


def upload(client, file=BILL):
    return client.post("/api/me/medicine-bills", files={"file": file} if file else None)


def db():
    c = sqlite3.connect(os.environ["UC2_DB_PATH"])
    c.row_factory = sqlite3.Row
    return c


def bill_rows(bill_id):
    c = db()
    doc = dict(c.execute("SELECT * FROM external_reports WHERE id = ?", (bill_id,)).fetchone())
    ev = dict(c.execute("SELECT * FROM clinical_events WHERE linked_document_id = ?", (bill_id,)).fetchone())
    c.close()
    return doc, ev


def refill_signal(priya, pid):
    p = priya.get(f"/api/patients/{pid}").json()
    return next(s for s in p["assessment"]["signals"] if s["key"] == "missed_refill")


# ------------------------------------------------ step 2: the patient's upload - the file is compulsory

def test_submission_without_a_file_is_rejected_by_the_backend(ramesh):
    assert upload(ramesh, file=None).status_code == 422
    assert ramesh.post("/api/me/medicine-bills", data={"note": "I bought it"}).status_code == 422
    assert upload(ramesh, file=("empty.jpg", b"", "image/jpeg")).status_code == 422
    assert upload(ramesh, file=("x.txt", b"hello", "text/plain")).status_code == 415
    assert ramesh.get("/api/me/medicine-bills").json() == []


def test_upload_creates_pending_bill_and_pending_refill_with_server_time(ramesh):
    r = upload(ramesh)
    assert r.status_code == 200, r.text
    assert r.json()["message"] == "Your bill has been submitted. Your care team will verify it shortly."
    doc, ev = bill_rows(r.json()["id"])
    assert (doc["document_type"], doc["status"], doc["uploaded_by"], doc["disease"]) == \
        ("medicine_bill", "pending_review", "patient", "Diabetes")
    assert "T" in doc["upload_date"]                                       # a server timestamp, never typed
    assert (ev["event_type"], ev["status"], ev["source"], ev["linked_document_id"]) == \
        ("refill", "pending_review", "patient_upload", doc["id"])
    mine = ramesh.get("/api/me/medicine-bills").json()
    assert [(b["status_text"], b["disease"]) for b in mine] == [("Pending Review", "Diabetes")]


def test_a_refill_can_no_longer_be_claimed_as_text_only(ramesh):
    assert ramesh.post("/api/me/refill", json={"taken": True}).status_code == 422
    assert ramesh.post("/api/me/refill", json={"taken": False}).status_code == 200     # "not collected yet" still works


# ------------------------------------------------ steps 3 + 4: queue, approve, reject

def test_queue_is_scoped_and_bills_stay_out_of_lab_flows(priya, ramesh, ids, make_client):
    bid = upload(ramesh).json()["id"]
    queue = priya.get("/api/medicine-bills/pending").json()
    assert [(q["id"], q["patient_code"]) for q in queue] == [(bid, "P-1001")]
    assert priya.get("/api/external-reports/pending").json() == []                   # not a lab report
    assert priya.get(f"/api/patients/{ids['P-1001']}/linkable-reports", params={"all": True}).json()["reports"] == []
    assert any(r["kind"] == "bill" and r["id"] == bid for r in priya.get("/api/worklist").json()["reviews"])
    from tests.conftest import login_clinician
    karan = make_client()
    login_clinician(karan, "CLN-KRNB58")
    assert karan.get("/api/medicine-bills/pending").json() == []
    assert karan.post(f"/api/patients/{ids['P-1001']}/medicine-bills/{bid}/approve", json={}).status_code == 404


def test_approve_without_purchase_date_uses_the_upload_time(priya, ramesh, ids):
    pid = ids["P-1001"]
    bid = upload(ramesh).json()["id"]
    doc0, ev0 = bill_rows(bid)
    r = priya.post(f"/api/patients/{pid}/medicine-bills/{bid}/approve", json={})
    assert r.status_code == 200, r.text
    doc, ev = bill_rows(bid)
    assert doc["status"] == "reviewed_approved" and doc["reviewed_by"] and doc["purchase_date"] is None
    assert (ev["status"], ev["confidence"]) == ("active", "high") and ev["reviewed_by"] == doc["reviewed_by"]
    assert ev["reviewed_date"] and ev["effective_date"] == ev0["effective_date"]      # the upload day
    assert priya.get("/api/medicine-bills/pending").json() == []
    assert priya.post(f"/api/patients/{pid}/medicine-bills/{bid}/reject", json={"reason": "unclear"}).status_code == 409


def test_approve_with_purchase_date_from_the_bill(priya, ramesh, ids):
    pid = ids["P-1001"]
    bid = upload(ramesh).json()["id"]
    upload_day = bill_rows(bid)[1]["effective_date"]
    assert priya.post(f"/api/patients/{pid}/medicine-bills/{bid}/approve", json={"purchase_date": "2099-01-01"}).status_code == 422
    assert priya.post(f"/api/patients/{pid}/medicine-bills/{bid}/approve", json={"purchase_date": "2020-01-01"}).status_code == 422
    purchase = date.fromisoformat(upload_day).replace(day=1).isoformat()
    assert priya.post(f"/api/patients/{pid}/medicine-bills/{bid}/approve", json={"purchase_date": purchase}).status_code == 200
    doc, ev = bill_rows(bid)
    assert doc["purchase_date"] == purchase and ev["effective_date"] == purchase


@pytest.mark.parametrize("body, stored", [
    ({"reason": "unclear"}, "Bill image unclear or unreadable"),
    ({"reason": "medicine_mismatch"}, "Bill does not match the prescribed medicine"),
    ({"reason": "date_mismatch"}, "Bill date does not match upload"),
    ({"reason": "other", "note": "This is a grocery bill"}, "This is a grocery bill"),
])
def test_reject_stores_the_reason(priya, ramesh, ids, body, stored):
    pid = ids["P-1001"]
    bid = upload(ramesh).json()["id"]
    r = priya.post(f"/api/patients/{pid}/medicine-bills/{bid}/reject", json=body)
    assert r.status_code == 200, r.text
    doc, ev = bill_rows(bid)
    assert (doc["status"], doc["reject_code"], doc["reject_reason"]) == ("reviewed_rejected", body["reason"], stored)
    assert (ev["status"], ev["confidence"]) == ("rejected", "low") and ev["reviewed_by"]
    assert priya.get("/api/medicine-bills/pending").json() == []


def test_reject_needs_a_reason(priya, ramesh, ids):
    pid = ids["P-1001"]
    bid = upload(ramesh).json()["id"]
    url = f"/api/patients/{pid}/medicine-bills/{bid}/reject"
    assert priya.post(url, json={}).status_code == 422
    assert priya.post(url, json={"reason": "because"}).status_code == 422
    assert priya.post(url, json={"reason": "other"}).status_code == 422                # "Other" needs a note
    assert priya.post(url, json={"reason": "other", "note": " "}).status_code == 422
    assert bill_rows(bid)[0]["status"] == "pending_review"


# ------------------------------------------------ step 1: the database keeps bill and refill in step

def test_database_keeps_bill_and_refill_together(priya, ramesh, ids):
    pid = ids["P-1001"]
    bid = upload(ramesh).json()["id"]
    c = db()
    c.execute("PRAGMA foreign_keys = ON")
    bad = [
        ("UPDATE clinical_events SET status = 'active' WHERE linked_document_id = ?", (bid,)),       # refill alone
        ("UPDATE clinical_events SET linked_document_id = NULL WHERE linked_document_id = ?", (bid,)),
        ("UPDATE external_reports SET status = 'reviewed_rejected', reviewed_by = 'x', reviewed_at = 'y' WHERE id = ?", (bid,)),
        ("UPDATE external_reports SET status = 'reviewed' , reviewed_by = 'x', reviewed_at = 'y' WHERE id = ?", (bid,)),
        ("INSERT INTO clinical_events (patient_id, event_type, name, value, unit, effective_date, source, confidence, status, "
         "asserted_by, linked_document_id, created_at) VALUES (?, 'refill', 'M', 30, 'days', '2026-09-20', 'patient_upload', "
         "'medium', 'active', 'x', ?, '2026-09-20')", (pid, bid)),                                   # born "active"
    ]
    for sql, args in bad:
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(sql, args)
        c.rollback()
    c.close()
    doc, ev = bill_rows(bid)
    assert doc["status"] == ev["status"] == "pending_review"


# ------------------------------------------------ step 5: the Silent Risk Detector

def test_pending_bill_does_not_raise_a_missed_refill_flag(priya, ramesh, ids):
    pid = ids["P-1001"]
    assert refill_signal(priya, pid)["fired"] is True                  # Ramesh's demo refill is 22 days late
    upload(ramesh)
    s = refill_signal(priya, pid)
    assert s["fired"] is False and s["short"] == "refill bill awaiting review"


def test_approved_bill_resets_the_clock_rejected_bill_does_not(priya, ramesh, ids):
    pid = ids["P-1001"]
    bid = upload(ramesh).json()["id"]
    priya.post(f"/api/patients/{pid}/medicine-bills/{bid}/reject", json={"reason": "unclear"})
    assert refill_signal(priya, pid)["fired"] is True                  # as if nothing had been sent
    bid2 = upload(ramesh).json()["id"]
    # the bill shows it was bought on 6 Sep: 2 days after the supply ran out (4 Sep) - on time, next due 6 Oct
    priya.post(f"/api/patients/{pid}/medicine-bills/{bid2}/approve", json={"purchase_date": "2026-09-06"})
    s = refill_signal(priya, pid)
    assert s["fired"] is False and s["short"] == "refills on time"


def test_missed_refill_rule_directly():
    from tests.test_detection import ev, patient
    base = ev("refill", 45)                                             # 30-day supply ran out 15 days ago
    assert missed_refill(patient(base), TODAY).fired
    # bill for a purchase 20 days ago (5 days after running out):
    pending = ev("refill", 20, status="pending_review", source="patient_upload")
    rejected = ev("refill", 20, status="rejected", source="patient_upload")
    approved = ev("refill", 20, status="active", source="patient_upload")
    assert missed_refill(patient(base, pending), TODAY).short == "refill bill awaiting review"   # waits, no flag
    assert missed_refill(patient(base, rejected), TODAY).fired                                   # clock kept running
    assert not missed_refill(patient(base, approved), TODAY).fired                               # clock reset


# ------------------------------------------------ step 6: notifications (e-mail + in-app, the same text)

def test_patient_is_told_on_approve_and_reject(priya, ramesh, ids):
    pid = ids["P-1001"]
    c = db()
    c.execute("UPDATE patients SET email = 'ramesh@example.com' WHERE id = ?", (pid,))
    c.commit()
    c.close()
    ok = upload(ramesh).json()["id"]
    bad = upload(ramesh).json()["id"]
    priya.post(f"/api/patients/{pid}/medicine-bills/{ok}/approve", json={})
    r = priya.post(f"/api/patients/{pid}/medicine-bills/{bad}/reject", json={"reason": "unclear"}).json()
    assert r["notification"]["email_to"] == "ramesh@example.com"
    notes = ramesh.get("/api/me/notifications").json()
    msgs = {n["kind"]: n for n in notes}
    assert msgs["bill_approved"]["message"].startswith("Your medicine bill from ")
    assert msgs["bill_approved"]["message"].endswith("has been verified. Thank you for keeping your record up to date.")
    assert msgs["bill_rejected"]["message"].endswith(
        "could not be verified. Reason: Bill image unclear or unreadable. Please upload a clearer bill or contact your care team.")
    assert {n["email_status"] for n in notes} == {"simulated"}
    tags = {b["id"]: b["status_text"] for b in ramesh.get("/api/me/medicine-bills").json()}
    assert tags == {ok: "Approved", bad: "Rejected — Bill image unclear or unreadable"}
    assert ramesh.post(f"/api/me/notifications/{notes[0]['id']}/read").status_code == 200
    assert next(n for n in ramesh.get("/api/me/notifications").json() if n["id"] == notes[0]["id"])["read_at"]


def test_no_email_on_file_is_recorded_not_hidden(priya, ramesh, ids):
    pid = ids["P-1001"]
    bid = upload(ramesh).json()["id"]
    priya.post(f"/api/patients/{pid}/medicine-bills/{bid}/approve", json={})
    assert ramesh.get("/api/me/notifications").json()[0]["email_status"] in ("no_email_on_file", "simulated")


def test_all_bills_list_every_status_for_own_patients_only(priya, ramesh, ids, make_client):
    pid = ids["P-1001"]
    pending, ok, bad = (upload(ramesh).json()["id"] for _ in range(3))
    priya.post(f"/api/patients/{pid}/medicine-bills/{ok}/approve", json={})
    priya.post(f"/api/patients/{pid}/medicine-bills/{bad}/reject", json={"reason": "date_mismatch"})
    every = {b["id"]: (b["status"], b["reject_reason"]) for b in priya.get("/api/medicine-bills").json()}
    assert every == {pending: ("pending_review", None), ok: ("reviewed_approved", None),
                     bad: ("reviewed_rejected", "Bill date does not match upload")}
    from tests.conftest import login_clinician
    karan = make_client()
    login_clinician(karan, "CLN-KRNB58")
    assert karan.get("/api/medicine-bills").json() == []
