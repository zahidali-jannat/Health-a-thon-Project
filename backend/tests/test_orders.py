"""Test Orders: the status machine, due-date rules, patient uploads and their verification, clinic-lab ingestion,
authorization, the FHIR view and reminders."""

import itertools
import os
import sqlite3
from datetime import date, timedelta

import pytest

from app import db, orders, repo
from tests.conftest import KARAN, TODAY, login_clinician, login_patient, patient_ids

PDF = ("report.pdf", b"%PDF-1.4 a lab report", "application/pdf")
NEXT_VISIT_1001 = TODAY + timedelta(days=14)          # Ramesh's next clinic visit in the demo data: 9 Oct 2026


@pytest.fixture()
def ids(priya):
    return patient_ids(priya)


@pytest.fixture()
def tests_by_code(priya):
    return {t["code"]: t["id"] for t in priya.get("/api/test-catalog").json()["tests"]}


def patient(make_client, code):
    c = make_client()
    login_patient(c, code)
    return c


def order(priya, pid, *items, ack=True, key=None, status=201):
    r = priya.post(f"/api/patients/{pid}/test-orders", json={"items": list(items), "acknowledge_warnings": ack},
                   headers={"Idempotency-Key": key} if key else {})
    assert r.status_code == status, r.text
    return r.json()


def items_of(priya, pid):
    return [i for s in priya.get(f"/api/patients/{pid}/test-orders").json()["sets"] for i in s["items"]]


def item_named(priya, pid, name):
    return next(i for i in items_of(priya, pid) if i["name"] == name)


def upload(client, item_id, test_date=TODAY, lab="City Diagnostics", file=PDF, key=None, **extra):
    return client.post(f"/api/me/test-orders/{item_id}/upload", files={"file": file},
                       data={"test_date": str(test_date), "lab_name": lab, **extra},
                       headers={"Idempotency-Key": key} if key else {})


# ------------------------------------------------------------------ the status machine: every pair

STATUSES = ["ordered", "sample_collected", "result_received", "submitted_by_patient", "verified", "closed",
            "rejected", "cancelled", "not_done"]
LEGAL = {("ordered", "sample_collected"), ("ordered", "result_received"), ("ordered", "submitted_by_patient"),
         ("ordered", "cancelled"), ("ordered", "not_done"), ("sample_collected", "result_received"),
         ("sample_collected", "submitted_by_patient"), ("sample_collected", "cancelled"), ("sample_collected", "not_done"),
         ("result_received", "verified"), ("result_received", "rejected"), ("submitted_by_patient", "verified"),
         ("submitted_by_patient", "rejected"), ("rejected", "sample_collected"), ("rejected", "result_received"),
         ("rejected", "submitted_by_patient"), ("rejected", "cancelled"), ("rejected", "not_done"), ("verified", "closed")}
PATH = {"ordered": [], "sample_collected": ["sample_collected"], "result_received": ["result_received"],
        "submitted_by_patient": ["submitted_by_patient"], "verified": ["result_received", "verified"],
        "closed": ["result_received", "verified", "closed"], "rejected": ["submitted_by_patient", "rejected"],
        "cancelled": ["cancelled"], "not_done": ["not_done"]}


def _new_item(conn, pid):
    test = orders.catalog(conn)[0]
    doctor = conn.execute("SELECT id FROM doctors LIMIT 1").fetchone()[0]
    user = conn.execute("SELECT id FROM clinical_users LIMIT 1").fetchone()[0]
    set_id, item_id = repo.new_id(), repo.new_id()
    conn.execute("INSERT INTO test_order_sets (id, patient_id, ordered_by_doctor_id, entered_by_user_id, created_at) "
                 "VALUES (?, ?, ?, ?, 'now')", (set_id, pid, doctor, user))
    conn.execute("INSERT INTO test_order_items (id, order_set_id, patient_id, test_catalog_id, due_by, created_at, updated_at) "
                 "VALUES (?, ?, ?, ?, ?, 'now', 'now')", (item_id, set_id, pid, test["id"], TODAY.isoformat()))
    report = repo.create_lab_report(conn, pid, "Lab", TODAY, "hospital_internal")
    result = repo.add_lab_result(conn, report, pid, "HbA1c", 7.0, "%", TODAY)
    return orders._load_item(conn, item_id), result


