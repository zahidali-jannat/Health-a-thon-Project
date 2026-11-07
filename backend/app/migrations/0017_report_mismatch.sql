-- migrate: foreign_keys=off
-- A report that does not match the test the patient chose (e.g. sent as "KFT" but it is a lipid report).
--   patient_notifications  new kind 'reupload_request': the clinical team asks the patient to upload the right report
--                          (linked to the upload it is about, processing_id)
--   document_processing    patient_override: the patient was told at upload that it did not match and sent it anyway

CREATE TABLE patient_notifications_v17 (
    id                  INTEGER PRIMARY KEY,
    patient_id          TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    kind                TEXT NOT NULL CHECK (kind IN ('bill_approved', 'bill_rejected', 'appointment_reschedule',
                                                      'test_reminder', 'test_rejected', 'reupload_request')),
    document_id         TEXT,
    appointment_id      TEXT REFERENCES appointments(id) ON DELETE CASCADE,
    test_order_item_id  TEXT REFERENCES test_order_items(id) ON DELETE CASCADE,
    processing_id       TEXT REFERENCES document_processing(id) ON DELETE CASCADE,
    message             TEXT NOT NULL,
    email_to            TEXT,
    email_status        TEXT NOT NULL CHECK (email_status IN ('simulated', 'no_email_on_file')),
    created_at          TEXT NOT NULL,
    read_at             TEXT,
    FOREIGN KEY (document_id, patient_id) REFERENCES external_reports (id, patient_id),
    CHECK ((kind IN ('bill_approved', 'bill_rejected')) = (document_id IS NOT NULL)),
    CHECK ((kind = 'appointment_reschedule') = (appointment_id IS NOT NULL)),
    CHECK ((kind IN ('test_reminder', 'test_rejected')) = (test_order_item_id IS NOT NULL)),
    CHECK ((kind = 'reupload_request') = (processing_id IS NOT NULL))
);
INSERT INTO patient_notifications_v17 (id, patient_id, kind, document_id, appointment_id, test_order_item_id, processing_id,
                                       message, email_to, email_status, created_at, read_at)
SELECT id, patient_id, kind, document_id, appointment_id, test_order_item_id, NULL, message, email_to, email_status,
       created_at, read_at
FROM patient_notifications;
DROP TABLE patient_notifications;
ALTER TABLE patient_notifications_v17 RENAME TO patient_notifications;
CREATE INDEX idx_notifications_patient ON patient_notifications (patient_id, created_at);

ALTER TABLE document_processing ADD COLUMN patient_override TEXT
    CHECK (patient_override IS NULL OR patient_override = 'test_mismatch');
