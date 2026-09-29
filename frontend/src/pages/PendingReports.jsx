import { Check, FileText, Keyboard, RefreshCw, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { api, formatDate, formatDateTime } from '../api.js'
import DocumentPreview from '../components/DocumentPreview.jsx'
import FilterMenu from '../components/FilterMenu.jsx'
import { AppShell, btn, ErrorBox, Notice, td, th } from '../components/ui.jsx'

/*
 * Pending reports: ONE inbox for everything patients sent from the app that still waits for a decision -
 * outside-lab test reports, medicine bills, photos (prescriptions, lab reports, meter photos) and typed readings.
 * Test reports and bills open their review screens; photos and readings are decided right here.
 * Once decided, an item leaves the list.
 */
const GROUPS = [
  ['all', 'All'], ['test_report', 'Test reports'], ['medicine_bill', 'Medicine bills'], ['photo', 'Photos'], ['reading', 'Readings'],
]

const fileUrl = (i, thumbnail = false) => (i.file.source === 'external'
  ? api.externalReportUrl(i.patient_id, i.file.id, thumbnail)
  : `${api.documentUrl(i.patient_id, i.file.id)}${thumbnail ? '?preview=thumbnail' : ''}`)

function Thumb({ item }) {
  const box = 'flex h-12 w-10 shrink-0 items-center justify-center rounded border border-line bg-subtle text-muted'
  if (!item.file) return <span className={box} title="Typed in the app"><Keyboard size={16} aria-hidden="true" /></span>
  if (item.file.content_type === 'application/pdf') {
    return <span className={`${box} flex-col`}><FileText size={15} aria-hidden="true" /><span className="text-[9px] font-semibold">PDF</span></span>
  }
  return <img src={fileUrl(item, true)} alt="" loading="lazy" className="h-12 w-10 shrink-0 rounded border border-line bg-subtle object-cover object-top" />
}

const received = (i) => (i.at.length > 10 ? formatDateTime(i.at) : formatDate(i.at))

export default function PendingReports() {
  const { state } = useLocation()
  const [items, setItems] = useState(null)
  const [group, setGroup] = useState('all')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState(state?.notice ?? '')
  const [loading, setLoading] = useState(true)
  const [preview, setPreview] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setItems(await api.pendingItems())
      setError('')
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => {
    load()
  }, [load])

  // decide a photo or a typed reading here; the list is then re-read from the server
  const decide = async (item, status) => {
    setError('')
    try {
      if (item.kind === 'entry') await api.reviewEvent(item.patient_id, item.id, status)
      else await api.reviewDocument(item.patient_id, item.id, status)
      setNotice(`${status === 'confirmed' ? 'Confirmed' : 'Rejected'}: ${item.label} from ${item.patient_name}.`)
      await load()          // e.g. a confirmed lab-report photo stays, now waiting only for its values
    } catch (e) {
      setError(e.message)
    }
  }

  const count = (g) => (items ?? []).filter((i) => g === 'all' || i.group === g).length
  const shown = (items ?? []).filter((i) => group === 'all' || i.group === group)
  const total = items?.length ?? 0

  return (
    <AppShell title="Pending reports" breadcrumbs={[{ label: 'Pending reports' }]}
      subtitle="Everything patients sent from the app that is waiting for your review — test reports, medicine bills, photos and readings."
      actions={<button type="button" onClick={load} aria-label="Refresh" title="Refresh" className={`${btn.secondary} ${btn.icon}`}>
        <RefreshCw size={15} className={loading ? 'animate-spin' : ''} aria-hidden="true" />
      </button>}>
      {notice && <div className="mb-4 rounded-md border border-ok-700/20 bg-ok-50 px-3 py-2"><Notice>{notice}</Notice></div>}
      <ErrorBox>{error}</ErrorBox>

      {items && (
        <div className="rounded-lg border border-line bg-surface">
          <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3">
            <span className={`flex h-10 min-w-10 items-center justify-center rounded-md px-2 text-xl font-semibold tnum ${
              total ? 'bg-warn-50 text-warn-800' : 'bg-ok-50 text-ok-700'}`}>{total}</span>
            <p className="flex-1 text-sm text-ink">
              {total === 0 ? 'Nothing waiting — everything patients sent has been reviewed.'
                : `${total === 1 ? 'item is' : 'items are'} waiting for review · oldest first`}
            </p>
            <FilterMenu name="pending items" options={GROUPS.map(([id, label]) => ({ id, label }))} value={group}
              counts={Object.fromEntries(GROUPS.map(([g]) => [g, count(g)]))} onChange={setGroup} />
          </div>

          {total > 0 && shown.length === 0 && <p className="px-4 py-6 text-sm text-muted">Nothing of this kind is waiting.</p>}
          {shown.length > 0 && (
            <>
              <div className="hidden overflow-x-auto md:block">
                <table className="w-full">
                  <thead className="border-b border-line bg-subtle"><tr>
                    <th className={th}>Patient</th><th className={th}>What was sent</th><th className={th}>Received</th>
                    <th className={`${th} text-right`}>Review</th>
                  </tr></thead>
                  <tbody className="divide-y divide-line">
                    {shown.map((i) => (
                      <tr key={`${i.kind}-${i.id}`} className="hover:bg-subtle">
                        <td className={td}>
                          <Link to={`/care-team/patients/${i.patient_id}`} className="font-medium text-ink hover:text-brand-700 hover:underline">{i.patient_name}</Link>
                          <span className="block text-xs text-muted tnum">{i.patient_code}</span>
                        </td>
                        <td className={td}>
                          <span className="font-medium">{i.label}</span>
                          {i.detail && <span className="block text-xs text-muted">{i.detail}</span>}
                        </td>
                        <td className={`${td} whitespace-nowrap text-muted tnum`}>{received(i)}</td>
                        <td className={`${td} text-right`}><Actions item={i} onDecide={decide} onView={() => setPreview(i)} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <ul className="divide-y divide-line md:hidden" aria-label="Pending items">
                {shown.map((i) => (
                  <li key={`${i.kind}-${i.id}`} className="px-4 py-3">
                    <div className="flex items-start gap-3">
                      <Thumb item={i} />
                      <span className="min-w-0 flex-1">
                        <span className="block font-medium text-ink">{i.label}</span>
                        <span className="block text-sm text-ink">{i.patient_name} <span className="text-muted tnum">· {i.patient_code}</span></span>
                        {i.detail && <span className="block text-xs text-muted">{i.detail}</span>}
                        <span className="block text-xs text-muted tnum">{received(i)}</span>
                      </span>
                    </div>
                    <div className="mt-2"><Actions item={i} onDecide={decide} onView={() => setPreview(i)} /></div>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      {preview && (
        <DocumentPreview patientId={preview.patient_id} url={fileUrl(preview)} title={`${preview.label} · ${preview.patient_name}`}
          report={{ id: preview.file.id, test_label: preview.label, upload_date: preview.at, content_type: preview.file.content_type }}
          onClose={() => setPreview(null)} />
      )}
    </AppShell>
  )
}

// What can be done with each kind of item, right from the inbox.
function Actions({ item: i, onDecide, onView }) {
  const [rejecting, setRejecting] = useState(false)
  const [busy, setBusy] = useState(false)
  const go = async (status) => {
    setBusy(true)
    await onDecide(i, status)
    setBusy(false)
    setRejecting(false)
  }
  const wrap = 'inline-flex flex-wrap items-center justify-end gap-1.5'

  if (i.kind === 'external') {
    return <Link to={`/care-team/reports/${i.patient_id}/${i.id}`} className={`${btn.primary} ${btn.sm}`}>Review</Link>
  }
  if (i.kind === 'bill') {
    return <Link to={`/care-team/bills/${i.patient_id}/${i.id}`} state={{ from: '/care-team/reports' }} className={`${btn.primary} ${btn.sm}`}>Review</Link>
  }
  // a lab report the patient uploaded: the file, Approval (Confirm / Reject) and its values - on one review screen
  if (i.kind === 'results' || i.document_type === 'lab_report') {
    return <Link to={`/care-team/lab-reports/${i.patient_id}/${i.report_id ?? i.id}`} className={`${btn.primary} ${btn.sm}`}>Review</Link>
  }
  if (rejecting) {
    return (
      <span className={wrap}>
        <span className="text-xs text-ink">Reject this?</span>
        <button type="button" disabled={busy} onClick={() => go('rejected')} className={`${btn.danger} ${btn.sm}`}>Yes, reject</button>
        <button type="button" onClick={() => setRejecting(false)} className={`${btn.secondary} ${btn.sm}`}>No</button>
      </span>
    )
  }
  return (
    <span className={wrap}>
      {i.file && <button type="button" onClick={onView} className={`${btn.ghost} ${btn.sm}`}>View</button>}
      <button type="button" disabled={busy} onClick={() => go('confirmed')} className={`${btn.secondary} ${btn.sm}`}>
        <Check size={13} aria-hidden="true" /> Confirm
      </button>
      <button type="button" disabled={busy} onClick={() => setRejecting(true)} className={`${btn.danger} ${btn.sm}`}>
        <X size={13} aria-hidden="true" /> Reject
      </button>
    </span>
  )
}
