-- Patient self sign-up. Details wait here until the phone number is verified with a one-time code;
-- only then is a row created in `patients`. Unverified sign-ups never become patients.
CREATE TABLE patient_signups (
    id             INTEGER PRIMARY KEY,
    phone          TEXT NOT NULL,
    full_name      TEXT NOT NULL,
    date_of_birth  TEXT,
    sex            TEXT CHECK (sex IN ('M', 'F', 'O')),
    code_hash      TEXT NOT NULL,
    attempts       INTEGER NOT NULL DEFAULT 0,
    expires_at     TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
CREATE INDEX idx_signups_phone ON patient_signups (phone, created_at);
