-- Patients sign in with Patient ID (or mobile) + password.
-- must_change_password = 1 when the password was issued by the clinic (temporary): the patient must
-- choose their own before anything else works. A patient without a password must also set one.
ALTER TABLE patients ADD COLUMN password_hash TEXT;
ALTER TABLE patients ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0;
ALTER TABLE patient_signups ADD COLUMN password_hash TEXT;
