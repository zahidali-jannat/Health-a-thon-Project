-- migrate: foreign_keys=off
-- Medicine Purchase Verification. A patient uploads the bill for their diabetes medicine (a file is compulsory);
-- the care team approves or rejects it. Reuses the External Report Review structure:
--   * the bill is a row in external_reports (the workflow "documents" table), document_type = 'medicine_bill'
--   * a refill row in clinical_events points at it through linked_document_id, starting as 'pending_review'
--   * ONE decision - the bill's status - drives both rows: a trigger moves the refill to 'active' or 'rejected'
--     inside the same statement, so the bill and the refill can never disagree.
-- Both tables are rebuilt because SQLite cannot change CHECK constraints in place (hence foreign_keys=off above;
-- the migration runner verifies every foreign key before committing).

-- every trigger on the two tables is dropped here and recreated at the end
DROP TRIGGER IF EXISTS trg_manual_link_change_is_recorded;
DROP TRIGGER IF EXISTS trg_manual_link_not_rejected_insert;
DROP TRIGGER IF EXISTS trg_manual_link_not_rejected_update;
DROP TRIGGER IF EXISTS trg_manual_link_record_is_honest;
DROP TRIGGER IF EXISTS trg_reviewed_value_link_is_permanent;
DROP TRIGGER IF EXISTS trg_reviewed_value_marks_report;
DROP TRIGGER IF EXISTS trg_reviewed_value_needs_pending_report;
DROP TRIGGER IF EXISTS trg_report_reviewed_needs_value;
DROP TRIGGER IF EXISTS trg_reviewed_report_is_permanent;

-- ------------------------------------------------------------------ documents: lab reports AND medicine bills
CREATE TABLE external_reports_v9 (
    id                      TEXT PRIMARY KEY,                    -- the document_id
    patient_id              TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    document_type           TEXT NOT NULL DEFAULT 'lab_report' CHECK (document_type IN ('lab_report', 'medicine_bill')),
    uploaded_by             TEXT NOT NULL DEFAULT 'patient' CHECK (uploaded_by = 'patient'),
    uploaded_by_patient_id  TEXT NOT NULL REFERENCES patients(id),
    upload_date             TEXT NOT NULL,                       -- the upload timestamp: set by the server, never typed
    disease                 TEXT,                                -- bills: 'Diabetes' (fixed)
    test_type               TEXT,                                -- lab reports only
    test_name               TEXT,
    test_date               TEXT,                                -- lab reports only (from the patient)
    purchase_date           TEXT,                                -- bills: as shown on the bill, entered by the clinician (optional)
    file_name               TEXT NOT NULL,
    content_type            TEXT NOT NULL,
    size_bytes              INTEGER NOT NULL,
    sha256                  TEXT NOT NULL,
    storage_key             TEXT NOT NULL UNIQUE,
    status                  TEXT NOT NULL DEFAULT 'pending_review'
                            CHECK (status IN ('pending_review', 'reviewed', 'rejected', 'reviewed_approved', 'reviewed_rejected')),
    reviewed_by             TEXT REFERENCES clinical_users(id),
    reviewed_at             TEXT,
    reject_code             TEXT CHECK (reject_code IS NULL OR reject_code IN ('unclear', 'medicine_mismatch', 'date_mismatch', 'other')),
    reject_reason           TEXT,
    UNIQUE (id, patient_id),
    CHECK ((document_type = 'lab_report'
                AND test_type IN ('HbA1c', 'Fasting Sugar', 'PP Sugar', 'Other') AND test_date IS NOT NULL
                AND status IN ('pending_review', 'reviewed', 'rejected') AND purchase_date IS NULL AND disease IS NULL)
        OR (document_type = 'medicine_bill'
                AND test_type IS NULL AND test_name IS NULL AND test_date IS NULL AND disease = 'Diabetes'
                AND status IN ('pending_review', 'reviewed_approved', 'reviewed_rejected'))),
    CHECK (test_type IS NULL OR test_type != 'Other' OR length(trim(COALESCE(test_name, ''))) >= 2),
    CHECK ((status = 'pending_review') = (reviewed_by IS NULL AND reviewed_at IS NULL)),
    -- a rejected bill always says why
    CHECK (status != 'reviewed_rejected' OR (reject_code IS NOT NULL AND length(trim(COALESCE(reject_reason, ''))) >= 2)),
    CHECK (purchase_date IS NULL OR status = 'reviewed_approved')
);
INSERT INTO external_reports_v9 (id, patient_id, document_type, uploaded_by, uploaded_by_patient_id, upload_date, test_type,
                                 test_name, test_date, file_name, content_type, size_bytes, sha256, storage_key, status,
                                 reviewed_by, reviewed_at, reject_reason)
SELECT id, patient_id, 'lab_report', uploaded_by, uploaded_by_patient_id, upload_date, test_type, test_name, test_date,
       file_name, content_type, size_bytes, sha256, storage_key, status, reviewed_by, reviewed_at, reject_reason
