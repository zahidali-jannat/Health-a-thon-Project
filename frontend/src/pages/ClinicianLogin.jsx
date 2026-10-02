import { CheckCircle2, ClipboardList, Copy, History, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import { homeFor } from '../auth.jsx'
import { btn, ErrorBox, input, Tabs } from '../components/ui.jsx'

const inputCls = `${input} mt-1`
const primary = `${btn.primary} h-10 w-full`

export default function ClinicianLogin() {
  const [mode, setMode] = useState('signin')
  const [created, setCreated] = useState(null)
  const navigate = useNavigate()
  const location = useLocation()
  // back to the page they came from - but only if it belongs to their own dashboard (doctor or clinic team)
  const afterSignIn = (user) => {
    const from = location.state?.from
    const home = homeFor(user)
    navigate(from && from.startsWith(home) ? from : home, { replace: true })
  }

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
      {/* context panel: what this is and how access works - no decoration */}
      <aside className="hidden border-r border-line bg-surface lg:flex lg:flex-col lg:justify-between lg:px-12 lg:py-10">
        <Link to="/" className="flex items-center gap-2.5">
          <img src="/favicon.svg" alt="" className="h-8 w-8" />
          <span className="leading-tight">
            <span className="block text-[13px] font-medium text-ink">UC2 Care</span>
            <span className="block text-xs text-muted">Consultation Readiness</span>
          </span>
        </Link>
        <div className="max-w-md">
          <h2 className="text-lg font-semibold text-ink">Diabetes care between visits</h2>
          <ul className="mt-3 space-y-2 text-sm text-muted">
            <li className="flex gap-2"><ShieldCheck size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-brand-700" />You see only patients on your care team.</li>
            <li className="flex gap-2"><ClipboardList size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-brand-700" />Flags come from plain, documented rules — every flag shows its reasons.</li>
            <li className="flex gap-2"><History size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-brand-700" />Every record you open or change is written to the access log.</li>
          </ul>
        </div>
        <p className="text-xs text-muted">For clinical staff. Patients use the <Link to="/patient" className="text-brand-700 hover:underline">patient app</Link>.</p>
      </aside>

      <main className="flex min-h-screen items-start justify-center px-4 py-10 lg:items-center">
        <div className="w-full max-w-sm">
          <Link to="/" className="mb-8 flex items-center gap-2 lg:hidden">
            <img src="/favicon.svg" alt="" className="h-7 w-7" /><span className="text-sm font-semibold text-ink">UC2 Care</span>
          </Link>
          {created ? (
            <AccountCreated user={created} onContinue={() => navigate(homeFor(created), { replace: true })} />
          ) : (
            <>
              <h1 className="text-xl font-bold text-ink">Clinic sign in</h1>
              <p className="mt-0.5 text-sm text-muted">{mode === 'signin' ? 'Doctors and the clinic team sign in here.' : 'Create a doctor or clinic team account.'}</p>
              <div className="mt-4">
                <Tabs label="Sign in or create account" value={mode} onChange={setMode}
                  tabs={[{ id: 'signin', label: 'Sign in' }, { id: 'signup', label: 'Create account' }]} />
              </div>
              <div className="pt-5">
                {mode === 'signin' ? <SignIn onDone={afterSignIn} /> : <SignUp onDone={setCreated} />}
              </div>
            </>
          )}
        </div>
      </main>
    </div>
  )
}

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
  return { busy, error, run }
}

function SignIn({ onDone }) {
  const [identifier, setIdentifier] = useState('')
  const [password, setPassword] = useState('')
  const { busy, error, run } = useSubmit()
  return (
    <form onSubmit={(e) => { e.preventDefault(); run(async () => onDone(await api.clinicianLogin(identifier, password))) }}>
      <label className="block text-[13px] font-medium text-ink">
        Clinician ID or phone number
        <input value={identifier} onChange={(e) => setIdentifier(e.target.value)} autoComplete="username" required
          placeholder="e.g. CLN-7K3Q9P" className={inputCls} />
      </label>
      <label className="mt-4 block text-[13px] font-medium text-ink">
        Password
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password"
          required className={inputCls} />
      </label>
      <div className="mt-4"><ErrorBox>{error}</ErrorBox></div>
      <button type="submit" disabled={busy} className={`${primary} mt-4`}>{busy ? 'Signing in…' : 'Sign in'}</button>
      <p className="mt-4 text-sm text-muted">New to the care team? Choose <strong>Create account</strong> above.</p>
    </form>
  )
}