def _go(conn, item, to, result_id):
    orders.transition(conn, item, to, orders.CLINIC_LAB_ACTOR, "test", reason="because of the test",
                      extra={"lab_result_id": result_id} if to == "verified" else None)


def test_every_status_change_is_either_allowed_or_refused(seeded_conn, monkeypatch):
    conn, codes = seeded_conn
    monkeypatch.setattr(repo, "audit", lambda *a, **k: None)
    for frm, to in itertools.permutations(STATUSES, 2):
        item, result = _new_item(conn, codes["P-1002"])
        for step in PATH[frm]:
            _go(conn, item, step, result)
        if (frm, to) in LEGAL:
            _go(conn, item, to, result)
            assert orders._load_item(conn, item["id"])["status"] == to
        else:
            with pytest.raises(orders.OrderError) as e:
                _go(conn, item, to, result)
            assert e.value.code == "illegal_transition", (frm, to)
    conn.rollback()


def test_the_database_itself_refuses_an_illegal_change_and_a_missing_reason(seeded_conn):
    conn, codes = seeded_conn
    item, _ = _new_item(conn, codes["P-1002"])
    with pytest.raises(sqlite3.IntegrityError, match="not allowed"):
        conn.execute("UPDATE test_order_items SET status = 'closed', version = version + 1 WHERE id = ?", (item["id"],))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE test_order_items SET status = 'cancelled', version = version + 1 WHERE id = ?", (item["id"],))
    with pytest.raises(sqlite3.IntegrityError):                    # every change moves the version on
        conn.execute("UPDATE test_order_items SET priority = 'urgent' WHERE id = ?", (item["id"],))
    with pytest.raises(sqlite3.IntegrityError):                    # a due date is required
        conn.execute("UPDATE test_order_items SET due_by = NULL, version = version + 1 WHERE id = ?", (item["id"],))


# ------------------------------------------------------------------ due-date rules

def test_default_due_date_is_the_appointment_minus_the_buffer(priya, ids, tests_by_code):
    plan = priya.post(f"/api/patients/{ids['P-1001']}/test-orders/preview", json={"items": [{"test_catalog_id": tests_by_code["HBA1C"]}]}).json()
    assert plan["next_appointment"]["date"] == NEXT_VISIT_1001.isoformat() and plan["buffer_days"] == 3
    assert plan["items"][0]["due_by"] == (NEXT_VISIT_1001 - timedelta(days=3)).isoformat() and plan["ok"]
    assert plan["items"][0]["instructions"] == "No fasting needed. You can eat and drink as usual."


def test_due_after_the_appointment_is_refused_and_inside_the_buffer_warns(priya, ids, tests_by_code):
    pid = ids["P-1001"]
    late = order(priya, pid, {"test_catalog_id": tests_by_code["FBG"], "due_by": str(NEXT_VISIT_1001 + timedelta(days=1))}, status=422)
    assert late["detail"]["code"] == "after_appointment"
    tight = {"test_catalog_id": tests_by_code["FBG"], "due_by": str(NEXT_VISIT_1001 - timedelta(days=1))}
    held = order(priya, pid, tight, ack=False, status=409)
    assert held["detail"]["code"] == "confirm_warnings"
    assert held["detail"]["plan"]["items"][0]["warnings"][0]["code"] == "buffer_not_met"
    assert order(priya, pid, tight)["items"] == 1                      # allowed once the warning is acknowledged


def test_with_no_next_appointment_a_due_date_must_be_chosen(priya, tests_by_code):
    new = priya.post("/api/patients", json={"full_name": "New Patient", "phone": "9123456789", "sex": "F",
                                             "date_of_birth": "1970-01-01"}).json()
    pid = new.get("id") or new["patient"]["id"]
    plan = priya.post(f"/api/patients/{pid}/test-orders/preview", json={"items": [{"test_catalog_id": tests_by_code["HB"]}]}).json()
    assert plan["next_appointment"] is None and plan["items"][0]["errors"][0]["code"] == "due_required"
    assert order(priya, pid, {"test_catalog_id": tests_by_code["HB"], "due_by": str(TODAY + timedelta(days=10))})["items"] == 1