FROM external_reports;
DROP TABLE external_reports;
ALTER TABLE external_reports_v9 RENAME TO external_reports;
CREATE INDEX idx_external_reports_status ON external_reports (document_type, status, upload_date);
CREATE INDEX idx_external_reports_patient ON external_reports (patient_id, upload_date);

-- ------------------------------------------------------------------ events: a refill can wait for review
CREATE TABLE clinical_events_v9 (
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
    status               TEXT NOT NULL CHECK (status IN ('ordered', 'active', 'resulted', 'stopped', 'pending_review', 'rejected')),
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
    previous_document_id TEXT,
    link_changed_by      TEXT REFERENCES clinical_users(id),
    link_changed_date    TEXT,
    created_at           TEXT NOT NULL,
    FOREIGN KEY (linked_document_id, patient_id) REFERENCES external_reports (id, patient_id),
    FOREIGN KEY (previous_document_id, patient_id) REFERENCES external_reports (id, patient_id),
    CHECK (source != 'patient_upload_reviewed' OR (
        linked_document_id IS NOT NULL AND reviewed_by IS NOT NULL AND reviewed_date IS NOT NULL
        AND confidence = 'high' AND value IS NOT NULL AND event_type = 'lab')),
    -- who may carry a document link: reviewed values, care-team test values, and refills backed by a bill
    CHECK (linked_document_id IS NULL OR source = 'patient_upload_reviewed'
           OR (source = 'care_team_manual' AND event_type = 'lab' AND value IS NOT NULL)
           OR (source = 'patient_upload' AND event_type = 'refill')),
    -- "waiting for review" and "rejected" exist only for a refill backed by a bill
    CHECK (status NOT IN ('pending_review', 'rejected')
           OR (source = 'patient_upload' AND event_type = 'refill' AND linked_document_id IS NOT NULL)),
    -- a bill-backed refill that was decided records who decided and when
    CHECK (NOT (source = 'patient_upload' AND event_type = 'refill' AND linked_document_id IS NOT NULL
                AND status IN ('active', 'rejected')) OR (reviewed_by IS NOT NULL AND reviewed_date IS NOT NULL)),
    CHECK ((link_changed_by IS NULL) = (link_changed_date IS NULL)),
    CHECK (previous_document_id IS NULL OR link_changed_by IS NOT NULL)
);
INSERT INTO clinical_events_v9 SELECT id, patient_id, event_type, name, value, unit, effective_date, source, confidence,
       status, asserted_by, recorded_by_user_id, facility, clinician, note, consent_id, document_id, linked_document_id,
       reviewed_by, reviewed_date, previous_document_id, link_changed_by, link_changed_date, created_at
FROM clinical_events;
DROP TABLE clinical_events;
ALTER TABLE clinical_events_v9 RENAME TO clinical_events;
CREATE INDEX idx_events_patient ON clinical_events (patient_id, event_type, effective_date);
CREATE UNIQUE INDEX idx_events_one_value_per_report ON clinical_events (linked_document_id)
    WHERE source = 'patient_upload_reviewed';
CREATE UNIQUE INDEX idx_events_one_refill_per_bill ON clinical_events (linked_document_id)
    WHERE source = 'patient_upload';

-- ------------------------------------------------------------------ notifications (email + in-app, always the same text)
CREATE TABLE patient_notifications (
    id            INTEGER PRIMARY KEY,
    patient_id    TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN ('bill_approved', 'bill_rejected')),
    document_id   TEXT NOT NULL,
    message       TEXT NOT NULL,
    email_to      TEXT,
    email_status  TEXT NOT NULL CHECK (email_status IN ('simulated', 'no_email_on_file')),
    created_at    TEXT NOT NULL,
    read_at       TEXT,
    FOREIGN KEY (document_id, patient_id) REFERENCES external_reports (id, patient_id)
);
CREATE INDEX idx_notifications_patient ON patient_notifications (patient_id, created_at);

-- ================================================================== triggers
-- External Report Review (0006/0008), unchanged in meaning; lab reports only.
CREATE TRIGGER trg_reviewed_value_needs_pending_report BEFORE INSERT ON clinical_events
WHEN NEW.source = 'patient_upload_reviewed'
     AND NOT EXISTS (SELECT 1 FROM external_reports WHERE id = NEW.linked_document_id AND patient_id = NEW.patient_id
                     AND document_type = 'lab_report' AND status = 'pending_review')
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

CREATE TRIGGER trg_reviewed_report_is_permanent BEFORE UPDATE ON external_reports
WHEN OLD.status != 'pending_review'
BEGIN
    SELECT RAISE(ABORT, 'A decided report cannot be changed.');
END;

CREATE TRIGGER trg_reviewed_value_link_is_permanent BEFORE UPDATE OF linked_document_id, source, reviewed_by, reviewed_date,
    patient_id ON clinical_events
WHEN OLD.source = 'patient_upload_reviewed'
BEGIN
    SELECT RAISE(ABORT, 'A reviewed value stays linked to its report; it cannot be changed.');
END;

