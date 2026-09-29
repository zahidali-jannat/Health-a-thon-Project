"""External Report Review, step 1: a value read from an uploaded report can never exist without its document.

Checked at both levels: the workflow's own save function, and the database itself (so no other code path
- or a hand-written SQL statement - can skip the link either)."""

import sqlite3
from datetime import date

import pytest

from app import repo

SQL_INSERT = ("INSERT INTO clinical_events (patient_id, event_type, name, value, unit, effective_date, source, confidence, "
              "status, asserted_by, linked_document_id, reviewed_by, reviewed_date, created_at) "
              "VALUES (?, 'lab', 'HbA1c', 7.9, '%', '2026-09-20', 'patient_upload_reviewed', 'high', 'resulted', 'x', ?, ?, ?, "
              "'2026-09-26T00:00:00+00:00')")


@pytest.fixture()
def setup(seeded_conn):
    conn, ids = seeded_conn
    priya = repo.find_clinician_for_login(conn, "CLN-PRYA27")
    report = repo.create_external_report(conn, ids["P-1001"], "HbA1c", date(2026, 9, 20), "hba1c.pdf",
                                         "application/pdf", b"%PDF-1.4 outside lab")
    conn.commit()
    return conn, ids, priya, report


def linked_values(conn):
    return conn.execute("SELECT COUNT(*) FROM clinical_events WHERE source = 'patient_upload_reviewed'").fetchone()[0]


def status(conn, report_id):
    return conn.execute("SELECT status FROM external_reports WHERE id = ?", (report_id,)).fetchone()[0]


def test_save_without_document_id_is_refused_by_the_workflow(setup):
    conn, ids, priya, _ = setup
    for missing in (None, ""):
        with pytest.raises(repo.RepoError, match="must be linked"):
            repo.save_reviewed_value(conn, ids["P-1001"], missing, 7.9, priya)
    assert linked_values(conn) == 0


def test_save_without_document_id_is_refused_by_the_database(setup):
    conn, ids, priya, _ = setup
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(SQL_INSERT, (ids["P-1001"], None, priya["id"], "2026-09-26T00:00:00+00:00"))
    conn.rollback()
    assert linked_values(conn) == 0


def test_database_refuses_missing_reviewer_unknown_document_or_other_patients_report(setup):
    conn, ids, priya, report = setup
    cases = [
        (ids["P-1001"], report, None, "2026-09-26T00:00:00+00:00"),            # no reviewer
        (ids["P-1001"], report, priya["id"], None),                            # no review time
        (ids["P-1001"], "no-such-document", priya["id"], "2026-09-26T00:00:00+00:00"),
        (ids["P-1002"], report, priya["id"], "2026-09-26T00:00:00+00:00"),     # someone else's report
    ]
    for args in cases:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(SQL_INSERT, args)
        conn.rollback()
    assert linked_values(conn) == 0 and status(conn, report) == "pending_review"


def test_one_save_writes_value_link_reviewer_and_report_status_together(setup):
    conn, ids, priya, report = setup
    event_id = repo.save_reviewed_value(conn, ids["P-1001"], report, 7.9, priya)
    conn.commit()
    row = conn.execute("SELECT * FROM clinical_events WHERE id = ?", (event_id,)).fetchone()
    assert row["linked_document_id"] == report and row["reviewed_by"] == priya["id"] and row["reviewed_date"]
    assert (row["source"], row["confidence"], row["event_type"], row["value"], row["unit"]) == \
        ("patient_upload_reviewed", "high", "lab", 7.9, "%")
    assert row["effective_date"] == "2026-09-20"                  # the test date, not the upload date
    x = repo.get_external_report(conn, ids["P-1001"], report)
    assert x["status"] == "reviewed" and x["reviewed_by_name"] == "Dr. Priya Nair" and x["value"] == 7.9


def test_a_report_gives_exactly_one_value(setup):
    conn, ids, priya, report = setup
    repo.save_reviewed_value(conn, ids["P-1001"], report, 7.9, priya)
    conn.commit()
    with pytest.raises(repo.RepoError, match="already been reviewed"):
        repo.save_reviewed_value(conn, ids["P-1001"], report, 8.4, priya)
    with pytest.raises(sqlite3.IntegrityError):                    # and the database agrees
        conn.execute(SQL_INSERT, (ids["P-1001"], report, priya["id"], "2026-09-26T00:00:00+00:00"))
    conn.rollback()
    assert linked_values(conn) == 1


def test_failed_save_leaves_nothing_behind(setup):
    """If the insert fails partway, there is no orphan value and the report is still waiting."""
    conn, ids, priya, report = setup
    with pytest.raises(sqlite3.IntegrityError):
        repo.save_reviewed_value(conn, ids["P-1001"], report, None, priya)   # value is required
    conn.rollback()
    assert linked_values(conn) == 0 and status(conn, report) == "pending_review"


def test_report_cannot_be_marked_reviewed_without_its_value(setup):
    conn, _, priya, report = setup
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE external_reports SET status = 'reviewed', reviewed_by = ?, reviewed_at = '2026-09-26' "
                     "WHERE id = ?", (priya["id"], report))
    conn.rollback()
    assert status(conn, report) == "pending_review"


def test_the_link_is_permanent(setup):
    conn, ids, priya, report = setup
    event_id = repo.save_reviewed_value(conn, ids["P-1001"], report, 7.9, priya)
    conn.commit()
    for sql in ("UPDATE clinical_events SET linked_document_id = NULL WHERE id = ?",
                "UPDATE clinical_events SET source = 'care_team_manual' WHERE id = ?",
                "UPDATE clinical_events SET reviewed_by = NULL WHERE id = ?"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(sql, (event_id,))
        conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM external_reports WHERE id = ?", (report,))   # the document can't be removed either
    conn.rollback()
    assert conn.execute("SELECT linked_document_id FROM clinical_events WHERE id = ?", (event_id,)).fetchone()[0] == report


def test_other_sources_cannot_borrow_the_link(setup):
    """Only reviewed values and care-team TEST values may carry a report link (see test_manual_report_links.py)."""
    conn, ids, priya, report = setup
    for source in ("hospital_internal", "patient_upload", "external_hospital_abdm"):
        with pytest.raises(sqlite3.IntegrityError):
            repo.insert_event(conn, ids["P-1001"], "lab", date(2026, 9, 20), source, "high", "resulted", "x",
                              name="HbA1c", value=7.9, unit="%", linked_document_id=report)
        conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):                   # a care-team refill is not a test value
        repo.insert_event(conn, ids["P-1001"], "refill", date(2026, 9, 20), "care_team_manual", "high", "active", "x",
                          name="Metformin", value=30, unit="days", linked_document_id=report)
    conn.rollback()


def test_rejecting_saves_no_value(setup):
    conn, ids, priya, report = setup
    assert repo.reject_external_report(conn, ids["P-1001"], report, priya["id"], "Photo is unreadable")
    conn.commit()
    assert status(conn, report) == "rejected" and linked_values(conn) == 0
    with pytest.raises(repo.RepoError):
        repo.save_reviewed_value(conn, ids["P-1001"], report, 7.9, priya)
