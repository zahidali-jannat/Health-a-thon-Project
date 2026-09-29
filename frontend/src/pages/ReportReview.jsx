import { AlertTriangle, CheckCircle2, ExternalLink, Link2, XCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, formatDate, formatDateTime, todayIso } from '../api.js'
import { AppShell, btn, ErrorBox, input } from '../components/ui.jsx'

/*
 * External Report Review - the split screen. Exactly two things side by side: the uploaded document,
 * and a one-field form. Saving writes the value AND its link to this document in one step (server side,
 * one transaction), so the value can always be traced back to this exact file.
 */
export default function ReportReview() {
  const { patientId, reportId } = useParams()
  const navigate = useNavigate()
  const [report, setReport] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api.externalReport(patientId, reportId).then(setReport).catch((e) => setError(e.message))
  }, [patientId, reportId])

  const done = (notice) => navigate('/care-team/reports', { state: { notice } })

  return (
    <AppShell
      title={report ? `${report.test_label} report` : 'Report'}
      subtitle={report && <>{report.patient_name} · <span className="tnum">{report.patient_code}</span></>}
      breadcrumbs={[{ label: 'Pending reports', to: '/care-team/reports' }, { label: report?.patient_name ?? '…' }]}>
      <ErrorBox>{error}</ErrorBox>
      {report && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_24rem] lg:items-start">
          <DocumentViewer report={report} />
          {report.status === 'pending_review'
            ? <ValueForm report={report} onDone={done} />
            : <Decided report={report} />}
        </div>
      )}
    </AppShell>
  )
}

