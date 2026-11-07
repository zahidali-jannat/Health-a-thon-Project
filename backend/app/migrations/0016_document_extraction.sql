-- Document extraction (CareCue Phase 1). Every uploaded report or prescription is read automatically and its values
-- are SUGGESTED to the clinical team - nothing here is a clinical record until a person verifies it.
--   document_processing  one row per uploaded file: its processing / verification state and why a person must look
--   document_pages       the text of each page and how it was read (PDF text or OCR), with quality notes
--   extracted_fields     each value read from the file: as printed, the page and the line it came from, how sure
--   validation_issues    what the deterministic checks found: missing unit, implausible value, conflict, duplicate...
-- Additive only: no existing table changes.

CREATE TABLE document_processing (
    id                 TEXT PRIMARY KEY,
    patient_id         TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    source_kind        TEXT NOT NULL CHECK (source_kind IN ('document', 'external_report')),
    source_id          TEXT NOT NULL,                    -- patient_documents.id or external_reports.id
    document_kind      TEXT NOT NULL CHECK (document_kind IN ('prescription', 'lab_report')),
    expected_test      TEXT,                             -- the test the patient chose (report_tests key), if any
    sha256             TEXT NOT NULL,
    content_type       TEXT NOT NULL,
    page_count         INTEGER,
    status             TEXT NOT NULL DEFAULT 'uploaded' CHECK (status IN ('uploaded', 'processing', 'extraction_completed',
                           'needs_review', 'verified', 'corrected', 'rejected', 'failed')),
    review_reason      TEXT CHECK (review_reason IS NULL OR review_reason IN ('handwritten_or_unclear', 'ocr_not_configured',
                           'unsupported_format', 'issues_found', 'nothing_found', 'too_many_pages')),
    extraction_method  TEXT CHECK (extraction_method IS NULL OR extraction_method IN ('pdf_text', 'ocr_sarvam', 'pdf_text+ocr_sarvam')),
    provider_job_ids   TEXT,                             -- JSON list: the OCR provider's job ids, for tracing
    attempts           INTEGER NOT NULL DEFAULT 0,
    error              TEXT,                             -- the last failure, plain text (no patient content)
    decided_by         TEXT REFERENCES clinical_users(id),
    decided_at         TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    processed_at       TEXT,
    UNIQUE (source_kind, source_id),
    CHECK ((status IN ('verified', 'corrected', 'rejected')) = (decided_by IS NOT NULL AND decided_at IS NOT NULL))
);
CREATE INDEX idx_processing_patient ON document_processing (patient_id, created_at);
CREATE INDEX idx_processing_status ON document_processing (status, updated_at);

-- The allowed moves. A decision (verified / corrected / rejected) is final. The team may decide before the automatic
-- reading finishes (uploaded / processing -> decided): a person's decision never waits for the machine.
CREATE TRIGGER trg_processing_transition BEFORE UPDATE OF status ON document_processing
WHEN NEW.status != OLD.status AND NOT (
       (OLD.status = 'uploaded' AND NEW.status IN ('processing', 'needs_review', 'failed', 'verified', 'corrected', 'rejected'))
    OR (OLD.status = 'processing' AND NEW.status IN ('extraction_completed', 'needs_review', 'failed', 'verified', 'corrected', 'rejected'))
    OR (OLD.status = 'extraction_completed' AND NEW.status IN ('needs_review', 'verified', 'corrected', 'rejected', 'processing'))
    OR (OLD.status = 'needs_review' AND NEW.status IN ('verified', 'corrected', 'rejected', 'processing'))
    OR (OLD.status = 'failed' AND NEW.status IN ('processing', 'needs_review', 'verified', 'corrected', 'rejected')))
BEGIN
    SELECT RAISE(ABORT, 'This processing step is not allowed.');
END;

CREATE TABLE document_pages (
    processing_id  TEXT NOT NULL REFERENCES document_processing(id) ON DELETE CASCADE,
    page_no        INTEGER NOT NULL CHECK (page_no >= 1),
    method         TEXT NOT NULL CHECK (method IN ('pdf_text', 'ocr_sarvam')),
    text           TEXT NOT NULL,
    quality        TEXT,                                 -- JSON list of notes: blank, low_resolution, blurry, little_text
    PRIMARY KEY (processing_id, page_no)
);

CREATE TABLE extracted_fields (
    id                INTEGER PRIMARY KEY,
    processing_id     TEXT NOT NULL REFERENCES document_processing(id) ON DELETE CASCADE,
    patient_id        TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    field_kind        TEXT NOT NULL CHECK (field_kind IN ('observation', 'medication', 'allergy')),
    analyte           TEXT,                              -- standard key for observations: hba1c, fpg, ldl, ...
    name              TEXT NOT NULL,                     -- as printed
    value_text        TEXT,                              -- as printed; NULL when the value was not readable
    value_num         REAL,
    unit              TEXT,                              -- as printed; NULL when none printed (never guessed)
    normalized_value  REAL,                              -- only when a supported conversion was applied
    normalized_unit   TEXT,
    conversion        TEXT,                              -- e.g. "mmol/L × 18.016 = mg/dL"
    reference_range   TEXT,                              -- as printed
    observed_on       TEXT,                              -- ISO date read from the report, if any
    date_text         TEXT,                              -- the date as printed
    lab_name          TEXT,
    page_no           INTEGER,
    source_text       TEXT NOT NULL,                     -- the line the value was read from
    method            TEXT NOT NULL CHECK (method IN ('pdf_text', 'ocr_sarvam')),
    confidence        TEXT NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
    status            TEXT NOT NULL DEFAULT 'needs_review' CHECK (status IN ('needs_review', 'verified', 'corrected', 'rejected')),
    final_value_text  TEXT,                              -- what the team saved, when it differs
    final_unit        TEXT,
    reviewed_by       TEXT REFERENCES clinical_users(id),
    reviewed_at       TEXT,
    created_at        TEXT NOT NULL,
    CHECK ((status = 'needs_review') = (reviewed_by IS NULL))
);
CREATE INDEX idx_fields_processing ON extracted_fields (processing_id);
CREATE INDEX idx_fields_patient ON extracted_fields (patient_id, analyte, observed_on);

CREATE TABLE validation_issues (
    id             INTEGER PRIMARY KEY,
    processing_id  TEXT NOT NULL REFERENCES document_processing(id) ON DELETE CASCADE,
    field_id       INTEGER REFERENCES extracted_fields(id) ON DELETE CASCADE,
    code           TEXT NOT NULL,
    severity       TEXT NOT NULL CHECK (severity IN ('error', 'warning', 'info')),
    message        TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
CREATE INDEX idx_issues_processing ON validation_issues (processing_id);
