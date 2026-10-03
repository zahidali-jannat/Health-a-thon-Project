import { CheckCircle2, ChevronRight, ClipboardCheck, FileText, Smartphone, Stethoscope } from 'lucide-react'
import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { btn } from '../components/ui.jsx'

/*
 * The public front page - the only page search engines index (everything signed-in sends noindex). It says what
 * UC2 Consultation Readiness is in plain words, then links to the two sign-ins. Demo logins show only on a public
 * demo build (VITE_PUBLIC_DEMO=1), never on a real clinic's site.
 */
const TITLE = 'UC2 Consultation Readiness – diabetes patients, ready before the consultation'
const PUBLIC_DEMO = import.meta.env.VITE_PUBLIC_DEMO === '1'

const SIGNS = [
  ['Refills slip', 'A medicine refill is 10 or more days late.'],
  ['Logging drops', 'Home sugar readings fall by 40% or more.'],
  ['HbA1c stalls', 'Results stay flat or get worse, still at 7% or above.'],
  ['A low-sugar episode', 'Hypoglycaemia at home that nobody hears about.'],
]

const STEPS = [
  { icon: Smartphone, who: 'Patient app', title: 'Sends what happened',
    text: 'Refill bills, sugar readings with a glucometer photo, outside lab reports and test results, from the phone.' },
  { icon: ClipboardCheck, who: 'Clinic team', title: 'Verifies and prepares',
    text: 'Checks each upload side by side with the file, orders missing tests before the visit, and prepares the consultation brief.' },
  { icon: Stethoscope, who: 'Doctor dashboard', title: 'Reads one page',
    text: '“The clinic team sent a report about this patient.” Confirm the patient’s identity, open the brief, consult.' },
]

const DIFFERENT = [
  ['Every flag says why', 'No unexplained score: each flag names its reason, with the dates and numbers.'],
  ['Only verified data reaches the doctor', 'Values a patient typed in are counted, not shown, until the clinic team checks them.'],
  ['One page for the doctor', 'A short consultation brief that prints on a single A4 sheet.'],
  ['Care between visits', 'Daily risk checks, tests ordered before the appointment, reminders to the patient.'],
  ['The right patient, every time', 'The doctor confirms the patient ID and date of birth before a brief opens.'],
  ['Open standards', 'FHIR R4 export and an ABHA consent flow for records from other hospitals.'],
]

const DEMO_LOGINS = [
  ['Clinic team', 'CLN-TNSQ01', '/care-team/login'],
  ['Doctor', 'CLN-PRYA27', '/care-team/login'],
  ['Patient', 'P-1001', '/patient'],
]

