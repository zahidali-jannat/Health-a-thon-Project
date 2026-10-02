import {
  AlertTriangle, ArrowLeft, CalendarDays, Camera, ClipboardList, CheckCircle2, ChevronRight, Droplet, FileText, FlaskConical, FolderOpen, House, KeyRound, LogIn, LogOut,
  Pill, ShieldCheck, Stethoscope, Upload, UserPlus,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, formatDate, formatTime, todayIso } from '../api.js'
import { AppShell, Badge, btn, input, Panel, td, th } from '../components/ui.jsx'

/*
 * Patient app. Same design system and shell as the care-team screens, on the `large` scale:
 * 16px body text, 48px controls, so it stays easy to use for older patients on a phone.
 */
const size = 'h-12! px-5! text-base! font-semibold'
const b = {
  primary: `${btn.primary} ${size}`,
  secondary: `${btn.secondary} ${size}`,
  danger: `${btn.primary} ${size} bg-alert-700! hover:bg-alert-800!`,
  ghost: `${btn.ghost} h-11! px-2! text-base! font-semibold`,
}
const field = `${input} h-12! px-3! text-lg!`
const link = 'font-semibold text-brand-700 underline-offset-2 hover:underline'

export default function Portal() {
  const [patient, setPatient] = useState(undefined)
  const [screen, setScreen] = useState('home')
  const [doneMessage, setDoneMessage] = useState('')
  const [ageLater, setAgeLater] = useState(false)     // "Not now": asked again at the next sign-in

  useEffect(() => {
    api.patientMe().then(setPatient).catch(() => setPatient(null))
    const onSignedOut = (e) => e.detail === 'patient' && setPatient(null)
    window.addEventListener('uc2:signed-out', onSignedOut)
    return () => window.removeEventListener('uc2:signed-out', onSignedOut)
  }, [])
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [screen])

  const finish = (message) => {
    setDoneMessage(message)
    setScreen('done')
  }
  const home = () => setScreen('home')
  const signOut = async () => {
    await api.patientLogout().catch(() => {})
    setPatient(null)
    setScreen('home')
  }

  if (patient === undefined) return <div className="min-h-screen" aria-busy="true" />
  if (patient === null) {
    return <SignedOut><Welcome onSignedIn={(p, next = 'home') => { setPatient(p); setScreen(next) }} /></SignedOut>
  }
  if (patient.needs_password) {
    return <SignedOut><SetPasswordScreen forced onSaved={(p) => { setPatient(p); setScreen('home') }} /></SignedOut>
  }
  if (patient.needs_age && !ageLater) {
    return <SignedOut><AgeScreen onSaved={() => setPatient((p) => ({ ...p, needs_age: false }))} onLater={() => setAgeLater(true)} /></SignedOut>
  }

  // Every signed-in screen renders its own AppShell with this navigation.
  const shell = {
    large: true,
    sidebar: (close) => (
      <PatientSidebar patient={patient} screen={screen} onSignOut={signOut}
        go={(s) => { setScreen(s); close?.() }} />
    ),
  }
  const crumbs = (...rest) => [{ label: 'Home', onClick: home }, ...rest]
  const props = { shell, crumbs, patient, go: setScreen, onBack: home, onDone: finish }

  switch (screen) {
    case 'refill': return <RefillScreen {...props} />
    case 'sugar': return <SugarScreen {...props} />
    case 'photo': return <PhotoScreen {...props} />
    case 'external': return <ExternalReportScreen {...props} />
    case 'documents': return <DocumentsScreen {...props} />
    case 'doctors': return <DoctorsScreen {...props} />
    case 'appointments': return <AppointmentsScreen {...props} />
    case 'tests': return <TestsScreen {...props} />
    case 'password': return <SetPasswordScreen {...props} onSaved={(p) => { setPatient(p); finish('Your new password is saved.') }} />
    case 'welcome-new': return <AccountCreated {...props} />
    case 'done': return <DoneScreen {...props} message={doneMessage} />
    default: return <HomeScreen {...props} />
  }
}

/* ============================================================== layout */

const NAV = [
  { key: 'home', label: 'Home', icon: House },
  { key: 'tests', label: 'My tests', icon: ClipboardList },
  { key: 'appointments', label: 'Appointments', icon: CalendarDays },
  { key: 'sugar', label: 'Sugar reading', icon: Droplet },
  { key: 'refill', label: 'Medicine refill', icon: Pill },
  { key: 'external', label: 'Send a test report', icon: FlaskConical },
  { key: 'photo', label: 'Send a document', icon: Upload },
  { key: 'documents', label: 'My documents', icon: FolderOpen },
  { key: 'doctors', label: 'My doctors', icon: Stethoscope },
]

function PatientSidebar({ patient, screen, go, onSignOut }) {
  const item = (active) =>
    `flex w-full items-center gap-3 rounded-md px-2.5 py-2 text-left text-[15px] ${
      active ? 'bg-brand-50 font-semibold text-brand-800' : 'text-muted hover:bg-subtle hover:text-ink'}`
  const initials = patient.full_name.split(/\s+/).map((w) => w[0]).slice(0, 2).join('').toUpperCase()
  const entry = ({ key, label, icon: Icon }) => (
    <li key={key}>
      <button type="button" onClick={() => go(key)} aria-current={screen === key ? 'page' : undefined} className={item(screen === key)}>
        <Icon size={18} aria-hidden="true" /> {label}
      </button>
    </li>
  )
  return (
    <div className="flex h-full flex-col">
      <button type="button" onClick={() => go('home')} className="flex items-center gap-2.5 border-b border-line px-4 py-3.5 text-left">
        <img src="/favicon.svg" alt="" className="h-7 w-7" />
        <span className="leading-tight">
          <span className="block text-sm font-semibold text-ink">My Diabetes Care</span>
          <span className="block text-[11px] text-muted">Patient app</span>
        </span>
      </button>
      <nav aria-label="Main" className="flex-1 overflow-y-auto px-2.5 py-3">
        <p className="px-2.5 pb-1 text-[11px] font-semibold uppercase tracking-wider text-muted">My care</p>
        <ul className="space-y-0.5">{NAV.map(entry)}</ul>
        <p className="mt-5 px-2.5 pb-1 text-[11px] font-semibold uppercase tracking-wider text-muted">Account</p>
        <ul>{entry({ key: 'password', label: 'Change password', icon: KeyRound })}</ul>
      </nav>
      <div className="border-t border-line px-3 py-3">
        <div className="flex items-center gap-2.5">
          <span aria-hidden="true" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-brand-50 text-xs font-semibold text-brand-800">
            {initials}
          </span>
          <span className="min-w-0 leading-tight">
            <span className="block truncate text-sm font-medium text-ink">{patient.full_name}</span>
            <span className="block text-xs text-muted tnum">{patient.patient_code}</span>
          </span>
        </div>
        <button type="button" onClick={onSignOut} className={`${btn.ghost} mt-2 w-full justify-start text-muted hover:text-ink`}>
          <LogOut size={15} aria-hidden="true" /> Sign out
        </button>
      </div>
    </div>
  )
}

// Signed-out screens: same two-column layout as the care-team sign-in.
function SignedOut({ children }) {
  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
      <aside className="hidden border-r border-line bg-surface lg:flex lg:flex-col lg:justify-between lg:px-12 lg:py-10">
        <Link to="/" className="flex items-center gap-2.5">
          <img src="/favicon.svg" alt="" className="h-8 w-8" />
          <span className="leading-tight">
            <span className="block text-[13px] font-medium text-ink">My Diabetes Care</span>
            <span className="block text-xs text-muted">Patient app</span>
          </span>
        </Link>
        <div className="max-w-md">
          <h2 className="text-lg font-semibold text-ink">Your diabetes care, between visits</h2>
          <ul className="mt-3 space-y-2 text-sm text-muted">
            <li className="flex gap-2"><Droplet size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-brand-700" />Send sugar readings, refills and reports from your phone.</li>
            <li className="flex gap-2"><Stethoscope size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-brand-700" />Only the doctors you choose can see your records.</li>
            <li className="flex gap-2"><ShieldCheck size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-brand-700" />Another hospital sees your records only if you approve.</li>
          </ul>
        </div>
        <p className="text-xs text-muted">For patients. Clinical staff use the <Link to="/care-team/login" className="text-brand-700 hover:underline">care-team sign-in</Link>.</p>
      </aside>
      <main className="flex min-h-screen items-start justify-center px-4 py-8 lg:items-center">
        <div className="w-full max-w-md text-base">
          <Link to="/" className="mb-8 flex items-center gap-2 lg:hidden">
            <img src="/favicon.svg" alt="" className="h-7 w-7" /><span className="text-base font-semibold text-ink">My Diabetes Care</span>
          </Link>
          {children}
        </div>
      </main>
    </div>
  )
}

/* ============================================================== form building blocks */

function useSubmit() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const run = async (fn) => {
    setBusy(true)
    setError('')
    try {
      await fn()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }
  return { busy, error, setError, run }
}

function Alert({ children }) {
  if (!children) return null
  return (
    <div role="alert" className="flex items-start gap-2 rounded-md border border-alert-200 bg-alert-50 px-3 py-2.5 text-base text-alert-800">
      <AlertTriangle size={18} aria-hidden="true" className="mt-0.5 shrink-0" /> <span>{children}</span>
    </div>
  )
}

