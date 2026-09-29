-- "Link to Report" for values the care team types in by hand (sugar, HbA1c, hemoglobin, eGFR).
-- Reuses linked_document_id -> external_reports from migration 0006. A manual value may be linked to a report,
-- re-linked, or unlinked later; every change is recorded ON the row (previous_document_id, link_changed_by,
-- link_changed_date) and the database refuses a change that doesn't fill them in.
-- Values reviewed through External Report Review stay permanently tied to their report, exactly as before.
--
-- Manual test values also become clinical_events rows (event_type 'lab'), so they can carry the link and appear on
-- the lab graphs. Existing care-team values are moved over unchanged (value, unit, date, who entered them).

DROP TRIGGER IF EXISTS trg_report_reviewed_needs_value;     -- references clinical_events; recreated below

CREATE TABLE clinical_events_v8 (
    id                   INTEGER PRIMARY KEY,
    patient_id           TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    event_type           TEXT NOT NULL CHECK (event_type IN ('refill', 'engagement_log', 'visit', 'vital', 'screening',
                                                             'hypo_event', 'lab')),
    name                 TEXT,
    value                REAL,
    unit                 TEXT,
    effective_date       TEXT NOT NULL,
    source               TEXT NOT NULL CHECK (source IN ('hospital_internal', 'patient_upload', 'external_hospital_abdm',
                                                         'care_team_manual', 'patient_upload_reviewed')),
    confidence           TEXT NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
    status               TEXT NOT NULL CHECK (status IN ('ordered', 'active', 'resulted', 'stopped')),
    asserted_by          TEXT NOT NULL,
    recorded_by_user_id  TEXT REFERENCES clinical_users(id),
    facility             TEXT,
    clinician            TEXT,
    note                 TEXT,
    consent_id           INTEGER REFERENCES consent_requests(id),
    document_id          TEXT REFERENCES patient_documents(id) ON DELETE SET NULL,
    linked_document_id   TEXT,
    reviewed_by          TEXT REFERENCES clinical_users(id),
    reviewed_date        TEXT,
    previous_document_id TEXT,                  -- the report it was linked to before the last change (NULL = none)
    link_changed_by      TEXT REFERENCES clinical_users(id),
    link_changed_date    TEXT,
    created_at           TEXT NOT NULL,
    FOREIGN KEY (linked_document_id, patient_id) REFERENCES external_reports (id, patient_id),
    FOREIGN KEY (previous_document_id, patient_id) REFERENCES external_reports (id, patient_id),
    -- a reviewed external value can never exist without its document, its reviewer and the review time
    CHECK (source != 'patient_upload_reviewed' OR (
        linked_document_id IS NOT NULL AND reviewed_by IS NOT NULL AND reviewed_date IS NOT NULL
        AND confidence = 'high' AND value IS NOT NULL AND event_type = 'lab')),
    -- only reviewed values and care-team test values carry a report link
    CHECK (linked_document_id IS NULL OR source = 'patient_upload_reviewed'
           OR (source = 'care_team_manual' AND event_type = 'lab' AND value IS NOT NULL)),
    -- the change record is complete or empty
    CHECK ((link_changed_by IS NULL) = (link_changed_date IS NULL)),
    CHECK (previous_document_id IS NULL OR link_changed_by IS NOT NULL)
);

INSERT INTO clinical_events_v8 (id, patient_id, event_type, name, value, unit, effective_date, source, confidence, status,
                                asserted_by, recorded_by_user_id, facility, clinician, note, consent_id, document_id,
                                linked_document_id, reviewed_by, reviewed_date, created_at)
SELECT id, patient_id, event_type, name, value, unit, effective_date, source, confidence, status,
       asserted_by, recorded_by_user_id, facility, clinician, note, consent_id, document_id,
       linked_document_id, reviewed_by, reviewed_date, created_at
FROM clinical_events;
DROP TABLE clinical_events;
ALTER TABLE clinical_events_v8 RENAME TO clinical_events;
CREATE INDEX idx_events_patient ON clinical_events (patient_id, event_type, effective_date);
-- one reviewed value per report (manual values may point at a report that also gave a reviewed value)
CREATE UNIQUE INDEX idx_events_one_value_per_report ON clinical_events (linked_document_id)
    WHERE source = 'patient_upload_reviewed';

-- Move existing care-team test values over: typed sugar (was a "vital") and care-team lab results.
UPDATE clinical_events SET event_type = 'lab',
       asserted_by = COALESCE((SELECT 'Entered by ' || u.full_name FROM clinical_users u WHERE u.id = recorded_by_user_id),
                              asserted_by)
WHERE source = 'care_team_manual' AND event_type = 'vital' AND name = 'Blood glucose';

INSERT INTO clinical_events (patient_id, event_type, name, value, unit, effective_date, source, confidence, status,
                             asserted_by, recorded_by_user_id, created_at)
