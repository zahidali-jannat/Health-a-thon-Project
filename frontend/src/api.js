async function request(path, options = {}) {
  let res
  try {
    res = await fetch(`/api${path}`, { credentials: 'same-origin', ...options })
  } catch {
    throw new Error('Cannot reach the server. Please check your connection and try again.')
  }
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    // detail is a sentence, a validation list, or {code, message} when the app must react (e.g. "slot_taken")
    const detail = Array.isArray(body.detail) ? 'Please check what you entered and try again.'
      : typeof body.detail === 'object' && body.detail ? body.detail.message : body.detail
    const err = new Error(detail || 'Something went wrong. Please try again.')
    err.status = res.status
    err.code = body.detail?.code
    err.data = typeof body.detail === 'object' ? body.detail : null   // e.g. a fresh preview with "schedule_changed"
    // A session that expires mid-use sends the user back to sign in.
    if (res.status === 401 && !path.startsWith('/auth/')) {
      window.dispatchEvent(new CustomEvent('uc2:signed-out', { detail: path.startsWith('/me/') ? 'patient' : 'clinician' }))
    }
    throw err
  }
  return body
}

const postJson = (path, data) =>
  request(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data ?? {}) })

const patchJson = (path, data) =>
  request(path, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data ?? {}) })

const postForm = (path, fields) => {
  const form = new FormData()
  Object.entries(fields).forEach(([k, v]) => v != null && v !== '' && form.append(k, v))
  return request(path, { method: 'POST', body: form })
}