export default function Home() {
  useEffect(() => {
    document.title = TITLE
  }, [])

  return (
    <div className="min-h-screen bg-page text-ink">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-5xl items-center gap-2.5 px-4 py-3">
          <img src="/favicon.svg" alt="" className="h-7 w-7" />
          <span className="flex-1 leading-tight">
            <span className="block text-sm font-semibold">UC2 Care</span>
            <span className="block text-xs text-muted">Consultation Readiness</span>
          </span>
          <nav aria-label="Sign in" className="flex gap-2">
            <Link to="/patient" className={`${btn.ghost} ${btn.sm}`}>Patient app</Link>
            <Link to="/care-team" className={`${btn.secondary} ${btn.sm}`}>Clinic sign in</Link>
          </nav>
        </div>
      </header>

      <main>
        <section className="border-b border-line bg-surface">
          <div className="mx-auto max-w-5xl px-4 py-14 sm:py-20">
            <p className="text-sm font-semibold uppercase tracking-wide text-brand-700">For diabetes clinics</p>
            <h1 className="mt-2 text-4xl font-bold leading-tight sm:text-5xl">UC2 Consultation Readiness</h1>
            <p className="mt-4 max-w-2xl text-lg text-muted">
              Every diabetes patient walks into the consultation already understood. Early risk flags with clear reasons,
              verified patient reports, tests ordered before the visit, and a one-page brief for the doctor.
            </p>
            <div className="mt-7 flex flex-wrap gap-3">
              <Link to="/care-team" className={btn.primary}><Stethoscope size={16} aria-hidden="true" /> Clinic sign in</Link>
              <Link to="/patient" className={btn.secondary}><Smartphone size={16} aria-hidden="true" /> Patient app</Link>
            </div>
          </div>
        </section>

        <section aria-labelledby="problem" className="mx-auto max-w-5xl px-4 py-12">
          <h2 id="problem" className="text-2xl font-bold">Between two visits, nobody is watching</h2>
          <p className="mt-2 max-w-3xl text-muted">
            A diabetes patient sees the doctor every three to six months. In between, they can quietly get worse, and
            nobody notices until the next visit. UC2 watches for four warning signs every day:
          </p>
          <ul className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {SIGNS.map(([title, text]) => (
              <li key={title} className="rounded-lg border border-line bg-surface p-4">
                <h3 className="font-semibold">{title}</h3>
                <p className="mt-1 text-sm text-muted">{text}</p>
              </li>
            ))}
          </ul>
        </section>

        <section aria-labelledby="how" className="border-y border-line bg-surface">
          <div className="mx-auto max-w-5xl px-4 py-12">
            <h2 id="how" className="text-2xl font-bold">How it works: three people, one flow</h2>
            <ol className="mt-6 grid gap-4 md:grid-cols-3">
              {STEPS.map(({ icon: Icon, who, title, text }, i) => (
                <li key={who} className="rounded-lg border border-line p-5">
                  <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-brand-700">
                    <Icon size={16} aria-hidden="true" /> {i + 1} · {who}
                  </p>
                  <h3 className="mt-2 text-lg font-semibold">{title}</h3>
                  <p className="mt-1 text-sm text-muted">{text}</p>
                </li>
              ))}
            </ol>
            <p className="mt-6 flex gap-2 text-sm text-muted">
              <FileText size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-brand-700" />
              Underneath, the Silent Risk Detector runs seven plain checks and flags a patient early. No AI model makes a
              clinical decision: every threshold lives in one configuration file.
            </p>
          </div>
        </section>

        <section aria-labelledby="different" className="mx-auto max-w-5xl px-4 py-12">
          <h2 id="different" className="text-2xl font-bold">What makes it different</h2>
          <ul className="mt-6 grid gap-x-8 gap-y-5 sm:grid-cols-2">
            {DIFFERENT.map(([title, text]) => (
              <li key={title} className="flex gap-3">
                <CheckCircle2 size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-ok-700" />
                <span><span className="block font-semibold">{title}</span><span className="block text-sm text-muted">{text}</span></span>
              </li>
            ))}
          </ul>
        </section>

        {PUBLIC_DEMO && (
          <section aria-labelledby="demo" className="mx-auto max-w-5xl px-4 pb-12">
            <div className="rounded-lg border border-line bg-surface p-5">
              <h2 id="demo" className="text-xl font-bold">Try the demo</h2>
              <p className="mt-1 text-sm text-muted">Made-up patients only. Password for every demo login: <strong className="text-ink">demo1234</strong></p>
              <ul className="mt-4 divide-y divide-line">
                {DEMO_LOGINS.map(([who, id, to]) => (
                  <li key={id}>
                    <Link to={to} className="flex items-center gap-4 py-3 hover:bg-subtle">
                      <span className="w-28 text-sm font-semibold">{who}</span>
                      <span className="flex-1 font-mono text-sm tnum">{id}</span>
                      <ChevronRight size={18} aria-hidden="true" className="text-muted" />
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          </section>
        )}
      </main>

      <footer className="border-t border-line bg-surface">
        <div className="mx-auto max-w-5xl px-4 py-5 text-sm text-muted">
          UC2 Consultation Readiness · Healthathon 2026 prototype. Not for emergencies: in an emergency, call 112.
        </div>
      </footer>
    </div>
  )
}