function Success({ children }) {
  if (!children) return null
  return (
    <p role="status" className="flex items-start gap-2 rounded-md border border-ok-700/20 bg-ok-50 px-3 py-2.5 text-base text-ok-700">
      <CheckCircle2 size={18} aria-hidden="true" className="mt-0.5 shrink-0" /> {children}
    </p>
  )
}

function PField({ label, htmlFor, required, optional, hint, hintId, error, children, className = '' }) {
  return (
    <div className={className}>
      <label htmlFor={htmlFor} className="block text-base font-semibold text-ink">
        {label}
        {required && <span className="ml-0.5 text-alert-700" aria-hidden="true">*</span>}
        {required && <span className="sr-only"> (required)</span>}
        {optional && <span className="ml-1 font-normal text-muted">(optional)</span>}
      </label>
      <div className="mt-1.5">{children}</div>
      {hint && !error && <p id={hintId} className="mt-1 text-sm text-muted">{hint}</p>}
      {error && (
        <p className="mt-1.5 flex items-start gap-1.5 text-sm font-medium text-alert-800">
          <AlertTriangle size={15} aria-hidden="true" className="mt-0.5 shrink-0" /> {error}
        </p>
      )}
    </div>
  )
}

// A form in a bordered panel: header, fields, then Cancel / primary action in the footer.
function FormPanel({ title, description, onSubmit, footer, children }) {
  return (
    <form onSubmit={onSubmit} className="max-w-xl rounded-lg border border-line bg-surface">
      {title && (
        <header className="border-b border-line px-4 py-3">
          <h2 className="text-base font-semibold text-ink">{title}</h2>
          {description && <p className="text-sm text-muted">{description}</p>}
        </header>
      )}
      <div className="space-y-5 px-4 py-4">{children}</div>
      {footer && <div className="flex flex-col-reverse gap-2 border-t border-line bg-subtle px-4 py-3 sm:flex-row sm:justify-end">{footer}</div>}
    </form>
  )
}

function BackButton({ onClick }) {
  return (
    <button type="button" onClick={onClick} className={`${b.ghost} -ml-2 mb-3`}>
      <ArrowLeft size={20} aria-hidden="true" /> Back
    </button>
  )
}

function PasswordInput({ id, value, onChange, autoComplete = 'current-password', describedBy }) {
  const [show, setShow] = useState(false)
  return (
    <div className="relative">
      <input id={id} type={show ? 'text' : 'password'} value={value} onChange={onChange} autoComplete={autoComplete}
        aria-describedby={describedBy} required className={`${field} pr-20!`} />
      <button type="button" onClick={() => setShow((v) => !v)} aria-pressed={show}
        className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded px-2.5 py-1.5 text-sm font-semibold text-brand-700 hover:bg-brand-50">
        {show ? 'Hide' : 'Show'}
      </button>
    </div>
  )
}

function DemoCode({ code }) {
  if (!code) return null
  return <p className="rounded-md border border-brand-100 bg-brand-50 px-3 py-2 text-base text-brand-800">Demo mode — your code is <strong className="tnum">{code}</strong></p>
}

const codeField = `${field} text-2xl! font-semibold tracking-[0.4em] tnum`

/* ============================================================== signed out */

function Welcome({ onSignedIn }) {
  const [mode, setMode] = useState(null)
  if (mode === 'signin') return <SignIn onSignedIn={(p) => onSignedIn(p)} onBack={() => setMode(null)} />
  if (mode === 'create') return <CreateAccount onCreated={(p) => onSignedIn(p, 'welcome-new')} onBack={() => setMode(null)} />
  return (
    <>
      <h1 className="text-2xl font-semibold text-ink">Welcome</h1>
      <p className="mt-1 text-base text-muted">Keep your diabetes care team up to date from your phone.</p>
      <div className="mt-6 space-y-3">
        <button type="button" className={`${b.primary} w-full`} onClick={() => setMode('signin')}>
          <LogIn size={20} aria-hidden="true" /> Sign in
        </button>
        <button type="button" className={`${b.secondary} w-full`} onClick={() => setMode('create')}>
          <UserPlus size={20} aria-hidden="true" /> Create account
        </button>
      </div>
      <p className="mt-5 text-base text-muted">New here? Choose <strong className="text-ink">Create account</strong>. You only need your mobile number.</p>
    </>
  )
}

function SignIn({ onSignedIn, onBack }) {
  const [useCode, setUseCode] = useState(false)
  const [identifier, setIdentifier] = useState('')
  const [password, setPassword] = useState('')
  const { busy, error, run } = useSubmit()

  if (useCode) return <CodeSignIn onSignedIn={onSignedIn} onBack={() => setUseCode(false)} />

  const submit = (e) => {
    e.preventDefault()
    run(async () => onSignedIn(await api.patientLogin(identifier, password)))
  }
  return (
    <form onSubmit={submit}>
      <BackButton onClick={onBack} />
      <h1 className="text-2xl font-semibold text-ink">Sign in</h1>
      <p className="mt-1 text-base text-muted">Use your Patient ID or mobile number.</p>
      <div className="mt-6 space-y-5">
        <PField label="Patient ID or mobile number" htmlFor="identifier" required>
          <input id="identifier" value={identifier} onChange={(e) => setIdentifier(e.target.value)} autoComplete="username"
            placeholder="e.g. P-1001" className={field} required minLength={3} />
        </PField>
        <PField label="Password" htmlFor="password" required>
          <PasswordInput id="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </PField>
        <Alert>{error}</Alert>
        <button type="submit" disabled={busy} className={`${b.primary} w-full`}>{busy ? 'Signing in…' : 'Sign in'}</button>
      </div>
      <button type="button" onClick={() => setUseCode(true)} className={`${link} mt-5 block text-base`}>
        Forgot password? Sign in with a code instead
      </button>
    </form>
  )
}

function CodeSignIn({ onSignedIn, onBack }) {
  const [identifier, setIdentifier] = useState('')
  const [sent, setSent] = useState(null)
  const [code, setCode] = useState('')
  const { busy, error, setError, run } = useSubmit()

  const requestCode = (e) => {
    e.preventDefault()
    run(async () => setSent(await api.requestCode(identifier)))
  }
  const verify = (e) => {
    e.preventDefault()
    run(async () => onSignedIn(await api.verifyCode(identifier, code, true)))
  }

  if (sent) {
    return (
      <form onSubmit={verify}>
        <BackButton onClick={() => { setSent(null); setCode(''); setError('') }} />
        <h1 className="text-2xl font-semibold text-ink">Enter your code</h1>
        <p className="mt-1 text-base text-muted">We sent a 6-digit code to your phone ending {sent.phone_hint.slice(-4)}.</p>
        <div className="mt-6 space-y-5">
          <PField label="6-digit code" htmlFor="code" required>
            <input id="code" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
              inputMode="numeric" autoComplete="one-time-code" required minLength={6} className={codeField} />
          </PField>
          <DemoCode code={sent.dev_code} />
          <Alert>{error}</Alert>
          <button type="submit" disabled={busy || code.length !== 6} className={`${b.primary} w-full`}>
            {busy ? 'Checking…' : 'Sign in'}
          </button>
        </div>
      </form>
    )
  }

  return (
    <form onSubmit={requestCode}>
      <BackButton onClick={onBack} />
      <h1 className="text-2xl font-semibold text-ink">Sign in with a code</h1>
      <p className="mt-1 text-base text-muted">Enter your mobile number or Patient ID. We will send you a code.</p>
      <div className="mt-6 space-y-5">
        <PField label="Mobile number or Patient ID" htmlFor="identifier" required>
          <input id="identifier" value={identifier} onChange={(e) => setIdentifier(e.target.value)} autoComplete="tel"
            placeholder="e.g. 9000000001" className={field} required minLength={3} />
        </PField>
        <Alert>{error}</Alert>
        <button type="submit" disabled={busy} className={`${b.primary} w-full`}>{busy ? 'Sending…' : 'Send me a code'}</button>
      </div>
    </form>
  )
}

const SEXES = [['F', 'Female'], ['M', 'Male'], ['O', 'Other']]

