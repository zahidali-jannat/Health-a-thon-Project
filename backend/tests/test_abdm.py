from datetime import date, datetime, timedelta

import pytest

from app import abdm_mock as abdm
from app import repo
from app.detection import assess_patient

TODAY = date(2026, 9, 25)
NOW = datetime(2026, 9, 25, 10, 0, 0)
RAMESH_ABHA = "91-1234-5678-9012"


@pytest.fixture()
def env(seeded_conn):
    return seeded_conn


def external_events(conn, pid):
    events = conn.execute("SELECT event_type, value, asserted_by FROM clinical_events "
                          "WHERE patient_id = ? AND source = 'external_hospital_abdm'", (pid,)).fetchall()
    results = conn.execute("SELECT 'lab' AS event_type, t.numeric_value AS value, r.laboratory_name AS asserted_by "
                           "FROM lab_test_results t JOIN lab_reports r ON r.id = t.report_id "
                           "WHERE t.patient_id = ? AND r.source = 'external_hospital_abdm'", (pid,)).fetchall()
    return [tuple(r) for r in events + results]


def test_request_is_pending_not_auto_approved(env):
    conn, ids = env
    cid = abdm.create_request(conn, ids["P-1001"], RAMESH_ABHA, ["Prescription", "DiagnosticReport"], NOW, TODAY)
    req = abdm.get_request(conn, cid)
    assert req["status"] == "REQUESTED"
    assert req["hip_name"] == "City Care Hospital, Pune"
    assert req["purpose_code"] == "CAREMGT" and req["date_to"] == "2026-09-25" and req["access_until"] == "2026-10-25"
    assert external_events(conn, ids["P-1001"]) == []
    assert [a["action"] for a in req["audit"]] == ["REQUESTED"]


def test_approve_writes_only_consented_records(env):
    conn, ids = env
    pid = ids["P-1001"]
    cid = abdm.create_request(conn, pid, RAMESH_ABHA, ["DiagnosticReport"], NOW, TODAY)
    req = abdm.respond(conn, cid, pid, approve=True, now=NOW, today=TODAY)

    assert req["status"] == "GRANTED" and req["records_received"] == 1
    assert external_events(conn, pid) == [("lab", 8.4, "City Care Hospital, Pune")]
    assert [a["action"] for a in req["audit"]] == ["REQUESTED", "GRANTED", "DATA_RECEIVED"]


def test_approved_emergency_visit_raises_hard_flag(env):
    """Data from the ABHA path feeds the same detection logic: the other hospital's ER visit -> HIGH PRIORITY."""
    conn, ids = env
    pid = ids["P-1001"]
    assert assess_patient(repo.load_patient_record(conn, pid), TODAY).level == "quietly_worse"
    cid = abdm.create_request(conn, pid, RAMESH_ABHA, list(abdm.HI_TYPES), NOW, TODAY)
    abdm.respond(conn, cid, pid, approve=True, now=NOW, today=TODAY)
    a = assess_patient(repo.load_patient_record(conn, pid), TODAY)
    assert a.level == "high_priority"
    assert "emergency visit on 7 Sep 2026 at City Care Hospital, Pune" in a.summary


def test_deny_shares_nothing(env):
    conn, ids = env
    pid = ids["P-1001"]
    cid = abdm.create_request(conn, pid, RAMESH_ABHA, ["Prescription"], NOW, TODAY)
    req = abdm.respond(conn, cid, pid, approve=False, now=NOW, today=TODAY)
    assert req["status"] == "DENIED"
    assert external_events(conn, pid) == []
    assert req["audit"][-1]["action"] == "DENIED"


def test_unanswered_request_expires(env):
    conn, ids = env
    pid = ids["P-1001"]
    cid = abdm.create_request(conn, pid, RAMESH_ABHA, ["Prescription"], NOW, TODAY)
    later = NOW + timedelta(days=2)
    assert abdm.list_requests(conn, later, patient_id=pid)[0]["status"] == "EXPIRED"
    with pytest.raises(abdm.ConsentError, match="no longer waiting"):
        abdm.respond(conn, cid, pid, approve=True, now=later, today=TODAY)
    assert external_events(conn, pid) == []
    assert [a["action"] for a in abdm.get_request(conn, cid)["audit"]] == ["REQUESTED", "EXPIRED"]


def test_simulate_expiry(env):
    conn, ids = env
    cid = abdm.create_request(conn, ids["P-1002"], "91234567890123", ["Prescription"], NOW, TODAY)
    assert abdm.simulate_expiry(conn, ids["P-1002"], cid, NOW)
    assert abdm.get_request(conn, cid)["status"] == "EXPIRED"


def test_cannot_answer_twice_or_for_someone_else(env):
    conn, ids = env
    cid = abdm.create_request(conn, ids["P-1001"], RAMESH_ABHA, ["Prescription"], NOW, TODAY)
    with pytest.raises(abdm.ConsentError, match="not found"):
        abdm.respond(conn, cid, ids["P-1002"], approve=True, now=NOW, today=TODAY)
    abdm.respond(conn, cid, ids["P-1001"], approve=False, now=NOW, today=TODAY)
    with pytest.raises(abdm.ConsentError):
        abdm.respond(conn, cid, ids["P-1001"], approve=True, now=NOW, today=TODAY)


@pytest.mark.parametrize("abha, types, message", [
    ("1234", ["Prescription"], "14 digits"),
    (RAMESH_ABHA, [], "at least one"),
    ("91234567890123", ["Prescription"], "does not match"),
])
def test_bad_requests_are_rejected(env, abha, types, message):
    conn, ids = env
    with pytest.raises(abdm.ConsentError, match=message):
        abdm.create_request(conn, ids["P-1001"], abha, types, NOW, TODAY)


def test_audit_log_lists_every_action(env):
    conn, ids = env
    a = abdm.create_request(conn, ids["P-1001"], RAMESH_ABHA, ["Prescription"], NOW, TODAY)
    b = abdm.create_request(conn, ids["P-1002"], "91234567890123", ["Prescription"], NOW, TODAY)
    abdm.respond(conn, a, ids["P-1001"], approve=True, now=NOW, today=TODAY)
    abdm.respond(conn, b, ids["P-1002"], approve=False, now=NOW, today=TODAY)
    actions = sorted(r["action"] for r in abdm.audit_log(conn, NOW, list(ids.values())))
    assert actions == ["DATA_RECEIVED", "DENIED", "GRANTED", "REQUESTED", "REQUESTED"]