def test_duplicate_open_test_warns(priya, ids, tests_by_code):
    pid = ids["P-1001"]
    order(priya, pid, {"test_catalog_id": tests_by_code["HB"]})
    again = order(priya, pid, {"test_catalog_id": tests_by_code["HB"]}, ack=False, status=409)
    assert [w["code"] for w in again["detail"]["plan"]["items"][0]["warnings"]] == ["duplicate"]


def test_rescheduled_or_cancelled_appointments_are_flagged_never_changed(priya, ids, tests_by_code, make_client):
    pid = ids["P-1002"]                                           # next clinic visit: 25 Oct 2026
    lakshmi = patient(make_client, "P-1002")
    doctor = lakshmi.get("/api/me/appointments").json()["doctors"][0]["id"]
    day = (TODAY + timedelta(days=6)).isoformat()                 # 1 Oct
    slot = lakshmi.get(f"/api/me/appointments/slots?doctor_id={doctor}&date={day}").json()[0]
    assert lakshmi.post("/api/me/appointments", json={"slot_id": slot["id"]}).status_code == 201
    order(priya, pid, {"test_catalog_id": tests_by_code["HBA1C"]})
    item = item_named(priya, pid, "HbA1c")
    assert item["due_by"] == (TODAY + timedelta(days=3)).isoformat() and item["flags"] == []
    # the doctor is marked away for that slot: the appointment needs a new time
    block = {"start_date": day, "start_time": slot["start_time"], "end_time": slot["end_time"]}
    plan = priya.post(f"/api/doctors/{doctor}/unavailability/preview", json=block).json()
    assert priya.post(f"/api/doctors/{doctor}/unavailability", json={**block, "fingerprint": plan["fingerprint"],
                                                                      "checked": True}).status_code == 200
    after = item_named(priya, pid, "HbA1c")
    assert after["due_by"] == item["due_by"]                      # never silently changed
    assert [f["code"] for f in after["flags"]] == ["appointment_changed"]
    # an order due 20 Oct, then an appointment booked for 3 Oct: flagged as due after the next appointment
    order(priya, pid, {"test_catalog_id": tests_by_code["UACR"], "due_by": str(TODAY + timedelta(days=25))})
    later = (TODAY + timedelta(days=8)).isoformat()
    slot2 = lakshmi.get(f"/api/me/appointments/slots?doctor_id={doctor}&date={later}").json()[0]
    assert lakshmi.post("/api/me/appointments", json={"slot_id": slot2["id"]}).status_code == 201
    assert "after_next_appointment" in [f["code"] for f in item_named(priya, pid, "Urine albumin-creatinine ratio")["flags"]]


def test_edits_need_the_current_version_and_are_audited(priya, ids, tests_by_code):
    pid = ids["P-1001"]
    order(priya, pid, {"test_catalog_id": tests_by_code["HB"]})
    item = item_named(priya, pid, "Haemoglobin")
    ok = priya.patch(f"/api/test-order-items/{item['id']}", json={"version": item["version"], "priority": "urgent",
                                                                  "reason": "Low Hb last time"})
    assert ok.status_code == 200
    stale = priya.patch(f"/api/test-order-items/{item['id']}", json={"version": item["version"], "priority": "routine"})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale"
    fresh = item_named(priya, pid, "Haemoglobin")
    assert priya.post(f"/api/test-order-items/{item['id']}/cancel", json={"version": fresh["version"], "reason": "x"}).status_code == 422
    assert priya.post(f"/api/test-order-items/{item['id']}/cancel",
                      json={"version": fresh["version"], "reason": "Done at the last visit"}).json()["status"] == "cancelled"
    c = sqlite3.connect(os.environ["UC2_DB_PATH"])
    log = c.execute("SELECT action, actor_label, old_value, new_value, detail FROM audit_log WHERE resource_id = ? ORDER BY id",
                    (item["id"],)).fetchall()
    assert [r[0] for r in log] == ["TEST_ORDERED", "TEST_ORDER_EDITED", "TEST_ORDER_STATUS_CHANGED"]
    assert log[1][2:] == ('{"priority": "routine"}', '{"priority": "urgent"}', "Low Hb last time")
    assert log[2][2] == "ordered" and log[2][3] == "cancelled" and "Done at the last visit" in log[2][4]
    assert all("Dr. Priya Nair" in r[1] for r in log)