function CreateAccount({ onCreated, onBack }) {
  const [f, setF] = useState({ full_name: '', phone: '', password: '', date_of_birth: '', sex: '' })
  const [sent, setSent] = useState(null)
  const [code, setCode] = useState('')
  const { busy, error, setError, run } = useSubmit()
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }))

  const send = (e) => {
    e.preventDefault()
    const details = Object.fromEntries(Object.entries(f).filter(([, v]) => v !== ''))
    run(async () => setSent(await api.patientSignup(details)))
  }
  const verify = (e) => {
    e.preventDefault()
    run(async () => onCreated(await api.patientSignupVerify(f.phone, code)))
  }

  if (sent) {
    return (
      <form onSubmit={verify}>
        <BackButton onClick={() => { setSent(null); setCode(''); setError('') }} />
        <h1 className="text-2xl font-semibold text-ink">Check your phone</h1>
        <p className="mt-1 text-base text-muted">We sent a 6-digit code to the number ending {sent.phone_hint.slice(-4)}.</p>
        <div className="mt-6 space-y-5">
          <PField label="6-digit code" htmlFor="signup-code" required>
            <input id="signup-code" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
              inputMode="numeric" autoComplete="one-time-code" required minLength={6} className={codeField} />
          </PField>
          <DemoCode code={sent.dev_code} />
          <Alert>{error}</Alert>
          <button type="submit" disabled={busy || code.length !== 6} className={`${b.primary} w-full`}>
            {busy ? 'Creating…' : 'Create my account'}
          </button>
        </div>
      </form>
    )
  }

  return (
    <form onSubmit={send}>
      <BackButton onClick={onBack} />
      <h1 className="text-2xl font-semibold text-ink">Create account</h1>
      <p className="mt-1 text-base text-muted">Fields marked <span className="text-alert-700">*</span> are required.</p>
      <div className="mt-6 space-y-5">
        <PField label="Your full name" htmlFor="su-name" required>
          <input id="su-name" value={f.full_name} onChange={set('full_name')} autoComplete="name" required maxLength={120} className={field} />
        </PField>
        <PField label="Mobile number" htmlFor="su-phone" required hint="We send a code to this number to check it is yours.">
          <input id="su-phone" value={f.phone} onChange={set('phone')} inputMode="tel" autoComplete="tel" required maxLength={20}
            placeholder="10 digits" className={field} />
        </PField>
        <PField label="Choose a password" htmlFor="su-password" required hintId="su-password-hint"
          hint="At least 6 characters. You will use it with your Patient ID to sign in.">
          <PasswordInput id="su-password" value={f.password} onChange={set('password')} autoComplete="new-password" describedBy="su-password-hint" />
        </PField>
        <PField label="Date of birth" htmlFor="su-dob" optional>
          <input id="su-dob" type="date" value={f.date_of_birth} onChange={set('date_of_birth')} max={todayIso()} className={`${field} sm:max-w-60`} />
        </PField>
        <fieldset>
          <legend className="text-base font-semibold text-ink">Sex <span className="font-normal text-muted">(optional)</span></legend>
          <div role="group" aria-label="Sex" className="mt-1.5 grid grid-cols-3 rounded-md border border-line-strong p-0.5">
            {SEXES.map(([v, label]) => (
              <button key={v} type="button" aria-pressed={f.sex === v} onClick={() => setF((s) => ({ ...s, sex: s.sex === v ? '' : v }))}
                className={`h-11 rounded text-base ${f.sex === v ? 'bg-brand-600 font-semibold text-white' : 'text-ink hover:bg-subtle'}`}>
                {label}
              </button>
            ))}
          </div>
        </fieldset>
        <Alert>{error}</Alert>
        <button type="submit" disabled={busy} className={`${b.primary} w-full`}>{busy ? 'Sending…' : 'Send me a code'}</button>
      </div>
    </form>
  )
}

function SetPasswordScreen({ forced = false, onSaved, onBack, shell, crumbs }) {
  const [current, setCurrent] = useState('')
  const [pw, setPw] = useState('')
  const [again, setAgain] = useState('')
  const [mismatch, setMismatch] = useState(false)
  const { busy, error, run } = useSubmit()

  const submit = (e) => {
    e.preventDefault()
    if (pw !== again) {
      setMismatch(true)
      return
    }
    run(async () => onSaved(await api.patientSetPassword(pw, forced ? null : current)))
  }
  const fields = (
    <>
      {!forced && (
        <PField label="Current password" htmlFor="pw-current" required>
          <PasswordInput id="pw-current" value={current} onChange={(e) => setCurrent(e.target.value)} />
        </PField>
      )}
      <PField label="New password" htmlFor="pw-new" required hint="At least 6 characters." hintId="pw-new-hint">
        <PasswordInput id="pw-new" value={pw} onChange={(e) => { setPw(e.target.value); setMismatch(false) }} autoComplete="new-password" describedBy="pw-new-hint" />
      </PField>
      <PField label="Type it again" htmlFor="pw-again" required
        error={mismatch && 'The two passwords are not the same. Please type them again.'}>
        <PasswordInput id="pw-again" value={again} onChange={(e) => { setAgain(e.target.value); setMismatch(false) }} autoComplete="new-password" />
      </PField>
      <Alert>{error}</Alert>
    </>
  )
  const save = <button type="submit" disabled={busy} className={b.primary}>{busy ? 'Saving…' : 'Save password'}</button>

  if (forced) {
    return (
      <form onSubmit={submit}>
        <h1 className="text-2xl font-semibold text-ink">Choose your own password</h1>
        <p className="mt-1 text-base text-muted">Please choose a new password that only you know.</p>
        <div className="mt-6 space-y-5">
          {fields}
          <div className="[&>button]:w-full">{save}</div>
        </div>
      </form>
    )
  }
  return (
    <AppShell {...shell} title="Change password" subtitle="Use a password that only you know." breadcrumbs={crumbs({ label: 'Change password' })}>
      <FormPanel onSubmit={submit} title="New password"
        footer={<><button type="button" onClick={onBack} className={b.secondary}>Cancel</button>{save}</>}>
        {fields}
      </FormPanel>
    </AppShell>
  )
}

// Asked once after sign-in when there is no date of birth on file. The care team uses the age, e.g. to
// compare the kidney test (eGFR) with the average for the patient's age group.
function AgeScreen({ onSaved, onLater }) {
  const [age, setAge] = useState('')
  const [invalid, setInvalid] = useState(false)
  const { busy, error, run } = useSubmit()
  const submit = (e) => {
    e.preventDefault()
    const n = Number(age)
    if (!Number.isInteger(n) || n < 1 || n > 120) {
      setInvalid(true)
      return
    }
    run(async () => {
      await api.giveAge(n)
      onSaved()
    })
  }
  return (
    <form onSubmit={submit} noValidate>
      <h1 className="text-2xl font-semibold text-ink">How old are you?</h1>
      <p className="mt-1 text-base text-muted">Your care team uses your age to read your test results, for example your kidney test.</p>
      <div className="mt-6 space-y-5">
        <PField label="Your age in years" htmlFor="age" required error={invalid && 'Please enter your age as a number, for example 58.'}>
          <input id="age" value={age} inputMode="numeric" autoComplete="off" maxLength={3} autoFocus
            onChange={(e) => { setAge(e.target.value.replace(/\D/g, '')); setInvalid(false) }}
            className={`${field} text-2xl! font-semibold tnum sm:max-w-40`} />
        </PField>
        <Alert>{error}</Alert>
        <button type="submit" disabled={busy} className={`${b.primary} w-full`}>{busy ? 'Saving…' : 'Save'}</button>
      </div>
      <button type="button" onClick={onLater} className={`${link} mt-5 block text-base`}>Not now</button>
    </form>
  )
}

/* ============================================================== home */

const ACTIONS = [
  { key: 'tests', icon: ClipboardList, title: 'My tests', text: 'Tests your doctor asked for, and by when' },
  { key: 'appointments', icon: CalendarDays, title: 'Book an appointment', text: 'Choose a time with your doctor' },
  { key: 'refill', icon: Pill, title: 'My medicine refill', text: 'Send the bill when you buy your medicine' },
  { key: 'sugar', icon: Droplet, title: "Today's sugar reading", text: 'Type the number from your meter' },
  { key: 'external', icon: FlaskConical, title: 'Send a test report', text: 'HbA1c or sugar test done at another lab' },
  { key: 'photo', icon: Camera, title: 'Send a photo', text: 'Prescription or lab report (optional)' },
  { key: 'documents', icon: FolderOpen, title: 'My documents', text: 'See what you have sent' },
  { key: 'doctors', icon: Stethoscope, title: 'My doctors', text: 'Choose who can see your records' },
]

const DOC_LABEL = {
  lab_report: 'Lab report', prescription: 'Prescription', glucometer_photo: 'Meter photo', other: 'Document',
  external: 'Test report', medicine_bill: 'Medicine bill',
}
const BILL_TONE = { pending_review: 'warn', reviewed_approved: 'ok', reviewed_rejected: 'alert' }
const STATUS = {
  pending: { label: 'Waiting for your care team', tone: 'info' },
  confirmed: { label: 'Checked by your care team', tone: 'ok' },
  rejected: { label: 'Not accepted', tone: 'alert' },
}
const docName = (d) => `${DOC_LABEL[d.document_type]}${d.description ? `: ${d.description}` : ''}`
const EXTERNAL_STATUS = { pending_review: 'pending', reviewed: 'confirmed', rejected: 'rejected' }
// Everything the patient sent, newest first: documents plus reports from outside labs.
async function loadMyDocuments() {
  const [docs, external, bills] = await Promise.all([api.myDocuments(), api.myExternalReports(), api.myBills()])
  return [
    ...bills.map((x) => ({
      key: `b${x.id}`, document_type: 'medicine_bill', description: null, uploaded_at: x.upload_date,
      url: api.myBillUrl(x.id), tag: x.status_text, tagTone: BILL_TONE[x.status],
    })),
    ...docs.map((d) => ({ ...d, key: `d${d.id}`, url: api.myDocumentUrl(d.id) })),
    ...external.map((x) => ({
      key: `x${x.id}`, document_type: 'external', description: x.test_label, uploaded_at: x.upload_date,
      review_status: EXTERNAL_STATUS[x.status], url: api.myExternalReportUrl(x.id), test_date: x.test_date,
    })),
  ].sort((a, b) => b.uploaded_at.localeCompare(a.uploaded_at))
}
const DocStatus = ({ status, tag, tone }) => (tag
  ? <Badge tone={tone} className="text-sm!">{tag}</Badge>
  : <Badge tone={STATUS[status]?.tone ?? 'neutral'} className="text-sm!">{STATUS[status]?.label ?? status}</Badge>)