SELECT t.patient_id, 'lab', t.test_name, t.numeric_value, t.unit, t.test_date, 'care_team_manual', 'high', 'resulted',
       r.laboratory_name, r.entered_by_user_id, t.created_at
FROM lab_test_results t JOIN lab_reports r ON r.id = t.report_id AND r.patient_id = t.patient_id
WHERE r.source = 'care_team_manual' AND t.numeric_value IS NOT NULL;
DELETE FROM lab_test_results WHERE numeric_value IS NOT NULL
    AND report_id IN (SELECT id FROM lab_reports WHERE source = 'care_team_manual');
DELETE FROM lab_reports WHERE source = 'care_team_manual'
    AND NOT EXISTS (SELECT 1 FROM lab_test_results t WHERE t.report_id = lab_reports.id);

-- ------------------------------------------------------------------ External Report Review triggers (0006), now scoped
-- to reviewed values only, so manual links don't touch the review queue.
CREATE TRIGGER trg_reviewed_value_needs_pending_report BEFORE INSERT ON clinical_events
WHEN NEW.source = 'patient_upload_reviewed'
     AND NOT EXISTS (SELECT 1 FROM external_reports WHERE id = NEW.linked_document_id
                     AND patient_id = NEW.patient_id AND status = 'pending_review')
BEGIN
    SELECT RAISE(ABORT, 'This report is not waiting for review, so no value can be saved from it.');
END;

CREATE TRIGGER trg_reviewed_value_marks_report AFTER INSERT ON clinical_events
WHEN NEW.source = 'patient_upload_reviewed'
BEGIN
    UPDATE external_reports SET status = 'reviewed', reviewed_by = NEW.reviewed_by, reviewed_at = NEW.reviewed_date
    WHERE id = NEW.linked_document_id AND patient_id = NEW.patient_id;
END;

CREATE TRIGGER trg_report_reviewed_needs_value BEFORE UPDATE OF status ON external_reports
WHEN NEW.status = 'reviewed'
     AND NOT EXISTS (SELECT 1 FROM clinical_events WHERE linked_document_id = NEW.id AND source = 'patient_upload_reviewed')
BEGIN
    SELECT RAISE(ABORT, 'A report is marked reviewed only by saving the value read from it.');
END;

-- A reviewed value stays linked to the report it was read from - it can never be re-linked.
CREATE TRIGGER trg_reviewed_value_link_is_permanent BEFORE UPDATE OF linked_document_id, source, reviewed_by, reviewed_date,
    patient_id ON clinical_events
WHEN OLD.source = 'patient_upload_reviewed'
BEGIN
    SELECT RAISE(ABORT, 'A reviewed value stays linked to its report; it cannot be changed.');
END;

-- ------------------------------------------------------------------ manual links
-- A manual value can't be linked to a report that was marked "not usable".
CREATE TRIGGER trg_manual_link_not_rejected_insert BEFORE INSERT ON clinical_events
WHEN NEW.source = 'care_team_manual' AND NEW.linked_document_id IS NOT NULL
     AND EXISTS (SELECT 1 FROM external_reports WHERE id = NEW.linked_document_id AND status = 'rejected')
BEGIN
    SELECT RAISE(ABORT, 'That report was marked as not usable, so a value cannot be linked to it.');
END;

CREATE TRIGGER trg_manual_link_not_rejected_update BEFORE UPDATE OF linked_document_id ON clinical_events
WHEN NEW.source = 'care_team_manual' AND NEW.linked_document_id IS NOT NULL
     AND EXISTS (SELECT 1 FROM external_reports WHERE id = NEW.linked_document_id AND status = 'rejected')
BEGIN
    SELECT RAISE(ABORT, 'That report was marked as not usable, so a value cannot be linked to it.');
END;

-- Every change of a manual link must say what it was before, who changed it and when - never a silent overwrite.
CREATE TRIGGER trg_manual_link_change_is_recorded BEFORE UPDATE OF linked_document_id ON clinical_events
WHEN OLD.source = 'care_team_manual' AND NEW.linked_document_id IS NOT OLD.linked_document_id
     AND (NEW.previous_document_id IS NOT OLD.linked_document_id OR NEW.link_changed_by IS NULL
          OR NEW.link_changed_date IS NULL OR NEW.link_changed_date IS OLD.link_changed_date)
BEGIN
    SELECT RAISE(ABORT, 'A link change must record the previous report, who changed it and when.');
END;

-- The change record itself can't be rewritten without an actual link change.
CREATE TRIGGER trg_manual_link_record_is_honest BEFORE UPDATE OF previous_document_id, link_changed_by, link_changed_date
    ON clinical_events
WHEN NEW.linked_document_id IS OLD.linked_document_id
     AND (NEW.previous_document_id IS NOT OLD.previous_document_id OR NEW.link_changed_by IS NOT OLD.link_changed_by
          OR NEW.link_changed_date IS NOT OLD.link_changed_date)
BEGIN
    SELECT RAISE(ABORT, 'The link history can only change together with the link.');
END;
