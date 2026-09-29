-- Doctor appointment booking.
--   doctors                  a clinician who takes appointments, with their own hours, slot length and buffer
--   appointment_slots        MATERIALISED per doctor per day; UNIQUE (doctor_id, date, start_time) makes
--                            slot generation idempotent
--   appointments             one LIVE appointment per slot (partial unique index) - the double-booking guard
--   doctor_unavailability    time ranges the care team marked the doctor away
--   appointment_notices      drafted "please rebook" messages; sending one is a separate, explicit step
-- patient_notifications is rebuilt so it can also carry those appointment messages.

CREATE TABLE doctors (
    id                     TEXT PRIMARY KEY,
    clinical_user_id       TEXT NOT NULL UNIQUE REFERENCES clinical_users(id) ON DELETE CASCADE,
    name                   TEXT NOT NULL,
    working_start_time     TEXT NOT NULL CHECK (working_start_time GLOB '[0-2][0-9]:[0-5][0-9]'),
    working_end_time       TEXT NOT NULL CHECK (working_end_time GLOB '[0-2][0-9]:[0-5][0-9]'),
    slot_duration_minutes  INTEGER NOT NULL DEFAULT 15 CHECK (slot_duration_minutes BETWEEN 5 AND 240),
    buffer_minutes         INTEGER NOT NULL DEFAULT 5 CHECK (buffer_minutes BETWEEN 0 AND 120),
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    CHECK (working_start_time < working_end_time)
);

CREATE TABLE appointment_slots (
    id          INTEGER PRIMARY KEY,
    doctor_id   TEXT NOT NULL REFERENCES doctors(id) ON DELETE CASCADE,
    date        TEXT NOT NULL,                           -- YYYY-MM-DD, clinic local
    start_time  TEXT NOT NULL,                           -- HH:MM, clinic local
    end_time    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'available' CHECK (status IN ('available', 'booked', 'blocked')),
    created_at  TEXT NOT NULL,
    UNIQUE (doctor_id, date, start_time),
    CHECK (start_time < end_time)
);
-- Every availability query is "this doctor, this day, this status" - served by this index alone.
CREATE INDEX idx_slots_availability ON appointment_slots (doctor_id, date, status, start_time);

CREATE TABLE appointments (
    id             TEXT PRIMARY KEY,
    slot_id        INTEGER NOT NULL REFERENCES appointment_slots(id),
    patient_id     TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    doctor_id      TEXT NOT NULL REFERENCES doctors(id),
    status         TEXT NOT NULL DEFAULT 'confirmed' CHECK (status IN ('confirmed', 'needs_reschedule', 'cancelled')),
    booked_at      TEXT NOT NULL,                        -- microsecond timestamp, for audit / tie-break visibility
    updated_at     TEXT NOT NULL,
    cancel_reason  TEXT,
    CHECK ((status = 'cancelled') = (cancel_reason IS NOT NULL))
);
-- One live appointment per slot. (A cancelled one no longer holds its slot, so the index skips it.)
CREATE UNIQUE INDEX idx_appointments_one_per_slot ON appointments (slot_id) WHERE status <> 'cancelled';
CREATE INDEX idx_appointments_patient ON appointments (patient_id, status);
CREATE INDEX idx_appointments_doctor ON appointments (doctor_id, status);

-- A live appointment can only sit on a slot of the same doctor that is marked booked (set in the same transaction).
CREATE TRIGGER trg_appointment_needs_booked_slot BEFORE INSERT ON appointments
WHEN NEW.status <> 'cancelled' AND NOT EXISTS (
    SELECT 1 FROM appointment_slots WHERE id = NEW.slot_id AND doctor_id = NEW.doctor_id AND status = 'booked')
BEGIN
    SELECT RAISE(ABORT, 'This slot is not booked for this doctor.');
END;

CREATE TABLE doctor_unavailability (
    id          INTEGER PRIMARY KEY,
    doctor_id   TEXT NOT NULL REFERENCES doctors(id) ON DELETE CASCADE,
    date        TEXT NOT NULL,
    start_time  TEXT NOT NULL,
    end_time    TEXT NOT NULL,
    reason      TEXT,
    created_by  TEXT NOT NULL REFERENCES clinical_users(id),
    created_at  TEXT NOT NULL,
    CHECK (start_time < end_time)
);
CREATE INDEX idx_unavailability_doctor_date ON doctor_unavailability (doctor_id, date);

CREATE TABLE appointment_notices (
    id                 INTEGER PRIMARY KEY,
    appointment_id     TEXT NOT NULL REFERENCES appointments(id) ON DELETE CASCADE,
    patient_id         TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    unavailability_id  INTEGER NOT NULL REFERENCES doctor_unavailability(id) ON DELETE CASCADE,
    suggested_slot_id  INTEGER REFERENCES appointment_slots(id),
    message            TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'sent')),
    created_at         TEXT NOT NULL,
    sent_at            TEXT,
    sent_by            TEXT REFERENCES clinical_users(id),
    UNIQUE (appointment_id, unavailability_id),
    CHECK ((status = 'sent') = (sent_at IS NOT NULL))
);
CREATE INDEX idx_notices_status ON appointment_notices (status, patient_id);

-- ------------------------------------------------------------------ patient_notifications: + appointment messages
CREATE TABLE patient_notifications_v10 (
    id              INTEGER PRIMARY KEY,
    patient_id      TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL CHECK (kind IN ('bill_approved', 'bill_rejected', 'appointment_reschedule')),
    document_id     TEXT,
    appointment_id  TEXT REFERENCES appointments(id) ON DELETE CASCADE,
    message         TEXT NOT NULL,
    email_to        TEXT,
    email_status    TEXT NOT NULL CHECK (email_status IN ('simulated', 'no_email_on_file')),
    created_at      TEXT NOT NULL,
    read_at         TEXT,
    FOREIGN KEY (document_id, patient_id) REFERENCES external_reports (id, patient_id),
    CHECK ((kind IN ('bill_approved', 'bill_rejected')) = (document_id IS NOT NULL)),
    CHECK ((kind = 'appointment_reschedule') = (appointment_id IS NOT NULL))
);
INSERT INTO patient_notifications_v10 (id, patient_id, kind, document_id, appointment_id, message, email_to, email_status,
                                       created_at, read_at)
SELECT id, patient_id, kind, document_id, NULL, message, email_to, email_status, created_at, read_at FROM patient_notifications;
DROP TABLE patient_notifications;
ALTER TABLE patient_notifications_v10 RENAME TO patient_notifications;
CREATE INDEX idx_notifications_patient ON patient_notifications (patient_id, created_at);
