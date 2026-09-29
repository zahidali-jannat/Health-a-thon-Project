import { Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api, formatDateTime } from '../api.js'
import { AppShell, Badge, ErrorBox, input, td, th } from '../components/ui.jsx'

const ACTION = {
  REQUESTED: { label: 'Requested', tone: 'info' },
  GRANTED: { label: 'Approved', tone: 'ok' },
  DATA_RECEIVED: { label: 'Data received', tone: 'ok' },
  DENIED: { label: 'Denied', tone: 'alert' },
  EXPIRED: { label: 'Expired', tone: 'warn' },
}

export default function ConsentLog() {
  const [rows, setRows] = useState(null)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [action, setAction] = useState('')

  useEffect(() => {
    api.consentLog().then(setRows).catch((e) => setError(e.message))
  }, [])

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (rows ?? []).filter((r) => (!action || r.action === action) &&
      (!q || r.full_name.toLowerCase().includes(q) || r.hip_name.toLowerCase().includes(q) || r.patient_code.toLowerCase().includes(q)))
  }, [rows, query, action])

  return (
    <AppShell title="Consent log" breadcrumbs={[{ label: 'Consent log' }]}
      subtitle="Every request, approval, denial and expiry for records from other hospitals (mock ABDM).">
      <ErrorBox>{error}</ErrorBox>
      <div className="rounded-lg border border-line bg-surface">
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2.5">
          <div className="relative w-full sm:w-72">
            <Search size={15} aria-hidden="true" className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
            <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search consent log"
              placeholder="Search patient or hospital" className={`${input} pl-8`} />
          </div>
          <select value={action} onChange={(e) => setAction(e.target.value)} aria-label="Filter by action" className={`${input} max-w-44`}>
            <option value="">All actions</option>
            {Object.entries(ACTION).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select>
          {rows && <span className="ml-auto text-xs text-muted tnum">{shown.length} of {rows.length} entries</span>}
        </div>

        {rows && rows.length === 0 && <p className="px-4 py-8 text-center text-sm text-muted">No consent activity yet.</p>}
        {rows && rows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="border-b border-line bg-subtle"><tr>
                <th className={th}>Time</th><th className={th}>Patient</th><th className={th}>Other hospital</th>
                <th className={th}>Action</th><th className={th}>By</th><th className={th}>Detail</th>
              </tr></thead>
              <tbody className="divide-y divide-line">
                {shown.map((r) => (
                  <tr key={r.id} className="hover:bg-subtle">
                    <td className={`${td} whitespace-nowrap text-muted tnum`}>{formatDateTime(r.at)}</td>
                    <td className={`${td} whitespace-nowrap`}>{r.full_name}<span className="block text-xs text-muted tnum">{r.patient_code}</span></td>
                    <td className={`${td} text-muted`}>{r.hip_name}</td>
                    <td className={td}><Badge tone={ACTION[r.action]?.tone ?? 'neutral'}>{ACTION[r.action]?.label ?? r.action}</Badge></td>
                    <td className={td}>{r.actor}</td>
                    <td className={`${td} text-muted`}>{r.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </AppShell>
  )
}
