-- Every uploaded lab-report file gets a lab_reports row straight away (report_type = what the patient
-- wrote, e.g. "kidney test"), so the report is listed even before anyone has entered its values.
ALTER TABLE lab_reports ADD COLUMN report_type TEXT;

-- Backfill: lab-report files uploaded before this migration.
INSERT INTO lab_reports (id, patient_id, document_id, laboratory_name, report_date, source, created_at, report_type)
SELECT lower(hex(randomblob(16))), d.patient_id, d.id, 'Uploaded by patient', substr(d.uploaded_at, 1, 10),
       d.source, d.uploaded_at, d.description
FROM patient_documents d
WHERE d.document_type = 'lab_report'
  AND NOT EXISTS (SELECT 1 FROM lab_reports r WHERE r.document_id = d.id);
