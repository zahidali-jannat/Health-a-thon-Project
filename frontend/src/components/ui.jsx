import {
  Activity, AlertTriangle, CalendarDays, CheckCircle2, ChevronDown, ChevronRight, CircleAlert, Copy, FileCheck2, History, Inbox, LayoutGrid, LogOut, Menu,
  Clock, OctagonAlert, Palette, ReceiptText, Settings, Share2, UserRound, Users, X,
} from 'lucide-react'
import { Children, createContext, useContext, useEffect, useState } from 'react'
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import { useClinician } from '../auth.jsx'

/* ============================================================== type scale
 * Every text element maps to one tier - nothing is "same size, same weight" as everything else.
 *   identity  patient name                                   24px bold
 *   section   section / panel / dialog titles, section boxes 16px bold
 *   row       row titles (a flag, a person, a key figure)    15px semibold
 *   body      values, dates, table cells                     14px regular
 *   meta      timestamps, "entered by", helper text          12px muted
 */
export const TYPE = {
  identity: 'text-2xl font-bold tracking-tight text-ink',
  section: 'text-base font-bold text-ink',
  row: 'text-[15px] font-semibold text-ink',
  body: 'text-sm font-normal text-ink',
  meta: 'text-xs text-muted',
}

// Panels inside a page that uses the full type scale (the patient record) get section-tier titles.
export const PanelTier = createContext('compact')

/* ============================================================== primitives */

const btnBase = 'inline-flex items-center justify-center gap-1.5 rounded-md text-sm font-medium whitespace-nowrap disabled:cursor-not-allowed disabled:opacity-50'
export const btn = {
  primary: `${btnBase} h-9 px-3.5 bg-brand-600 text-white hover:bg-brand-700`,
  secondary: `${btnBase} h-9 px-3.5 border border-line-strong bg-surface text-ink hover:bg-subtle`,
  ghost: `${btnBase} h-9 px-2.5 text-brand-700 hover:bg-brand-50`,
  danger: `${btnBase} h-9 px-3.5 border border-alert-200 bg-surface text-alert-800 hover:bg-alert-50`,
  // modifiers — always combined with a variant above, so they must win over its height/padding
  sm: 'h-7! px-2.5! text-xs!',
  icon: 'w-9! px-0!',
  iconSm: 'h-7! w-7! px-0!',
}
export const input =
  'block h-9 w-full rounded-md border border-line-strong bg-surface px-2.5 text-sm text-ink placeholder:text-muted/70 ' +
  'focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-100'
export const th = 'px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-muted'
export const td = 'px-3 py-2.5 align-top text-sm text-ink'

const TONES = {
  ok: 'bg-ok-50 text-ok-700 border-ok-700/20',
  warn: 'bg-warn-50 text-warn-800 border-warn-200',
  alert: 'bg-alert-50 text-alert-800 border-alert-200',
  info: 'bg-brand-50 text-brand-800 border-brand-100',
  neutral: 'bg-subtle text-muted border-line',
}

