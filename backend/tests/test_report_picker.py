"""The "link a report" picker: EVERY report the patient uploaded (both upload paths), one lightweight list,
label corrections, value edits and link / change / unlink - each audited with old and new - and file access
only through a short-lived signed link, only for the patient's own care team."""

import os
import sqlite3
import time

import pytest

from app import report_links as rl
from tests.conftest import KARAN, login_clinician, login_patient, patient_ids

IMG = ("r.png", b"\x89PNG fake report", "image/png")
PDF = ("r.pdf", b"%PDF-1.4 fake", "application/pdf")


def send_test_report(client, test_type, test_date, test_name=None):
    data = {"test_type": test_type, "test_date": test_date} | ({"test_name": test_name} if test_name else {})
    r = client.post("/api/me/external-reports", data=data, files={"file": IMG})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def send_document(client, description, file=PDF):
    r = client.post("/api/me/documents", data={"kind": "lab_report", "description": description}, files={"file": file})
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.fixture()
def ids(priya):
    return patient_ids(priya)


@pytest.fixture()
def lakshmi_reports(make_client):
    """Lakshmi (P-1002): 2 eGFR, 2 Hemoglobin, 3 HbA1c, 2 Sugar - through BOTH upload paths."""
    c = make_client()
    login_patient(c, "P-1002")
    return {
        "hba1c_1": send_test_report(c, "HbA1c", "2026-09-20"),
        "hba1c_2": send_test_report(c, "HbA1c", "2026-06-20"),
        "hba1c_3": send_document(c, "HbA1c test"),                                   # no test date given
        "sugar_1": send_test_report(c, "Fasting Sugar", "2026-09-15"),
        "sugar_2": send_test_report(c, "PP Sugar", "2026-09-01"),
        "egfr_1": send_test_report(c, "Other", "2026-09-10", test_name="eGFR"),
        "egfr_2": send_document(c, "Kidney report"),
        "hb_1": send_test_report(c, "Other", "2026-08-10", test_name="Haemoglobin"),
        "hb_2": send_document(c, "Hemoglobin report"),
    }


def enter(priya, pid, **fields):
    r = priya.post(f"/api/patients/{pid}/manual", json=fields)
    assert r.status_code == 200, r.text
    return {v["name"]: v for v in priya.get(f"/api/patients/{pid}/manual-values").json()}


# ------------------------------------------------------------------ the list

def test_every_uploaded_report_is_listed_with_its_type_on_every_row(priya, ids, lakshmi_reports):
    pid = ids["P-1002"]
    body = priya.get(f"/api/patients/{pid}/uploaded-reports").json()
    got = {r["id"]: r for r in body["reports"]}
    assert body["total"] == 9 and set(got) == set(lakshmi_reports.values())
    by_type = {}
    for r in body["reports"]:
        by_type[r["report_type"]] = by_type.get(r["report_type"], 0) + 1
    assert by_type == {"egfr": 2, "hemoglobin": 2, "hba1c": 3, "sugar": 2}
    assert got[lakshmi_reports["egfr_2"]]["report_type_label"] == "eGFR report"
    assert got[lakshmi_reports["hba1c_3"]]["display_name"] == "HbA1c test"
    # lightweight: no file contents, no storage paths
    assert set(got[lakshmi_reports["hb_2"]]) == {
        "id", "origin", "display_name", "file_name", "report_type", "report_type_label", "test_date", "uploaded_at",
        "uploaded_by_role", "uploaded_by_name", "status", "usable", "file_kind", "page_count", "thumbnail_url"}
    # a document sent without a test date never borrows the upload date
    assert got[lakshmi_reports["hba1c_3"]]["test_date"] is None
    # newest test date first, unknown test dates last
    dated = [r["test_date"] for r in body["reports"]]
    assert dated[:6] == sorted(dated[:6], reverse=True) and dated[6:] == [None, None, None]
    assert got[lakshmi_reports["hb_1"]]["uploaded_by_name"] == "Patient"


def test_an_unchanged_list_answers_304(priya, ids, lakshmi_reports):
    url = f"/api/patients/{ids['P-1002']}/uploaded-reports"
    first = priya.get(url)
    assert first.headers["cache-control"] == "private, no-cache"
    assert priya.get(url, headers={"If-None-Match": first.headers["etag"]}).status_code == 304
    priya.patch(f"{url}/{lakshmi_reports['hba1c_3']}", json={"test_date": "2026-09-02"})
    assert priya.get(url, headers={"If-None-Match": first.headers["etag"]}).status_code == 200    # changed -> new list


def test_the_list_uses_one_query(seeded_conn, ids):
    conn, _ = seeded_conn
    seen = []
    conn.set_trace_callback(seen.append)
    rl.list_reports(conn, ids["P-1001"])
    conn.set_trace_callback(None)
    assert len([q for q in seen if q.lstrip().upper().startswith("SELECT")]) == 1


