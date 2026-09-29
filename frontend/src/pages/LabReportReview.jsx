import { Check, ChevronDown, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, formatDateTime } from '../api.js'
import EnterResults from '../components/EnterResults.jsx'
import { AppShell, Badge, btn, ErrorBox, Notice, td, th } from '../components/ui.jsx'
import { DocumentViewer } from './ReportReview.jsx'

/*
 * Review a lab report a patient uploaded (the kind with numerical results):
 *   left   - the report itself (PDF or photo) in a viewer of limited height
 *   right  - Approval (click to open): Confirm / Reject
 *            below it: enter the values of this report (saving them also confirms the report)
 * The report leaves Pending reports once its values are entered, or when it is rejected.
 */
export default function LabReportReview() {
  const { patientId, reportId } = useParams()
  const navigate = useNavigate()
  const [report, setReport] = useState(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [approvalOpen, setApprovalOpen] = useState(false)
  const [rejecting, setRejecting] = useState(false)
  const [busy, setBusy] = useState(false)
  const [formKey, setFormKey] = useState(0)

  const load = useCallback(() => api.labReport(patientId, reportId).then(setReport).catch((e) => setError(e.message)), [patientId, reportId])
  useEffect(() => {
    load()
  }, [load])

  const backToInbox = (message) => navigate('/care-team/reports', { state: { notice: message } })
  const decide = async (status) => {
    setBusy(true)
    setError('')
    try {
      await api.reviewDocument(patientId, report.document.id, status)
      if (status === 'rejected') {
        backToInbox(`Rejected the ${name} from ${report.patient_name}. Its values will not be used.`)
        return
      }
      await load()
      setApprovalOpen(false)
      setNotice('Report confirmed. Now enter its values below.')
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const name = report ? `lab report${report.report_type ? ` (${report.report_type})` : ''}` : 'lab report'
  const doc = report?.document
  const status = doc?.review_status
  return (
    <AppShell title={report ? `Lab report: ${report.report_type || 'report'}` : 'Lab report'}
      subtitle={report && <>{report.patient_name} · <span className="tnum">{report.patient_code}</span></>}
      breadcrumbs={[{ label: 'Pending reports', to: '/care-team/reports' }, { label: report?.patient_name ?? '…' }]}>
      <ErrorBox>{error}</ErrorBox>
      {notice && <div className="mb-4 rounded-md border border-ok-700/20 bg-ok-50 px-3 py-2"><Notice>{notice}</Notice></div>}

      {report && !doc && <p className="text-sm text-muted">This lab report has no uploaded file.</p>}
      {report && doc && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_34rem] lg:items-start">
          <DocumentViewer report={{ ...doc, patient_name: report.patient_name }} url={api.documentUrl(patientId, doc.id)}
            label={`Lab report uploaded by ${report.patient_name}`} height="h-[55vh] lg:h-[68vh]" />

          <div className="space-y-4">
            <section className="rounded-lg border border-line bg-surface" aria-label="Report details">
              <dl className="divide-y divide-line px-4">
                <div className="flex justify-between gap-3 py-2 text-sm"><dt className="text-muted">Patient</dt><dd className="font-medium text-ink">{report.patient_name}</dd></div>
                <div className="flex justify-between gap-3 py-2 text-sm"><dt className="text-muted">Report type</dt><dd className="text-ink">{report.report_type || '—'}</dd></div>
                <div className="flex justify-between gap-3 py-2 text-sm"><dt className="text-muted">Received</dt><dd className="text-ink tnum">{formatDateTime(doc.uploaded_at)}</dd></div>
                <div className="flex justify-between gap-3 py-2 text-sm"><dt className="text-muted">Status</dt><dd><StatusBadge doc={doc} results={report.results.length} /></dd></div>
              </dl>
            </section>

            {/* Approval: click to open the two options */}
            <section className="rounded-lg border border-line bg-surface" aria-label="Approval">
              <button type="button" aria-expanded={approvalOpen} aria-controls="approval-options" onClick={() => setApprovalOpen((o) => !o)}
                className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left hover:bg-subtle">
                <span>
                  <span className="block text-base font-semibold text-ink">Approval</span>
                  <span className="block text-sm text-muted">
                    {status === 'pending' ? 'Confirm the report is genuine and readable, or reject it' : status === 'confirmed' ? 'Confirmed' : 'Rejected'}
                  </span>
                </span>
                <ChevronDown size={18} aria-hidden="true" className={`shrink-0 text-muted ${approvalOpen ? 'rotate-180' : ''}`} />
              </button>
              {approvalOpen && (
                <div id="approval-options" className="border-t border-line px-4 py-3">
                  {status !== 'pending' ? (
                    <p className="text-sm text-ink">
                      {status === 'confirmed' ? 'Confirmed' : 'Rejected'}{doc.reviewed_by_name ? ` by ${doc.reviewed_by_name}` : ''}
                      {doc.reviewed_at ? ` on ${formatDateTime(doc.reviewed_at)}` : ''}.
                    </p>
                  ) : !rejecting ? (
                    <div className="grid grid-cols-2 gap-2">
                      <button type="button" disabled={busy} onClick={() => decide('confirmed')} className={`${btn.primary} h-11!`}>
                        <Check size={16} aria-hidden="true" /> Confirm
                      </button>
                      <button type="button" disabled={busy} onClick={() => setRejecting(true)} className={`${btn.danger} h-11!`}>
                        <X size={16} aria-hidden="true" /> Reject
                      </button>
                    </div>
                  ) : (
                    <div className="rounded-md border border-alert-200 bg-alert-50 p-3">
                      <p className="text-sm text-ink">Reject this report? Its values will not be used.</p>
                      <div className="mt-2 flex gap-2">
                        <button type="button" disabled={busy} onClick={() => decide('rejected')} className={btn.danger}>Yes, reject</button>
                        <button type="button" onClick={() => setRejecting(false)} className={btn.secondary}>No</button>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </section>

            {/* Enter the values of this lab report */}
            <section className="rounded-lg border border-line bg-surface" aria-label="Values of this lab report">
              <header className="border-b border-line px-4 py-3">
                <h2 className="text-base font-semibold text-ink">Enter the values of this lab report</h2>
                <p className="text-sm text-muted">Exactly as printed. Saving them also confirms the report.</p>
              </header>
              <div className="px-4 py-3">
                {status === 'rejected' ? (
                  <p className="text-sm text-muted">This report was rejected, so its values are not entered.</p>
                ) : (
                  <>
                    {report.results.length > 0 && (
                      <table className="mb-3 w-full rounded-md border border-line">
                        <thead className="border-b border-line bg-subtle"><tr>
                          <th className={th}>Test</th><th className={`${th} text-right`}>Result</th><th className={th}>Reference</th>
                        </tr></thead>
                        <tbody className="divide-y divide-line">
                          {report.results.map((t) => (
                            <tr key={t.id}>
                              <td className={td}>{t.test_name}</td>
                              <td className={`${td} text-right font-medium tnum`}>{t.value_text}{t.unit ? ` ${t.unit}` : ''}</td>
                              <td className={`${td} tnum`}>{t.reference_text ?? <span className="text-muted">Not printed</span>}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                    <EnterResults key={formKey} report={report} patientId={patientId}
                      onCancel={() => setFormKey((k) => k + 1)}
                      onSaved={() => backToInbox(`Saved the values of the ${name} from ${report.patient_name}. The report is confirmed.`)} />
                  </>
                )}
              </div>
            </section>
          </div>
        </div>
      )}
    </AppShell>
  )
}

function StatusBadge({ doc, results }) {
  if (doc.review_status === 'rejected') return <Badge tone="alert">Rejected</Badge>
  if (doc.review_status === 'confirmed') {
    return <Badge tone={results ? 'ok' : 'warn'}>{results ? `Confirmed · ${results} value${results === 1 ? '' : 's'} entered` : 'Confirmed · values not entered yet'}</Badge>
  }
  return <Badge tone="warn">Waiting for approval</Badge>
}

