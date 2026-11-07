-- migrate: foreign_keys=off
-- Re-upload requests: a patient cannot send the same report again while it is being checked; instead they ask the
-- clinical team. A team member accepts (the old upload is set aside, the patient may upload again) or declines.
--   reupload_requests      one per request; at most one open request per upload
--   patient_notifications  new kinds: reupload_accepted / reupload_declined (linked to the request)

CREATE TABLE reupload_requests (
    id            INTEGER PRIMARY KEY,
    patient_id    TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    source_kind   TEXT NOT NULL CHECK (source_kind IN ('document', 'external_report')),
    source_id     TEXT NOT NULL,                     -- the upload the patient wants to replace
    test_key      TEXT,                              -- report_tests key, or 'prescription'; NULL for an "Other" report
    label         TEXT NOT NULL,                     -- what the patient saw: "HbA1c", "Lipid", "Prescription", "Vitamin D"
    reason        TEXT CHECK (reason IS NULL OR length(reason) <= 200),
    status        TEXT NOT NULL DEFAULT 'requested' CHECK (status IN ('requested', 'accepted', 'declined')),
    requested_at  TEXT NOT NULL,
    decided_by    TEXT REFERENCES clinical_users(id),
    decided_at    TEXT,
    note          TEXT CHECK (note IS NULL OR length(note) <= 200),   -- the team's reason when declining
    CHECK ((status = 'requested') = (decided_by IS NULL AND decided_at IS NULL))
);
CREATE UNIQUE INDEX idx_reupload_one_open ON reupload_requests (source_kind, source_id) WHERE status = 'requested';
CREATE INDEX idx_reupload_patient ON reupload_requests (patient_id, requested_at);

CREATE TABLE patient_notifications_v18 (
    id                  INTEGER PRIMARY KEY,
    patient_id          TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    kind                TEXT NOT NULL CHECK (kind IN ('bill_approved', 'bill_rejected', 'appointment_reschedule',
                                                      'test_reminder', 'test_rejected', 'reupload_request',
                                                      'reupload_accepted', 'reupload_declined')),
    document_id         TEXT,
    appointment_id      TEXT REFERENCES appointments(id) ON DELETE CASCADE,
    test_order_item_id  TEXT REFERENCES test_order_items(id) ON DELETE CASCADE,
    processing_id       TEXT REFERENCES document_processing(id) ON DELETE CASCADE,
    reupload_request_id INTEGER REFERENCES reupload_requests(id) ON DELETE CASCADE,
    message             TEXT NOT NULL,
    email_to            TEXT,
    email_status        TEXT NOT NULL CHECK (email_status IN ('simulated', 'no_email_on_file')),
    created_at          TEXT NOT NULL,
    read_at             TEXT,
    FOREIGN KEY (document_id, patient_id) REFERENCES external_reports (id, patient_id),
    CHECK ((kind IN ('bill_approved', 'bill_rejected')) = (document_id IS NOT NULL)),
    CHECK ((kind = 'appointment_reschedule') = (appointment_id IS NOT NULL)),
    CHECK ((kind IN ('test_reminder', 'test_rejected')) = (test_order_item_id IS NOT NULL)),
    CHECK ((kind = 'reupload_request') = (processing_id IS NOT NULL)),
    CHECK ((kind IN ('reupload_accepted', 'reupload_declined')) = (reupload_request_id IS NOT NULL))
);
INSERT INTO patient_notifications_v18 (id, patient_id, kind, document_id, appointment_id, test_order_item_id, processing_id,
                                       reupload_request_id, message, email_to, email_status, created_at, read_at)
SELECT id, patient_id, kind, document_id, appointment_id, test_order_item_id, processing_id, NULL, message, email_to,
       email_status, created_at, read_at
FROM patient_notifications;
DROP TABLE patient_notifications;
ALTER TABLE patient_notifications_v18 RENAME TO patient_notifications;
CREATE INDEX idx_notifications_patient ON patient_notifications (patient_id, created_at);
