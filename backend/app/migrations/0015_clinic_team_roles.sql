-- migrate: foreign_keys=off
-- Doctors and the clinic team are different accounts with different dashboards.
--   clinical_users.role   'doctor'    - takes appointments, sees the doctor dashboard (briefs sent to them)
--                         'care_team' - the clinic team: patients, reports, schedule, prepares and sends briefs.
--                                       Sees every patient of the clinic (single-clinic deployment).
--   clinical_users.phone  becomes optional: a shared clinic-desk login need not belong to one person's phone.
--   clinic_profile        the clinic's name, shown to doctors as the sender of a brief.
-- Every existing account was a doctor taking appointments, so they all start as 'doctor'.
-- Many tables reference clinical_users, hence foreign_keys=off for the rebuild.

CREATE TABLE clinical_users_v15 (
    id              TEXT PRIMARY KEY,                -- UUID
    clinician_code  TEXT NOT NULL UNIQUE,            -- system-generated, e.g. CLN-7K3Q9P
    full_name       TEXT NOT NULL,
    phone           TEXT UNIQUE,                     -- NULL only for a shared clinic-team login
    password_hash   TEXT NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    theme           TEXT NOT NULL DEFAULT 'light' CHECK (theme IN ('light', 'dark', 'pleasant')),
    role            TEXT NOT NULL DEFAULT 'doctor' CHECK (role IN ('doctor', 'care_team')),
    CHECK (phone IS NOT NULL OR role = 'care_team')
);
INSERT INTO clinical_users_v15 (id, clinician_code, full_name, phone, password_hash, is_active, created_at, updated_at, theme, role)
SELECT id, clinician_code, full_name, phone, password_hash, is_active, created_at, updated_at, theme, 'doctor' FROM clinical_users;
DROP TABLE clinical_users;
ALTER TABLE clinical_users_v15 RENAME TO clinical_users;

CREATE TABLE clinic_profile (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    name        TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 2 AND 120),
    updated_at  TEXT NOT NULL
);
INSERT INTO clinic_profile (id, name, updated_at) VALUES (1, 'Tanishq Clinic Management', strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'));
