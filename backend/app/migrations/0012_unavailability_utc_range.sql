-- migrate: foreign_keys=off
-- "Doctor unavailable" becomes a real time range: a UTC start and end instant (it may cross midnight or span days)
-- instead of one clinic-local date + two times. Clinic times are Asia/Kolkata (UTC+05:30, no daylight saving),
-- so the old rows convert exactly. `confirmation` keeps what the care team saw and accepted in the confirmation
-- pop-up (warnings shown, AM/PM fixes applied). appointment_notices points at this table, hence foreign_keys=off.

CREATE TABLE doctor_unavailability_v12 (
    id            INTEGER PRIMARY KEY,
    doctor_id     TEXT NOT NULL REFERENCES doctors(id) ON DELETE CASCADE,
    starts_at     TEXT NOT NULL CHECK (starts_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:00Z'),
    ends_at       TEXT NOT NULL CHECK (ends_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:00Z'),
    reason        TEXT,
    created_by    TEXT NOT NULL REFERENCES clinical_users(id),
    created_at    TEXT NOT NULL,
    confirmation  TEXT,                         -- JSON: what the confirmation pop-up showed and the user accepted
    CHECK (starts_at < ends_at)
);
INSERT INTO doctor_unavailability_v12 (id, doctor_id, starts_at, ends_at, reason, created_by, created_at, confirmation)
SELECT id, doctor_id,
       strftime('%Y-%m-%dT%H:%M:00Z', date || ' ' || start_time, '-330 minutes'),
       strftime('%Y-%m-%dT%H:%M:00Z', date || ' ' || end_time, '-330 minutes'),
       reason, created_by, created_at, NULL
FROM doctor_unavailability;
DROP TABLE doctor_unavailability;
ALTER TABLE doctor_unavailability_v12 RENAME TO doctor_unavailability;
CREATE INDEX idx_unavailability_doctor_range ON doctor_unavailability (doctor_id, starts_at, ends_at);