function SignUp({ onDone }) {
  const [f, setF] = useState({ name: '', phone: '', password: '', role: 'doctor' })
  const { busy, error, run } = useSubmit()
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }))
  return (
    <form onSubmit={(e) => { e.preventDefault(); run(async () => onDone(await api.clinicianSignup(f.name, f.phone, f.password, f.role))) }}>
      <fieldset>
        <legend className="text-[13px] font-medium text-ink">I am</legend>
        <div className="mt-1 grid grid-cols-2 gap-2">
          {[['doctor', 'A doctor', 'Sees briefs on the doctor dashboard'], ['care_team', 'Clinic team', 'Patients, reports, briefs']].map(([id, label, hint]) => (
            <label key={id} className={`cursor-pointer rounded-md border px-3 py-2 text-sm focus-within:ring-2 focus-within:ring-brand-100 ${f.role === id ? 'border-brand-600 bg-brand-50' : 'border-line-strong'}`}>
              <input type="radio" name="role" value={id} checked={f.role === id} onChange={set('role')} className="sr-only" />
              <span className="block font-semibold text-ink">{label}</span>
              <span className="block text-xs text-muted">{hint}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <label className="mt-4 block text-[13px] font-medium text-ink">
        Full name
        <input value={f.name} onChange={set('name')} autoComplete="name" required maxLength={120}
          placeholder={f.role === 'doctor' ? 'e.g. Dr. Asha Verma' : 'e.g. Neha Sharma'} className={inputCls} />
      </label>
      <label className="mt-4 block text-[13px] font-medium text-ink">
        Phone number
        <input value={f.phone} onChange={set('phone')} inputMode="tel" autoComplete="tel" required maxLength={20}
          placeholder="10-digit mobile number" className={inputCls} />
      </label>
      <label className="mt-4 block text-[13px] font-medium text-ink">
        Password
        <input type="password" value={f.password} onChange={set('password')} autoComplete="new-password" required minLength={8}
          className={inputCls} />
        <span className="mt-1 block text-xs font-normal text-muted">At least 8 characters.</span>
      </label>
      <p className="mt-4 text-sm text-muted">Your unique Clinician ID is created automatically.</p>
      <div className="mt-4"><ErrorBox>{error}</ErrorBox></div>
      <button type="submit" disabled={busy} className={`${primary} mt-4`}>{busy ? 'Creating account…' : 'Create account'}</button>
    </form>
  )
}

function AccountCreated({ user, onContinue }) {
  const [copied, setCopied] = useState(false)
  const copy = () => navigator.clipboard?.writeText(user.clinician_code).then(() => setCopied(true)).catch(() => {})
  return (
    <div className="rounded-lg border border-line bg-surface p-6 text-center">
      <CheckCircle2 size={32} className="mx-auto text-ok-700" aria-hidden="true" />
      <h1 className="mt-2 text-lg font-semibold text-ink">Account created</h1>
      <p className="mt-0.5 text-sm text-muted">Welcome, {user.full_name}.</p>
      <p className="mt-5 text-xs font-medium uppercase tracking-wide text-muted">Your Clinician ID</p>
      <p className="mt-1 text-3xl font-semibold tracking-wider text-ink tnum" data-testid="clinician-code">{user.clinician_code}</p>
      <button type="button" onClick={copy} className="mt-2 inline-flex items-center gap-1 text-sm font-semibold text-brand-700 hover:underline">
        <Copy size={16} aria-hidden="true" /> {copied ? 'Copied' : 'Copy ID'}
      </button>
      <p className="mx-auto mt-4 max-w-sm text-sm text-muted">
        {user.role === 'doctor'
          ? 'Use it to sign in. Patients and the clinic team use it to add you to a patient’s care team.'
          : `Use it to sign in. You see every patient of ${user.clinic_name}.`}
      </p>
      <button type="button" onClick={onContinue} className={`${primary} mt-6`}>{user.role === 'doctor' ? 'Continue to my dashboard' : 'Continue'}</button>
    </div>
  )
}
