import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, RefreshCw, Search, UserPlus } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, daysBetween, formatDate } from '../api.js'
import AddPatientDialog from '../components/AddPatient.jsx'
import FilterMenu from '../components/FilterMenu.jsx'
import { AppShell, btn, ErrorBox, input, LEVELS, StatusBadge, td, th } from '../components/ui.jsx'
import { useClinician } from '../auth.jsx'

const LEVEL_RANK = { high_priority: 0, quietly_worse: 1, watch: 2, none: 3 }
const PAGE_SIZE = 25
// Low priority = not flagged (one warning sign or none), so All = Flagged + Low priority.
const FILTERS = [
  { id: 'all', label: 'All', test: () => true },
  { id: 'flagged', label: 'Flagged', test: (p) => p.assessment.flagged },
  { id: 'high', label: 'High priority', test: (p) => p.assessment.level === 'high_priority' },
  { id: 'low', label: 'Low priority', test: (p) => !p.assessment.flagged },
]

export function mainConcern(p) {
  const fired = p.assessment.signals.filter((s) => s.fired).sort((a, b) => (a.tier === 'hard' ? -1 : 0) - (b.tier === 'hard' ? -1 : 0))
  if (!fired.length) return null
  const first = fired[0].short.charAt(0).toUpperCase() + fired[0].short.slice(1)
  return fired.length > 1 ? `${first} +${fired.length - 1} more` : first
}

const SORTS = {
  status: (a, b) => LEVEL_RANK[a.assessment.level] - LEVEL_RANK[b.assessment.level] || b.assessment.soft_count - a.assessment.soft_count,
  name: (a, b) => a.full_name.localeCompare(b.full_name),
  last_visit: (a, b) => (a.last_visit ?? '').localeCompare(b.last_visit ?? ''),
  next_visit: (a, b) => (a.next_visit ?? '9999').localeCompare(b.next_visit ?? '9999'),
  hba1c: (a, b) => (a.latest_hba1c?.value ?? -1) - (b.latest_hba1c?.value ?? -1),
}

function SortHead({ k, sort, onSort, children, className = '' }) {
  return (
    <th scope="col" className={`${th} ${className}`} aria-sort={sort.key === k ? (sort.dir > 0 ? 'ascending' : 'descending') : 'none'}>
      <button type="button" onClick={() => onSort(k)} className="inline-flex items-center gap-1 uppercase hover:text-ink">
        {children}
        {sort.key === k && (sort.dir > 0 ? <ArrowUp size={12} aria-hidden="true" /> : <ArrowDown size={12} aria-hidden="true" />)}
      </button>
    </th>
  )
}