export function Badge({ tone = 'neutral', icon: Icon, children, className = '' }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs font-medium ${TONES[tone]} ${className}`}>
      {Icon && <Icon size={13} aria-hidden="true" className="shrink-0" />}
      {children}
    </span>
  )
}

// A bordered section with a header bar. Used where grouping helps - not around every block.
// `large` is the patient app's bigger type scale.
// With no children the panel is just its header (no empty body underneath).
export function Panel({ title, description, actions, children, className = '', bodyClass = 'px-4 py-3', id, large = false }) {
  const hasBody = Children.toArray(children).length > 0   // null / false / empty children → header-only panel
  const section = useContext(PanelTier) === 'section'
  return (
    <section id={id} aria-label={typeof title === 'string' ? title : undefined}
      className={`rounded-lg border border-line bg-surface ${className}`}>
      {(title || actions) && (
        <header className={`flex flex-wrap items-center justify-between gap-2 px-4 ${hasBody ? 'border-b border-line' : ''} ${large ? 'py-3' : 'py-2.5'}`}>
          <div>
            <h2 className={section ? TYPE.section : `font-semibold text-ink ${large ? 'text-base' : 'text-sm'}`}>{title}</h2>
            {description && <p className={section ? TYPE.meta : `text-muted ${large ? 'text-sm' : 'text-xs'}`}>{description}</p>}
          </div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      {hasBody && <div className={bodyClass}>{children}</div>}
    </section>
  )
}

export function Field({ label, htmlFor, required, hint, error, children, className = '' }) {
  return (
    <div className={className}>
      <label htmlFor={htmlFor} className="mb-1 block text-[13px] font-medium text-ink">
        {label}
        {required && <span className="ml-0.5 text-alert-700" aria-hidden="true">*</span>}
        {required && <span className="sr-only"> (required)</span>}
      </label>
      {children}
      {hint && !error && <p className="mt-1 text-xs text-muted">{hint}</p>}
      {error && <p className="mt-1 flex items-center gap-1 text-xs text-alert-800"><AlertTriangle size={12} aria-hidden="true" /> {error}</p>}
    </div>
  )
}

export function Tabs({ tabs, value, onChange, label }) {
  return (
    <div role="tablist" aria-label={label} className="flex gap-1 overflow-x-auto border-b border-line">
      {tabs.map((t) => {
        const active = t.id === value
        return (
          <button key={t.id} type="button" role="tab" aria-selected={active} onClick={() => onChange(t.id)}
            className={`-mb-px flex shrink-0 items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium ${
              active ? 'border-brand-600 text-brand-700' : 'border-transparent text-muted hover:text-ink'}`}>
            {t.label}
            {t.count != null && t.count > 0 && (
              <span className={`rounded px-1.5 text-xs ${t.attention ? 'bg-warn-50 text-warn-800' : 'bg-subtle text-muted'}`}>{t.count}</span>
            )}
          </button>
        )
      })}
    </div>
  )
}

export function ErrorBox({ children }) {
  if (!children) return null
  return (
    <div role="alert" className="flex items-start gap-2 rounded-md border border-alert-200 bg-alert-50 px-3 py-2 text-sm text-alert-800">
      <AlertTriangle size={16} aria-hidden="true" className="mt-0.5 shrink-0" /> <span>{children}</span>
    </div>
  )
}

export function Notice({ children }) {
  if (!children) return null
  return (
    <p role="status" className="flex items-center gap-1.5 text-sm font-medium text-ok-700">
      <CheckCircle2 size={15} aria-hidden="true" /> {children}
    </p>
  )
}

/* ============================================================== application shell */

export const SETTINGS_SECTIONS = [
  { id: 'account', label: 'Account', text: 'Name, phone, password', icon: UserRound },
  { id: 'appearance', label: 'Appearance', text: 'Light, dark or pleasant', icon: Palette },
  { id: 'consultation-hours', label: 'Consultation hours', text: 'When patients can book you', icon: Clock },
  { id: 'sharing', label: 'Sharing', text: 'Share a patient with a colleague', icon: Share2 },
  { id: 'access-log', label: 'Access log', text: 'Who opened or changed records', icon: History },
  { id: 'patient-activity', label: 'Activity by patients', text: 'What your patients sent in the last 14 days', icon: Activity },
]

const NAV = [
  { to: '/care-team', label: 'Overview', icon: LayoutGrid, end: true },
  { to: '/care-team/patients', label: 'Patients', icon: Users },
  { to: '/care-team/schedule', label: 'Schedule', icon: CalendarDays },
  { to: '/care-team/reports', label: 'Pending reports', icon: Inbox, count: 'pendingReports' },
  { to: '/care-team/bills', label: 'Medicine bills', icon: ReceiptText, count: 'pendingBills' },
  { to: '/care-team/consent-log', label: 'Consent log', icon: FileCheck2 },
]

function Sidebar({ onNavigate }) {
  const { user } = useClinician()
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const onSettings = pathname.startsWith('/care-team/settings')
  const [settingsOpen, setSettingsOpen] = useState(onSettings)

  const item = ({ isActive }) =>
    `flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm ${
      isActive ? 'bg-brand-50 font-semibold text-brand-800' : 'text-muted hover:bg-subtle hover:text-ink'}`

  const signOut = async () => {
    await api.clinicianLogout().catch(() => {})
    navigate('/care-team/login', { replace: true })
  }

  // how many outside-lab reports wait for review - refreshed whenever a page (and so the sidebar) mounts
  const [pendingReports, setPendingReports] = useState(null)
  const [pendingBills, setPendingBills] = useState(null)
  useEffect(() => {
    api.pendingItems().then((r) => setPendingReports(r.length)).catch(() => {})     // everything waiting, not just lab reports
    api.pendingBills().then((r) => setPendingBills(r.length)).catch(() => {})
  }, [])
  const counts = { pendingReports, pendingBills }

  const initials = user.full_name.replace(/^Dr\.?\s+/i, '').split(/\s+/).map((w) => w[0]).slice(0, 2).join('').toUpperCase()

  return (
    <div className="flex h-full flex-col">
      <Link to="/care-team" onClick={onNavigate} className="flex items-center gap-2.5 border-b border-line px-4 py-3.5">
        <img src="/favicon.svg" alt="" className="h-7 w-7" />
        <span className="leading-tight">
          <span className="block text-sm font-semibold text-ink">UC2 Care</span>
          <span className="block text-[11px] text-muted">Consultation Readiness</span>
        </span>
      </Link>

      <nav aria-label="Main" className="flex-1 overflow-y-auto px-2.5 py-3">
        <p className="px-2.5 pb-1 text-[11px] font-semibold uppercase tracking-wider text-muted">Clinical</p>
        <ul className="space-y-0.5">
          {NAV.map(({ to, label, icon: Icon, end, count }) => (
            <li key={to}>
              <NavLink to={to} end={end} onClick={onNavigate} className={item}>
                <Icon size={16} aria-hidden="true" /> {label}
                {count && counts[count] > 0 && (
                  <span className="ml-auto rounded bg-warn-50 px-1.5 text-xs font-semibold text-warn-800 tnum"
                    aria-label={`${counts[count]} waiting`}>{counts[count]}</span>
                )}
              </NavLink>
            </li>
          ))}
        </ul>

        <p className="mt-5 px-2.5 pb-1 text-[11px] font-semibold uppercase tracking-wider text-muted">Account</p>
        <button type="button" aria-expanded={settingsOpen} aria-controls="settings-menu"
          onClick={() => setSettingsOpen((o) => !o)}
          className={`flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm ${
            onSettings ? 'font-semibold text-brand-800' : 'text-muted hover:bg-subtle hover:text-ink'}`}>
          <Settings size={16} aria-hidden="true" /> Settings
          <ChevronDown size={14} aria-hidden="true" className={`ml-auto ${settingsOpen ? 'rotate-180' : ''}`} />
        </button>
        {settingsOpen && (
          <ul id="settings-menu" aria-label="Settings" className="mt-0.5 space-y-0.5 border-l border-line pl-2 ml-4">
            {SETTINGS_SECTIONS.map(({ id, label }) => (
              <li key={id}>
                <NavLink to={`/care-team/settings/${id}`} onClick={onNavigate}
                  className={({ isActive }) => `block rounded-md px-2.5 py-1.5 text-sm ${
                    isActive ? 'bg-brand-50 font-semibold text-brand-800' : 'text-muted hover:bg-subtle hover:text-ink'}`}>
                  {label}
                </NavLink>
              </li>
            ))}
          </ul>
        )}
      </nav>

      <div className="border-t border-line px-3 py-3">
        <div className="flex items-center gap-2.5">
          <span aria-hidden="true" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-brand-50 text-xs font-semibold text-brand-800">
            {initials}
          </span>
          <span className="min-w-0 leading-tight">
            <span className="block truncate text-sm font-medium text-ink">{user.full_name}</span>
            <span className="block text-xs text-muted tnum">{user.clinician_code}</span>
          </span>
        </div>
        <button type="button" onClick={signOut} className={`${btn.ghost} mt-2 w-full justify-start text-muted hover:text-ink`}>
          <LogOut size={15} aria-hidden="true" /> Sign out
        </button>
      </div>
    </div>
  )
}

const careTeamSidebar = (close) => <Sidebar onNavigate={close} />

/**
 * Every signed-in page (care team and patient app) renders inside this shell:
 * sidebar navigation, a top bar with breadcrumbs, then the page header (title + primary action).
 * `sidebar(close)` supplies the navigation; `large` is the patient app's bigger type scale.
 * A breadcrumb is a link (`to`), a button (`onClick`) or, for the current page, plain text.
 */
export function AppShell({ title, subtitle, breadcrumbs = [], actions, meta, children, sidebar = careTeamSidebar, large = false }) {
  const [drawer, setDrawer] = useState(false)
  const { pathname } = useLocation()
  useEffect(() => setDrawer(false), [pathname])

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[232px_1fr]">
      <div className="hidden border-r border-line bg-surface lg:block">
        <aside className="sticky top-0 h-screen">
          {sidebar()}
        </aside>
      </div>

      {drawer && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <button type="button" aria-label="Close navigation" onClick={() => setDrawer(false)} className="absolute inset-0 bg-ink/40" />
          <div className="absolute inset-y-0 left-0 w-72 max-w-[85vw] border-r border-line bg-surface">
            <button type="button" onClick={() => setDrawer(false)} aria-label="Close navigation"
              className="absolute right-2 top-2.5 rounded-md p-1.5 text-muted hover:bg-subtle"><X size={18} /></button>
            {sidebar(() => setDrawer(false))}
          </div>
        </div>
      )}

      <div className="min-w-0">
        <div className="sticky top-0 z-20 flex h-12 items-center gap-3 border-b border-line bg-surface/95 px-4 backdrop-blur-sm lg:px-6">
          <button type="button" onClick={() => setDrawer(true)} aria-label="Open navigation"
            className="-ml-1 rounded-md p-1.5 text-muted hover:bg-subtle lg:hidden"><Menu size={20} /></button>
          <nav aria-label="Breadcrumb" className="min-w-0 flex-1">
            <ol className={`flex min-w-0 items-center gap-1 text-muted ${large ? 'text-base' : 'text-sm'}`}>
              {breadcrumbs.map((b, i) => (
                <li key={i} className="flex min-w-0 items-center gap-1">
                  {i > 0 && <ChevronRight size={14} aria-hidden="true" className="shrink-0" />}
                  {b.to ? <Link to={b.to} className="truncate hover:text-ink">{b.label}</Link>
                    : b.onClick ? <button type="button" onClick={b.onClick} className="truncate hover:text-ink hover:underline">{b.label}</button>
                      : <span aria-current="page" className="truncate font-medium text-ink">{b.label}</span>}
                </li>
              ))}
            </ol>
          </nav>
          {meta && <div className="hidden shrink-0 text-xs text-muted sm:block">{meta}</div>}
        </div>

        <main className={`px-4 py-5 lg:px-6 ${large ? 'text-base' : ''}`}>
          {(title || actions) && (
            <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <h1 className={`font-semibold tracking-tight text-ink ${large ? 'text-2xl' : 'text-xl'}`}>{title}</h1>
                {subtitle && <div className={`mt-0.5 text-muted ${large ? 'text-base' : 'text-sm'}`}>{subtitle}</div>}
              </div>
              {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
            </div>
          )}
          {children}
        </main>
      </div>
    </div>
  )
}

/* ============================================================== clinical status */

export const LEVELS = {
  high_priority: { label: 'High priority', short: 'High priority', tone: 'alert', icon: OctagonAlert,
    text: 'text-alert-800', iconCls: 'text-alert-700', rail: 'border-l-alert-700' },
  quietly_worse: { label: 'Quietly getting worse since last visit', short: 'Worsening since last visit', tone: 'warn',
    icon: AlertTriangle, text: 'text-warn-800', iconCls: 'text-warn-700', rail: 'border-l-warn-700' },
  watch: { label: '1 warning sign · watch', short: '1 warning sign', tone: 'neutral', icon: CircleAlert,
    text: 'text-ink', iconCls: 'text-warn-700', rail: 'border-l-line-strong' },
  none: { label: 'No warning signs', short: 'No warning signs', tone: 'ok', icon: CheckCircle2,
    text: 'text-ink', iconCls: 'text-ok-700', rail: 'border-l-ok-700' },
}

export function StatusBadge({ level, short = false }) {
  const l = LEVELS[level]
  return <Badge tone={l.tone} icon={l.icon}>{short ? l.short : l.label}</Badge>
}

// The dated facts behind one check (the "evidence"): label, value and an optional second line.
// Hierarchy inside an open check: value (17px; 18px semibold when it is the flagged fact) → label (15px) →
// supporting line (14px). The check's own title above stays larger than all of these.
export function SignalFacts({ signal: s }) {
  const badTone = `text-lg font-semibold ${s.tier === 'hard' ? 'text-alert-800' : 'text-warn-800'}`
  return (
    <dl className="divide-y divide-line">
      {s.facts.map((f, i) => {
        const rule = f.label === 'Flag when'
        const tone = f.tone === 'bad' ? badTone : f.tone === 'ok' ? 'text-[17px] text-ok-700'
          : rule ? 'text-[15px] text-muted' : 'text-[17px] text-ink'
        return (
          <div key={i} className="grid grid-cols-[minmax(8rem,14rem)_1fr] items-baseline gap-4 py-2">
            <dt className="text-[15px] text-muted">{f.label}</dt>
            <dd className="leading-snug tnum">
              <span className={`block ${tone}`}>{f.value}</span>
              {f.sub && <span className="mt-0.5 block text-sm text-muted">{f.sub}</span>}
            </dd>
          </div>
        )
      })}
    </dl>
  )
}

// Shown once, right after the clinic creates a patient or resets their password.
export function PatientCredentials({ title, credentials: c }) {
  const [copied, setCopied] = useState(false)
  const text = `Patient ID: ${c.patient_code}\nTemporary password: ${c.temporary_password}\nSign in at the patient app and choose your own password.`
  const copy = () => navigator.clipboard?.writeText(text).then(() => setCopied(true)).catch(() => {})
  return (
    <div role="status">
      <p className="flex items-center gap-2 text-sm font-semibold text-ok-700">
        <CheckCircle2 size={16} aria-hidden="true" /> {title} — {c.full_name}
      </p>
      <dl className="mt-3 grid grid-cols-2 divide-x divide-line rounded-md border border-line">
        <div className="px-3 py-2.5">
          <dt className="text-xs text-muted">Patient ID</dt>
          <dd className="text-lg font-semibold text-ink tnum" data-testid="new-patient-code">{c.patient_code}</dd>
        </div>
        <div className="px-3 py-2.5">
          <dt className="text-xs text-muted">Temporary password</dt>
          <dd className="font-mono text-lg font-semibold tracking-wider text-ink" data-testid="temp-password">{c.temporary_password}</dd>
        </div>
      </dl>
      <p className="mt-2 text-xs text-muted">
        Give both to the patient now — the password is shown only once. At first sign-in they must choose their own password.
      </p>
      <button type="button" onClick={copy} className={`${btn.ghost} mt-1 -ml-2.5`}>
        <Copy size={14} aria-hidden="true" /> {copied ? 'Copied' : 'Copy for the patient'}
      </button>
    </div>
  )
}

export const SOURCE_LABELS = {
  hospital_internal: 'Hospital records',
  patient_upload: 'Patient upload',
  external_hospital_abdm: 'Other hospital (ABHA)',
  care_team_manual: 'Care team entry',
}
