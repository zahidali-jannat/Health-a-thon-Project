# UC2 Consultation Readiness — Feature 1: Silent Risk Detector
 Link of the MVP:    https://uc2-consultation-readiness.onrender.com/
Doctors usually see a diabetes patient only every 3–6 months. In between, a patient can quietly
get worse: they miss medicine refills, log their sugar less often, their HbA1c stops improving,
or they have a dangerous low-sugar episode. Often nobody notices until the next visit.

This prototype watches for those signs all the time and raises an early warning.
**Every warning says exactly why it was raised.** No AI model makes any decision.

---

## How to run it

You need **Python 3.11+** and **Node.js 20+**. Then, from this folder:

```bash
./start.sh
```

| Screen | Link |
|---|---|
| Care team | http://localhost:5173/care-team |
| Patient app (best on a phone or narrow window) | http://localhost:5173/patient |

The system starts **empty**: no clinicians, no patients. Everything you enter is saved in the database and kept
between runs. Press Ctrl+C to stop.

### First steps in an empty system

1. **Clinician:** open the Care team page → **Create account** → name, phone number, password. The system
   generates a unique **Clinician ID** (e.g. `CLN-7K3Q9P`) that you sign in with.
2. **Patients get a Patient ID and a password.** There are two ways:
   - **The clinic registers them:** **Add patient → New patient**. The system creates a unique **Patient ID**
     (e.g. `P-1001`) and a **temporary password** (e.g. `SCVQ-A3AJ`). Both are shown once, to hand to the patient.
     At their first sign-in the patient must choose their own password. The temporary one then stops working.
   - **They register themselves:** patient app → **Create account** → name, mobile number, a password, and a
     one-time code to confirm the phone. The Patient ID is shown at the end.
3. **Patients sign in** with **Patient ID (or mobile number) + password**. If they forget it, they tap
   **"Forgot password? Sign in with a code instead"** and then choose a new password. The clinician can also
   press **Reset patient password** on the patient's page to issue a new temporary password; this signs the
   patient out everywhere. There's no SMS in this prototype, so codes are shown on screen in development mode.
4. **Connecting a clinician to a patient** who registered elsewhere: **Add patient → Existing patient** with the
   **Patient ID + the patient's mobile number** (both must match). Or the patient adds the doctor's Clinician ID
   under **My doctors**. The patient can remove a doctor at any time.

Passwords are stored only as scrypt hashes. Five wrong attempts lock that account for 15 minutes.

**Start completely fresh** (deletes everything, development only):

```bash
cd backend && .venv/bin/python -m app.reset
```

**Optional demo data**: 3 example patients and 2 demo clinicians, password `demo1234`. This
**replaces** everything in the database:

```bash
cd backend && .venv/bin/python -m app.seed
```

### Running the pieces by hand

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q                  # 138 tests
.venv/bin/uvicorn app.main:app --port 8000     # API; explore it at http://localhost:8000/docs