export default function Patients() {
  const { user } = useClinician()
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [adding, setAdding] = useState(false)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const [sort, setSort] = useState({ key: 'status', dir: 1 })
  const [page, setPage] = useState(0)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setData(await api.patients())
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => {
    load()
  }, [load])

  const all = useMemo(() => data?.patients ?? [], [data])
  const counts = Object.fromEntries(FILTERS.map((f) => [f.id, all.filter(f.test).length]))
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    return all
      .filter(FILTERS.find((f) => f.id === filter).test)
      .filter((p) => !q || p.full_name.toLowerCase().includes(q) || p.patient_code.toLowerCase().includes(q) || (p.phone ?? '').includes(q))
      .sort((a, b) => SORTS[sort.key](a, b) * sort.dir || a.full_name.localeCompare(b.full_name))
  }, [all, filter, query, sort])
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE))
  const shown = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)
  useEffect(() => setPage(0), [filter, query, sort])

  const sortBy = (key) => setSort((s) => ({ key, dir: s.key === key ? -s.dir : 1 }))
  const head = { sort, onSort: sortBy }

  return (
    <AppShell title="Patients" breadcrumbs={[{ label: 'Patients' }]}
      subtitle={data && <>{counts.all} under your care · <span className="text-alert-800">{counts.high} high priority</span> · {counts.flagged - counts.high} worsening</>}
      meta={data && <>Data as of {formatDate(data.as_of)}</>}
      actions={<button type="button" onClick={() => setAdding(true)} className={btn.primary}><UserPlus size={16} aria-hidden="true" /> Add patient</button>}>

      <div className="rounded-lg border border-line bg-surface">
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2.5">
          <div className="relative w-full sm:w-72">
            <Search size={15} aria-hidden="true" className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
            <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search patients"
              placeholder="Search name, Patient ID or phone" className={`${input} pl-8`} />
          </div>
          <FilterMenu name="patients" options={FILTERS} value={filter} counts={counts} onChange={setFilter} />
          <button type="button" onClick={load} aria-label="Refresh" title="Refresh" className={`${btn.secondary} ${btn.icon} ml-auto`}>
            <RefreshCw size={15} className={loading ? 'animate-spin' : ''} aria-hidden="true" />
          </button>
        </div>

        {error && <div className="p-3"><ErrorBox>{error}</ErrorBox></div>}

        {/* desktop / tablet: table */}
        <div className="hidden overflow-x-auto md:block">
          <table className="w-full">
            <thead className="border-b border-line bg-subtle">
              <tr>
                <SortHead {...head} k="name">Patient</SortHead>
                <SortHead {...head} k="status">Status</SortHead>
                <th scope="col" className={th}>Main concern</th>
                <SortHead {...head} k="last_visit">Last visit</SortHead>
                <SortHead {...head} k="hba1c" className="text-right">HbA1c</SortHead>
                <SortHead {...head} k="next_visit">Next visit</SortHead>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {shown.map((p) => (
                <tr key={p.id} onClick={() => navigate(`/care-team/patients/${p.id}`)}
                  className={`cursor-pointer border-l-[3px] hover:bg-subtle ${p.assessment.flagged ? LEVELS[p.assessment.level].rail : 'border-l-transparent'}`}>
                  <td className={td}>
                    <Link to={`/care-team/patients/${p.id}`} onClick={(e) => e.stopPropagation()} className="font-medium text-ink hover:text-brand-700 hover:underline">
                      {p.full_name}
                    </Link>
                    <span className="block text-xs text-muted tnum">{p.patient_code} · {p.age ?? '—'} y · {p.sex ?? '—'}</span>
                  </td>
                  <td className={td}><StatusBadge level={p.assessment.level} short /></td>
                  <td className={`${td} max-w-xs text-muted`}>{mainConcern(p) ?? '—'}</td>
                  <td className={`${td} whitespace-nowrap tnum`}>
                    {formatDate(p.last_visit)}
                    {p.last_visit && <span className="block text-xs text-muted">{daysBetween(p.last_visit, data.as_of)} days ago</span>}
                  </td>
                  <td className={`${td} text-right tnum`}>
                    {p.latest_hba1c ? `${p.latest_hba1c.value.toFixed(1)}%` : '—'}
                    {p.latest_hba1c && <span className="block text-xs text-muted">{formatDate(p.latest_hba1c.date)}</span>}
                  </td>
                  <td className={`${td} whitespace-nowrap tnum`}>
                    {formatDate(p.next_visit)}
                    {p.next_visit && <span className="block text-xs text-muted">in {daysBetween(data.as_of, p.next_visit)} days</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* phone: compact list, ordered by what needs attention */}
        <ul className="divide-y divide-line md:hidden" aria-label="Patients">
          {shown.map((p) => (
            <li key={p.id}>
              <Link to={`/care-team/patients/${p.id}`}
                className={`block border-l-[3px] px-3 py-2.5 ${p.assessment.flagged ? LEVELS[p.assessment.level].rail : 'border-l-transparent'}`}>
                <span className="flex items-center justify-between gap-2">
                  <span className="font-medium text-ink">{p.full_name}</span>
                  <StatusBadge level={p.assessment.level} short />
                </span>
                <span className="mt-0.5 block text-xs text-muted tnum">
                  {p.patient_code} · HbA1c {p.latest_hba1c ? `${p.latest_hba1c.value.toFixed(1)}%` : '—'} · Next {formatDate(p.next_visit)}
                </span>
                {mainConcern(p) && <span className="mt-0.5 block text-xs text-ink">{mainConcern(p)}</span>}
              </Link>
            </li>
          ))}
        </ul>

        {data && all.length === 0 && (
          <div className="px-4 py-10 text-center">
            <p className="font-medium text-ink">No patients yet</p>
            <p className="mx-auto mt-1 max-w-md text-sm text-muted">
              Add a patient, or give your Clinician ID <strong className="text-ink">{user.clinician_code}</strong> to your patients
              (they add you under “My doctors”) or to a colleague who can share a patient with you.
            </p>
            <button type="button" onClick={() => setAdding(true)} className={`${btn.primary} mt-4`}><UserPlus size={16} aria-hidden="true" /> Add patient</button>
          </div>
        )}
        {data && all.length > 0 && rows.length === 0 && (
          <p className="px-4 py-8 text-center text-sm text-muted">No patients match. <button type="button" className="text-brand-700 underline" onClick={() => { setQuery(''); setFilter('all') }}>Clear filters</button></p>
        )}

        {rows.length > 0 && (
          <div className="flex items-center justify-between border-t border-line px-3 py-2 text-xs text-muted tnum">
            <span>{page * PAGE_SIZE + 1}–{Math.min(rows.length, (page + 1) * PAGE_SIZE)} of {rows.length}</span>
            <span className="flex items-center gap-1">
              <button type="button" disabled={page === 0} onClick={() => setPage((p) => p - 1)} aria-label="Previous page" className={`${btn.secondary} ${btn.iconSm}`}><ChevronLeft size={14} aria-hidden="true" /></button>
              <span className="px-1">Page {page + 1} of {pages}</span>
              <button type="button" disabled={page >= pages - 1} onClick={() => setPage((p) => p + 1)} aria-label="Next page" className={`${btn.secondary} ${btn.iconSm}`}><ChevronRight size={14} aria-hidden="true" /></button>
            </span>
          </div>
        )}
      </div>

      <details className="mt-4 text-sm text-muted">
        <summary className="cursor-pointer font-medium text-ink">How flags work</summary>
        <div className="mt-2 space-y-1 border-l-2 border-line pl-3">
          <p>Plain rules, no AI scoring. Thresholds live in one configuration file.</p>
          <p><strong className="text-alert-800">High priority</strong> — any one of: a hypoglycaemia event in the last 90 days, or an emergency visit since the last clinic visit.</p>
          <p><strong className="text-warn-800">Quietly getting worse</strong> — 2 or more of: refill 10+ days late, sugar logs down 40%+, HbA1c flat or worse, missed last appointment, eye/foot/kidney screening overdue.</p>
        </div>
      </details>

      {adding && <AddPatientDialog onClose={() => setAdding(false)} />}
    </AppShell>
  )
}