def test_unlabelled_report_gets_a_type_name_and_date_from_the_care_team(priya, ids, make_client):
    c = make_client()
    login_patient(c, "P-1002")
    doc = send_document(c, "Scan from clinic")
    url = f"/api/patients/{ids['P-1002']}/uploaded-reports"
    r = next(x for x in priya.get(url).json()["reports"] if x["id"] == doc)
    assert (r["report_type"], r["report_type_label"], r["test_date"]) == (None, "Unlabelled", None)
    fixed = priya.patch(f"{url}/{doc}", json={"display_name": "CBC", "report_type": "hemoglobin", "test_date": "2026-09-20"})
    assert fixed.status_code == 200
    assert (fixed.json()["display_name"], fixed.json()["report_type_label"], fixed.json()["test_date"]) == \
        ("CBC", "Hemoglobin report", "2026-09-20")
    assert priya.patch(f"{url}/{doc}", json={"test_date": "2099-01-01"}).status_code == 422
    log = [e for e in priya.get(f"/api/patients/{ids['P-1002']}/access-log").json() if e["action"] == "REPORT_DETAILS_CHANGED"]
    assert len(log) == 1


def test_care_team_upload_shows_the_uploader(priya, ids):
    r = priya.post(f"/api/patients/{ids['P-1002']}/uploaded-reports", files={"file": PDF},
                   data={"display_name": "Paper KFT", "report_type": "egfr", "test_date": "2026-09-01"})
    assert r.status_code == 201, r.text
    assert (r.json()["uploaded_by_name"], r.json()["uploaded_by_role"], r.json()["report_type"]) == \
        ("Dr. Priya Nair", "care_team", "egfr")


# ------------------------------------------------------------------ link / change / unlink, edits

def test_link_change_unlink_are_three_audited_actions_and_the_row_says_so(priya, ids, lakshmi_reports):
    pid = ids["P-1002"]
    v = enter(priya, pid, egfr={"value": 60, "date": "2026-09-20"})["eGFR"]
    url = f"/api/patients/{pid}/manual-values/{v['id']}"

    linked = priya.patch(url, json={"report_id": lakshmi_reports["egfr_2"]}).json()        # a "Send a document" upload
    assert linked["linked"]["id"] == lakshmi_reports["egfr_2"] and linked["latest_action"]["text"] == "Report linked"
    assert linked["latest_action"]["by"] == "Dr. Priya Nair"
    changed = priya.patch(url, json={"report_id": lakshmi_reports["hba1c_1"]}).json()      # a "Send a test report" upload
    assert changed["linked"]["id"] == lakshmi_reports["hba1c_1"] and changed["latest_action"]["text"] == "Report changed"
    unlinked = priya.patch(url, json={"report_id": None}).json()
    assert unlinked["linked"] is None and unlinked["latest_action"]["text"] == "Report unlinked"

    c = sqlite3.connect(os.environ["UC2_DB_PATH"])
    rows = c.execute("SELECT action, old_value, new_value FROM audit_log WHERE resource_type = 'clinical_event' "
                     "AND resource_id = ? AND action LIKE 'VALUE_REPORT_%' ORDER BY id", (str(v["id"]),)).fetchall()
    assert [r[0] for r in rows] == ["VALUE_REPORT_LINKED", "VALUE_REPORT_CHANGED", "VALUE_REPORT_UNLINKED"]
    assert rows[0][1] is None and "Kidney report" in rows[0][2]
    assert "Kidney report" in rows[1][1] and "HbA1c report" in rows[1][2]
    assert rows[2][2] is None
    row = c.execute("SELECT linked_document_id, linked_upload_id, previous_document_id FROM clinical_events WHERE id = ?",
                    (v["id"],)).fetchone()
    assert row == (None, None, lakshmi_reports["hba1c_1"])


def test_a_value_linked_to_a_document_shows_its_source_on_the_graph(priya, ids, lakshmi_reports):
    pid = ids["P-1002"]
    v = enter(priya, pid, hemoglobin={"value": 12.2, "date": "2026-09-21", "linked_document_id": lakshmi_reports["hb_2"]})
    assert v["Hemoglobin"]["linked"]["id"] == lakshmi_reports["hb_2"]
    panel = next(p for p in priya.get(f"/api/patients/{pid}/labs").json() if p["name"] == "Hemoglobin")
    point = next(r for r in panel["results"] if r["value"] == 12.2)
    assert point["provenance"]["origin"] == "document" and point["provenance"]["document_id"] == lakshmi_reports["hb_2"]
    assert point["provenance"]["text"].startswith("Lakshmi Iyer uploaded “Hemoglobin report”")