cd ../frontend && npm install && npm run dev   # in a second terminal
```

---

## How the detection works (plain language)

There are seven simple yes/no checks, in two groups.

### Hard signs: any ONE is enough → red "High priority"

| Check | Fires when… |
|---|---|
| **Hypoglycaemia event** | A low-sugar episode is recorded in the last 90 days. |
| **Emergency visit** | The patient went to an emergency room after their last clinic visit. |

### Soft signs: TWO or more together → orange "Quietly getting worse since last visit"

| Check | Fires when… |
|---|---|
| **Medicine refill late** | A refill is **10 or more days late**. A medicine the doctor stopped doesn't count. |
| **Fewer sugar logs** | Sugar logs and check-ins in the last 60 days are **40% or more lower** than in the 60 days before. |
| **HbA1c not improving** | The last 2–3 HbA1c results are flat or worse (improved by less than 0.3 points) and still at or above 7.0%. |
| **Missed last appointment** | The most recent booked clinic visit never happened. |
| **Screening overdue** | An eye, foot or kidney check is more than a year old, or the care team marked it overdue. |

One soft sign alone is shown as **"watch"**, but not flagged.

Every flag comes with a sentence such as:

> **HIGH PRIORITY** — hypoglycaemia event recorded on 16 Sep 2026 (52 mg/dL).

> **Flagged because:** Metformin 500 mg refill 22 days late + sugar logs down 72% (8 vs 29) + HbA1c worse across last 3 results (7.8% → 7.9% → 8.2%) + missed visit on 4 Sep 2026 + screening overdue: eye (115 days).

Clicking a badge shows each check with the actual dates, numbers, and who recorded them.

**All numbers (10 days, 40%, 90 days, …) live in one file:
[`backend/app/config.py`](backend/app/config.py).** Change them there; nothing is buried in the logic.

---

## Lab results and reference ranges

Each patient's page has a **Lab results** section with one card per test. Each card shows:
the latest value (large), the **reference range**, a status with a text label
(*Within reference range*, *Above reference range ↑*, *Below reference range ↓*, *Critical*, or
*Reference range unavailable*), whether the value is **moving toward or away from** the range,
and a graph with the range drawn as a soft shaded band. Every graph also has a **Show as table** view.

- **The range always comes from that result's own lab report.** It is never filled in from a
  general "typical" range. For example, our lab prints HbA1c as `< 5.7 %`, while City Care Hospital
  (via ABHA) prints `4.0 – 5.6 %`. Each result keeps its own range, and the graph says when they differ.
- **Supported range types:** a full range (`12.0 – 16.0 g/dL`), an upper limit only (`< 5.7 %`),
  a lower limit only (`> 60 mL/min/1.73m²`), and word results (`Negative` / `Positive`).
  Word results are shown as a short history table, not a graph.
- **Critical** is shown only if the lab itself reported critical limits.
- **No range on the report** → *Reference range unavailable*. Nothing is guessed.
- Results in different units are never put on the same graph.

### eGFR: average for the patient's age

The eGFR graph can show the **average eGFR for the patient's age group** (National Kidney Foundation):
20–29: 116 · 30–39: 107 · 40–49: 99 · 50–59: 93 · 60–69: 85 · 70+: 75 mL/min/1.73 m². The age group is chosen
**automatically** from the patient's age, so this is the default view for eGFR. It is drawn as a dashed line,
not a shaded "normal" band: it is a population average, and a healthy person can be below it. The range printed
on the report (e.g. > 60) is still one click away under **Reference Range**.

Where the age comes from: the date of birth if one is on file. Otherwise the patient app asks
**"How old are you?"** once after sign-in ("Not now" asks again at the next sign-in). The answer is stored with the
date it was given, so it keeps counting up. The patient page shows *age told by patient* when it came from this question.

### HbA1c: diabetes stages

Every HbA1c result is also placed in a diabetes stage, using the ADA diagnostic categories
(thresholds in `backend/app/config.py`, lower limit included, upper limit not):

| Stage | HbA1c (%) | HbA1c (mmol/mol) |
|---|---|---|
| Normal | below 5.7 | below 39 |
| Prediabetes | 5.7–6.4 | 39–47 |
| Diabetes | 6.5 or above | 48 or above |

On the HbA1c graph, **Range on graph** lets the clinician choose what is shaded:

- **All stages**: the three bands, with each result coloured by its stage.
- **Normal**, **Prediabetes** or **Diabetes**: only that band. Results outside it fade, and the card says how many
  results fall in it.
- **Lab report range**: the range printed on each report, as before.

The stages are a clinical classification, **not** a lab reference range. The printed range is still stored and
shown unchanged. So an outside-lab value entered without a range still gets a stage
(e.g. *Diabetes range · ≥ 6.5 %*). The hover box also shows the other unit (7.9 % = 63 mmol/mol).

---

## Where the data comes from: three paths, one table

On each patient's page the care team sees three cards:

1. **Hospital records**: what our own hospital system already has.
2. **Patient upload**: what the patient sent from their phone: refill confirmations, sugar readings
   (optionally with a photo of the glucometer screen as proof, shown next to the reading), and photos or PDFs
   of prescriptions and lab reports. For a lab report the patient **must** write what type of report it is
   (e.g. "HbA1c test"), and the care team sees that name next to the file.
   These start at *medium* confidence until a care-team member clicks **Confirm**.
3. **Other hospital (via ABHA)**: records fetched from a different hospital, only with the patient's consent (see below).

There is also a short **Log data manually** form for the care team (sugar level, HbA1c,
missed refill, overdue screening, hypoglycaemia, Hemoglobin, eGFR, and emergency visit). Answering
**Hypoglycaemia: Yes**, or recording an **Emergency visit** (date, hospital, problem, treating doctor/surgeon),
turns the patient red straight away.

Every path writes into the same tables. Only the `source` column differs:
`hospital_internal`, `patient_upload`, `external_hospital_abdm`, `care_team_manual`, and
`patient_upload_reviewed` (see *External Report Review* below).
The detection checks never look at the source, so they work the same whichever way the data arrived.

---

## External Report Review: a test done outside the hospital

A patient had a test (HbA1c, fasting sugar, PP sugar, or another test) at a **different lab**.

1. **Patient** (app → *Send a test report*): chooses the test type, the date of the test, and a photo or PDF.
   They do **not** type any value. They see: *"Report uploaded. Your care team will review it shortly."*
2. **Clinician** (sidebar → *Pending reports*, with a count): opens a report and sees it **side by side** with a
   one-field form (*Enter value*, with the unit). The test type and date are already filled in.
   **Save value** does everything in one step. If the report can't be used, *Can't use this report?* marks it
   not usable, and no value is saved.
3. **Graph**: the value appears on the patient's lab graph. Clicking the point shows:
   *"Ramesh Kumar (P-1001) uploaded this HbA1c report on 26 Sep 2026. Reviewed by Dr. Priya Nair on 26 Sep 2026."*
   Clicking that line opens the original uploaded file.

### Why the document link is mandatory, and how it is enforced

A number without its source can't be checked. If a value were saved first and its document "attached" later,
a crash, a bug or a forgotten step could leave the value with no source, and nothing could reconnect it afterwards.
So the value and its link are created **in the same action**, and the **database itself** refuses anything else.
This does not rely on the app remembering to add the link:

- The value is saved in `clinical_events` with `source = 'patient_upload_reviewed'`, `confidence = 'high'`,
  `linked_document_id`, `reviewed_by` and `reviewed_date`, all in **one INSERT**.
- A database rule (`CHECK`) rejects any `patient_upload_reviewed` row without a document, a reviewer or a
  review time. A `FOREIGN KEY` makes the document id point to a real report **of the same patient**.
- A database trigger marks the report *reviewed* **inside that same statement**. If the report isn't waiting for
  review (already reviewed, rejected, or missing), the whole insert is undone. Half a save is impossible:
  there is never a value without its document, and never a "reviewed" report without its value.
- One report gives exactly one value (a unique index). Once saved, the link can't be changed or removed,
  and a report with a value can't be deleted.
- Tests prove it: `backend/tests/test_external_report_link.py` tries to save a value without a document, with
  another patient's document, twice, or half-way, both through the app code and with raw SQL. Every attempt is refused.

### Pending reports: one inbox for everything waiting

Everything a patient sends from the app that still needs a decision appears in **Pending reports** (sidebar, with a
count), oldest first, with filters *Test reports · Medicine bills · Photos · Readings*:

| Sent from the patient app | In the inbox | Decided with |
|---|---|---|
| Send a test report (outside lab) | "HbA1c report from an outside lab" | **Review** → split screen, enter the value |
| My medicine refill → bill | "Medicine bill" | **Review** → Approve / Reject with reason |
| My medicine refill → not collected yet | "Medicine refill" | **Confirm / Reject** right in the list |
| Today's sugar reading (typed) | "Sugar reading · 140 mg/dL" | **Confirm / Reject** |
| Today's sugar reading + meter photo | "Sugar reading with meter photo · Reading 131 mg/dL" | **View**, **Confirm / Reject** |
| Send a photo → prescription | "Prescription" | **View**, **Confirm / Reject** |
| Send a photo → lab report | "Lab report: kidney test · values not entered yet" | **View**, **Enter values**, **Confirm / Reject** |

A decided item leaves the list. A lab-report photo that was confirmed but has no values yet stays, marked
"values not entered yet", until its values are entered. The Overview's *Waiting for your review* uses the same list.

### Medicine Purchase Verification: refills are confirmed from the bill

1. **Patient** (app → *My medicine refill*): the disease is shown as a fixed *Diabetes* tag. **Take photo** or
   **Choose file**, then **Submit**. Submit stays disabled until a bill is attached, and the server refuses a
   submission without one. There is no date field: the server records the upload time. A refill can no longer be
   claimed as text only. *"I haven't collected my medicine yet"* still works.
   This creates a `medicine_bill` row in `external_reports` (status `pending_review`, disease `Diabetes`) and a `refill`
   event (status `pending_review`, source `patient_upload`, `linked_document_id` = the bill).
2. **Care team** (sidebar → *Medicine bills*, with a count): the bill is shown large, with **Approve** and **Reject**.
   *Purchase date (as shown on bill)* is optional and tucked away. Left empty, the upload day is used.
   Reject needs a reason: *unclear/unreadable*, *does not match the prescribed medicine*, *date does not match*,
   or *Other* with a required note.
3. **One decision, two rows.** The bill's status is the decision. A database trigger moves its refill to `active`
   (confidence high, reviewed_by/date, effective date = purchase date or upload day) or to `rejected`, inside the
   same statement. Neither row can change without the other.
4. **Silent Risk Detector.** While a bill is `pending_review`, the refill check waits (*"awaiting confirmation"*)
   and never flags. A rejected bill counts as if nothing had been sent, so the clock keeps running. Only an approved
   bill counts as a refill.
5. **The patient is told.** An in-app message and an e-mail carry the same text, e.g. *"Your medicine bill from
   26 Sep 2026 could not be verified. Reason: Bill image unclear or unreadable. Please upload a clearer bill or contact
   your care team."* Every bill shows its tag at all times: *Pending Review* / *Approved* /
   *Rejected — reason*. No mail service is connected, so e-mails are simulated: they are logged by the server
   (`EMAIL (simulated)`) and recorded in `patient_notifications`, including when a patient has no e-mail on file.

### Link to Report: values the care team types in by hand

Every paperclip, on the *Log data manually* rows and on *Values entered by the care team*, opens one right-side
panel. The panel lists **every lab report the patient uploaded**, through either path: *Send a test report*
(`external_reports`) or *Send a document → Lab report* (`patient_documents`), plus files the care team uploads.
The row's own test comes first under *Matches this test*, then all other reports. Matching only changes the order.

- **One request per patient page:** `GET /api/patients/{id}/uploaded-reports` returns a light list (name, type, test
  date, upload time, uploader, file kind). It is one query with no file contents. Every picker on the page reuses
  it. It is fetched again only after the care team uploads or corrects a report. An unchanged list answers **304**
  (ETag).
- **Type** comes from the test type the patient chose, or from the report's own name ("Kidney report" → eGFR report).
  Otherwise it shows *Unlabelled*.
- **Test date** is never the upload date standing in for it. A document sent without one shows *Test date not
  recorded*.
- **Corrections:** the care team can correct a report's name, type and test date from the preview. The correction is
  stored beside the upload (`report_details`), which is never rewritten.
- **Opening a file** uses `POST …/file-link`, which returns a link valid for 5 minutes, only for the clinician who
  asked, and only while they are on the patient's care team. The file is served with `no-store`.
- **Editing a value:** a pencil edits the value, unit (mmol/L and mmol/mol are converted to the unit the graphs use)
  and test date. Linking, changing and unlinking use `PATCH …/manual-values/{id}`: one transaction, with one audit
  entry per change that records old and new values. The row shows the latest action, e.g. *"Report changed by Dr. X ·
  time"*. Unlink asks first. The screen updates at once and rolls back with a message if the save fails.
- **Database rules:** a value links to `linked_document_id` (external report) or `linked_upload_id` (document), never
  both. Triggers enforce same patient, lab report only, and not rejected. They also make every link change record
  the previous link, who changed it and when. Rejected reports are listed as *Not usable* and cannot be linked.
- **Graph source line:** it reads the current link every time. A linked point says, for example, *"Ramesh Kumar
  uploaded "Kidney report" on 26 Sep 2026. Reviewed by Dr. Priya Nair."* and opens the file. An unlinked one says
  *"Entered manually by Dr. Priya Nair on 26 Sep 2026 — no report attached."*

## Appointment booking

A doctor sets their hours once in **Settings → Consultation hours**: start, end, slot length (default 15 min) and
buffer (default 5 min). Slots follow that cadence: 9:00–9:15, then 9:20–9:35. Patients book in the patient app
(**Appointments**), only with doctors on their own care team, up to 60 days ahead. A booking is **confirmed at once**.
It shows immediately on the patient's dashboard, on the Overview under *Upcoming appointments*, and in **Schedule**.

**Why two patients can never get the same slot**

- Slots are real rows (`appointment_slots`), made per doctor per day the first time that day is opened.
  `UNIQUE (doctor_id, date, start_time)` plus `INSERT OR IGNORE` means making a day twice, even at the same moment,
  never duplicates a slot.
- Booking is one transaction (`BEGIN IMMEDIATE`). A conditional update, `UPDATE … SET status = 'booked' WHERE id = ? AND
  status = 'available'`, moves the slot, and the appointment is inserted in the same transaction.
  Only one request can make that update succeed. The other gets **409 `slot_taken`**: *"This slot was just booked by
  someone else. Please choose another time."* The app then reloads the free times.
- Two database-level backstops: a partial unique index allows one live appointment per slot, and a trigger refuses an
  appointment on a slot that isn't booked.
- Availability is a single indexed query on `(doctor_id, date, status, start_time)`.
- `tests/test_appointments.py` races two bookings for the same slot 25 times on separate connections, and again over
  the API. Each time exactly one wins and the other gets `slot_taken`.
- A patient can hold one upcoming appointment per doctor. Booking again after the doctor became unavailable *is* the
  reschedule, and it closes the old appointment.

**Doctor unavailable** (Schedule → *Mark doctor unavailable*):

- **Entering the time:** times are entered in 12-hour format with an AM/PM choice that has no default. A live line
  under the form reads, for example, *"Blocking Dr. X: Thu, 1 Oct 2026, 2:00 PM to 5:30 PM (3 h 30 min)"*. A block can
  run all day or end on another day.
- ***Block time*** asks the server for a **preview** and opens a confirmation pop-up built only from that preview:
  - the doctor, and the date with its weekday;
  - the time in large type (AM/PM in bold) with the 24-hour time beside it;
  - the duration;
  - a midnight-to-midnight timeline per day, showing working hours, appointments and the hatched block;
  - who moves to *Needs rescheduling*, and how many free slots get blocked.
- **Warnings and errors:**
  - *Warnings (amber):* outside working hours (with a one-click *"Change to 2:00 PM – 5:30 PM"*), crossing midnight,
    longer than 12 hours, or shorter than 15 minutes.
  - *Errors (red, no confirm button):* the end is before the start (with an AM/PM swap), or the start is in the past.
- **Confirming:** needs a ticked *"I have checked the doctor, date and time (AM/PM)"*. The button repeats the action,
  e.g. *"Block 2:00 PM – 5:30 PM (moves 1 appointment)"*. *Edit time* has focus when the pop-up opens, and Enter never
  confirms.
- **Re-check on confirm:** the confirm call works out the plan again inside the save transaction. If it differs from
  the preview's fingerprint, because someone booked meanwhile, nothing is saved. The pop-up then shows *"The schedule
  changed - please review again."* with the fresh plan.
- **Storage:** blocks are stored as UTC instants (`starts_at`, `ends_at`, migration 0012) and always shown in
  Asia/Kolkata time.
- **Audit:** the audit entry records that the confirmation was shown and accepted, which warnings appeared, and any
  AM/PM fix made in the pop-up.
- **After confirming:**
  1. Free slots in the range are **blocked**, including slots of days made later.
  2. Booked appointments in the range become **Needs a new time**. They are never deleted.
  3. One message is drafted per patient, naming the doctor's next free slot after the block. Two patients are never
     offered the same slot.

Sending is a **separate step**, with **Send** on each message or **Send all**. So blocking stands even if delivery
fails. If the suggested slot was taken in the meantime, a fresh one is found before sending. The patient sees the
message on their home screen with *Choose a new time*.

A booked slot shows the patient's name only to clinicians on that patient's care team. Others see *"Another care team's
patient"* and cannot send that patient's message. Changing your hours rebuilds only future days nobody has booked or
blocked. Days with bookings keep their slots.

## Test Orders: tests to do before the next appointment

After a consultation, the care team opens the patient and uses **Tests to Do → Add tests**.

**Ordering**
- Each test gets a due date, priority, route (clinic lab, outside lab or either) and patient instructions. The
  instructions are pre-filled from the catalog, for example the fasting rules.
- **Default due date:** the next appointment minus `due_buffer_days` (3).
- **Due-date rules:**
  - A date **after** the appointment is refused.
  - A date inside the buffer is allowed, with a warning.
  - With no appointment booked, a date must be chosen.
  - Ordering a test that is already open shows a warning.
- **Review** shows a confirmation summary of the tests, their due dates and the appointment date. Saving is one
  transaction with an idempotency key, so a double click saves the order once.

**The patient**
- **My tests** shows a card per test. If the clinic lab may do it, the card says *"Get this done at our clinic by …
  Your results will appear automatically."*
- **I did this test elsewhere** uploads the report from the test's own card: test, test date, lab and file (PDF,
  JPG or PNG, checked by its real content, 10 MB max), with upload progress. It then says *"Sent for clinician
  review"*.

**Results**
- **Uploads:** an upload is verified with the existing report review screen, now showing *Answers order* and the
  lab. Saving the value verifies the order item. Rejecting needs a reason. The item goes back to waiting, the patient
  is told why, and a **Re-upload** button appears.
- **Clinic lab results** arrive at `POST /api/internal/lab-results`:
  - It needs an `X-Service-Token` matching the `LAB_SERVICE_TOKEN` environment variable. With no token set, the
    endpoint answers 503.
  - It is idempotent per (`lab_order_ref`, `test_code`).
  - A plausible matched result is verified automatically. An implausible value or a different unit goes to
    clinician review. A result with no matching order goes to *Clinic lab results with no order*.
- **Only clinician-verified values count as results.** Pending and rejected uploads are shown as reports, never as
  values, never on graphs, and never in FHIR as Observations.

**Status and audit**
- **Statuses:** ordered → (sample collected) → result received | uploaded by patient → verified → closed. Other
  states are rejected, cancelled and not done; each needs a reason.
- **Enforced twice:** the code and a database trigger (`test_order_transitions`) both enforce the status changes.
- **Every change is audited:** who, when, old and new values, and why.
- **Edits** need the item's current `version` (optimistic locking).
- **Overdue** and **"appointment changed"** flags are worked out on every read. Moving or cancelling an appointment,
  including through *Mark doctor unavailable*, flags open tests. It never changes their dates.

**Reminders, dashboard and header**
- **Reminders:** an hourly job sends in-app reminders 7, 3 and 1 day(s) before the due date, and once when a test
  becomes overdue. SMS and WhatsApp are log-only stubs that record the notification id only, never its text.
  Each (item, kind) and each (item, day) is sent at most once.
- **Overview:** the *Tests overdue before appointment* table can be filtered by doctor and by days to the
  appointment.
- **Patient header:** shows pending test counts.

**FHIR view** (*Tests to Do → FHIR view*): FHIR R4-shaped resources generated on request from the record.
Nothing is stored.
- Mapping: ServiceRequest per order, Observation per verified result, DocumentReference per linked report.
- Each resource has a readable summary and a raw JSON toggle, plus *Download bundle*. Each view is audited.
- Endpoints: `GET /api/patients/{id}/fhir/ServiceRequest|Observation|DocumentReference|Bundle`.
- Tests validate the output with `fhir.resources` (its R4B models, the R4 technical correction). It is FHIR R4
  shaped data only, with no ABDM or national compliance claimed.

**Demo simulator:** in the dev environment, each order that the clinic lab may do has a **Demo simulator** button.
It sends a made-up clinic-lab result through the same ingestion path as the real lab system. Turn it off with
`DEMO_MODE=0`; it is never available outside `APP_ENV=dev`. To call the real endpoint, start the backend with
`LAB_SERVICE_TOKEN=<secret>` and post:
`{"lab_order_ref": "...", "test_code": "HBA1C", "patient_code": "P-1001", "value": 7.4, "test_date": "2026-10-02"}`.

**Configuration:** `backend/config/test_orders.json` holds:
- the catalog, synced into the database at start-up by code (with LOINC codes, units, plausible ranges and
  instructions);
- `due_buffer_days`, `reminder_days_before`, `max_upload_mb` and `clinic_lab_name`.

Environment variables: `DEMO_MODE`, `LAB_SERVICE_TOKEN`, and `UC2_REMINDERS` (`off` disables the hourly job).

## Consultation Brief

**Who does what**
- **Clinical team:** **Briefs** lists the day's appointments with a readiness checklist (vitals today, items awaiting
  verification, how old the medicines list is, brief status). *Prepare brief* drafts the brief from verified data. The
  composer shows the brief exactly as the doctor will see it, with:
  - hide / pin for each line;
  - a team note (280 characters);
  - completeness warnings;
  - **Send**, whose confirmation shows the patient, ID, doctor and time with AM/PM.

  *Send all ready* sends every prepared draft for today after a review list.
- **Doctor:** **Doctor queue** shows today's patients whose brief was sent to them: name, age, ID, time, priority,
  status, nothing clinical.
  - *Call* asks for two identifiers (patient ID plus date of birth, or age). Only then does the brief appear, and
    calling the next patient clears it.
  - New verified data after the cut-off shows an **Updated since sent** banner, with a toggle to see the latest data.
    The sent version stays as sent.
  - **Print** gives one A4 page.

**Rules**
- **Deterministic:** templates and the rules in `backend/config/brief_rules.toml` (every threshold marked *requires
  clinician sign-off*). There is no generated text, no diagnosis and no dose advice. Medicines are "as recorded".
- **Verified data only:** values come from hospital records, ABHA records, care-team entries and reviewed uploads.
  Anything unreviewed appears only as "N items awaiting verification".
- **Versions are snapshots:** a sent version is immutable (database trigger). Re-sending creates the next version
  and marks the old one *superseded*. Drafting is idempotent (one draft per appointment), and sending a sent brief
  again returns it unchanged.
- **Comparison point:** "since last visit" compares against the snapshot saved when the doctor finished the last
  consultation, otherwise the last attended clinic visit.
- **Audit:** every draft, edit, send, call, identity check (passed or failed), open, acknowledgement and completion.

**Content schema 1.0** (`consultation_briefs.content_json`). It is a stable contract; the component
`frontend/src/components/ConsultationBrief.jsx` renders it.

```
identity          name, age, sex, patient_code, appointment {date, time, doctor}, visit_type,
                  priority {level, label, reason, rule}