function HomeScreen({ shell, patient, go, onDone }) {
  const [doctors, setDoctors] = useState(null)
  const [docs, setDocs] = useState(null)
  useEffect(() => {
    api.myDoctors().then(setDoctors).catch(() => setDoctors([]))
    loadMyDocuments().then(setDocs).catch(() => setDocs([]))
  }, [])

  return (
    <AppShell {...shell} title={`Hello, ${patient.first_name}`} breadcrumbs={[{ label: 'Home' }]}
      subtitle={<>Patient ID <strong className="text-ink tnum">{patient.patient_code}</strong></>}
      actions={<button type="button" onClick={() => go('sugar')} className={b.primary}><Droplet size={18} aria-hidden="true" /> Log a sugar reading</button>}>
      <PendingConsents patient={patient} onDone={onDone} />
      <CareTeamMessages go={go} />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_24rem]">
        <Panel large title="What would you like to do?" bodyClass="">
          <ul className="divide-y divide-line">
            {ACTIONS.map(({ key, icon: Icon, title, text }) => (
              <li key={key}>
                <button type="button" onClick={() => go(key)} className="flex w-full items-center gap-4 px-4 py-3.5 text-left hover:bg-subtle">
                  <Icon size={22} aria-hidden="true" className="shrink-0 text-brand-700" />
                  <span className="flex-1">
                    <span className="block text-base font-semibold text-ink">{title}</span>
                    <span className="block text-sm text-muted">{text}</span>
                  </span>
                  <ChevronRight size={20} aria-hidden="true" className="shrink-0 text-muted" />
                </button>
              </li>
            ))}
          </ul>
        </Panel>

        <div className="space-y-4">
          <MyAppointments go={go} />

          <Panel large title="My doctors" description="Only these doctors can see your records" bodyClass=""
            actions={<button type="button" onClick={() => go('doctors')} className={`${link} text-sm`}>Manage</button>}>
            {doctors === null ? <p className="px-4 py-4 text-sm text-muted">Loading…</p>
              : doctors.length === 0 ? (
                <div className="px-4 py-4">
                  <p className="text-base text-muted">No doctors yet. Your records are not shared with anyone.</p>
                  <button type="button" onClick={() => go('doctors')} className={`${b.secondary} mt-3`}><Stethoscope size={18} aria-hidden="true" /> Add my doctor</button>
                </div>
              ) : (
                <ul className="divide-y divide-line">
                  {doctors.map((d) => (
                    <li key={d.clinician_code} className="px-4 py-2.5">
                      <span className="block text-base font-medium text-ink">{d.full_name}</span>
                      <span className="block text-sm text-muted tnum">ID {d.clinician_code}</span>
                    </li>
                  ))}
                </ul>
              )}
          </Panel>

          <MyBills go={go} />

          <Panel large title="Recently sent" description="Photos and reports you sent" bodyClass=""
            actions={docs?.length > 0 && <button type="button" onClick={() => go('documents')} className={`${link} text-sm`}>All documents</button>}>
            {docs === null ? <p className="px-4 py-4 text-sm text-muted">Loading…</p>
              : docs.length === 0 ? <p className="px-4 py-4 text-base text-muted">Nothing sent yet.</p> : (
                <ul className="divide-y divide-line">
                  {docs.slice(0, 3).map((d) => (
                    <li key={d.key} className="px-4 py-2.5">
                      <span className="block text-base font-medium text-ink">{docName(d)}</span>
                      <span className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted">
                        <span className="tnum">Sent {formatDate(d.uploaded_at)}</span> <DocStatus status={d.review_status} tag={d.tag} tone={d.tagTone} />
                      </span>
                    </li>
                  ))}
                </ul>
              )}
          </Panel>
        </div>
      </div>

      <button type="button" onClick={() => go('password')} className={`${link} mt-5 inline-flex items-center gap-1.5 text-base`}>
        <KeyRound size={16} aria-hidden="true" /> Change my password
      </button>
    </AppShell>
  )
}

// Messages from the care team (the same text is e-mailed). Shown until the patient taps OK.
function CareTeamMessages({ go }) {
  const [messages, setMessages] = useState([])
  useEffect(() => {
    api.notifications().then((n) => setMessages(n.filter((m) => !m.read_at))).catch(() => {})
  }, [])
  const dismiss = (m) => {
    setMessages((list) => list.filter((x) => x.id !== m.id))
    api.readNotification(m.id).catch(() => {})
  }
  if (!messages.length) return null
  return (
    <ul className="mb-4 space-y-2" aria-label="Messages from your care team">
      {messages.map((m) => {
        const ok = m.kind === 'bill_approved'
        const rebook = m.kind === 'appointment_reschedule'
        const test = m.kind === 'test_reminder' || m.kind === 'test_rejected'
        return (
          <li key={m.id} className={`rounded-lg border border-l-4 bg-surface px-4 py-3 ${ok ? 'border-line border-l-ok-700' : 'border-line border-l-alert-700'}`}>
            <p className={`flex items-start gap-2 text-base ${ok ? 'text-ink' : 'text-ink'}`}>
              {ok ? <CheckCircle2 size={20} aria-hidden="true" className="mt-0.5 shrink-0 text-ok-700" />
                : <AlertTriangle size={20} aria-hidden="true" className="mt-0.5 shrink-0 text-alert-700" />}
              <span>{m.message}</span>
            </p>
            <div className="mt-2 flex flex-wrap gap-2 pl-7">
              {test ? <button type="button" onClick={() => { dismiss(m); go('tests') }} className={b.primary}><ClipboardList size={18} aria-hidden="true" /> Open my tests</button>
                : rebook ? <button type="button" onClick={() => { dismiss(m); go('appointments') }} className={b.primary}><CalendarDays size={18} aria-hidden="true" /> Choose a new time</button>
                : !ok && <button type="button" onClick={() => { dismiss(m); go('refill') }} className={b.primary}><Upload size={18} aria-hidden="true" /> Upload a new bill</button>}
              <button type="button" onClick={() => dismiss(m)} className={b.secondary}>OK</button>
            </div>
          </li>
        )
      })}
    </ul>
  )
}

// Home: the patient's booked appointments, straight from the server - confirmed the moment they book.
function MyAppointments({ go }) {
  const [list, setList] = useState(null)
  useEffect(() => {
    api.myAppointments().then((d) => setList(d.appointments)).catch(() => setList([]))
  }, [])
  return (
    <Panel large title="My appointments" bodyClass=""
      actions={<button type="button" onClick={() => go('appointments')} className={`${link} text-sm`}>Book</button>}>
      {list === null ? <p className="px-4 py-4 text-sm text-muted">Loading…</p>
        : list.length === 0 ? <p className="px-4 py-4 text-base text-muted">No appointment booked.</p> : (
          <ul className="divide-y divide-line">{list.map((a) => <AppointmentRow key={a.id} a={a} />)}</ul>
        )}
    </Panel>
  )
}

function AppointmentRow({ a }) {
  const rebook = a.status === 'needs_reschedule'
  return (
    <li className="px-4 py-3">
      <span className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-base font-semibold text-ink tnum">{formatDate(a.date)} · {formatTime(a.start_time)}</span>
        <Badge tone={rebook ? 'warn' : 'ok'}>{rebook ? 'Needs a new time' : 'Confirmed'}</Badge>
      </span>
      <span className="block text-sm text-muted">{a.doctor_name}</span>
      {rebook && a.notice && <p className="mt-1.5 text-sm text-ink">{a.notice.message}</p>}
    </li>
  )
}

// Every bill the patient sent, with its status always visible.
function MyBills({ go }) {
  const [bills, setBills] = useState(null)
  useEffect(() => {
    api.myBills().then(setBills).catch(() => setBills([]))
  }, [])
  const latest = bills?.[0]
  return (
    <Panel large title="My medicine bills" description="Checked by your care team" bodyClass=""
      actions={<button type="button" onClick={() => go('refill')} className={`${link} text-sm`}>Upload a bill</button>}>
      {bills === null ? <p className="px-4 py-4 text-sm text-muted">Loading…</p>
        : bills.length === 0 ? <p className="px-4 py-4 text-base text-muted">No bills sent yet.</p> : (
          <ul className="divide-y divide-line">
            {bills.slice(0, 4).map((x) => (
              <li key={x.id} className="px-4 py-2.5">
                <span className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-base font-medium text-ink tnum">Bill · {formatDate(x.upload_date)}</span>
                  <Badge tone={BILL_TONE[x.status]} className="text-sm!">{x.status === 'reviewed_rejected' ? 'Rejected' : x.status_text}</Badge>
                </span>
                {x.status === 'reviewed_rejected' && <span className="mt-0.5 block text-sm text-alert-800">{x.reject_reason}</span>}
              </li>
            ))}
          </ul>
        )}
      {latest?.status === 'reviewed_rejected' && (
        <div className="border-t border-line px-4 py-3">
          <button type="button" onClick={() => go('refill')} className={`${b.primary} w-full`}><Upload size={18} aria-hidden="true" /> Upload a new bill</button>
        </div>
      )}
    </Panel>
  )
}