def test_create_is_idempotent(priya, ids, tests_by_code):
    pid = ids["P-1001"]
    body = {"test_catalog_id": tests_by_code["RBG"]}
    first = order(priya, pid, body, key="order-key-0001")
    second = order(priya, pid, body, key="order-key-0001")
    assert second["order_set_id"] == first["order_set_id"] and second["replayed"] is True
    assert len([i for i in items_of(priya, pid) if i["name"] == "Random glucose"]) == 1
    other = priya.post(f"/api/patients/{pid}/test-orders", json={"items": [{"test_catalog_id": tests_by_code["HB"]}]},
                       headers={"Idempotency-Key": "order-key-0001"})
    assert other.status_code == 422 and other.json()["detail"]["code"] == "idempotency_mismatch"


# ------------------------------------------------------------------ the patient's upload and its verification

def test_upload_links_to_the_item_and_verifying_it_completes_the_item(priya, ids, tests_by_code, make_client):
    pid = ids["P-1001"]
    order(priya, pid, {"test_catalog_id": tests_by_code["HB"]})
    item = item_named(priya, pid, "Haemoglobin")
    ramesh = patient(make_client, "P-1001")
    mine = ramesh.get("/api/me/test-orders").json()
    card = next(t for t in mine["todo"] if t["id"] == item["id"])
    assert card["patient_status_label"] == "To do" and card["can_upload"] and card["instructions"] == "No fasting needed."
    r = upload(ramesh, item["id"], test_date=TODAY - timedelta(days=1))
    assert r.status_code == 201 and r.json()["message"] == "Sent for clinician review."
    report_id = r.json()["report_id"]
    waiting = item_named(priya, pid, "Haemoglobin")
    assert waiting["status"] == "submitted_by_patient" and waiting["result"] is None          # pending: not a result
    assert waiting["report"]["lab_name"] == "City Diagnostics"
    pending = priya.get(f"/api/patients/{pid}/external-reports/{report_id}").json()
    assert pending["order_test_name"] == "Haemoglobin" and pending["expected_unit"] == "g/dL"
    assert priya.post(f"/api/patients/{pid}/external-reports/{report_id}/review", json={"value": 40}).status_code == 422  # implausible
    assert priya.post(f"/api/patients/{pid}/external-reports/{report_id}/review", json={"value": 12.4}).status_code == 200
    done = item_named(priya, pid, "Haemoglobin")
    assert done["status"] == "verified" and done["section"] == "completed"
    res = done["result"]
    assert (res["value"], res["unit"], res["test_date"], res["source"], res["lab_name"]) == \
        (12.4, "g/dL", (TODAY - timedelta(days=1)).isoformat(), "patient_upload", "City Diagnostics")
    assert res["uploaded_at"] and res["verified_at"]
    assert done["suggested_next_due"] == (TODAY - timedelta(days=1) + timedelta(days=180)).isoformat()   # suggestion only
    assert any(p["name"] == "Hemoglobin" and any(x["value"] == 12.4 for x in p["results"])
               for p in priya.get(f"/api/patients/{pid}/labs").json())                              # on the graph


def test_rejecting_an_upload_returns_the_item_and_tells_the_patient_why(priya, ids, tests_by_code, make_client):
    pid = ids["P-1001"]
    order(priya, pid, {"test_catalog_id": tests_by_code["HBA1C"]})
    item = item_named(priya, pid, "HbA1c")
    ramesh = patient(make_client, "P-1001")
    report_id = upload(ramesh, item["id"]).json()["report_id"]
    assert upload(ramesh, item["id"]).status_code == 409                                  # already waiting for review
    no_reason = priya.post(f"/api/patients/{pid}/external-reports/{report_id}/reject", json={"reason": ""})
    assert no_reason.status_code == 422
    assert priya.post(f"/api/patients/{pid}/external-reports/{report_id}/reject",
                      json={"reason": "The report is cut off at the bottom"}).status_code == 200
    rejected = item_named(priya, pid, "HbA1c")
    assert rejected["status"] == "rejected" and rejected["section"] == "rejected" and rejected["result"] is None
    card = next(t for t in ramesh.get("/api/me/test-orders").json()["todo"] if t["id"] == item["id"])
    assert card["status_reason"] == "The report is cut off at the bottom" and card["can_upload"]
    note = next(n for n in ramesh.get("/api/me/notifications").json() if n["kind"] == "test_rejected")
    assert "cut off at the bottom" in note["message"]
    assert upload(ramesh, item["id"]).status_code == 201                                   # re-upload
    assert item_named(priya, pid, "HbA1c")["status"] == "submitted_by_patient"