export const api = {
  // clinician account
  clinicianSignup: (full_name, phone, password) => postJson('/auth/clinician/signup', { full_name, phone, password }),
  clinicianLogin: (identifier, password) => postJson('/auth/clinician/login', { identifier, password }),
  clinicianLogout: () => postJson('/auth/clinician/logout'),
  clinicianMe: () => request('/auth/clinician/me'),
  updateAccount: (changes) =>
    request('/auth/clinician/me', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changes) }),
  changeClinicianPassword: (currentPassword, newPassword) =>
    postJson('/auth/clinician/change-password', { current_password: currentPassword, new_password: newPassword }),
  myAccessLog: (patientId) => request(`/access-log${patientId ? `?patient_id=${encodeURIComponent(patientId)}` : ''}`),

  // care team - every patient route is authorised server-side
  patients: () => request('/patients'),
  worklist: () => request('/worklist'),
  createPatient: (data) => postJson('/patients', data),
  linkPatient: (patientCode, phone) => postJson('/patients/link', { patient_code: patientCode, phone }),
  resetPatientPassword: (id) => postJson(`/patients/${id}/reset-password`),
  patient: (id) => request(`/patients/${id}`),
  events: (id, source, limit = 12) => request(`/patients/${id}/events?source=${source}&limit=${limit}`),
  uploads: (id, all = false) => request(`/patients/${id}/uploads${all ? '?all=true' : ''}`),
  labs: (id) => request(`/patients/${id}/labs`),
  reports: (id) => request(`/patients/${id}/reports`),
  labReport: (id, reportId) => request(`/patients/${id}/reports/${reportId}`),
  enterReportResults: (id, reportId, body) => postJson(`/patients/${id}/reports/${reportId}/results`, body),
  documents: (id) => request(`/patients/${id}/documents`),
  documentUrl: (id, docId) => `/api/patients/${id}/documents/${docId}/file`,
  reviewDocument: (id, docId, status) => postJson(`/patients/${id}/documents/${docId}/review`, { status }),
  emergencyVisits: (id) => request(`/patients/${id}/emergency-visits`),
  reviewEvent: (id, eventId, status) => postJson(`/patients/${id}/events/${eventId}/review`, { status }),
  manual: (id, data) => postJson(`/patients/${id}/manual`, data),
  careTeam: (id) => request(`/patients/${id}/care-team`),
  addToCareTeam: (id, clinicianCode) => postJson(`/patients/${id}/care-team`, { clinician_code: clinicianCode }),
  removeFromCareTeam: (id, clinicianCode) =>
    request(`/patients/${id}/care-team/${encodeURIComponent(clinicianCode)}`, { method: 'DELETE' }),
  accessLog: (id) => request(`/patients/${id}/access-log`),
  hiTypes: () => request('/abdm/hi-types'),
  consents: (id) => request(`/patients/${id}/consent-requests`),
  requestConsent: (id, abha, hiTypes) => postJson(`/patients/${id}/consent-requests`, { abha_number: abha, hi_types: hiTypes }),
  simulateExpiry: (id, consentId) => postJson(`/patients/${id}/consent-requests/${consentId}/simulate-expiry`),
  consentLog: () => request('/consent-log'),

  // patient - identity comes from the session, never from the request
  requestCode: (identifier) => postJson('/auth/patient/request-code', { identifier }),
  patientSignup: (details) => postJson('/auth/patient/signup', details),
  patientLogin: (identifier, password) => postJson('/auth/patient/login', { identifier, password }),
  patientSetPassword: (newPassword, currentPassword) =>
    postJson('/auth/patient/set-password', { new_password: newPassword, current_password: currentPassword || null }),
  patientSignupVerify: (phone, code) => postJson('/auth/patient/signup/verify', { phone, code }),
  myProfile: () => request('/me/profile'),
  myDoctors: () => request('/me/care-team'),
  addMyDoctor: (clinicianCode) => postJson('/me/care-team', { clinician_code: clinicianCode }),
  removeMyDoctor: (clinicianCode) => request(`/me/care-team/${encodeURIComponent(clinicianCode)}`, { method: 'DELETE' }),
  verifyCode: (identifier, code, forgotPassword = false) =>
    postJson('/auth/patient/verify', { identifier, code, forgot_password: forgotPassword }),
  patientMe: () => request('/auth/patient/me'),
  patientLogout: () => postJson('/auth/patient/logout'),
  pendingConsents: () => request('/me/consents'),
  answerConsent: (cid, approve) => postJson(`/me/consents/${cid}`, { approve }),
  refill: (taken, refillDate) => postJson('/me/refill', { taken, refill_date: refillDate || null }),
  sugar: (value, photo) => postForm('/me/sugar', { value, photo }),
  upload: (kind, file, description) => postForm('/me/documents', { kind, description, file }),
  myDocuments: () => request('/me/documents'),
  myDocumentUrl: (docId) => `/api/me/documents/${docId}/file`,
  giveAge: (age) => postJson('/me/age', { age }),
  // medicine purchase verification - the bill is compulsory, no date is ever sent (the server stamps the time)
  uploadBill: (file) => postForm('/me/medicine-bills', { file }),
  myBills: () => request('/me/medicine-bills'),
  myBillUrl: (id) => `/api/me/medicine-bills/${id}/file`,
  // appointments - patient: only doctors on their own care team; a lost race answers err.code === 'slot_taken'
  myAppointments: () => request('/me/appointments'),
  bookableSlots: (doctorId, date) => request(`/me/appointments/slots?doctor_id=${encodeURIComponent(doctorId)}&date=${date}`),
  bookSlot: (slotId) => postJson('/me/appointments', { slot_id: slotId }),
  notifications: () => request('/me/notifications'),
  readNotification: (id) => postJson(`/me/notifications/${id}/read`),
  // External Report Review - the patient sends a report (type, date, file), never a value
  uploadExternalReport: ({ testType, testName, testDate, file }) =>
    postForm('/me/external-reports', { test_type: testType, test_name: testName, test_date: testDate, file }),
  myExternalReports: () => request('/me/external-reports'),
  myExternalReportUrl: (id) => `/api/me/external-reports/${id}/file`,

  // External Report Review - care team
  pendingReports: () => request('/external-reports/pending'),
  pendingItems: () => request('/pending-items'),        // Pending reports: everything waiting, one inbox
  externalReports: (patientId) => request(`/patients/${patientId}/external-reports`),
  externalReport: (patientId, reportId) => request(`/patients/${patientId}/external-reports/${reportId}`),
  externalReportUrl: (patientId, reportId, thumbnail = false) =>
    `/api/patients/${patientId}/external-reports/${reportId}/file${thumbnail ? '?preview=thumbnail' : ''}`,
  saveExternalValue: (patientId, reportId, body) => postJson(`/patients/${patientId}/external-reports/${reportId}/review`, body),
  // appointments - care team
  doctors: () => request('/doctors'),
  doctorSchedule: (doctorId, date) => request(`/doctors/${doctorId}/schedule?date=${date}`),
  // doctor unavailable: preview (what the confirmation pop-up shows) -> confirm the same plan (fingerprint)
  previewDoctorAway: (doctorId, block) => postJson(`/doctors/${doctorId}/unavailability/preview`, block),
  confirmDoctorAway: (doctorId, body) => postJson(`/doctors/${doctorId}/unavailability`, body),
  rescheduleNotices: (doctorId) => request(`/doctors/${doctorId}/reschedule-notices`),
  sendNotice: (noticeId) => postJson(`/reschedule-notices/${noticeId}/send`),
  sendAllNotices: (doctorId) => postJson(`/doctors/${doctorId}/reschedule-notices/send-all`),
  patientAppointments: (patientId) => request(`/patients/${patientId}/appointments`),
  doctorProfile: () => request('/doctor-profile'),
  saveDoctorProfile: (body) =>
    request('/doctor-profile', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  // medicine purchase verification - care team
  pendingBills: () => request('/medicine-bills/pending'),
  allBills: () => request('/medicine-bills'),
  patientBills: (patientId) => request(`/patients/${patientId}/medicine-bills`),
  bill: (patientId, billId) => request(`/patients/${patientId}/medicine-bills/${billId}`),
  approveBill: (patientId, billId, purchaseDate) =>
    postJson(`/patients/${patientId}/medicine-bills/${billId}/approve`, { purchase_date: purchaseDate || null }),
  rejectBill: (patientId, billId, reason, note) =>
    postJson(`/patients/${patientId}/medicine-bills/${billId}/reject`, { reason, note: note || null }),

  // "Link to Report" for manual values
  // every report this patient uploaded (both upload paths) - loaded once per patient page, reused by every picker
  uploadedReports: (patientId) => request(`/patients/${patientId}/uploaded-reports`),
  correctReport: (patientId, reportId, body) => patchJson(`/patients/${patientId}/uploaded-reports/${reportId}`, body),
  uploadReport: (patientId, { file, displayName, reportType, testDate }) =>
    postForm(`/patients/${patientId}/uploaded-reports`, { file, display_name: displayName, report_type: reportType, test_date: testDate }),
  reportFileLink: (patientId, reportId) => postJson(`/patients/${patientId}/uploaded-reports/${reportId}/file-link`),
  editManualValue: (patientId, eventId, body) => patchJson(`/patients/${patientId}/manual-values/${eventId}`, body),
  manualValues: (patientId) => request(`/patients/${patientId}/manual-values`),
  rejectExternalReport: (patientId, reportId, reason) =>
    postJson(`/patients/${patientId}/external-reports/${reportId}/reject`, { reason: reason || null }),
}

export function formatDate(iso) {
  if (!iso) return '—'
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`)
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

// "09:20" -> "9:20 AM" (appointment times are clinic wall-clock times, not instants)
export function formatTime(hm) {
  if (!hm) return '—'
  const [h, m] = hm.split(':').map(Number)
  return `${h % 12 || 12}:${String(m).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`
}

export function formatDateTime(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

export function daysBetween(fromIso, toIso) {
  return Math.round((new Date(`${toIso}T00:00:00`) - new Date(`${fromIso}T00:00:00`)) / 86400000)
}

export function todayIso() {
  const d = new Date()
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10)
}

export function formatAbha(abha) {
  return abha ? abha.replace(/^(\d{2})(\d{4})(\d{4})(\d{4})$/, '$1-$2-$3-$4') : '—'
}
