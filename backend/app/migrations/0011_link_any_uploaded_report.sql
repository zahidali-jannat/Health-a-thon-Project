-- Link a care-team value to ANY report the patient uploaded.
-- Reports live in two places: external_reports ("Send a test report") and patient_documents ("Send a document" ->
-- lab report, or a file the care team uploaded). Until now a value could only point at the first. This adds
-- linked_upload_id (-> patient_documents) beside linked_document_id (-> external_reports); a value carries at most
-- one of the two, and the triggers below give the new column the same rules the old one has.

ALTER TABLE clinical_events ADD COLUMN linked_upload_id TEXT REFERENCES patient_documents(id);
ALTER TABLE clinical_events ADD COLUMN previous_upload_id TEXT REFERENCES patient_documents(id);

-- Only a care-team test value may point at an uploaded document: this patient's own, a lab report, not rejected,
-- and never together with an external report link.
CREATE TRIGGER trg_upload_link_rules_insert BEFORE INSERT ON clinical_events
WHEN NEW.linked_upload_id IS NOT NULL AND (
     NEW.source != 'care_team_manual' OR NEW.event_type != 'lab' OR NEW.value IS NULL OR NEW.linked_document_id IS NOT NULL
     OR NOT EXISTS (SELECT 1 FROM patient_documents WHERE id = NEW.linked_upload_id AND patient_id = NEW.patient_id
                    AND document_type = 'lab_report' AND review_status != 'rejected'))
BEGIN
    SELECT RAISE(ABORT, 'A value can only be linked to a usable lab report of the same patient.');
END;

CREATE TRIGGER trg_upload_link_rules_update BEFORE UPDATE OF linked_upload_id, linked_document_id ON clinical_events
WHEN NEW.linked_upload_id IS NOT NULL AND NEW.linked_upload_id IS NOT OLD.linked_upload_id AND (
     NEW.source != 'care_team_manual' OR NEW.event_type != 'lab' OR NEW.linked_document_id IS NOT NULL
     OR NOT EXISTS (SELECT 1 FROM patient_documents WHERE id = NEW.linked_upload_id AND patient_id = NEW.patient_id
                    AND document_type = 'lab_report' AND review_status != 'rejected'))
BEGIN
    SELECT RAISE(ABORT, 'A value can only be linked to a usable lab report of the same patient.');
END;

CREATE TRIGGER trg_upload_link_single BEFORE UPDATE OF linked_document_id ON clinical_events
WHEN NEW.linked_document_id IS NOT NULL AND NEW.linked_upload_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'A value is linked to one report at a time.');
END;

-- The link history now covers both columns (replaces the 0008/0009 versions).
DROP TRIGGER IF EXISTS trg_manual_link_change_is_recorded;
CREATE TRIGGER trg_manual_link_change_is_recorded BEFORE UPDATE OF linked_document_id, linked_upload_id ON clinical_events
WHEN OLD.source = 'care_team_manual'
     AND (NEW.linked_document_id IS NOT OLD.linked_document_id OR NEW.linked_upload_id IS NOT OLD.linked_upload_id)
     AND (NEW.previous_document_id IS NOT OLD.linked_document_id OR NEW.previous_upload_id IS NOT OLD.linked_upload_id
          OR NEW.link_changed_by IS NULL OR NEW.link_changed_date IS NULL OR NEW.link_changed_date IS OLD.link_changed_date)
BEGIN
    SELECT RAISE(ABORT, 'A link change must record the previous report, who changed it and when.');
END;

DROP TRIGGER IF EXISTS trg_manual_link_record_is_honest;
CREATE TRIGGER trg_manual_link_record_is_honest BEFORE UPDATE OF previous_document_id, previous_upload_id, link_changed_by,
    link_changed_date ON clinical_events
WHEN NEW.linked_document_id IS OLD.linked_document_id AND NEW.linked_upload_id IS OLD.linked_upload_id
     AND (NEW.previous_document_id IS NOT OLD.previous_document_id OR NEW.previous_upload_id IS NOT OLD.previous_upload_id
          OR NEW.link_changed_by IS NOT OLD.link_changed_by OR NEW.link_changed_date IS NOT OLD.link_changed_date)
BEGIN
    SELECT RAISE(ABORT, 'The link history can only change together with the link.');
END;

-- ------------------------------------------------------------------ care-team corrections to a report's label
-- The uploaded row is never rewritten; the care team's correction sits beside it and wins when shown.
CREATE TABLE report_details (
    report_id     TEXT PRIMARY KEY,                -- an external_reports.id or a patient_documents.id
    patient_id    TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    display_name  TEXT CHECK (display_name IS NULL OR length(trim(display_name)) BETWEEN 1 AND 120),
    report_type   TEXT CHECK (report_type IS NULL OR report_type IN ('egfr', 'hemoglobin', 'hba1c', 'sugar', 'other')),
    test_date     TEXT CHECK (test_date IS NULL OR test_date GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
    updated_by    TEXT NOT NULL REFERENCES clinical_users(id),
    updated_at    TEXT NOT NULL
);
CREATE INDEX idx_report_details_patient ON report_details (patient_id);

CREATE TRIGGER trg_report_details_same_patient BEFORE INSERT ON report_details
WHEN NOT EXISTS (SELECT 1 FROM external_reports WHERE id = NEW.report_id AND patient_id = NEW.patient_id
                 AND document_type = 'lab_report')
     AND NOT EXISTS (SELECT 1 FROM patient_documents WHERE id = NEW.report_id AND patient_id = NEW.patient_id
                     AND document_type = 'lab_report')
BEGIN
    SELECT RAISE(ABORT, 'Report not found for this patient.');
END;

CREATE TRIGGER trg_report_details_fixed_owner BEFORE UPDATE OF report_id, patient_id ON report_details
BEGIN
    SELECT RAISE(ABORT, 'A report correction stays with its report.');
END;

-- ------------------------------------------------------------------ audit: old and new value of every change
ALTER TABLE audit_log ADD COLUMN old_value TEXT;
ALTER TABLE audit_log ADD COLUMN new_value TEXT;

-- ------------------------------------------------------------------ indexes for the report list
CREATE INDEX idx_external_reports_patient_test ON external_reports (patient_id, test_date DESC);
CREATE INDEX idx_documents_patient_type ON patient_documents (patient_id, document_type, uploaded_at DESC);
CREATE INDEX idx_events_linked_upload ON clinical_events (linked_upload_id) WHERE linked_upload_id IS NOT NULL;
