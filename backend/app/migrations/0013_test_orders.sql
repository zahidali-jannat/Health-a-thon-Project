-- Test Orders: after a consultation the care team records which tests the patient must do, and by when.
--   test_catalog              orderable tests (synced at start-up from backend/config/test_orders.json)
--   test_order_sets           one "order" = the tests chosen together, with the next appointment they were checked against
--   test_order_items          one test to do: due date, route, status (the status machine is enforced below)
--   test_order_transitions    the legal status changes; a trigger refuses every other one
--   lab_result_ingestions     results from the clinic lab system: idempotent per (lab_order_ref, test_code), and the
--                             queue for results that need review or could not be matched to an order
--   test_reminders_sent       at most one reminder of each kind, and one per day, per item
--   idempotency_keys          a repeated create / upload request returns the first answer instead of doing it twice
-- external_reports (patient uploads from outside labs) gains the order item it answers and the lab's name.

CREATE TABLE test_catalog (
    id                            INTEGER PRIMARY KEY,
    code                          TEXT NOT NULL UNIQUE,
    display_name                  TEXT NOT NULL,
    category                      TEXT NOT NULL,
    fasting_required              INTEGER NOT NULL DEFAULT 0 CHECK (fasting_required IN (0, 1)),
    default_instructions          TEXT,
    default_repeat_interval_days  INTEGER CHECK (default_repeat_interval_days IS NULL OR default_repeat_interval_days > 0),
    expected_unit                 TEXT,
    ucum_unit                     TEXT,
    result_name                   TEXT,            -- the name its result gets on the graphs
    plausible_low                 REAL,
    plausible_high                REAL,
    loinc_code                    TEXT,
    is_other                      INTEGER NOT NULL DEFAULT 0 CHECK (is_other IN (0, 1)),
    active                        INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    updated_at                    TEXT NOT NULL,
    CHECK (plausible_low IS NULL OR plausible_high IS NULL OR plausible_low < plausible_high)
);

CREATE TABLE test_order_sets (
    id                     TEXT PRIMARY KEY,
    patient_id             TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    consultation_id        TEXT,                    -- no consultation record exists in this app yet
    ordered_by_doctor_id   TEXT NOT NULL REFERENCES doctors(id),
    entered_by_user_id     TEXT NOT NULL REFERENCES clinical_users(id),
    next_appointment_id    TEXT REFERENCES appointments(id),
    next_appointment_date  TEXT,                    -- the date the due dates were checked against
    note                   TEXT,
    created_at             TEXT NOT NULL,
    UNIQUE (id, patient_id)
);
CREATE INDEX idx_order_sets_patient ON test_order_sets (patient_id, created_at);

CREATE TABLE test_order_items (
    id                 TEXT PRIMARY KEY,
    order_set_id       TEXT NOT NULL,
    patient_id         TEXT NOT NULL,
    test_catalog_id    INTEGER NOT NULL REFERENCES test_catalog(id),
    custom_name        TEXT,                        -- for "Other (specify)"
    instructions       TEXT,
    due_by             TEXT NOT NULL CHECK (due_by GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
    priority           TEXT NOT NULL DEFAULT 'routine' CHECK (priority IN ('routine', 'urgent')),
    fulfilment_route   TEXT NOT NULL DEFAULT 'either' CHECK (fulfilment_route IN ('clinic_lab', 'external', 'either')),
    status             TEXT NOT NULL DEFAULT 'ordered' CHECK (status IN ('ordered', 'sample_collected', 'result_received',
                           'submitted_by_patient', 'verified', 'closed', 'rejected', 'cancelled', 'not_done')),
    status_reason      TEXT,                        -- why it was cancelled, waived or rejected
    linked_report_id   TEXT,                        -- the patient's upload (external_reports)
    lab_result_id      TEXT REFERENCES lab_test_results(id),    -- the clinic lab's result
    version            INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    FOREIGN KEY (order_set_id, patient_id) REFERENCES test_order_sets (id, patient_id) ON DELETE CASCADE,
    FOREIGN KEY (linked_report_id, patient_id) REFERENCES external_reports (id, patient_id),
    CHECK (status NOT IN ('cancelled', 'not_done', 'rejected') OR length(trim(COALESCE(status_reason, ''))) >= 3),
    CHECK (status NOT IN ('verified', 'closed') OR linked_report_id IS NOT NULL OR lab_result_id IS NOT NULL)
);
CREATE INDEX idx_order_items_patient ON test_order_items (patient_id);
CREATE INDEX idx_order_items_status ON test_order_items (status);
CREATE INDEX idx_order_items_due ON test_order_items (due_by);
CREATE INDEX idx_order_items_patient_test ON test_order_items (patient_id, test_catalog_id, status);
CREATE INDEX idx_order_items_set ON test_order_items (order_set_id);

CREATE TABLE test_order_transitions (
    from_status  TEXT NOT NULL,
    to_status    TEXT NOT NULL,
    PRIMARY KEY (from_status, to_status)
) WITHOUT ROWID;
INSERT INTO test_order_transitions VALUES
    ('ordered', 'sample_collected'), ('ordered', 'result_received'), ('ordered', 'submitted_by_patient'),
    ('ordered', 'cancelled'), ('ordered', 'not_done'),
    ('sample_collected', 'result_received'), ('sample_collected', 'submitted_by_patient'),
    ('sample_collected', 'cancelled'), ('sample_collected', 'not_done'),
    ('result_received', 'verified'), ('result_received', 'rejected'),
    ('submitted_by_patient', 'verified'), ('submitted_by_patient', 'rejected'),
    ('rejected', 'sample_collected'), ('rejected', 'result_received'), ('rejected', 'submitted_by_patient'),
    ('rejected', 'cancelled'), ('rejected', 'not_done'),
    ('verified', 'closed');

CREATE TRIGGER trg_test_order_new_is_ordered BEFORE INSERT ON test_order_items
WHEN NEW.status != 'ordered' OR NEW.version != 1
BEGIN
    SELECT RAISE(ABORT, 'A new test order starts as ordered.');
END;

CREATE TRIGGER trg_test_order_legal_transition BEFORE UPDATE OF status ON test_order_items
WHEN NEW.status != OLD.status
     AND NOT EXISTS (SELECT 1 FROM test_order_transitions WHERE from_status = OLD.status AND to_status = NEW.status)
BEGIN
    SELECT RAISE(ABORT, 'That status change is not allowed for a test order.');
END;

CREATE TRIGGER trg_test_order_version_moves BEFORE UPDATE ON test_order_items
WHEN NEW.version != OLD.version + 1
BEGIN
    SELECT RAISE(ABORT, 'Every change to a test order must move its version on by one.');
END;

-- ------------------------------------------------------------------ patient uploads answer an order item
ALTER TABLE external_reports ADD COLUMN test_order_item_id TEXT REFERENCES test_order_items(id);
ALTER TABLE external_reports ADD COLUMN lab_name TEXT;
CREATE INDEX idx_external_reports_order_item ON external_reports (test_order_item_id) WHERE test_order_item_id IS NOT NULL;

CREATE TRIGGER trg_report_answers_own_order BEFORE INSERT ON external_reports
WHEN NEW.test_order_item_id IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM test_order_items WHERE id = NEW.test_order_item_id AND patient_id = NEW.patient_id)
BEGIN
    SELECT RAISE(ABORT, 'A report can only answer a test order of the same patient.');