function PendingConsents({ patient, onDone }) {
  const [pending, setPending] = useState([])
  useEffect(() => {
    const load = () => api.pendingConsents().then(setPending).catch(() => {})
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [patient.patient_code])
  if (pending.length === 0) return null
  return (
    <section className="mb-4 rounded-lg border border-line border-l-4 border-l-brand-600 bg-surface">
      <header className="border-b border-line px-4 py-3">
        <h2 className="flex items-center gap-2 text-base font-semibold text-brand-800">
          <ShieldCheck size={20} aria-hidden="true" /> Waiting for your answer ({pending.length})
        </h2>
        <p className="text-sm text-muted">Nothing is shared unless you approve.</p>
      </header>
      <ul className="divide-y divide-line" aria-label="Waiting for your answer">
        {pending.map((r) => <ConsentItem key={r.id} request={r} onDone={onDone} />)}
      </ul>
    </section>
  )
}

function ConsentItem({ request: r, onDone }) {
  const { busy, error, run } = useSubmit()
  const answer = (approve) => run(async () => onDone((await api.answerConsent(r.id, approve)).message))
  return (
    <li className="px-4 py-4">
      <p className="text-base font-semibold text-ink">Request to see your records</p>
      <dl className="mt-2 grid gap-x-6 gap-y-2 sm:grid-cols-3">
        {[['Who is asking', r.requester], ['What they want to see', `${r.hi_type_labels.join(', ')} from ${r.hip_name}`], ['Until', formatDate(r.access_until)]].map(([k, v]) => (
          <div key={k}>
            <dt className="text-sm text-muted">{k}</dt>
            <dd className="text-base font-medium text-ink">{v}</dd>
          </div>
        ))}
      </dl>
      <div className="mt-3"><Alert>{error}</Alert></div>
      <div className="mt-3 grid grid-cols-2 gap-2 sm:flex">
        <button type="button" disabled={busy} onClick={() => answer(true)} className={b.primary}>Approve</button>
        <button type="button" disabled={busy} onClick={() => answer(false)} className={b.secondary}>Deny</button>
      </div>
    </li>
  )
}

/* ============================================================== actions */

// Medicine refill = upload the purchase bill. The bill is compulsory (the server refuses a submission without one)
// and the patient never types a date: the server records the upload time.
function RefillScreen({ shell, crumbs, onBack, onDone }) {
  const [file, setFile] = useState(null)
  const [url, setUrl] = useState(null)
  const { busy, error, run } = useSubmit()
  const notYet = useSubmit()

  useEffect(() => () => url && URL.revokeObjectURL(url), [url])
  const choose = (e) => {
    const f = e.target.files?.[0] ?? null
    e.target.value = ''
    if (!f) return
    setFile(f)
    setUrl(f.type.startsWith('image/') ? URL.createObjectURL(f) : null)
  }
  const clear = () => { setFile(null); setUrl(null) }
  const submit = (e) => {
    e.preventDefault()
    if (!file) return
    run(async () => onDone((await api.uploadBill(file)).message))
  }

  return (
    <AppShell {...shell} title="Medicine refill" subtitle="When you buy your diabetes medicine, send a photo of the bill."
      breadcrumbs={crumbs({ label: 'Medicine refill' })}>
      <form onSubmit={submit} className="max-w-xl rounded-lg border border-line bg-surface">
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
          <h2 className="text-base font-semibold text-ink">Upload your medicine bill</h2>
          <span className="rounded border border-line bg-subtle px-2 py-0.5 text-sm font-medium text-ink" title="Disease">Diabetes</span>
        </header>
        <div className="space-y-4 px-4 py-4">
          {!file ? (
            <div className="grid gap-2 sm:grid-cols-2">
              <label className={`${b.primary} cursor-pointer`}>
                <Camera size={20} aria-hidden="true" /> Take photo
                <input type="file" accept="image/*" capture="environment" className="sr-only" aria-label="Take a photo of the bill" onChange={choose} />
              </label>
              <label className={`${b.secondary} cursor-pointer`}>
                <Upload size={20} aria-hidden="true" /> Choose file
                <input type="file" accept="image/*,application/pdf" className="sr-only" aria-label="Choose a photo or PDF of the bill" onChange={choose} />
              </label>
            </div>
          ) : (
            <div className="flex items-center gap-3 rounded-md border border-line bg-subtle p-3">
              {url ? <img src={url} alt="Your bill" className="h-20 w-16 shrink-0 rounded border border-line object-cover object-top" />
                : <span className="flex h-20 w-16 shrink-0 items-center justify-center rounded border border-line bg-surface text-sm font-semibold text-muted">PDF</span>}
              <span className="min-w-0 flex-1">
                <span className="block break-all text-base font-medium text-ink">{file.name}</span>
                <button type="button" onClick={clear} className={`${link} text-base`}>Choose a different bill</button>
              </span>
            </div>
          )}
          <Alert>{error}</Alert>
          <div>
            <button type="submit" disabled={!file || busy} className={`${b.primary} w-full disabled:bg-line-strong! disabled:text-muted!`}>
              {busy ? 'Submitting…' : 'Submit'}
            </button>
            {!file && <p className="mt-1.5 text-center text-sm text-muted">Please attach your bill to continue</p>}
          </div>
        </div>
      </form>
      <button type="button" disabled={notYet.busy} className={`${link} mt-4 text-base`}
        onClick={() => notYet.run(async () => onDone((await api.refill(false)).message))}>
        I haven’t collected my medicine yet
      </button>
      <Alert>{notYet.error}</Alert>
      <button type="button" onClick={onBack} className={`${link} mt-3 block text-base`}>Cancel and go home</button>
    </AppShell>
  )
}

function SugarScreen({ shell, crumbs, onBack, onDone }) {
  const [value, setValue] = useState('')
  const [photo, setPhoto] = useState(null)
  const [invalid, setInvalid] = useState(false)
  const { busy, error, run } = useSubmit()
  const num = Number(value)

  const submit = (e) => {
    e.preventDefault()
    if (!value || Number.isNaN(num) || num < 20 || num > 600) {
      setInvalid(true)
      return
    }
    run(async () => {
      const res = await api.sugar(num, photo)
      const warning =
        num < 70 || num > 300 ? ' This reading is outside the usual range. If you feel unwell, call your doctor.' : ''
      onDone(res.message + warning)
    })
  }

  return (
    <AppShell {...shell} title="Today's sugar reading" subtitle="Type the number shown on your glucose meter."
      breadcrumbs={crumbs({ label: 'Sugar reading' })}>
      <FormPanel onSubmit={submit} title="Your reading"
        footer={<>
          <button type="button" onClick={onBack} className={b.secondary}>Cancel</button>
          <button type="submit" disabled={busy} className={b.primary}>{busy ? 'Saving…' : 'Save reading'}</button>
        </>}>
        <PField label="Number on your meter" htmlFor="sugar" required
          error={invalid && 'Please enter the number shown on your meter (between 20 and 600).'}>
          <div className="relative sm:max-w-60">
            <input id="sugar" inputMode="decimal" value={value} onChange={(e) => { setValue(e.target.value.replace(/[^\d.]/g, '')); setInvalid(false) }}
              placeholder="e.g. 126" aria-invalid={invalid || undefined} className={`${field} pr-20! text-2xl! font-semibold tnum`} />
            <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-base text-muted">mg/dL</span>
          </div>
        </PField>

        <div>
          <p className="text-base font-semibold text-ink">Photo of your meter <span className="font-normal text-muted">(optional)</span></p>
          <p className="text-sm text-muted">Helps your care team check the number.</p>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <label className={`${b.secondary} cursor-pointer`}>
              <Camera size={18} aria-hidden="true" /> {photo ? 'Change photo of your meter' : 'Add a photo of your meter'}
              <input type="file" accept="image/*" className="sr-only" aria-label="Photo of your meter (optional)"
                onChange={(e) => setPhoto(e.target.files?.[0] ?? null)} />
            </label>
            {photo && (
              <span className="flex min-w-0 items-center gap-2 text-base text-ink">
                <span className="truncate">{photo.name}</span>
                <button type="button" onClick={() => setPhoto(null)} className={`${link} shrink-0`}>Remove photo</button>
              </span>
            )}
          </div>
        </div>
        <Alert>{error}</Alert>
      </FormPanel>
    </AppShell>
  )
}

const KINDS = [
  { key: 'prescription', label: 'Prescription', text: 'A prescription from any doctor' },
  { key: 'lab_report', label: 'Lab report', text: 'A full lab report with several results' },
  { key: 'external', label: 'Test report from another lab', text: 'One HbA1c or sugar test done outside the hospital' },
]

function PhotoScreen({ shell, crumbs, go, onBack, onDone }) {
  const [kind, setKind] = useState(null)
  const [file, setFile] = useState(null)
  const [reportType, setReportType] = useState('')
  const { busy, error, setError, run } = useSubmit()
  const isLab = kind === 'lab_report'
  const typed = reportType.trim()
  const reset = () => { setKind(null); setFile(null); setReportType(''); setError('') }

  const submit = (e) => {
    e.preventDefault()
    if (isLab && typed.length < 2) {
      setError('Please write what type of report this is.')
      return
    }
    if (!file) {
      setError('Please take a photo or choose a file first.')
      return
    }
    run(async () => onDone((await api.upload(kind, file, isLab ? typed : null)).message))
  }

  if (!kind) {
    return (
      <AppShell {...shell} title="Send a document" subtitle="Send a photo or PDF to your care team."
        breadcrumbs={crumbs({ label: 'Send a document' })}>
        <Panel large title="What are you sending?" bodyClass="" className="max-w-xl">
          <ul className="divide-y divide-line">
            {KINDS.map((k) => (
              <li key={k.key}>
                <button type="button" onClick={() => (k.key === 'external' ? go('external') : setKind(k.key))}
                  className="flex w-full items-center gap-4 px-4 py-3.5 text-left hover:bg-subtle">
                  {k.key === 'external' ? <FlaskConical size={22} aria-hidden="true" className="shrink-0 text-brand-700" />
                    : <FileText size={22} aria-hidden="true" className="shrink-0 text-brand-700" />}
                  <span className="flex-1">
                    <span className="block text-base font-semibold text-ink">{k.label}</span>
                    <span className="block text-sm text-muted">{k.text}</span>
                  </span>
                  <ChevronRight size={20} aria-hidden="true" className="shrink-0 text-muted" />
                </button>
              </li>
            ))}
          </ul>
        </Panel>
        <button type="button" onClick={onBack} className={`${link} mt-4 text-base`}>Cancel and go home</button>
      </AppShell>
    )
  }

  const label = isLab ? 'Lab report' : 'Prescription'
  return (
    <AppShell {...shell} title={`Send your ${isLab ? 'lab report' : 'prescription'}`} subtitle="Your care team checks it before using it."
      breadcrumbs={crumbs({ label: 'Send a document', onClick: reset }, { label })}>
      <FormPanel onSubmit={submit} title={label}
        footer={<>
          <button type="button" onClick={reset} className={b.secondary}>Back</button>
          <button type="submit" disabled={busy || !file || (isLab && typed.length < 2)} className={b.primary}>
            <Upload size={18} aria-hidden="true" /> {busy ? 'Sending…' : 'Send to my care team'}
          </button>
        </>}>
        {isLab && (
          <PField label="What type of report is this?" htmlFor="report-type" required hintId="report-type-hint"
            hint="For example: HbA1c, sugar test, kidney test, cholesterol">
            <input id="report-type" value={reportType} maxLength={100} required aria-describedby="report-type-hint"
              onChange={(e) => { setReportType(e.target.value); setError('') }}
              placeholder="Type the report name" className={field} />
          </PField>
        )}
        <div>
          <p className="text-base font-semibold text-ink">
            Photo or PDF<span className="ml-0.5 text-alert-700" aria-hidden="true">*</span>
          </p>
          <label className="mt-1.5 flex min-h-28 cursor-pointer flex-col items-center justify-center gap-2 rounded-md border-2 border-dashed border-line-strong bg-subtle p-4 text-center hover:border-brand-600">
            <Camera size={28} className="text-brand-700" aria-hidden="true" />
            <span className="text-base font-semibold text-brand-800">{file ? 'Choose a different file' : 'Take a photo or choose a PDF'}</span>
            <input type="file" accept="image/*,application/pdf" className="sr-only" aria-label="Photo or PDF of the document"
              onChange={(e) => { setFile(e.target.files?.[0] ?? null); setError('') }} />
          </label>
          {file && <p className="mt-2 break-all text-base text-ink">Selected: <strong>{file.name}</strong></p>}
        </div>
        <Alert>{error}</Alert>
      </FormPanel>
    </AppShell>
  )
}

/* ============================================================== External Report Review: upload */

const TEST_TYPES = [
  ['HbA1c', 'HbA1c (3-month sugar)'],
  ['Fasting Sugar', 'Fasting Sugar (before breakfast)'],
  ['PP Sugar', 'PP Sugar (after a meal)'],
  ['Other', 'Other test'],
]

// The patient sends the report only - type, date and file. The care team reads it and enters the value.
function ExternalReportScreen({ shell, crumbs, onBack, onDone }) {
  const [testType, setTestType] = useState('')
  const [testName, setTestName] = useState('')
  const [testDate, setTestDate] = useState('')
  const [file, setFile] = useState(null)
  const [errors, setErrors] = useState({})
  const { busy, error, run } = useSubmit()
  const other = testType === 'Other'

  const submit = (e) => {
    e.preventDefault()
    const problems = {
      testType: !testType && 'Please choose the type of test.',
      testName: other && testName.trim().length < 2 && 'Please write which test this is.',
      testDate: !testDate ? 'Please choose the date of the test.' : testDate > todayIso() && 'The test date cannot be in the future.',
      file: !file && 'Please take a photo of the report or choose a PDF.',
    }
    setErrors(problems)
    if (Object.values(problems).some(Boolean)) return
    run(async () => onDone((await api.uploadExternalReport({ testType, testName: other ? testName.trim() : null, testDate, file })).message))
  }
  const clear = (k) => setErrors((x) => ({ ...x, [k]: null }))

  return (
    <AppShell {...shell} title="Send a test report" subtitle="For a test you had done at another lab or clinic. You don’t need to type the result."
      breadcrumbs={crumbs({ label: 'Send a test report' })}>
      <FormPanel onSubmit={submit} title="About the test" description="Your care team reads the report and enters the result."
        footer={<>
          <button type="button" onClick={onBack} className={b.secondary}>Cancel</button>
          <button type="submit" disabled={busy} className={b.primary}><Upload size={18} aria-hidden="true" /> {busy ? 'Uploading…' : 'Upload report'}</button>
        </>}>
        <PField label="Test type" htmlFor="x-type" required error={errors.testType}>
          <select id="x-type" value={testType} onChange={(e) => { setTestType(e.target.value); clear('testType') }} className={`${field} sm:max-w-80`}>
            <option value="">Choose…</option>
            {TEST_TYPES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
        </PField>
        {other && (
          <PField label="Which test?" htmlFor="x-name" required error={errors.testName} hint="As written on the report, e.g. Vitamin D, Creatinine">
            <input id="x-name" value={testName} maxLength={80} onChange={(e) => { setTestName(e.target.value); clear('testName') }} className={field} />
          </PField>
        )}
        <PField label="Date of the test" htmlFor="x-date" required error={errors.testDate} hint="The day the test was done — usually printed on the report.">
          <input id="x-date" type="date" value={testDate} max={todayIso()} onChange={(e) => { setTestDate(e.target.value); clear('testDate') }}
            className={`${field} sm:max-w-60`} />
        </PField>
        <div>
          <p className="text-base font-semibold text-ink">
            Photo or PDF of the report<span className="ml-0.5 text-alert-700" aria-hidden="true">*</span>
          </p>
          <label className="mt-1.5 flex min-h-28 cursor-pointer flex-col items-center justify-center gap-2 rounded-md border-2 border-dashed border-line-strong bg-subtle p-4 text-center hover:border-brand-600">
            <Camera size={28} className="text-brand-700" aria-hidden="true" />
            <span className="text-base font-semibold text-brand-800">{file ? 'Choose a different file' : 'Take a photo or choose a PDF'}</span>
            <input type="file" accept="image/*,application/pdf" className="sr-only" aria-label="Photo or PDF of the test report"
              onChange={(e) => { setFile(e.target.files?.[0] ?? null); clear('file') }} />
          </label>
          {file && <p className="mt-2 break-all text-base text-ink">Selected: <strong>{file.name}</strong></p>}
          {errors.file && (
            <p className="mt-1.5 flex items-start gap-1.5 text-sm font-medium text-alert-800">
              <AlertTriangle size={15} aria-hidden="true" className="mt-0.5 shrink-0" /> {errors.file}
            </p>
          )}
        </div>
        <Alert>{error}</Alert>
      </FormPanel>
    </AppShell>
  )
}

/* ============================================================== my records */

function DocumentsScreen({ shell, crumbs, go }) {
  const [docs, setDocs] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
    loadMyDocuments().then(setDocs).catch((e) => setError(e.message))
  }, [])
  return (
    <AppShell {...shell} title="My documents" subtitle="Everything you have sent to your care team."
      breadcrumbs={crumbs({ label: 'My documents' })}
      actions={<button type="button" onClick={() => go('photo')} className={b.primary}><Upload size={18} aria-hidden="true" /> Send a document</button>}>
      <Alert>{error}</Alert>
      <Panel large title={`Sent documents${docs ? ` (${docs.length})` : ''}`} bodyClass="">
        {!docs && !error && <p className="px-4 py-6 text-base text-muted">Loading…</p>}
        {docs && docs.length === 0 && <p className="px-4 py-6 text-base text-muted">You have not sent any documents yet.</p>}
        {docs && docs.length > 0 && (
          <>
            <div className="hidden overflow-x-auto md:block">
              <table className="w-full">
                <thead className="border-b border-line bg-subtle"><tr>
                  <th className={th}>Document</th><th className={th}>Sent</th><th className={th}>Status</th><th className={th}><span className="sr-only">Open</span></th>
                </tr></thead>
                <tbody className="divide-y divide-line">
                  {docs.map((d) => (
                    <tr key={d.key} className="hover:bg-subtle">
                      <td className={`${td} text-base!`}>{docName(d)}</td>
                      <td className={`${td} text-base! whitespace-nowrap text-muted tnum`}>{formatDate(d.uploaded_at)}</td>
                      <td className={td}><DocStatus status={d.review_status} tag={d.tag} tone={d.tagTone} /></td>
                      <td className={`${td} text-right`}>
                        <a href={d.url} target="_blank" rel="noreferrer" className={`${link} text-base`}>Open</a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <ul className="divide-y divide-line md:hidden">
              {docs.map((d) => (
                <li key={d.key}>
                  <a href={d.url} target="_blank" rel="noreferrer" className="flex items-center gap-3 px-4 py-3 hover:bg-subtle">
                    <span className="min-w-0 flex-1">
                      <span className="block text-base font-semibold text-ink">{docName(d)}</span>
                      <span className="block text-sm text-muted tnum">Sent {formatDate(d.uploaded_at)}</span>
                      <span className="mt-1 block"><DocStatus status={d.review_status} tag={d.tag} tone={d.tagTone} /></span>
                    </span>
                    <ChevronRight size={20} aria-hidden="true" className="shrink-0 text-muted" />
                  </a>
                </li>
              ))}
            </ul>
          </>
        )}
      </Panel>
    </AppShell>
  )
}

function DoctorsScreen({ shell, crumbs }) {
  const [doctors, setDoctors] = useState(null)
  const [code, setCode] = useState('')
  const [removing, setRemoving] = useState(null)
  const [notice, setNotice] = useState('')
  const { busy, error, setError, run } = useSubmit()

  useEffect(() => {
    api.myDoctors().then(setDoctors).catch((e) => setError(e.message))
  }, [setError])

  const add = (e) => {
    e.preventDefault()
    setNotice('')
    run(async () => {
      const list = await api.addMyDoctor(code)
      setDoctors(list)
      setNotice(`${list.find((d) => d.clinician_code === code.trim().toUpperCase())?.full_name ?? 'Your doctor'} can now see your records.`)
      setCode('')
    })
  }
  const remove = (d) =>
    run(async () => {
      setDoctors(await api.removeMyDoctor(d.clinician_code))
      setRemoving(null)
      setNotice(`${d.full_name} can no longer see your records.`)
    })

  return (
    <AppShell {...shell} title="My doctors" subtitle="Only the doctors on this list can see your records."
      breadcrumbs={crumbs({ label: 'My doctors' })}>
      {notice && <div className="mb-4 max-w-xl"><Success>{notice}</Success></div>}
      <div className="grid max-w-5xl gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:items-start">
        <Panel large title={`Doctors who can see your records${doctors ? ` (${doctors.length})` : ''}`} bodyClass="">
          {doctors && doctors.length === 0 && (
            <p className="px-4 py-5 text-base text-muted">No doctors yet. Ask your clinic for your doctor’s ID and add it here.</p>
          )}
          <ul className="divide-y divide-line">
            {doctors?.map((d) => (
              <li key={d.clinician_code} className="px-4 py-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span>
                    <span className="block text-base font-semibold text-ink">{d.full_name}</span>
                    <span className="block text-sm text-muted tnum">ID {d.clinician_code}{d.granted_at ? ` · added ${formatDate(d.granted_at)}` : ''}</span>
                  </span>
                  {removing !== d.clinician_code && (
                    <button type="button" onClick={() => setRemoving(d.clinician_code)} className={`${btn.danger} h-10! px-3! text-base!`}>Remove</button>
                  )}
                </div>
                {removing === d.clinician_code && (
                  <div className="mt-3 rounded-md border border-alert-200 bg-alert-50 p-3">
                    <p className="text-base text-ink">Remove {d.full_name}? They will no longer see your records.</p>
                    <div className="mt-3 grid grid-cols-2 gap-2 sm:flex">
                      <button type="button" disabled={busy} onClick={() => remove(d)} className={b.danger}>Yes, remove</button>
                      <button type="button" onClick={() => setRemoving(null)} className={b.secondary}>Keep</button>
                    </div>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </Panel>

        <FormPanel onSubmit={add} title="Add a doctor" description="Your doctor can then see your readings and reports."
          footer={<button type="submit" disabled={busy} className={b.primary}>{busy ? 'Adding…' : 'Add doctor'}</button>}>
          <PField label="Doctor’s ID" htmlFor="doc-code" required hint="It looks like CLN-7K3Q9P. Ask your clinic for it.">
            <input id="doc-code" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} required maxLength={20}
              placeholder="CLN-" className={`${field} uppercase tracking-wider`} />
          </PField>
          <Alert>{error}</Alert>
        </FormPanel>
      </div>
    </AppShell>
  )
}

/* ============================================================== my tests */

// Tests the doctor asked for, soonest first. A test done elsewhere is uploaded from its own card; the care team
// checks the report before the result counts. Results done at our clinic lab appear on their own.
function TestsScreen({ shell, crumbs }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [uploadFor, setUploadFor] = useState(null)
  const [sent, setSent] = useState('')
  const load = () => api.myTests().then(setData).catch((e) => setError(e.message))
  useEffect(() => {
    load()
  }, [])
  const longDay = (iso) => new Date(`${iso}T00:00:00`).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
  const uploadable = data?.todo.filter((t) => t.can_upload) ?? []

  return (
    <AppShell {...shell} title="My tests" breadcrumbs={crumbs({ label: 'My tests' })}
      subtitle={data?.next_appointment ? <>Next appointment <strong className="text-ink tnum">{longDay(data.next_appointment.date)}</strong></> : null}>
      <div className="max-w-3xl space-y-4">
        <Alert>{error}</Alert>
        {sent && <Success>{sent}</Success>}
        {data && data.todo.length === 0 && (
          <Panel large title="Nothing to do"><p className="px-4 py-4 text-base text-muted">Your doctor has not asked for any tests right now.</p></Panel>
        )}
        {data?.todo.map((t) => (
          <article key={t.id} className={`rounded-lg border bg-surface ${t.overdue ? 'border-alert-200' : 'border-line'}`} aria-labelledby={`t-${t.id}`}>
            <div className="space-y-2 px-4 py-3.5">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <h2 id={`t-${t.id}`} className="text-lg font-semibold text-ink">{t.name}</h2>
                <Badge tone={t.section === 'rejected' ? 'alert' : t.section === 'awaiting_verification' ? 'info' : 'neutral'} className="text-sm!">{t.patient_status_label}</Badge>
              </div>
              <p className="text-base text-ink tnum">
                Do this by <strong>{longDay(t.due_by)}</strong>
                {t.overdue && <strong className="ml-2 text-alert-800">Overdue</strong>}
                {t.priority === 'urgent' && <strong className="ml-2 text-ink">Urgent</strong>}
              </p>
              {t.fasting_required && <p className="text-base font-semibold text-ink">Fasting needed.</p>}
              {t.instructions && <p className="text-base text-ink">{t.instructions}</p>}
              {t.fulfilment_route !== 'external' && t.section !== 'awaiting_verification' && (
                <p className="text-base text-muted">Get this done at our clinic by {longDay(t.due_by)}. Your results will appear automatically.</p>
              )}
              {t.status === 'submitted_by_patient' && <p className="text-base text-ink">Sent for clinician review{t.report_sent_at ? ` on ${formatDate(t.report_sent_at)}` : ''}.</p>}
              {t.status === 'rejected' && t.status_reason && (
                <p role="status" className="rounded-md border border-alert-200 bg-alert-50 px-3 py-2 text-base text-alert-800">
                  Not accepted: {t.status_reason}
                </p>
              )}
              {t.can_upload && uploadFor !== t.id && (
                <button type="button" onClick={() => { setSent(''); setUploadFor(t.id) }} className={t.status === 'rejected' ? b.primary : b.secondary}>
                  <Upload size={18} aria-hidden="true" /> {t.status === 'rejected' ? 'Re-upload' : 'I did this test elsewhere'}
                </button>
              )}
            </div>
            {uploadFor === t.id && (
              <TestUpload item={t} options={uploadable} onCancel={() => setUploadFor(null)}
                onSent={(name) => { setUploadFor(null); setSent(`${name}: sent for clinician review.`); load() }} />
            )}
          </article>
        ))}
        <button type="button" onClick={() => { setSent(''); setUploadFor('other-top') }} className={`${link} text-base`}>Send a report for a test that is not on this list</button>
        {uploadFor === 'other-top' && (
          <div className="rounded-lg border border-line bg-surface">
            <TestUpload item={null} options={uploadable} onCancel={() => setUploadFor(null)}
              onSent={(name) => { setUploadFor(null); setSent(`${name}: sent for clinician review.`); load() }} />
          </div>
        )}

        {data?.done.length > 0 && (
          <Panel large title="Done" bodyClass="">
            <ul className="divide-y divide-line">
              {data.done.map((t) => (
                <li key={t.id} className="px-4 py-3">
                  <span className="block text-base font-semibold text-ink">{t.name}</span>
                  {t.result && <span className="block text-base text-ink tnum">{t.result.value} {t.result.unit} · test on {formatDate(t.result.test_date)}</span>}
                  <span className="block text-sm text-muted">{t.result?.source === 'clinic_lab_system' ? 'From our clinic lab' : 'Checked by your care team'}</span>
                </li>
              ))}
            </ul>
          </Panel>
        )}
      </div>
    </AppShell>
  )
}

// "I did this test elsewhere": which test (pre-selected, changeable), test date, lab, file - with upload progress.
function TestUpload({ item, options, onCancel, onSent }) {
  const [target, setTarget] = useState(item?.id ?? 'other')
  const [f, setF] = useState({ name: '', date: '', lab: '' })
  const [file, setFile] = useState(null)
  const [progress, setProgress] = useState(null)
  const { busy, error, run } = useSubmit()
  const [key] = useState(() => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`))
  const choices = item && !options.some((o) => o.id === item.id) ? [item, ...options] : options
  const submit = (e) => {
    e.preventDefault()
    run(async () => {
      setProgress(0)
      try {
        await api.uploadTestReport(target, { file, test_date: f.date, lab_name: f.lab, custom_name: target === 'other' ? f.name : null }, setProgress, key)
        onSent(target === 'other' ? f.name : choices.find((o) => o.id === target)?.name)
      } finally {
        setProgress(null)
      }
    })
  }
  const id = item?.id ?? 'other'
  return (
    <form onSubmit={submit} className="space-y-4 border-t border-line bg-subtle px-4 py-4">
      <PField label="Which test is this report for?" htmlFor={`up-test-${id}`}>
        <select id={`up-test-${id}`} value={target} onChange={(e) => setTarget(e.target.value)} className={field}>
          {choices.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          <option value="other">Other test (not on my list)</option>
        </select>
      </PField>
      {target === 'other' && (
        <PField label="Name of the test" htmlFor={`up-name-${id}`} required>
          <input id={`up-name-${id}`} required maxLength={80} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} className={field} />
        </PField>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <PField label="Date of the test" htmlFor={`up-date-${id}`} required hint="The day the sample was taken">
          <input id={`up-date-${id}`} type="date" required max={todayIso()} value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} className={field} />
        </PField>
        <PField label="Lab name" htmlFor={`up-lab-${id}`} required>
          <input id={`up-lab-${id}`} required maxLength={120} value={f.lab} onChange={(e) => setF({ ...f, lab: e.target.value })} className={field} />
        </PField>
      </div>
      <PField label="Report (PDF, JPG or PNG)" htmlFor={`up-file-${id}`} required>
        <input id={`up-file-${id}`} type="file" required accept="application/pdf,image/jpeg,image/png" onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="block w-full text-base text-ink file:mr-3 file:h-11 file:rounded-md file:border file:border-line-strong file:bg-surface file:px-4 file:text-base file:font-semibold" />
      </PField>
      {progress !== null && (
        <div role="progressbar" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100} aria-label="Upload progress">
          <div className="h-2 overflow-hidden rounded bg-line"><div className="h-full bg-brand-600" style={{ width: `${progress}%` }} /></div>
          <p className="mt-1 text-sm text-muted tnum">Uploading… {progress}%</p>
        </div>
      )}
      <Alert>{error}</Alert>
      <div className="flex flex-col-reverse gap-2 sm:flex-row">
        <button type="button" onClick={onCancel} className={b.secondary}>Cancel</button>
        <button type="submit" disabled={busy || !file} className={b.primary}>{busy ? 'Sending…' : 'Submit'}</button>
      </div>
    </form>
  )
}

/* ============================================================== appointments */

// Pick a doctor and a day, see only the free times, book one. Booking is confirmed at once. If someone else
// took the time a moment earlier, the server says so (code "slot_taken") and the list is fetched again.
function AppointmentsScreen({ shell, crumbs }) {
  const [data, setData] = useState(null)
  const [doctorId, setDoctorId] = useState('')
  const [day, setDay] = useState('')
  const [slots, setSlots] = useState(null)
  const [picked, setPicked] = useState(null)
  const [taken, setTaken] = useState('')
  const [booked, setBooked] = useState(null)
  const [refresh, setRefresh] = useState(0)
  const { busy, error, setError, run } = useSubmit()

  const loadMine = () => api.myAppointments().then((d) => {
    setData(d)
    setDoctorId((id) => id || d.doctors[0]?.id || '')
    setDay((x) => x || d.today)
  })
  useEffect(() => {
    loadMine().catch((e) => setError(e.message))
  }, [setError])
  useEffect(() => {
    if (!doctorId || !day) return
    let live = true
    api.bookableSlots(doctorId, day).then((s) => live && setSlots(s)).catch((e) => { if (live) { setSlots([]); setError(e.message) } })
    return () => { live = false }
  }, [doctorId, day, refresh, setError])

  const doctor = data?.doctors.find((d) => d.id === doctorId)
  const choose = (patch) => { setSlots(null); setPicked(null); setTaken(''); setBooked(null); setError(''); patch() }
  const confirm = () => run(async () => {
    try {
      const a = await api.bookSlot(picked.id)
      setBooked(a)
      setTaken('')
      await loadMine()
    } catch (e) {
      if (e.status !== 409) throw e
      setTaken(e.message)              // e.g. "This slot was just booked by someone else. Please choose another time."
    } finally {
      setPicked(null)
      setSlots(null)
      setRefresh((n) => n + 1)         // never offer the stale time again
    }
  })

  return (
    <AppShell {...shell} title="Appointments" breadcrumbs={crumbs({ label: 'Appointments' })}>
      <div className="grid max-w-5xl gap-4 lg:grid-cols-[minmax(0,1fr)_22rem] lg:items-start">
        <Panel large title="Book an appointment" bodyClass="">
          {data && data.doctors.length === 0 ? (
            <p className="px-4 py-5 text-base text-muted">None of your doctors takes appointments here yet.</p>
          ) : (
            <div className="space-y-4 px-4 py-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <PField label="Doctor" htmlFor="appt-doctor">
                  <select id="appt-doctor" value={doctorId} onChange={(e) => choose(() => setDoctorId(e.target.value))} className={field}>
                    {data?.doctors.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                  </select>
                </PField>
                <PField label="Date" htmlFor="appt-day">
                  <input id="appt-day" type="date" value={day} min={data?.today} max={data?.last_day}
                    onChange={(e) => e.target.value && choose(() => setDay(e.target.value))} className={field} />
                </PField>
              </div>

              {booked && <Success>Booked: {formatDate(booked.date)}, {formatTime(booked.start_time)} with {booked.doctor_name}.</Success>}
              <Alert>{taken || error}</Alert>

              <div>
                <h3 className="text-base font-semibold text-ink">Free times{doctor ? ` · ${doctor.slot_duration_minutes} minutes each` : ''}</h3>
                {slots === null ? <p className="mt-2 text-sm text-muted">Loading…</p>
                  : slots.length === 0 ? <p className="mt-2 text-base text-muted">No free times on this day. Please choose another date.</p> : (
                    <ul className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3" aria-label="Free times">
                      {slots.map((s) => (
                        <li key={s.id} className={`flex items-center justify-between gap-2 rounded-md border px-3 py-2 ${picked?.id === s.id ? 'border-brand-600 bg-brand-50' : 'border-line'}`}>
                          <span className="text-base font-medium text-ink tnum">{formatTime(s.start_time)} – {formatTime(s.end_time)}</span>
                          <button type="button" onClick={() => { setPicked(s); setBooked(null); setTaken('') }}
                            aria-label={`Book ${formatTime(s.start_time)}`} className={`${btn.secondary} h-10! px-3! text-base!`}>Book</button>
                        </li>
                      ))}
                    </ul>
                  )}
              </div>

              {picked && (
                <div role="dialog" aria-label="Confirm booking" className="rounded-md border border-brand-600 bg-brand-50 p-3">
                  <p className="text-base text-ink">
                    Book <strong className="tnum">{formatTime(picked.start_time)} – {formatTime(picked.end_time)}</strong> on{' '}
                    <strong>{formatDate(picked.date)}</strong> with <strong>{doctor?.name}</strong>?
                  </p>
                  <div className="mt-3 grid grid-cols-2 gap-2 sm:flex">
                    <button type="button" disabled={busy} onClick={confirm} className={b.primary}>{busy ? 'Booking…' : 'Confirm booking'}</button>
                    <button type="button" onClick={() => setPicked(null)} className={b.secondary}>Cancel</button>
                  </div>
                </div>
              )}
            </div>
          )}
        </Panel>

        <Panel large title="My appointments" bodyClass="">
          {!data ? <p className="px-4 py-4 text-sm text-muted">Loading…</p>
            : data.appointments.length === 0 ? <p className="px-4 py-4 text-base text-muted">No appointment booked.</p> : (
              <ul className="divide-y divide-line">{data.appointments.map((a) => <AppointmentRow key={a.id} a={a} />)}</ul>
            )}
        </Panel>
      </div>
    </AppShell>
  )
}

/* ============================================================== confirmations */

function AccountCreated({ shell, patient, go }) {
  return (
    <AppShell {...shell} title={`Welcome, ${patient.first_name}`} subtitle="Your account is ready." breadcrumbs={[{ label: 'Home' }]}>
      <section className="max-w-xl rounded-lg border border-line bg-surface px-4 py-6 text-center">
        <CheckCircle2 size={36} className="mx-auto text-ok-700" aria-hidden="true" />
        <p className="mt-4 text-sm font-medium uppercase tracking-wide text-muted">Your Patient ID</p>
        <p className="mt-1 text-4xl font-semibold tracking-wide text-ink tnum" data-testid="patient-code">{patient.patient_code}</p>
        <p className="mx-auto mt-2 max-w-sm text-base text-muted">Use it with your password to sign in.</p>
        <p className="mx-auto mt-5 max-w-sm text-base text-ink">Next, add your doctor so they can see your sugar readings and reports.</p>
        <div className="mt-5 flex flex-col-reverse justify-center gap-2 sm:flex-row">
          <button type="button" className={b.secondary} onClick={() => go('home')}>Later</button>
          <button type="button" className={b.primary} onClick={() => go('doctors')}>
            <Stethoscope size={18} aria-hidden="true" /> Add my doctor
          </button>
        </div>
      </section>
    </AppShell>
  )
}

function DoneScreen({ shell, crumbs, message, onBack }) {
  return (
    <AppShell {...shell} title="Saved" breadcrumbs={crumbs({ label: 'Saved' })}>
      <section className="max-w-xl rounded-lg border border-line border-l-4 border-l-ok-700 bg-surface px-4 py-5">
        <p role="status" className="flex items-start gap-2.5 text-base text-ink">
          <CheckCircle2 size={24} aria-hidden="true" className="shrink-0 text-ok-700" /> {message}
        </p>
        <button type="button" onClick={onBack} className={`${b.primary} mt-5 w-full sm:w-auto`}>Done</button>
      </section>
    </AppShell>
  )
}
