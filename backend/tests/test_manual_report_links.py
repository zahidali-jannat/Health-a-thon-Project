"""'Link to Report' for values the care team types in by hand: picker, link / re-link / unlink with a recorded
history, and the graph's reference line always reading the CURRENT link."""

import os
import re
import sqlite3

import pytest

from tests.conftest import login_patient, patient_ids

IMG = ("r.png", b"\x89PNG fake report", "image/png")


@pytest.fixture()
def ids(priya):
    return patient_ids(priya)


def upload(client, test_type, test_date, test_name=None):
    data = {"test_type": test_type, "test_date": test_date} | ({"test_name": test_name} if test_name else {})
    r = client.post("/api/me/external-reports", data=data, files={"file": IMG})
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.fixture()
def reports(make_client):
    """Ramesh (P-1001) uploads four reports; Meena (P-1003) uploads one."""
    ramesh, meena = make_client(), make_client()
    login_patient(ramesh, "P-1001")
    login_patient(meena, "P-1003")
    return {
        "fasting_old": upload(ramesh, "Fasting Sugar", "2026-08-01"),
        "pp_new": upload(ramesh, "PP Sugar", "2026-09-18"),
        "hba1c": upload(ramesh, "HbA1c", "2026-09-10"),
        "kidney": upload(ramesh, "Other", "2026-09-12", test_name="Kidney function test"),
        "meena": upload(meena, "Fasting Sugar", "2026-09-19"),
    }


def db_row(event_id):
    c = sqlite3.connect(os.environ["UC2_DB_PATH"])
    c.row_factory = sqlite3.Row
    row = dict(c.execute("SELECT * FROM clinical_events WHERE id = ?", (event_id,)).fetchone())
    c.close()
    return row


def point(priya, pid, panel_name, value):
    panel = next(p for p in priya.get(f"/api/patients/{pid}/labs").json() if p["name"] == panel_name)
    return next(r for r in panel["results"] if r["value"] == value)


# ------------------------------------------------ step 2: the picker

def test_picker_lists_only_this_patients_matching_reports_newest_first(priya, ids, reports):
    pid = ids["P-1001"]
    sugar = priya.get(f"/api/patients/{pid}/linkable-reports", params={"field": "sugar"}).json()
    assert [r["id"] for r in sugar["reports"]] == [reports["pp_new"], reports["fasting_old"]]   # newest test date first
    assert reports["meena"] not in [r["id"] for r in sugar["reports"]]                        # never another patient's
    assert sugar["total"] == 4
    assert [r["id"] for r in priya.get(f"/api/patients/{pid}/linkable-reports", params={"field": "hba1c"}).json()["reports"]] \
        == [reports["hba1c"]]
    assert [r["id"] for r in priya.get(f"/api/patients/{pid}/linkable-reports", params={"field": "egfr"}).json()["reports"]] \
        == [reports["kidney"]]                                                              # "Other" matched by name
    everything = priya.get(f"/api/patients/{pid}/linkable-reports", params={"field": "sugar", "all": True}).json()
    assert len(everything["reports"]) == 4 and reports["meena"] not in [r["id"] for r in everything["reports"]]


def test_picker_for_a_patient_with_no_uploads(priya, ids):
    r = priya.get(f"/api/patients/{ids['P-1002']}/linkable-reports", params={"field": "sugar"}).json()
    assert r == {"total": 0, "reports": []}


def test_rejected_reports_are_not_offered(priya, ids, reports):
    pid = ids["P-1001"]
    priya.post(f"/api/patients/{pid}/external-reports/{reports['pp_new']}/reject", json={"reason": "blurred"})
    ids_offered = [r["id"] for r in priya.get(f"/api/patients/{pid}/linkable-reports", params={"all": True}).json()["reports"]]
    assert reports["pp_new"] not in ids_offered


# ------------------------------------------------ step 3: link, change, unlink - same row, history recorded

def test_link_then_change_updates_the_same_row_with_history(priya, ids, reports):
    pid = ids["P-1001"]
    r = priya.post(f"/api/patients/{pid}/manual", json={
        "sugar": {"value": 168, "date": "2026-09-18", "linked_document_id": reports["fasting_old"]}})
    assert r.status_code == 200 and r.json()["saved"] == ["Sugar level 168 mg/dL (linked to report)"]
    value = next(v for v in priya.get(f"/api/patients/{pid}/manual-values").json() if v["value"] == 168)
    first = db_row(value["id"])
    assert first["linked_document_id"] == reports["fasting_old"]
    assert (first["previous_document_id"], first["link_changed_by"], first["link_changed_date"]) == (None, None, None)

    changed = priya.put(f"/api/patients/{pid}/manual-values/{value['id']}/link", json={"document_id": reports["pp_new"]})
    assert changed.status_code == 200, changed.text
    after = db_row(value["id"])
    assert after["id"] == value["id"]                                   # an UPDATE of the same row
    assert after["linked_document_id"] == reports["pp_new"]
    assert after["previous_document_id"] == reports["fasting_old"]
    assert after["link_changed_by"] and after["link_changed_date"]
    assert changed.json()["linked"]["id"] == reports["pp_new"] and changed.json()["link_changed_by_name"] == "Dr. Priya Nair"
    count = sum(1 for v in priya.get(f"/api/patients/{pid}/manual-values").json() if v["value"] == 168)
    assert count == 1                                                   # no duplicate

    cleared = priya.put(f"/api/patients/{pid}/manual-values/{value['id']}/link", json={"document_id": None}).json()
    row = db_row(value["id"])
    assert cleared["linked"] is None and row["linked_document_id"] is None and row["previous_document_id"] == reports["pp_new"]
    log = [e for e in priya.get(f"/api/patients/{pid}/access-log").json() if e["action"] == "VALUE_LINK_CHANGED"]
    assert len(log) == 2                                                # every change is also in the audit log


