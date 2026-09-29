import { CheckCircle2, XCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, formatDate, formatDateTime } from '../api.js'
import { AppShell, btn, ErrorBox, input } from '../components/ui.jsx'
import { DocumentViewer } from './ReportReview.jsx'

/*
 * Medicine bill review: the bill (large) and two actions. Approving confirms the refill; rejecting needs a reason,
 * which the patient receives word for word. The purchase date is optional - only if the bill clearly shows one.
 */
export default function BillReview() {
  const { patientId, billId } = useParams()
  const navigate = useNavigate()
  const { state } = useLocation()
  const back = state?.from ?? '/care-team/bills'        // back to the list it was opened from
  const [bill, setBill] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api.bill(patientId, billId).then(setBill).catch((e) => setError(e.message))
  }, [patientId, billId])

  const done = (notice) => navigate(back, { state: { notice } })
  return (
    <AppShell title="Medicine bill" subtitle={bill && <>{bill.patient_name} · <span className="tnum">{bill.patient_code}</span></>}
      breadcrumbs={[back === '/care-team/reports' ? { label: 'Pending reports', to: back } : { label: 'Medicine bills', to: back },
        { label: bill?.patient_name ?? '…' }]}>
      <ErrorBox>{error}</ErrorBox>
      {bill && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_24rem] lg:items-start">
          <DocumentViewer report={bill} />
          {bill.status === 'pending_review' ? <Decide bill={bill} onDone={done} /> : <Decided bill={bill} />}
        </div>
      )}
    </AppShell>
  )
}

const row = 'flex justify-between gap-3 py-2 text-sm'

function Decide({ bill, onDone }) {
  const uploadDay = new Date(bill.upload_date).toLocaleDateString('en-CA')          // yyyy-mm-dd, local
  const [showDate, setShowDate] = useState(false)
  const [purchase, setPurchase] = useState('')
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const act = async (fn, notice) => {
    setBusy(true)
    setError('')
    try {
      await fn()
      onDone(notice)
    } catch (e) {
      setError(e.message)
      setBusy(false)
    }
  }
  const approve = () => act(() => api.approveBill(bill.patient_id, bill.id, showDate ? purchase : null),
    `Approved ${bill.patient_name}’s medicine bill. The refill is confirmed and the patient has been notified.`)
  const reject = () => act(() => api.rejectBill(bill.patient_id, bill.id, reason, note),
    `Rejected ${bill.patient_name}’s medicine bill. The patient has been told why and asked to upload it again.`)
  const canReject = reason && (reason !== 'other' || note.trim().length >= 3)

  return (
    <section className="rounded-lg border border-line bg-surface lg:sticky lg:top-16" aria-label="Decision">
      <header className="border-b border-line px-4 py-3">
        <h2 className="text-base font-semibold text-ink">Verify this bill</h2>
        <p className="text-sm text-muted">Does it show the patient’s diabetes medicine?</p>
      </header>
      <div className="px-4 pb-4">
        <dl className="divide-y divide-line">
          <div className={row}><dt className="text-muted">Patient</dt><dd className="font-medium text-ink">{bill.patient_name}</dd></div>
          <div className={row}><dt className="text-muted">Disease</dt><dd className="text-ink">Diabetes</dd></div>
          <div className={row}><dt className="text-muted">Uploaded</dt><dd className="text-ink tnum">{formatDateTime(bill.upload_date)}</dd></div>
        </dl>

        {!rejecting ? (
          <>
            <div className="mt-3 grid gap-2">
              <button type="button" disabled={busy || (showDate && !purchase)} onClick={approve}
                className={`${btn.primary} h-12! text-base!`}><CheckCircle2 size={18} aria-hidden="true" /> Approve</button>
              <button type="button" disabled={busy} onClick={() => setRejecting(true)}
                className={`${btn.danger} h-12! text-base!`}><XCircle size={18} aria-hidden="true" /> Reject</button>
            </div>
            {/* secondary and optional: only if the purchase date is clearly printed on the bill */}
            <div className="mt-4 border-t border-line pt-3 text-sm">
              {!showDate ? (
                <button type="button" onClick={() => setShowDate(true)} className="text-muted underline hover:text-ink">
                  Add the purchase date shown on the bill (optional)
                </button>
              ) : (
                <div>
                  <label htmlFor="purchase" className="block text-[13px] text-muted">Purchase date (as shown on bill) — optional</label>
                  <div className="mt-1 flex items-center gap-2">
                    <input id="purchase" type="date" value={purchase} max={uploadDay} onChange={(e) => setPurchase(e.target.value)} className={`${input} w-44!`} />
                    <button type="button" onClick={() => { setShowDate(false); setPurchase('') }} className="text-xs text-muted underline hover:text-ink">Remove</button>
                  </div>
                  <p className="mt-1 text-xs text-muted">Left empty, the upload date ({formatDate(uploadDay)}) is used.</p>
                </div>
              )}
            </div>
          </>
        ) : (
          <fieldset className="mt-3 rounded-md border border-alert-200 bg-alert-50 p-3">
            <legend className="px-1 text-sm font-semibold text-ink">Why is it rejected?<span className="ml-0.5 text-alert-700" aria-hidden="true">*</span></legend>
            <div className="space-y-1.5">
              {Object.entries(bill.reject_reasons).map(([code, label]) => (
                <label key={code} className="flex items-start gap-2 text-sm text-ink">
                  <input type="radio" name="reason" value={code} checked={reason === code} onChange={() => setReason(code)} className="mt-0.5 h-4 w-4 accent-alert-700" />
                  {label}
                </label>
              ))}
            </div>
            {reason === 'other' && (
              <div className="mt-2">
                <label htmlFor="reject-note" className="block text-[13px] font-medium text-ink">Short note for the patient<span className="ml-0.5 text-alert-700" aria-hidden="true">*</span></label>
                <input id="reject-note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={200}
                  placeholder="e.g. This is a grocery bill" className={`${input} mt-1`} />
              </div>
            )}
            <p className="mt-2 text-xs text-muted">The patient sees this reason and is asked to upload the bill again.</p>
            <div className="mt-2 flex gap-2">
              <button type="button" disabled={busy || !canReject} onClick={reject} className={btn.danger}>Reject bill</button>
              <button type="button" onClick={() => { setRejecting(false); setReason(''); setNote('') }} className={btn.secondary}>Back</button>
            </div>
          </fieldset>
        )}
        {error && <div className="mt-3"><ErrorBox>{error}</ErrorBox></div>}
      </div>
    </section>
  )
}

function Decided({ bill }) {
  const ok = bill.status === 'reviewed_approved'
  return (
    <section className={`rounded-lg border border-line border-l-4 bg-surface px-4 py-4 ${ok ? 'border-l-ok-700' : 'border-l-alert-700'}`}>
      <p className={`flex items-center gap-2 font-semibold ${ok ? 'text-ok-700' : 'text-alert-800'}`}>
        {ok ? <CheckCircle2 size={18} aria-hidden="true" /> : <XCircle size={18} aria-hidden="true" />} {ok ? 'Approved' : 'Rejected'}
      </p>
      <p className="mt-2 text-sm text-ink">By {bill.reviewed_by_name} on {formatDateTime(bill.reviewed_at)}.</p>
      {ok && <p className="text-sm text-muted">Refill dated {formatDate(bill.purchase_date || bill.upload_date)}{bill.purchase_date ? ' (purchase date on the bill)' : ' (upload date)'}.</p>}
      {!ok && <p className="mt-1 text-sm text-ink">Reason: {bill.reject_reason}</p>}
    </section>
  )
}