def test_edit_value_unit_and_date(priya, ids):
    pid = ids["P-1002"]
    v = enter(priya, pid, sugar={"value": 140, "date": "2026-09-20"})["Blood glucose"]
    url = f"/api/patients/{pid}/manual-values/{v['id']}"
    edited = priya.patch(url, json={"value": 7, "unit": "mmol/L", "effective_date": "2026-09-19"}).json()
    assert (edited["value"], edited["unit"], edited["effective_date"]) == (126.1, "mg/dL", "2026-09-19")   # stored as graphed
    assert edited["latest_action"]["text"] == "Value edited"
    assert priya.patch(url, json={"value": 9000}).status_code == 422
    assert priya.patch(url, json={"unit": "furlongs"}).status_code == 422
    assert priya.patch(url, json={"effective_date": "2099-01-01"}).status_code == 422
    c = sqlite3.connect(os.environ["UC2_DB_PATH"])
    old, new = c.execute("SELECT old_value, new_value FROM audit_log WHERE action = 'VALUE_EDITED' AND resource_id = ?",
                         (str(v["id"]),)).fetchone()
    assert old == "140 mg/dL · 20 Sep 2026" and new == "126.1 mg/dL · 19 Sep 2026"


def test_a_rejected_report_is_listed_but_cannot_be_linked(priya, ids, lakshmi_reports):
    pid = ids["P-1002"]
    doc = lakshmi_reports["hb_2"]
    assert priya.post(f"/api/patients/{pid}/documents/{doc}/review", json={"status": "rejected"}).status_code == 200
    listed = next(r for r in priya.get(f"/api/patients/{pid}/uploaded-reports").json()["reports"] if r["id"] == doc)
    assert listed["usable"] is False and listed["status"] == "rejected"
    v = enter(priya, pid, hemoglobin={"value": 12, "date": "2026-09-20"})["Hemoglobin"]
    r = priya.patch(f"/api/patients/{pid}/manual-values/{v['id']}", json={"report_id": doc})
    assert r.status_code == 422 and "not usable" in r.json()["detail"]


def test_the_database_refuses_a_link_to_another_patients_document(seeded_conn, ids, lakshmi_reports):
    conn, _ = seeded_conn
    conn.execute("INSERT INTO clinical_events (patient_id, event_type, name, value, unit, effective_date, source, confidence, "
                 "status, asserted_by, created_at) VALUES (?, 'lab', 'eGFR', 60, 'x', '2026-09-20', 'care_team_manual', "
                 "'high', 'resulted', 'x', 'x')", (ids["P-1001"],))
    vid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE clinical_events SET linked_upload_id = ?, previous_upload_id = NULL, link_changed_by = "
                     "(SELECT id FROM clinical_users LIMIT 1), link_changed_date = 'now' WHERE id = ?",
                     (lakshmi_reports["egfr_2"], vid))


# ------------------------------------------------------------------ privacy: other patients, other clinicians, files

def test_another_patients_reports_can_never_be_fetched(priya, ids, lakshmi_reports, make_client):
    ramesh = ids["P-1001"]
    listed = {r["id"] for r in priya.get(f"/api/patients/{ramesh}/uploaded-reports").json()["reports"]}
    assert not listed & set(lakshmi_reports.values())
    stolen = lakshmi_reports["hba1c_1"]
    assert priya.post(f"/api/patients/{ramesh}/uploaded-reports/{stolen}/file-link").status_code == 404
    assert priya.patch(f"/api/patients/{ramesh}/uploaded-reports/{stolen}", json={"display_name": "x"}).status_code == 422
    v = enter(priya, ramesh, egfr={"value": 60, "date": "2026-09-20"})["eGFR"]
    assert priya.patch(f"/api/patients/{ramesh}/manual-values/{v['id']}", json={"report_id": stolen}).status_code == 422

    karan = make_client()
    login_clinician(karan, KARAN)                  # not on Lakshmi's care team
    assert karan.get(f"/api/patients/{ids['P-1002']}/uploaded-reports").status_code == 404
    assert karan.post(f"/api/patients/{ids['P-1002']}/uploaded-reports/{stolen}/file-link").status_code == 404


def test_files_open_only_through_a_short_lived_link_for_the_same_clinician(priya, ids, lakshmi_reports, make_client, monkeypatch):
    pid, rid = ids["P-1002"], lakshmi_reports["egfr_2"]
    link = priya.post(f"/api/patients/{pid}/uploaded-reports/{rid}/file-link").json()["url"]
    ok = priya.get(link)
    assert ok.status_code == 200 and ok.content == PDF[1] and ok.headers["cache-control"] == "private, no-store"
    assert priya.get(link.replace("sig=", "sig=0")).status_code == 404                       # tampered
    assert priya.get(link.replace(pid, ids["P-1001"])).status_code == 404                     # other patient
    karan = make_client()
    login_clinician(karan, KARAN)
    assert karan.get(link).status_code == 404                                                 # another clinician
    assert make_client().get(link).status_code == 401                                         # nobody signed in
    later = time.time() + rl.LINK_SECONDS + 1
    monkeypatch.setattr(rl, "time", type("Clock", (), {"time": staticmethod(lambda: later)}))   # the link's clock only
    assert priya.get(link).status_code == 404