def test_link_rules(priya, ids, reports):
    pid = ids["P-1001"]
    bad = priya.post(f"/api/patients/{pid}/manual", json={
        "hba1c": {"value": 7.2, "date": "2026-09-10", "linked_document_id": reports["meena"]}})
    assert bad.status_code == 422                                       # another patient's report
    priya.post(f"/api/patients/{pid}/manual", json={"hba1c": {"value": 7.2, "date": "2026-09-10"}})
    vid = next(v for v in priya.get(f"/api/patients/{pid}/manual-values").json() if v["value"] == 7.2)["id"]
    url = f"/api/patients/{pid}/manual-values/{vid}/link"
    assert priya.put(url, json={"document_id": reports["meena"]}).status_code == 422
    assert priya.put(url, json={"document_id": None}).status_code == 409          # nothing to change
    priya.post(f"/api/patients/{pid}/external-reports/{reports['hba1c']}/reject", json={})
    assert priya.put(url, json={"document_id": reports["hba1c"]}).status_code == 422   # marked not usable


def test_reviewed_values_cannot_be_relinked(priya, ids, reports):
    pid = ids["P-1001"]
    priya.post(f"/api/patients/{pid}/external-reports/{reports['hba1c']}/review", json={"value": 7.6})
    reviewed = point(priya, pid, "HbA1c", 7.6)
    r = priya.put(f"/api/patients/{pid}/manual-values/{reviewed['id']}/link", json={"document_id": reports["kidney"]})
    assert r.status_code == 422


def test_database_refuses_a_silent_link_change(priya, ids, reports):
    pid = ids["P-1001"]
    priya.post(f"/api/patients/{pid}/manual", json={
        "sugar": {"value": 151, "date": "2026-09-18", "linked_document_id": reports["fasting_old"]}})
    vid = next(v for v in priya.get(f"/api/patients/{pid}/manual-values").json() if v["value"] == 151)["id"]
    c = sqlite3.connect(os.environ["UC2_DB_PATH"])
    c.execute("PRAGMA foreign_keys = ON")
    for sql, args in [
        ("UPDATE clinical_events SET linked_document_id = ? WHERE id = ?", (reports["pp_new"], vid)),        # no history
        ("UPDATE clinical_events SET linked_document_id = ?, previous_document_id = ?, link_changed_by = 'x', "
         "link_changed_date = '2026-09-26' WHERE id = ?", (reports["pp_new"], reports["hba1c"], vid)),     # wrong 'before'
        ("UPDATE clinical_events SET previous_document_id = ?, link_changed_by = 'x', link_changed_date = 'y' "
         "WHERE id = ?", (reports["hba1c"], vid)),                                                         # fake history
    ]:
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(sql, args)
        c.rollback()
    c.close()
    assert db_row(vid)["linked_document_id"] == reports["fasting_old"]


# ------------------------------------------------ step 5: the graph always reads the CURRENT link

def test_graph_reference_follows_the_current_link(priya, ids, reports):
    pid = ids["P-1001"]
    priya.post(f"/api/patients/{pid}/manual", json={"sugar": {"value": 177, "date": "2026-09-18"}})
    p = point(priya, pid, "Blood glucose", 177)
    assert p["provenance"]["document_id"] is None
    assert re.fullmatch(r"Entered manually by Dr\. Priya Nair on \d{1,2} \w{3} \d{4} — no report attached\.",
                        p["provenance"]["text"])

    vid = p["id"]
    priya.put(f"/api/patients/{pid}/manual-values/{vid}/link", json={"document_id": reports["fasting_old"]})
    p = point(priya, pid, "Blood glucose", 177)
    assert p["provenance"]["document_id"] == reports["fasting_old"]
    assert re.fullmatch(r"Ramesh Kumar uploaded this Fasting Sugar report on \d{1,2} \w{3} \d{4}\. Reviewed by Dr\. Priya Nair\.",
                        p["provenance"]["text"])

    priya.put(f"/api/patients/{pid}/manual-values/{vid}/link", json={"document_id": reports["pp_new"]})
    p = point(priya, pid, "Blood glucose", 177)
    assert p["provenance"]["document_id"] == reports["pp_new"] and "PP Sugar report" in p["provenance"]["text"]
    assert priya.get(f"/api/patients/{pid}/external-reports/{p['provenance']['document_id']}/file").content == IMG[1]