since_last_visit  since, basis (snapshot | last_visit | none), items[≤5] {key, rule, rule_text, text, direction,
                  date, link, pinned}, more[], stable_line
key_numbers       rows (HbA1c, fasting glucose, post-meal glucose, eGFR, BP systolic, weight - only rows with data)
                  {latest, previous, change {value, direction}, trend[≤6], mixed_labs, labs}, not_on_file[], notice
medicines         items[≤8] {name, dose, status, source, date, changed}, more[], last_confirmed_on, possibly_outdated
tests             {name, due_by, state (done | awaiting verification | overdue | not done | open), result}
attention         items[≤3] {severity (Attention | Info), label, rule, rule_text, link}, more[]
team_note         {text, by, at}
footer            data_cutoff_at (UTC), verified_only, awaiting_verification, link
```

**API**

| Who | Endpoint |
|---|---|
| Clinical team | `GET /api/clinic/briefs/today?day=` |
| Clinical team | `POST /api/appointments/{id}/brief/draft` |
| Clinical team | `GET /api/briefs/{id}` |
| Clinical team | `PATCH /api/briefs/{id}` (hide / unhide / pin / unpin / note, with `row_version`) |
| Clinical team | `POST /api/briefs/{id}/send` (`allow_not_today`) |
| Clinical team | `POST /api/clinic/briefs/send-ready` (preview without ids) |
| Doctor | `GET /api/doctor/queue` (ETag) |
| Doctor | `POST /api/doctor/briefs/{id}/call` |
| Doctor | `POST /api/doctor/briefs/{id}/confirm-identity` |
| Doctor | `GET /api/doctor/briefs/{id}` |
| Doctor | `POST /api/doctor/briefs/{id}/acknowledge` |
| Doctor | `POST /api/doctor/briefs/{id}/complete` |
| Doctor | `GET /api/doctor/briefs/{id}/updates` (ETag) |

Screens poll every 20 seconds.

**The future doctor dashboard** reads the same endpoints and renders `content_json` with the same
`ConsultationBrief` component. Bump `schema_version` for any breaking change.

**Stubs:**
- per-patient targets (`brief.patient_targets`): the "above target" rule never fires until targets exist;
- contradiction cards (`brief.conflict_cards`);
- dose and schedule: medicines show "Dose and schedule not recorded" because the app stores none.

**Demo:** `python -m app.seed` books today's appointments with Dr. Priya Nair for Ramesh (worsening), Lakshmi
(stable) and Arjun Mehta, P-1004, a new patient with only today's weight. Sign in as the clinic team
(`CLN-TNSQ01`) to prepare and send the briefs, then as Dr. Priya (`CLN-PRYA27`) to see them; the password for
both is `demo1234`.

---

## Doctors and the clinic team: two dashboards

Every clinic account is either a **doctor** or the **clinic team**. The clinic's name is stored in
`clinic_profile` and is "Tanishq Clinic Management".

| | Clinic team (`/care-team`) | Doctor (`/doctor`) |
|---|---|---|
| Sees | every patient of the clinic | only the briefs sent to them |
| Menu | Overview, Patients, Schedule, Briefs, Pending reports, Medicine bills, Consent log | Dashboard |
| Briefs | prepares and sends them | "Tanishq Clinic Management sent a report about <patient>" → Open → confirm identity → brief |
| Settings | Account, Appearance, Sharing, Access log, Activity | Account, Appearance, Consultation hours |
| Takes appointments | no | yes (default hours on first use) |

- **Who is which:** people choose "A doctor" or "Clinic team" when they create an account.
- **Upgrading:** migration 0015 makes every existing account a doctor, because until then every account took
  appointments.
- **Clinic team login:** create one on an existing database with `python -m app.accounts add-team`. This makes a
  shared sign-in named after the clinic (no phone needed) and prints its Clinician ID and password once; change the
  password in Settings → Account.
- **Enforced by the server:**
  - Brief preparation and sending (`/api/clinic/briefs/*`, `/api/briefs/*`, `/api/appointments/{id}/brief/draft`)
    answer 403 to a doctor.
  - The doctor endpoints (`/api/doctor/*`, `/api/doctor-profile`) answer 403 to the clinic team.
  - `GET /api/doctor/inbox` returns the doctor's messages from the last 7 days, each with `sent_by.team`, `new`
    and `is_today`.
- **Clinic team access:** the team sees a patient while at least one active care-team assignment exists
  (`repo.visible_sql`).
- **Booking:** a patient the team registered can book any of the clinic's doctors.

---

## How the data is stored

The **database** is the source of truth for all structured data. **File storage** holds the uploaded files.
The **frontend** only displays data and holds no patient data of its own.

```text
ClinicalUser ──< CareTeamAssignment >── Patient
                                           ├──< PatientDocument ──(optional)── LabReport ──< LabTestResult
                                           ├──< LabReport ──< LabTestResult   (every measurement is its own row)
                                           ├──< ClinicalEvent  (refills, sugar logs, visits, emergencies, vitals, screenings, hypos)
                                           ├──< ConsentRequest
                                           └──< AuditLog
```

| Table | What it holds |
|---|---|
| `patients` | UUID id, patient code (P-1001), name, phone (for login and contact only, never used as the id), DOB, sex, ABHA |
| `clinical_users` | UUID id, system-generated Clinician ID, name, phone, scrypt password hash |
| `care_team_assignments` | which clinician may open which patient. This is what every access check reads. |
| `patient_documents` | file metadata: owner, type, description, size, sha256, `storage_key`, uploader, review status |
| `lab_reports` → `lab_test_results` | one report per lab per date. Each result keeps the value **as reported**, its unit, and the reference range **as printed on that report**. Results are never overwritten, so the full history is kept. |
| `clinical_events` | the timeline used by the 7 checks; also values read from outside-lab reports, each with its `linked_document_id` |
| `external_reports` | reports from outside labs uploaded by patients: test type, test date, upload date, file, status (`pending_review` / `reviewed` / `rejected`), reviewer |
| `consent_requests`, `audit_log` | the ABHA consent flow, and who viewed, uploaded or changed what, and when (no medical content in the log) |

Lab status (High/Low/Critical) and the risk flag are **calculated** from the stored values, not stored separately,
so they can never disagree with the data. Files live under `backend/storage/patients/{patient_id}/documents/`.
Schema changes are versioned SQL files in `backend/app/migrations/`, applied automatically at start-up.

### Keeping each patient's data separate

- **Patients** sign up and sign in with a one-time code sent to their phone, then use `/api/me/…`. The server identifies the patient from their sign-in session. No patient id is
  ever taken from the request, so editing a URL can't reach another patient.
- **Clinicians** can open `/api/patients/{id}/…` only with an active care-team assignment. Otherwise the answer
  is *404 Patient not found*, so the server doesn't even confirm the patient exists, and the attempt is logged.
- Files are only served through these checked routes, never as public links.
- Sessions are random tokens in HttpOnly cookies, stored only as hashes. Five wrong passwords or codes lock
  that account for 15 minutes.

---

## How the mock ABDM consent flow works

India's **ABDM** (Ayushman Bharat Digital Mission) lets one hospital see another hospital's
records **only if the patient agrees**. Getting access to the real ABDM test system takes weeks,
so this prototype includes a **pretend ABDM gateway** that follows the same steps:

1. **The care team asks.** They type the patient's 14-digit **ABHA number** and choose what they
   need (prescriptions, lab reports, emergency/discharge summaries). The system creates a consent
   request with a purpose ("Care management"), the record types, the period (last 12 months), how
   long access lasts (30 days), and a deadline to answer (24 hours).
2. **Nothing is shared yet.** The request waits on the **patient's phone**, which shows who is asking,
   what they want, and until when, with two big buttons: **Approve** or **Deny**.
3. **Approve**: the pretend "other hospital" sends its records. Only the types and dates the patient
   agreed to are saved into `events`, with `source = external_hospital_abdm`.
   The flags update straight away. In the demo, Ramesh's approved records reveal an emergency visit,
   which raises a High priority flag.
4. **Deny**: nothing is shared, and the care team sees *"Not approved"*.
5. **No answer in time**: the request expires, and the care team sees
   *"No response from City Care Hospital, Pune — the request expired unanswered"*.
   It never fails silently.
6. **Everything is logged**: requested, approved, data received, denied, expired, each with a
   timestamp and who did it. You can see this on each patient's page and in **Consent log**.

In the code ([`backend/app/abdm_mock.py`](backend/app/abdm_mock.py)) the other hospitals are small
built-in fixtures that return records shaped like real ABDM/FHIR data (`Encounter`,
`Observation`, `MedicationRequest`). Lab observations become a `lab_report` with its results; everything else becomes `clinical_events`.

---

## Optional demo patients (only after `python -m app.seed`)

| Patient | Story | Result |
|---|---|---|
| **Ramesh Kumar** (P-1001) | Refill 22 days overdue, logs down 72%, HbA1c 7.8 → 7.9 → 8.2, missed his last visit, eye screening overdue. An old medicine that was stopped is correctly ignored. | **Orange** (5 soft signs). Turns **red** after ABHA consent reveals an ER visit. |
| **Meena Rao** (P-1003) | Everything stable, but one hypoglycaemia episode (52 mg/dL) 9 days ago | **Red** on that one sign alone |
| **Lakshmi Iyer** (P-1002) | Refills on time, steady logs, HbA1c at goal, screenings up to date | **No warning signs** |

Each has 5–6 visits over 12–18 months, monthly refills, regular sugar logs, and HbA1c results.

---

## Project layout

```
backend/app/migrations/    versioned database schema
backend/app/repo.py        all database access (every patient query requires patient_id)
backend/app/deps.py        sign-in and care-team access checks
backend/app/routers/       auth.py · clinical.py (care team) · portal.py (patient) · appointments.py (booking, both sides)
backend/app/appointments.py  slots, race-safe booking, doctor unavailable, rebooking messages
backend/app/report_links.py  every uploaded report, label corrections, value edits and links, signed file links
backend/app/orders.py      Test Orders: status machine, due dates, uploads, clinic-lab ingestion, reminders · fhir.py
backend/config/            test_orders.json (test catalog, Test Orders settings) · brief_rules.toml (Consultation Brief rules)
backend/app/brief.py       Consultation Brief: deterministic builder, versions, send, doctor queue, identity gate
backend/app/storage.py     file storage (local disk; replaceable with S3 without touching the rest)
backend/app/detection.py   the 7 checks + the combine rule      backend/app/config.py  all thresholds
backend/app/labs.py        lab results vs. their own reference ranges
backend/app/abdm_mock.py   pretend ABDM gateway
backend/app/seed.py        optional demo data (never loaded automatically) · reset.py: wipe to empty
backend/tests/             312 tests, incl. test_security.py (isolation, authorization, file access), test_patient_accounts.py
                           test_external_report_link.py (the mandatory document link), test_appointments.py (booking races)
frontend/src/pages/        ClinicianLogin, Overview, Patients, PatientDetail, PendingReports, ReportReview, ConsentLog,
                           Schedule, Settings (care team) · Portal (patient)
frontend/src/components/   ui.jsx (design system: AppShell, sidebar, buttons, tables, forms), Modal, AddPatient, LabResults
```

## Hosting it publicly (demo)

The API also serves the built website, so the whole app runs as one service at one address.

- **One click on Render:** `render.yaml` + `Dockerfile`. Render → *New* → *Blueprint* → this repository. After the
  first deploy, set `SITE_URL` to the public address (no trailing slash) and redeploy.
- **Demo data only:** `UC2_SEED_DEMO=1` loads the made-up demo patients into an EMPTY database (never into one that
  has patients). `VITE_PUBLIC_DEMO=1` shows the demo logins on the front page. Never upload `backend/uc2.db`: it holds
  real people's records. `.gitignore` and `.dockerignore` keep every `*.db` file out.
- **Production mode:** `APP_ENV=prod` sets secure cookies and never shows one-time codes on screen. Patients sign in
  with a password; sign-up by SMS code needs a real SMS provider (the hook is in `routers/auth.py`).

**Search engines:**
- **Indexed:** only the front page `/`, which explains the product in plain words. `index.html` carries the title,
  description, canonical link, Open Graph and Twitter preview (`public/og-image.png`) and schema.org
  `WebSite` + `SoftwareApplication` data.
- **Served by the API:**
  - `/robots.txt` allows `/` and disallows `/api`, `/care-team`, `/doctor` and `/patient`.
  - `/sitemap.xml` is built from `SITE_URL`.
  - Every signed-in page and the API answer with `X-Robots-Tag: noindex, nofollow`.
- **After going live:**
  1. Add the address to Google Search Console and Bing Webmaster Tools.
  2. Submit `/sitemap.xml`.
  3. Request indexing of `/`.
  4. Link to the site from the GitHub repository and the Healthathon submission.

---

## Limits of this prototype

- Patient sign-in codes are shown on screen in demo mode. A real deployment would send them by SMS (the hook is in `routers/auth.py`) and run with `APP_ENV=prod`.
- The ABDM gateway and the other hospitals are simulated. No real ABDM connection is made.
- Uploaded lab-report files are stored and reviewed, but their contents are not read automatically (no OCR).
- SQLite suits a single server. For several servers, move to PostgreSQL; the schema is plain SQL.
# Health-a-thon-Project
