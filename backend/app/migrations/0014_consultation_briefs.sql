-- Consultation Brief: a short, deterministic summary the clinical team prepares and sends to the doctor before a visit.
--   consultation_briefs     one row per version. A draft is regenerated freely; a SENT version is an immutable snapshot
--                           (trigger below). Re-sending creates the next version and marks the old one superseded.
--   brief_edits             what the clinical team hid, pinned or noted (who, when)
--   consultation_snapshots  the key numbers when a consultation is completed: the next brief compares against them

CREATE TABLE consultation_briefs (
    id                     TEXT PRIMARY KEY,
    patient_id             TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    appointment_id         TEXT NOT NULL REFERENCES appointments(id),
    doctor_id              TEXT NOT NULL REFERENCES doctors(id),
    version                INTEGER NOT NULL CHECK (version >= 1),
    status                 TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'sent', 'in_consultation', 'opened',
                               'acknowledged', 'completed', 'superseded', 'cancelled')),
    content_json           TEXT NOT NULL,          -- the full rendered brief, as the doctor sees it
    schema_version         TEXT NOT NULL,
    data_cutoff_at         TEXT NOT NULL,          -- UTC: the data the brief was built from
    composed_by            TEXT NOT NULL REFERENCES clinical_users(id),
    team_note              TEXT CHECK (team_note IS NULL OR length(team_note) <= 280),
    team_note_by           TEXT REFERENCES clinical_users(id),
    team_note_at           TEXT,
    row_version            INTEGER NOT NULL DEFAULT 1,   -- optimistic lock for edits to a draft
    sent_at                TEXT,
    sent_by                TEXT REFERENCES clinical_users(id),
    called_at              TEXT,
    identity_confirmed_at  TEXT,
    identity_confirmed_by  TEXT REFERENCES clinical_users(id),
    opened_at              TEXT,
    acknowledged_at        TEXT,
    completed_at           TEXT,
    superseded_by          TEXT REFERENCES consultation_briefs(id),
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    UNIQUE (appointment_id, version),
    CHECK ((status = 'draft') = (sent_at IS NULL)),
    CHECK (status != 'superseded' OR superseded_by IS NOT NULL),
    CHECK (identity_confirmed_at IS NULL OR identity_confirmed_by IS NOT NULL)
);
CREATE INDEX idx_briefs_status ON consultation_briefs (status);
CREATE INDEX idx_briefs_doctor ON consultation_briefs (doctor_id, status);
CREATE INDEX idx_briefs_appointment ON consultation_briefs (appointment_id, version);
CREATE INDEX idx_briefs_cutoff ON consultation_briefs (data_cutoff_at);
CREATE INDEX idx_briefs_patient ON consultation_briefs (patient_id, created_at);
-- at most one draft, and one live (sent / in progress) version, per appointment
CREATE UNIQUE INDEX idx_briefs_one_draft ON consultation_briefs (appointment_id) WHERE status = 'draft';
CREATE UNIQUE INDEX idx_briefs_one_live ON consultation_briefs (appointment_id)
    WHERE status IN ('sent', 'in_consultation', 'opened', 'acknowledged', 'completed');

-- A sent brief is a snapshot: what the doctor received can never be rewritten.
CREATE TRIGGER trg_brief_sent_is_immutable BEFORE UPDATE OF content_json, data_cutoff_at, team_note, schema_version,
    patient_id, appointment_id, doctor_id, version ON consultation_briefs
WHEN OLD.status != 'draft'
BEGIN
    SELECT RAISE(ABORT, 'A sent brief cannot be changed. Prepare a new version instead.');
END;

CREATE TABLE brief_edits (
    id         INTEGER PRIMARY KEY,
    brief_id   TEXT NOT NULL REFERENCES consultation_briefs(id) ON DELETE CASCADE,
    item_key   TEXT NOT NULL,
    action     TEXT NOT NULL CHECK (action IN ('hide', 'unhide', 'pin', 'unpin', 'note')),
    edited_by  TEXT NOT NULL REFERENCES clinical_users(id),
    edited_at  TEXT NOT NULL
);
CREATE INDEX idx_brief_edits_brief ON brief_edits (brief_id, id);

CREATE TABLE consultation_snapshots (
    id              INTEGER PRIMARY KEY,
    patient_id      TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    brief_id        TEXT NOT NULL REFERENCES consultation_briefs(id),
    appointment_id  TEXT NOT NULL REFERENCES appointments(id),
    visit_date      TEXT NOT NULL,
    numbers_json    TEXT NOT NULL,      -- {key: {value, unit, date}} for every key number on file
    taken_at        TEXT NOT NULL,
    UNIQUE (brief_id)
);
CREATE INDEX idx_snapshots_patient ON consultation_snapshots (patient_id, visit_date);