-- Link to Report (0008): only to a usable LAB report (never to a medicine bill).
CREATE TRIGGER trg_manual_link_not_rejected_insert BEFORE INSERT ON clinical_events
WHEN NEW.source = 'care_team_manual' AND NEW.linked_document_id IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM external_reports WHERE id = NEW.linked_document_id
                     AND document_type = 'lab_report' AND status != 'rejected')
BEGIN
    SELECT RAISE(ABORT, 'A value can only be linked to a usable lab report.');
END;

CREATE TRIGGER trg_manual_link_not_rejected_update BEFORE UPDATE OF linked_document_id ON clinical_events
WHEN NEW.source = 'care_team_manual' AND NEW.linked_document_id IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM external_reports WHERE id = NEW.linked_document_id
                     AND document_type = 'lab_report' AND status != 'rejected')
BEGIN
    SELECT RAISE(ABORT, 'A value can only be linked to a usable lab report.');
END;

CREATE TRIGGER trg_manual_link_change_is_recorded BEFORE UPDATE OF linked_document_id ON clinical_events
WHEN OLD.source = 'care_team_manual' AND NEW.linked_document_id IS NOT OLD.linked_document_id
     AND (NEW.previous_document_id IS NOT OLD.linked_document_id OR NEW.link_changed_by IS NULL
          OR NEW.link_changed_date IS NULL OR NEW.link_changed_date IS OLD.link_changed_date)
BEGIN
    SELECT RAISE(ABORT, 'A link change must record the previous report, who changed it and when.');
END;

CREATE TRIGGER trg_manual_link_record_is_honest BEFORE UPDATE OF previous_document_id, link_changed_by, link_changed_date
    ON clinical_events
WHEN NEW.linked_document_id IS OLD.linked_document_id
     AND (NEW.previous_document_id IS NOT OLD.previous_document_id OR NEW.link_changed_by IS NOT OLD.link_changed_by
          OR NEW.link_changed_date IS NOT OLD.link_changed_date)
BEGIN
    SELECT RAISE(ABORT, 'The link history can only change together with the link.');
END;

-- Medicine bills ------------------------------------------------------------------
-- A bill-backed refill must point at a medicine bill of the same patient that is waiting for review.
CREATE TRIGGER trg_bill_refill_needs_pending_bill BEFORE INSERT ON clinical_events
WHEN NEW.source = 'patient_upload' AND NEW.linked_document_id IS NOT NULL
     AND NOT (NEW.status = 'pending_review' AND EXISTS (SELECT 1 FROM external_reports WHERE id = NEW.linked_document_id
              AND patient_id = NEW.patient_id AND document_type = 'medicine_bill' AND status = 'pending_review'))
BEGIN
    SELECT RAISE(ABORT, 'A refill from a bill starts as pending review and must point at that bill.');
END;

-- A bill can be decided only if its refill row exists.
CREATE TRIGGER trg_bill_decision_needs_refill BEFORE UPDATE OF status ON external_reports
WHEN NEW.document_type = 'medicine_bill' AND NEW.status != OLD.status
     AND NOT EXISTS (SELECT 1 FROM clinical_events WHERE linked_document_id = NEW.id AND source = 'patient_upload')
BEGIN
    SELECT RAISE(ABORT, 'This bill has no refill record to update.');
END;

-- The decision on the bill IS the decision on the refill - same statement, never out of step.
CREATE TRIGGER trg_bill_decision_updates_refill AFTER UPDATE OF status ON external_reports
WHEN NEW.document_type = 'medicine_bill' AND OLD.status = 'pending_review' AND NEW.status != 'pending_review'
BEGIN
    UPDATE clinical_events
    SET status = CASE NEW.status WHEN 'reviewed_approved' THEN 'active' ELSE 'rejected' END,
        confidence = CASE NEW.status WHEN 'reviewed_approved' THEN 'high' ELSE 'low' END,
        reviewed_by = NEW.reviewed_by, reviewed_date = NEW.reviewed_at,
        effective_date = CASE WHEN NEW.status = 'reviewed_approved' THEN COALESCE(NEW.purchase_date, effective_date)
                              ELSE effective_date END
    WHERE linked_document_id = NEW.id AND patient_id = NEW.patient_id AND source = 'patient_upload';
END;

-- ...and a bill-backed refill's status can only move together with its bill.
CREATE TRIGGER trg_bill_refill_follows_bill BEFORE UPDATE OF status, linked_document_id ON clinical_events
WHEN OLD.source = 'patient_upload' AND OLD.linked_document_id IS NOT NULL
     AND (NEW.linked_document_id IS NOT OLD.linked_document_id
          OR NEW.status IS NOT (SELECT CASE x.status WHEN 'reviewed_approved' THEN 'active'
                                                     WHEN 'reviewed_rejected' THEN 'rejected' ELSE 'pending_review' END
                                FROM external_reports x WHERE x.id = OLD.linked_document_id))
BEGIN
    SELECT RAISE(ABORT, 'A refill from a bill changes only when the bill is approved or rejected.');
END;
