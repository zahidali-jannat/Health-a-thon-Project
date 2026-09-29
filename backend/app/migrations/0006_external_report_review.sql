-- External Report Review: a patient uploads a report from an OUTSIDE lab/clinic, a clinician reads it
-- and types the value. The value and the document it came from must never become disconnected, so the
-- link is enforced by the database itself - not just by application code.

-- 1. The workflow's documents table: one uploaded report = one test.
CREATE TABLE external_reports (
    id                      TEXT PRIMARY KEY,                    -- the document_id
    patient_id              TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    uploaded_by             TEXT NOT NULL DEFAULT 'patient' CHECK (uploaded_by = 'patient'),
    uploaded_by_patient_id  TEXT NOT NULL REFERENCES patients(id),
    upload_date             TEXT NOT NULL,                       -- set by the server, never typed
    test_type               TEXT NOT NULL CHECK (test_type IN ('HbA1c', 'Fasting Sugar', 'PP Sugar', 'Other')),
    test_name               TEXT,                                -- "Other" only: what the patient says the test is
    test_date               TEXT NOT NULL,                       -- when the test was done (from the patient)
    file_name               TEXT NOT NULL,                       -- display only - never used as a path
    content_type            TEXT NOT NULL,
    size_bytes              INTEGER NOT NULL,
    sha256                  TEXT NOT NULL,
    storage_key             TEXT NOT NULL UNIQUE,
    status                  TEXT NOT NULL DEFAULT 'pending_review'
                            CHECK (status IN ('pending_review', 'reviewed', 'rejected')),
    reviewed_by             TEXT REFERENCES clinical_users(id),
    reviewed_at             TEXT,
    reject_reason           TEXT,
    UNIQUE (id, patient_id),
    CHECK (test_type != 'Other' OR length(trim(COALESCE(test_name, ''))) >= 2),
    -- a decided report always records who decided and when; a pending one has neither
    CHECK ((status = 'pending_review') = (reviewed_by IS NULL AND reviewed_at IS NULL))
);
CREATE INDEX idx_external_reports_status ON external_reports (status, upload_date);
CREATE INDEX idx_external_reports_patient ON external_reports (patient_id, upload_date);

-- 2. clinical_events gains the link. SQLite can't change CHECK constraints in place, so the table is rebuilt
--    (nothing references clinical_events by foreign key, so this is safe).
CREATE TABLE clinical_events_v6 (
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
    created_at           TEXT NOT NULL,
    -- the link must point at a report of THIS patient
    FOREIGN KEY (linked_document_id, patient_id) REFERENCES external_reports (id, patient_id),
    -- a reviewed external value can never exist without its document, its reviewer and the review time
    CHECK (source != 'patient_upload_reviewed' OR (
        linked_document_id IS NOT NULL AND reviewed_by IS NOT NULL AND reviewed_date IS NOT NULL
        AND confidence = 'high' AND value IS NOT NULL AND event_type = 'lab')),
    -- and the link is used only by this workflow
    CHECK (linked_document_id IS NULL OR source = 'patient_upload_reviewed')
);
INSERT INTO clinical_events_v6 (id, patient_id, event_type, name, value, unit, effective_date, source, confidence, status,
                                asserted_by, recorded_by_user_id, facility, clinician, note, consent_id, document_id, created_at)
SELECT id, patient_id, event_type, name, value, unit, effective_date, source, confidence, status,
       asserted_by, recorded_by_user_id, facility, clinician, note, consent_id, document_id, created_at
FROM clinical_events;
DROP TABLE clinical_events;
ALTER TABLE clinical_events_v6 RENAME TO clinical_events;
CREATE INDEX idx_events_patient ON clinical_events (patient_id, event_type, effective_date);
-- one report gives exactly one value
CREATE UNIQUE INDEX idx_events_one_value_per_report ON clinical_events (linked_document_id) WHERE linked_document_id IS NOT NULL;

-- 3. Saving the value IS the review. Inserting the linked value flips its report to "reviewed" inside the same
--    statement; if the report isn't pending (already reviewed, rejected, or missing) the whole insert is undone.
CREATE TRIGGER trg_reviewed_value_needs_pending_report BEFORE INSERT ON clinical_events
WHEN NEW.linked_document_id IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM external_reports WHERE id = NEW.linked_document_id
                     AND patient_id = NEW.patient_id AND status = 'pending_review')
BEGIN
    SELECT RAISE(ABORT, 'This report is not waiting for review, so no value can be saved from it.');
END;

CREATE TRIGGER trg_reviewed_value_marks_report AFTER INSERT ON clinical_events
WHEN NEW.linked_document_id IS NOT NULL
BEGIN
    UPDATE external_reports SET status = 'reviewed', reviewed_by = NEW.reviewed_by, reviewed_at = NEW.reviewed_date
    WHERE id = NEW.linked_document_id AND patient_id = NEW.patient_id;
END;

-- A report can only become "reviewed" through that insert - never by a bare status update.
CREATE TRIGGER trg_report_reviewed_needs_value BEFORE UPDATE OF status ON external_reports
WHEN NEW.status = 'reviewed'
     AND NOT EXISTS (SELECT 1 FROM clinical_events WHERE linked_document_id = NEW.id)
BEGIN
    SELECT RAISE(ABORT, 'A report is marked reviewed only by saving the value read from it.');
END;

-- Once written, the link (and what makes the value traceable) can never be changed or removed.
CREATE TRIGGER trg_reviewed_value_link_is_permanent BEFORE UPDATE OF linked_document_id, source, reviewed_by, reviewed_date,
    patient_id ON clinical_events
WHEN OLD.linked_document_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'A reviewed value stays linked to its report; it cannot be changed.');
END;

CREATE TRIGGER trg_reviewed_report_is_permanent BEFORE UPDATE ON external_reports
WHEN OLD.status != 'pending_review'
BEGIN
    SELECT RAISE(ABORT, 'A decided report cannot be changed.');
END;