def test_upload_rules(priya, ids, tests_by_code, make_client):
    pid = ids["P-1001"]
    order(priya, pid, {"test_catalog_id": tests_by_code["LIPID"], "fulfilment_route": "clinic_lab"},
          {"test_catalog_id": tests_by_code["FBG"]})
    clinic_only, fasting = item_named(priya, pid, "Lipid profile"), item_named(priya, pid, "Fasting glucose")
    ramesh = patient(make_client, "P-1001")
    assert upload(ramesh, clinic_only["id"]).json()["detail"]["code"] == "clinic_only"
    fake = upload(ramesh, fasting["id"], file=("report.pdf", b"just text pretending to be a pdf", "application/pdf"))
    assert fake.status_code == 415                                                         # the real type is checked
    assert upload(ramesh, fasting["id"], test_date=TODAY + timedelta(days=1)).status_code == 422
    assert upload(ramesh, fasting["id"], lab="").status_code == 422
    first = upload(ramesh, fasting["id"], key="upload-key-0001")
    again = upload(ramesh, fasting["id"], key="upload-key-0001")
    assert first.status_code == again.status_code == 201 and again.json()["replayed"] is True
    assert again.json()["report_id"] == first.json()["report_id"]
    other = upload(ramesh, "other", custom_name="Thyroid profile")
    assert other.status_code == 201 and other.json()["test_order_item_id"] is None          # the unlinked queue
    queue = priya.get("/api/external-reports/pending").json()
    assert any(x["id"] == other.json()["report_id"] and x["test_order_item_id"] is None for x in queue)


# ------------------------------------------------------------------ clinic lab ingestion

@pytest.fixture()
def lab(empty_client, monkeypatch, demo_ids):
    monkeypatch.setenv("LAB_SERVICE_TOKEN", "lab-secret-123")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield lambda body, token="lab-secret-123": c.post("/api/internal/lab-results", json=body,
                                                          headers={"X-Service-Token": token} if token else {})


def result(code, value, ref="LAB-0001", patient="P-1001", **kw):
    return {"lab_order_ref": ref, "test_code": code, "patient_code": patient, "value": value,
            "test_date": TODAY.isoformat(), **kw}


def test_clinic_lab_matched_duplicate_unmatched_and_implausible(priya, ids, tests_by_code, lab, make_client):
    pid = ids["P-1001"]
    order(priya, pid, {"test_catalog_id": tests_by_code["HBA1C"]}, {"test_catalog_id": tests_by_code["EGFR"]})
    assert lab(result("HBA1C", 7.6), token=None).status_code == 401
    assert lab(result("HBA1C", 7.6), token="wrong").status_code == 401
    first = lab(result("HBA1C", 7.6)).json()
    assert first["outcome"] == "verified"
    hb = item_named(priya, pid, "HbA1c")
    assert hb["status"] == "verified" and hb["result"]["source"] == "clinic_lab_system" and hb["result"]["value"] == 7.6
    assert lab(result("HBA1C", 9.9)).json()["outcome"] == "duplicate"                       # same ref + code: ignored
    assert item_named(priya, pid, "HbA1c")["result"]["value"] == 7.6
    assert lab(result("HB", 13.0, ref="LAB-0002")).json()["outcome"] == "unlinked"          # no Hb order
    data = priya.get(f"/api/patients/{pid}/test-orders").json()
    assert [u["test_code"] for u in data["unlinked_lab_results"]] == ["HB"]
    odd = lab(result("EGFR", 900, ref="LAB-0003")).json()                                   # implausible eGFR
    assert odd["outcome"] == "needs_review"
    egfr = item_named(priya, pid, "Serum creatinine / eGFR")
    assert egfr["status"] == "result_received" and egfr["result"] is None and egfr["review"]["value"] == 900
    r = priya.post(f"/api/patients/{pid}/lab-results/{egfr['review']['ingestion_id']}/reject",
                   json={"reason": "Sample haemolysed, please repeat"})
    assert r.status_code == 200 and item_named(priya, pid, "Serum creatinine / eGFR")["status"] == "rejected"
    ramesh = patient(make_client, "P-1001")
    assert any("Sample haemolysed" in n["message"] for n in ramesh.get("/api/me/notifications").json())
    # the unlinked Hb result: the clinician accepts it (no order to link)
    hb_unlinked = data["unlinked_lab_results"][0]["id"]
    assert priya.post(f"/api/patients/{pid}/lab-results/{hb_unlinked}/accept", json={}).status_code == 200
    assert priya.get(f"/api/patients/{pid}/test-orders").json()["unlinked_lab_results"] == []