END;

-- ------------------------------------------------------------------ clinic lab system results
CREATE TABLE lab_result_ingestions (
    id                  TEXT PRIMARY KEY,
    lab_order_ref       TEXT NOT NULL,
    test_code           TEXT NOT NULL,
    patient_id          TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    test_order_item_id  TEXT REFERENCES test_order_items(id),
    value               REAL NOT NULL,
    unit                TEXT,
    test_date           TEXT NOT NULL,
    lab_name            TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ('recorded', 'needs_review', 'unlinked', 'rejected')),
    lab_result_id       TEXT REFERENCES lab_test_results(id),
    received_at         TEXT NOT NULL,
    reviewed_by         TEXT REFERENCES clinical_users(id),
    reviewed_at         TEXT,
    review_note         TEXT,
    UNIQUE (lab_order_ref, test_code),
    CHECK ((status = 'recorded') = (lab_result_id IS NOT NULL)),
    CHECK (status != 'rejected' OR length(trim(COALESCE(review_note, ''))) >= 3)
);
CREATE INDEX idx_ingestions_patient ON lab_result_ingestions (patient_id, status);

-- ------------------------------------------------------------------ reminders: never twice for the same item and day
CREATE TABLE test_reminders_sent (
    test_order_item_id  TEXT NOT NULL REFERENCES test_order_items(id) ON DELETE CASCADE,
    kind                TEXT NOT NULL CHECK (kind IN ('due_in_7', 'due_in_3', 'due_in_1', 'overdue')),
    sent_on             TEXT NOT NULL,
    notification_id     INTEGER,
    PRIMARY KEY (test_order_item_id, kind),
    UNIQUE (test_order_item_id, sent_on)
);

-- ------------------------------------------------------------------ idempotent create / upload
CREATE TABLE idempotency_keys (
    scope         TEXT NOT NULL,            -- who sent it: 'clinician:<id>' or 'patient:<id>'
    key           TEXT NOT NULL,
    route         TEXT NOT NULL,
    request_hash  TEXT NOT NULL,
    status_code   INTEGER NOT NULL,
    response      TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (scope, key)
);

-- ------------------------------------------------------------------ patient_notifications: + test reminders / rejections
CREATE TABLE patient_notifications_v13 (
    id                  INTEGER PRIMARY KEY,
    patient_id          TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    kind                TEXT NOT NULL CHECK (kind IN ('bill_approved', 'bill_rejected', 'appointment_reschedule',
                                                      'test_reminder', 'test_rejected')),
    document_id         TEXT,
    appointment_id      TEXT REFERENCES appointments(id) ON DELETE CASCADE,
    test_order_item_id  TEXT REFERENCES test_order_items(id) ON DELETE CASCADE,
    message             TEXT NOT NULL,
    email_to            TEXT,
    email_status        TEXT NOT NULL CHECK (email_status IN ('simulated', 'no_email_on_file')),
    created_at          TEXT NOT NULL,
    read_at             TEXT,
    FOREIGN KEY (document_id, patient_id) REFERENCES external_reports (id, patient_id),
    CHECK ((kind IN ('bill_approved', 'bill_rejected')) = (document_id IS NOT NULL)),
    CHECK ((kind = 'appointment_reschedule') = (appointment_id IS NOT NULL)),
    CHECK ((kind IN ('test_reminder', 'test_rejected')) = (test_order_item_id IS NOT NULL))
);
INSERT INTO patient_notifications_v13 (id, patient_id, kind, document_id, appointment_id, test_order_item_id, message,
                                       email_to, email_status, created_at, read_at)
SELECT id, patient_id, kind, document_id, appointment_id, NULL, message, email_to, email_status, created_at, read_at
FROM patient_notifications;
DROP TABLE patient_notifications;
ALTER TABLE patient_notifications_v13 RENAME TO patient_notifications;
CREATE INDEX idx_notifications_patient ON patient_notifications (patient_id, created_at);