// url / label / height can be given for other kinds of file (e.g. a lab-report photo) and a smaller viewer.
export function DocumentViewer({ report: r, url: fileUrl, label, height = 'h-[70vh] lg:h-[calc(100vh-11.5rem)]' }) {
  const url = fileUrl ?? api.externalReportUrl(r.patient_id, r.id)
  const pdf = r.content_type === 'application/pdf'
  const bill = r.document_type === 'medicine_bill'
  const what = label ?? (bill ? `Medicine bill uploaded by ${r.patient_name}` : `${r.test_label} report uploaded by ${r.patient_name}`)
  return (
    <section aria-label="Uploaded report" className="min-w-0 overflow-hidden rounded-lg border border-line bg-surface">
      <header className="flex items-center justify-between gap-2 border-b border-line px-4 py-2">
        <h2 className="text-sm font-semibold text-ink">Uploaded {bill ? 'bill' : 'report'} <span className="font-normal text-muted">· {pdf ? 'PDF' : 'Photo'}</span></h2>
        <a href={url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sm text-brand-700 hover:underline">
          <ExternalLink size={14} aria-hidden="true" /> Open full size
        </a>
      </header>
      {pdf ? (
        <iframe src={url} title={what} className={`block w-full bg-subtle ${height}`} />
      ) : (
        <div className={`overflow-auto bg-subtle ${height}`}>
          <img src={url} alt={what} className="mx-auto block w-full max-w-4xl" />
        </div>
      )}
    </section>
  )
}

const row = 'flex justify-between gap-3 py-2 text-sm'

function ValueForm({ report: r, onDone }) {
  const other = r.test_type === 'Other'
  const [value, setValue] = useState('')
  const [unit, setUnit] = useState('')
  const [changingDate, setChangingDate] = useState(false)
  const [testDate, setTestDate] = useState(r.test_date)
  const [errors, setErrors] = useState({})
  const [serverError, setServerError] = useState('')
  const [busy, setBusy] = useState(false)
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState('')

  const save = async (e) => {
    e.preventDefault()
    const n = Number(value.replace(',', '.'))
    const problems = {
      value: value.trim() === '' ? 'Enter the value printed on the report.' : Number.isNaN(n) && 'Enter a number, e.g. 7.9',
      unit: other && !unit.trim() && 'Enter the unit printed on the report.',
      date: changingDate && (!testDate ? 'Choose the test date.' : testDate > todayIso() && 'The test date cannot be in the future.'),
    }
    setErrors(problems)
    if (Object.values(problems).some(Boolean)) return
    setBusy(true)
    setServerError('')
    try {
      const saved = await api.saveExternalValue(r.patient_id, r.id, {
        value: n, unit: other ? unit.trim() : null, test_date: changingDate && testDate !== r.test_date ? testDate : null,
      })
      const res = saved.result
      onDone(`Saved ${r.test_label} ${res.value}${res.unit === '%' ? ' %' : ` ${res.unit}`} for ${r.patient_name}, linked to the uploaded report.`)
    } catch (err) {
      if (err.status === 422) setErrors({ value: err.message })
      else setServerError(err.message)
      setBusy(false)
    }
  }

  const reject = async () => {
    setBusy(true)
    try {
      await api.rejectExternalReport(r.patient_id, r.id, reason.trim())
      onDone(`The ${r.test_label} report from ${r.patient_name} was marked as not usable. No value was saved.`)
    } catch (err) {
      setServerError(err.message)
      setBusy(false)
    }
  }

  return (
    <form onSubmit={save} noValidate className="rounded-lg border border-line bg-surface lg:sticky lg:top-16">
      <header className="border-b border-line px-4 py-3">
        <h2 className="text-base font-semibold text-ink">Enter the result</h2>
        <p className="text-sm text-muted">Read it from the report on the left.</p>
      </header>
      <div className="px-4 pb-4">
        <dl className="divide-y divide-line">
          <div className={row}><dt className="text-muted">Test type</dt><dd className="font-medium text-ink">{r.test_label}</dd></div>
          <div className={row}>
            <dt className="text-muted">Test date</dt>
            <dd className="text-right">
              {changingDate ? (
                <input type="date" aria-label="Test date" value={testDate} max={todayIso()} onChange={(e) => setTestDate(e.target.value)}
                  className={`${input} w-40!`} />
              ) : (
                <span className="font-medium text-ink tnum">{formatDate(r.test_date)}</span>
              )}
              <button type="button" onClick={() => { setChangingDate((c) => !c); setTestDate(r.test_date); setErrors((x) => ({ ...x, date: null })) }}
                className="mt-0.5 block w-full text-right text-xs text-brand-700 hover:underline">
                {changingDate ? 'Keep the date from the upload' : 'Date is wrong? Change it'}
              </button>
              {errors.date && <span className="block text-xs text-alert-800">{errors.date}</span>}
            </dd>
          </div>
          <div className={row}><dt className="text-muted">Uploaded</dt><dd className="text-right text-ink tnum">{formatDateTime(r.upload_date)}<span className="block text-xs text-muted">by the patient</span></dd></div>
        </dl>

        <label htmlFor="ext-value" className="mt-3 block text-sm font-semibold text-ink">
          Enter value<span className="ml-0.5 text-alert-700" aria-hidden="true">*</span><span className="sr-only"> (required)</span>
        </label>
        <div className="mt-1.5 flex items-center gap-2">
          <input id="ext-value" value={value} onChange={(e) => { setValue(e.target.value); setErrors((x) => ({ ...x, value: null })) }}
            inputMode="decimal" autoComplete="off" autoFocus placeholder={r.test_type === 'HbA1c' ? 'e.g. 7.9' : 'e.g. 126'}
            aria-invalid={Boolean(errors.value) || undefined} aria-describedby="ext-value-help"
            className={`${input} h-12! text-2xl! font-semibold tnum`} />
          {!other && <span className="w-16 shrink-0 text-lg text-muted">{r.expected_unit}</span>}
        </div>
        {errors.value ? (
          <p className="mt-1.5 flex items-start gap-1.5 text-sm font-medium text-alert-800"><AlertTriangle size={15} aria-hidden="true" className="mt-0.5 shrink-0" /> {errors.value}</p>
        ) : (
          <p id="ext-value-help" className="mt-1 text-xs text-muted">Exactly as printed. It is saved together with a link to this report.</p>
        )}

        {other && (
          <>
            <label htmlFor="ext-unit" className="mt-3 block text-sm font-semibold text-ink">
              Unit<span className="ml-0.5 text-alert-700" aria-hidden="true">*</span><span className="sr-only"> (required)</span>
            </label>
            <input id="ext-unit" value={unit} onChange={(e) => { setUnit(e.target.value); setErrors((x) => ({ ...x, unit: null })) }}
              maxLength={20} placeholder="e.g. ng/mL" className={`${input} mt-1.5 max-w-40`} />
            {errors.unit && <p className="mt-1.5 text-sm font-medium text-alert-800">{errors.unit}</p>}
          </>
        )}

        {serverError && <div className="mt-3"><ErrorBox>{serverError}</ErrorBox></div>}

        <button type="submit" disabled={busy} className={`${btn.primary} mt-4 h-11! w-full text-base!`}>{busy ? 'Saving…' : 'Save value'}</button>

        <div className="mt-4 border-t border-line pt-3">
          {!rejecting ? (
            <button type="button" onClick={() => setRejecting(true)} className="text-sm text-muted underline hover:text-ink">
              Can’t use this report?
            </button>
          ) : (
            <div className="rounded-md border border-alert-200 bg-alert-50 p-3">
              <label htmlFor="ext-reason" className="block text-sm font-medium text-ink">Why can’t it be used? <span className="font-normal text-muted">(optional)</span></label>
              <input id="ext-reason" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={200}
                placeholder="e.g. Photo is blurred, wrong patient" className={`${input} mt-1`} />
              <div className="mt-2 flex gap-2">
                <button type="button" disabled={busy} onClick={reject} className={btn.danger}>Mark as not usable</button>
                <button type="button" onClick={() => setRejecting(false)} className={btn.secondary}>Back</button>
              </div>
              <p className="mt-2 text-xs text-muted">No value is saved. The patient sees that the report wasn’t accepted.</p>
            </div>
          )}
        </div>
      </div>
    </form>
  )
}

function Decided({ report: r }) {
  const reviewed = r.status === 'reviewed'
  return (
    <section className={`rounded-lg border border-line border-l-4 bg-surface px-4 py-4 ${reviewed ? 'border-l-ok-700' : 'border-l-alert-700'}`}>
      <p className={`flex items-center gap-2 font-semibold ${reviewed ? 'text-ok-700' : 'text-alert-800'}`}>
        {reviewed ? <CheckCircle2 size={18} aria-hidden="true" /> : <XCircle size={18} aria-hidden="true" />}
        {reviewed ? 'Reviewed' : 'Marked as not usable'}
      </p>
      {reviewed && r.result && (
        <>
          <p className="mt-3 text-3xl font-semibold text-ink tnum">{r.result.value}{r.result.unit === '%' ? ' %' : ` ${r.result.unit}`}</p>
          <p className="text-sm text-muted">{r.result.name} · test on {formatDate(r.result.date)}</p>
          <p className="mt-3 flex items-start gap-1.5 text-sm text-ink"><Link2 size={15} aria-hidden="true" className="mt-0.5 shrink-0 text-brand-700" /> {r.reference_line}</p>
          <Link to={`/care-team/patients/${r.patient_id}?tab=labs`} className={`${btn.secondary} mt-4`}>See it on the lab graph</Link>
        </>
      )}
      {!reviewed && (
        <p className="mt-2 text-sm text-ink">
          By {r.reviewed_by_name} on {formatDateTime(r.reviewed_at)}.{r.reject_reason && <> Reason: {r.reject_reason}</>}
        </p>
      )}
    </section>
  )
}
