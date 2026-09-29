-- UC2 Consultation Readiness - initial schema.
-- Source of truth: this database for structured data; file storage for uploaded files.

-- ------------------------------------------------------------------ people & access

CREATE TABLE patients (
    id             TEXT PRIMARY KEY,                 -- UUID, never the phone number
    patient_code   TEXT NOT NULL UNIQUE,             -- human-friendly, e.g. P-1001
    full_name      TEXT NOT NULL,
    phone          TEXT NOT NULL UNIQUE,             -- login / contact attribute only
    email          TEXT,
    date_of_birth  TEXT,
    sex            TEXT CHECK (sex IN ('M', 'F', 'O')),
    abha_number    TEXT UNIQUE,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE clinical_users (
    id              TEXT PRIMARY KEY,                -- UUID
    clinician_code  TEXT NOT NULL UNIQUE,            -- system-generated, e.g. CLN-7K3Q9P
    full_name       TEXT NOT NULL,
    phone           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- "ClinicalAccess": a clinician may see a patient only while an active row exists here.
CREATE TABLE care_team_assignments (
    clinical_user_id  TEXT NOT NULL REFERENCES clinical_users(id) ON DELETE CASCADE,
    patient_id        TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    granted_by        TEXT REFERENCES clinical_users(id),
    granted_at        TEXT NOT NULL,
    revoked_at        TEXT,
    PRIMARY KEY (clinical_user_id, patient_id)
);
CREATE INDEX idx_care_team_patient ON care_team_assignments (patient_id);

CREATE TABLE auth_sessions (
    id                INTEGER PRIMARY KEY,
    token_hash        TEXT NOT NULL UNIQUE,          -- sha256 of the cookie token; the token itself is never stored
    patient_id        TEXT REFERENCES patients(id) ON DELETE CASCADE,
    clinical_user_id  TEXT REFERENCES clinical_users(id) ON DELETE CASCADE,
    created_at        TEXT NOT NULL,
    expires_at        TEXT NOT NULL,
    revoked_at        TEXT,
    CHECK ((patient_id IS NULL) <> (clinical_user_id IS NULL))
);

CREATE TABLE patient_login_codes (
    id          INTEGER PRIMARY KEY,
    patient_id  TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    code_hash   TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    expires_at  TEXT NOT NULL,
    used_at     TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX idx_login_codes_patient ON patient_login_codes (patient_id, created_at);

-- ------------------------------------------------------------------ documents & labs

CREATE TABLE patient_documents (
    id                      TEXT PRIMARY KEY,        -- UUID
    patient_id              TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    document_type           TEXT NOT NULL CHECK (document_type IN ('lab_report', 'prescription', 'glucometer_photo', 'other')),
    description             TEXT,                    -- e.g. "HbA1c test" (required for lab reports)
    file_name               TEXT NOT NULL,           -- original name, display only - never used as a path
    content_type            TEXT NOT NULL,
    size_bytes              INTEGER NOT NULL,
    sha256                  TEXT NOT NULL,
    storage_key             TEXT NOT NULL UNIQUE,    -- patients/{patient_id}/documents/{id}.{ext}
    source                  TEXT NOT NULL,
    uploaded_by_patient_id  TEXT REFERENCES patients(id),
    uploaded_by_user_id     TEXT REFERENCES clinical_users(id),
    uploaded_at             TEXT NOT NULL,
    review_status           TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending', 'confirmed', 'rejected')),
    reviewed_by             TEXT REFERENCES clinical_users(id),
    reviewed_at             TEXT
);
CREATE INDEX idx_documents_patient ON patient_documents (patient_id, uploaded_at);

CREATE TABLE consent_requests (
    id                    INTEGER PRIMARY KEY,
    patient_id            TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    requested_by_user_id  TEXT REFERENCES clinical_users(id),
    abha_number           TEXT NOT NULL,
    requester             TEXT NOT NULL,
    hip_name              TEXT NOT NULL,
    purpose_code          TEXT NOT NULL,
    purpose_text          TEXT NOT NULL,
    hi_types              TEXT NOT NULL,             -- JSON list
    date_from             TEXT NOT NULL,
    date_to               TEXT NOT NULL,
    access_until          TEXT NOT NULL,
    expires_at            TEXT NOT NULL,
    status                TEXT NOT NULL CHECK (status IN ('REQUESTED', 'GRANTED', 'DENIED', 'EXPIRED')),
    records_received      INTEGER NOT NULL DEFAULT 0,
    created_at            TEXT NOT NULL,
    responded_at          TEXT
);
CREATE INDEX idx_consents_patient ON consent_requests (patient_id, status);

-- One lab report = one laboratory on one date. It may or may not have an uploaded file.
CREATE TABLE lab_reports (
    id                  TEXT PRIMARY KEY,            -- UUID
    patient_id          TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    document_id         TEXT REFERENCES patient_documents(id) ON DELETE SET NULL,
    laboratory_name     TEXT NOT NULL,
    report_date         TEXT NOT NULL,
    source              TEXT NOT NULL CHECK (source IN ('hospital_internal', 'patient_upload', 'external_hospital_abdm', 'care_team_manual')),
    consent_id          INTEGER REFERENCES consent_requests(id),
    entered_by_user_id  TEXT REFERENCES clinical_users(id),
    created_at          TEXT NOT NULL,
    UNIQUE (id, patient_id)
);
CREATE INDEX idx_reports_patient ON lab_reports (patient_id, report_date);

-- Every measurement is its own row; results are never overwritten.
CREATE TABLE lab_test_results (
    id              TEXT PRIMARY KEY,                -- UUID
    report_id       TEXT NOT NULL,
    patient_id      TEXT NOT NULL,
    test_name       TEXT NOT NULL,
    test_code       TEXT,                            -- LOINC where known
    value_text      TEXT NOT NULL,                   -- exactly as reported ("8.4", "Positive (1+)")
    numeric_value   REAL,
    unit            TEXT,
    reference_kind  TEXT CHECK (reference_kind IN ('range', 'upper', 'lower', 'qualitative')),
    reference_low   REAL,
    reference_high  REAL,
    reference_text  TEXT,                            -- as printed on the report
    critical_low    REAL,
    critical_high   REAL,
    reported_flag   TEXT,                            -- the lab's own H/L flag, if printed
    test_date       TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (report_id, patient_id) REFERENCES lab_reports (id, patient_id) ON DELETE CASCADE
);
CREATE INDEX idx_results_history ON lab_test_results (patient_id, test_code, test_date);
CREATE INDEX idx_results_name ON lab_test_results (patient_id, test_name, test_date);
CREATE INDEX idx_results_report ON lab_test_results (report_id);

-- ------------------------------------------------------------------ clinical timeline (non-lab facts)

CREATE TABLE clinical_events (
    id                   INTEGER PRIMARY KEY,
    patient_id           TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    event_type           TEXT NOT NULL CHECK (event_type IN ('refill', 'engagement_log', 'visit', 'vital', 'screening', 'hypo_event')),
    name                 TEXT,
    value                REAL,
    unit                 TEXT,
    effective_date       TEXT NOT NULL,
    source               TEXT NOT NULL CHECK (source IN ('hospital_internal', 'patient_upload', 'external_hospital_abdm', 'care_team_manual')),
    confidence           TEXT NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
    status               TEXT NOT NULL CHECK (status IN ('ordered', 'active', 'resulted', 'stopped')),
    asserted_by          TEXT NOT NULL,              -- display text: facility, patient or care-team member
    recorded_by_user_id  TEXT REFERENCES clinical_users(id),
    facility             TEXT,
    clinician            TEXT,
    note                 TEXT,
    consent_id           INTEGER REFERENCES consent_requests(id),
    document_id          TEXT REFERENCES patient_documents(id) ON DELETE SET NULL,
    created_at           TEXT NOT NULL
);
CREATE INDEX idx_events_patient ON clinical_events (patient_id, event_type, effective_date);

-- ------------------------------------------------------------------ audit

CREATE TABLE audit_log (
    id             INTEGER PRIMARY KEY,
    actor_type     TEXT NOT NULL CHECK (actor_type IN ('patient', 'clinical_user', 'system')),
    actor_id       TEXT,
    actor_label    TEXT NOT NULL,                    -- name shown in logs, e.g. "Dr. Priya Nair (CLN-…)"
    patient_id     TEXT REFERENCES patients(id) ON DELETE CASCADE,
    action         TEXT NOT NULL,
    resource_type  TEXT,
    resource_id    TEXT,
    detail         TEXT,                             -- short plain text, no medical content
    at             TEXT NOT NULL
);
CREATE INDEX idx_audit_patient ON audit_log (patient_id, at);
CREATE INDEX idx_audit_actor ON audit_log (actor_type, actor_id, at);
CREATE INDEX idx_audit_resource ON audit_log (resource_type, resource_id);
