import { ChevronRight, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { api, formatDate, formatDateTime } from '../api.js'
import ReportThumb from '../components/ReportThumb.jsx'
import { AppShell, Badge, btn, ErrorBox, Notice, td, th } from '../components/ui.jsx'

const reviewPath = (b) => `/care-team/bills/${b.patient_id}/${b.id}`

// The Pending Medicine Bills queue: bills patients uploaded when buying their diabetes medicine, oldest first.
export default function MedicineBills() {
  const navigate = useNavigate()
  const { state } = useLocation()
  const [bills, setBills] = useState(null)
  const [all, setAll] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [pending, every] = await Promise.all([api.pendingBills(), api.allBills()])
      setBills(pending)
      setAll(every)
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

  const count = bills?.length ?? 0
  return (
    <AppShell title="Medicine bills" breadcrumbs={[{ label: 'Medicine bills' }]}
      subtitle="Bills patients uploaded when buying their diabetes medicine. Approve to confirm the refill."
      actions={<button type="button" onClick={load} aria-label="Refresh" title="Refresh" className={`${btn.secondary} ${btn.icon}`}>
        <RefreshCw size={15} className={loading ? 'animate-spin' : ''} aria-hidden="true" />
      </button>}>
      {state?.notice && <div className="mb-4 rounded-md border border-ok-700/20 bg-ok-50 px-3 py-2"><Notice>{state.notice}</Notice></div>}
      <ErrorBox>{error}</ErrorBox>

      {bills && (
        <div className="rounded-lg border border-line bg-surface">
          <div className="flex items-center gap-3 border-b border-line px-4 py-3">
            <span className={`flex h-10 min-w-10 items-center justify-center rounded-md px-2 text-xl font-semibold tnum ${
              count ? 'bg-warn-50 text-warn-800' : 'bg-ok-50 text-ok-700'}`}>{count}</span>
            <p className="text-sm text-ink">
              {count === 0 ? 'Nothing waiting — every bill has been checked.'
                : `${count === 1 ? 'bill is' : 'bills are'} waiting for review · oldest first`}
            </p>
          </div>
          {count > 0 && (
            <>
              <div className="hidden overflow-x-auto md:block">
                <table className="w-full">
                  <thead className="border-b border-line bg-subtle"><tr>
                    <th className={th}><span className="sr-only">Preview</span></th>
                    <th className={th}>Patient</th><th className={th}>Uploaded</th>
                    <th className={th}><span className="sr-only">Action</span></th>
                  </tr></thead>
                  <tbody className="divide-y divide-line">
                    {bills.map((b) => (
                      <tr key={b.id} onClick={() => navigate(reviewPath(b))} className="cursor-pointer hover:bg-subtle">
                        <td className={`${td} w-16`}><ReportThumb report={b} /></td>
                        <td className={td}>
                          <span className="font-medium">{b.patient_name}</span>
                          <span className="block text-xs text-muted tnum">{b.patient_code} · Diabetes</span>
                        </td>
                        <td className={`${td} whitespace-nowrap text-muted tnum`}>{formatDateTime(b.upload_date)}</td>
                        <td className={`${td} text-right`}>
                          <Link to={reviewPath(b)} onClick={(e) => e.stopPropagation()} className={btn.primary}>Review</Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <ul className="divide-y divide-line md:hidden" aria-label="Pending medicine bills">
                {bills.map((b) => (
                  <li key={b.id}>
                    <Link to={reviewPath(b)} className="flex items-center gap-3 px-4 py-3 hover:bg-subtle">
                      <ReportThumb report={b} />
                      <span className="min-w-0 flex-1">
                        <span className="block font-medium text-ink">{b.patient_name}</span>
                        <span className="block text-xs text-muted tnum">Uploaded {formatDateTime(b.upload_date)}</span>
                      </span>
                      <ChevronRight size={18} aria-hidden="true" className="shrink-0 text-muted" />
                    </Link>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      {all && <AllBills bills={all} />}
    </AppShell>
  )
}

const STATUS = {
  pending_review: { label: 'Pending review', tone: 'warn' },
  reviewed_approved: { label: 'Approved', tone: 'ok' },
  reviewed_rejected: { label: 'Rejected', tone: 'alert' },
}
const FILTERS = [['all', 'All'], ['pending_review', 'Pending'], ['reviewed_approved', 'Approved'], ['reviewed_rejected', 'Rejected']]

// Every bill of your patients - pending, approved and rejected - each one open to view (and review if pending).
function AllBills({ bills }) {
  const navigate = useNavigate()
  const [filter, setFilter] = useState('all')
  const count = (f) => (f === 'all' ? bills.length : bills.filter((b) => b.status === f).length)
  const shown = bills.filter((b) => filter === 'all' || b.status === filter)
  return (
    <section aria-labelledby="all-bills" className="mt-4 rounded-lg border border-line bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2.5">
        <div>
          <h2 id="all-bills" className="text-sm font-semibold text-ink">All bills</h2>
          <p className="text-xs text-muted">Every bill your patients uploaded · newest first</p>
        </div>
        <div role="group" aria-label="Filter bills by status" className="flex rounded-md border border-line-strong p-0.5">
          {FILTERS.map(([f, label]) => (
            <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}
              className={`rounded px-2.5 py-1 text-sm ${filter === f ? 'bg-brand-600 font-medium text-white' : 'text-muted hover:text-ink'}`}>
              {label} <span className="tnum opacity-80">{count(f)}</span>
            </button>
          ))}
        </div>
      </div>
      {shown.length === 0 ? (
        <p className="px-4 py-6 text-sm text-muted">{bills.length === 0 ? 'No bills uploaded yet.' : 'No bills with this status.'}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="border-b border-line bg-subtle"><tr>
              <th className={th}><span className="sr-only">Preview</span></th>
              <th className={th}>Patient</th><th className={th}>Uploaded</th><th className={th}>Status</th>
              <th className={th}>Refill dated</th><th className={th}><span className="sr-only">Action</span></th>
            </tr></thead>
            <tbody className="divide-y divide-line">
              {shown.map((b) => (
                <tr key={b.id} onClick={() => navigate(reviewPath(b))} className="cursor-pointer hover:bg-subtle">
                  <td className={`${td} w-16`}><ReportThumb report={b} size="h-12 w-10" /></td>
                  <td className={td}>
                    <span className="font-medium">{b.patient_name}</span>
                    <span className="block text-xs text-muted tnum">{b.patient_code}</span>
                  </td>
                  <td className={`${td} whitespace-nowrap text-muted tnum`}>{formatDateTime(b.upload_date)}</td>
                  <td className={td}>
                    <Badge tone={STATUS[b.status].tone}>{STATUS[b.status].label}</Badge>
                    {b.reviewed_by_name && <span className="mt-0.5 block text-xs text-muted">by {b.reviewed_by_name} · {formatDateTime(b.reviewed_at)}</span>}
                    {b.reject_reason && <span className="block text-xs text-alert-800">{b.reject_reason}</span>}
                  </td>
                  <td className={`${td} whitespace-nowrap tnum`}>
                    {b.status === 'reviewed_approved' ? formatDate(b.purchase_date || b.upload_date) : <span className="text-muted">—</span>}
                  </td>
                  <td className={`${td} text-right`}>
                    <Link to={reviewPath(b)} onClick={(e) => e.stopPropagation()}
                      className={`${b.status === 'pending_review' ? btn.primary : btn.secondary} ${btn.sm}`}>
                      {b.status === 'pending_review' ? 'Review' : 'View'}
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