def test_lab_link_is_closed_without_a_configured_credential(empty_client):
    r = empty_client.post("/api/internal/lab-results", json=result("HBA1C", 7), headers={"X-Service-Token": "anything"})
    assert r.status_code == 503


def test_demo_simulator_uses_the_same_path_and_is_off_outside_demo_mode(priya, ids, tests_by_code, monkeypatch):
    pid = ids["P-1001"]
    order(priya, pid, {"test_catalog_id": tests_by_code["FBG"]})
    item = item_named(priya, pid, "Fasting glucose")
    assert priya.get("/api/test-catalog").json()["demo_mode"] is True
    r = priya.post(f"/api/patients/{pid}/test-orders/demo-lab-result", json={"test_order_item_id": item["id"], "value": 132})
    assert r.json()["outcome"] == "verified" and item_named(priya, pid, "Fasting glucose")["result"]["value"] == 132
    monkeypatch.setenv("DEMO_MODE", "0")
    assert priya.get("/api/test-catalog").json()["demo_mode"] is False
    assert priya.post(f"/api/patients/{pid}/test-orders/demo-lab-result",
                      json={"test_order_item_id": item["id"], "value": 132}).status_code == 404


# ------------------------------------------------------------------ authorization

def test_patients_and_other_clinicians_cannot_reach_someone_elses_tests(priya, ids, tests_by_code, make_client):
    order(priya, ids["P-1002"], {"test_catalog_id": tests_by_code["HB"]})
    theirs = item_named(priya, ids["P-1002"], "Haemoglobin")
    ramesh = patient(make_client, "P-1001")
    assert all(t["id"] != theirs["id"] for t in ramesh.get("/api/me/test-orders").json()["todo"])
    assert upload(ramesh, theirs["id"]).status_code == 404                                  # patient A -> patient B's item
    assert ramesh.get(f"/api/patients/{ids['P-1002']}/test-orders").status_code == 401       # a patient on a clinician route
    karan = make_client()
    login_clinician(karan, KARAN)                                                           # not on the care team
    for method, url, body in [
        ("get", f"/api/patients/{ids['P-1002']}/test-orders", None),
        ("post", f"/api/patients/{ids['P-1002']}/test-orders", {"items": [{"test_catalog_id": tests_by_code["HB"]}]}),
        ("patch", f"/api/test-order-items/{theirs['id']}", {"version": 1, "priority": "urgent"}),
        ("post", f"/api/test-order-items/{theirs['id']}/cancel", {"version": 1, "reason": "not mine"}),
        ("post", f"/api/test-order-items/{theirs['id']}/waive", {"version": 1, "reason": "not mine"}),
        ("get", f"/api/patients/{ids['P-1002']}/fhir/Bundle", None),
    ]:
        r = getattr(karan, method)(url, **({"json": body} if body else {}))
        assert r.status_code == 404, (url, r.status_code)
    assert karan.get("/api/clinic/tests/overdue").json()["total"] == 0


# ------------------------------------------------------------------ overdue dashboard

def test_overdue_dashboard(priya, ids, tests_by_code, monkeypatch):
    pid = ids["P-1001"]
    order(priya, pid, {"test_catalog_id": tests_by_code["UACR"], "due_by": str(TODAY + timedelta(days=1))})
    assert priya.get("/api/clinic/tests/overdue").json()["total"] == 0
    monkeypatch.setenv("DEMO_TODAY", (TODAY + timedelta(days=3)).isoformat())                # two days later
    rows = priya.get("/api/clinic/tests/overdue").json()["items"]
    assert [(r["patient_code"], r["test"], r["days_overdue"], r["days_to_appointment"]) for r in rows] == \
        [("P-1001", "Urine albumin-creatinine ratio", 2, 11)]
    assert priya.get("/api/clinic/tests/overdue?within_days=5").json()["total"] == 0
    item = item_named(priya, pid, "Urine albumin-creatinine ratio")
    assert item["overdue"] is True and priya.get(f"/api/patients/{pid}/test-orders").json()["summary"]["overdue"] == 1


