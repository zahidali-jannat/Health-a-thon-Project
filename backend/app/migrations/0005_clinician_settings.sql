-- Clinician display preference, saved with the account so it follows them to any computer.
ALTER TABLE clinical_users ADD COLUMN theme TEXT NOT NULL DEFAULT 'light' CHECK (theme IN ('light', 'dark', 'pleasant'));