# ------------------------------------------------------------------ FHIR

def test_fhir_view_validates_and_leaves_out_unverified_results(priya, ids, tests_by_code, lab, make_client):
    from fhir.resources.R4B.bundle import Bundle
    from fhir.resources.R4B.documentreference import DocumentReference
    from fhir.resources.R4B.observation import Observation
    from fhir.resources.R4B.servicerequest import ServiceRequest
    pid = ids["P-1001"]
    order(priya, pid, {"test_catalog_id": tests_by_code["HBA1C"]}, {"test_catalog_id": tests_by_code["HB"]},
          {"test_catalog_id": tests_by_code["UACR"]})
    lab(result("HBA1C", 7.4))                                                               # verified automatically
    ramesh = patient(make_client, "P-1001")
    pending = upload(ramesh, item_named(priya, pid, "Haemoglobin")["id"]).json()["report_id"]   # not verified yet
    srs = priya.get(f"/api/patients/{pid}/fhir/ServiceRequest").json()
    obs = priya.get(f"/api/patients/{pid}/fhir/Observation").json()
    docs = priya.get(f"/api/patients/{pid}/fhir/DocumentReference").json()
    for e in srs["entry"]:
        ServiceRequest.model_validate(e["resource"])
    for e in obs["entry"]:
        Observation.model_validate(e["resource"])
    for e in docs["entry"]:
        DocumentReference.model_validate(e["resource"])
    Bundle.model_validate(priya.get(f"/api/patients/{pid}/fhir/Bundle").json())
    assert len(srs["entry"]) == 3 and len(obs["entry"]) == 1                                # the pending Hb is not an Observation
    o = obs["entry"][0]["resource"]
    assert o["status"] == "final" and o["valueQuantity"] == {"value": 7.4, "unit": "%", "system": "http://unitsofmeasure.org", "code": "%"}
    assert o["code"]["coding"][0]["code"] == "4548-4" and o["basedOn"][0]["reference"].startswith("ServiceRequest/")
    assert [d["resource"]["id"] for d in docs["entry"]] == [pending]
    assert docs["entry"][0]["resource"]["docStatus"] == "preliminary"
    assert "storage" not in str(docs) and "patients/" + pid + "/documents" not in str(docs)  # no storage paths
    sr = next(e["resource"] for e in srs["entry"] if e["resource"]["code"]["text"] == "HbA1c")
    assert sr["status"] == "completed" and sr["intent"] == "order" and sr["occurrencePeriod"]["end"]
    c = sqlite3.connect(os.environ["UC2_DB_PATH"])
    assert c.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'FHIR_VIEWED'").fetchone()[0] == 4


# ------------------------------------------------------------------ reminders

def test_reminders_are_sent_once_per_item_and_day_and_only_while_open(priya, ids, tests_by_code, make_client):
    pid = ids["P-1001"]
    due = TODAY + timedelta(days=7)
    order(priya, pid, {"test_catalog_id": tests_by_code["HB"], "due_by": str(due)},
          {"test_catalog_id": tests_by_code["RBG"], "due_by": str(due)})
    rbg = item_named(priya, pid, "Random glucose")
    priya.post(f"/api/test-order-items/{rbg['id']}/cancel", json={"version": rbg["version"], "reason": "Not needed now"})
    conn = db.connect(os.environ["UC2_DB_PATH"])
    run = lambda day: orders.run_reminders(conn, day)       # noqa: E731
    assert run(TODAY) == 1 and run(TODAY) == 0                                              # 7 days before - once
    assert run(due - timedelta(days=5)) == 0                                                # no reminder for 5 days
    assert run(due - timedelta(days=3)) == 1 and run(due - timedelta(days=3)) == 0
    assert run(due - timedelta(days=1)) == 1
    assert run(due) == 0                                                                    # due today: none
    assert run(due + timedelta(days=1)) == 1 and run(due + timedelta(days=2)) == 0          # overdue - once
    messages = [n["message"] for n in patient(make_client, "P-1001").get("/api/me/notifications").json()
                if n["kind"] == "test_reminder"]
    assert len(messages) == 4 and all("Haemoglobin" in m for m in messages)                 # never the cancelled one
    conn.close()
