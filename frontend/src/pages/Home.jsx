import { ChevronRight, Smartphone, Stethoscope } from 'lucide-react'
import { Link } from 'react-router-dom'

const choices = [
  { to: '/care-team', icon: Stethoscope, title: 'Care team', text: 'Doctors, nurses and clinic staff — patients between visits, flags, reports.' },
  { to: '/patient', icon: Smartphone, title: 'Patient app', text: 'Approve record requests, confirm a refill, log sugar, send a report.' },
]

export default function Home() {
  return (
    <div className="min-h-screen">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-3xl items-center gap-2.5 px-4 py-3">
          <img src="/favicon.svg" alt="" className="h-7 w-7" />
          <span className="leading-tight">
            <span className="block text-sm font-semibold text-ink">UC2 Care</span>
            <span className="block text-xs text-muted">Consultation Readiness</span>
          </span>
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-4 py-10">
        <h1 className="text-xl font-semibold text-ink">Sign in to UC2 Care</h1>
        <p className="mt-1 text-sm text-muted">Choose how you use the service.</p>
        <ul className="mt-6 divide-y divide-line rounded-lg border border-line bg-surface">
          {choices.map(({ to, icon: Icon, title, text }) => (
            <li key={to}>
              <Link to={to} className="flex items-center gap-4 px-4 py-4 hover:bg-subtle">
                <Icon size={20} aria-hidden="true" className="shrink-0 text-brand-700" />
                <span className="flex-1">
                  <span className="block text-sm font-semibold text-ink">{title}</span>
                  <span className="block text-sm text-muted">{text}</span>
                </span>
                <ChevronRight size={18} aria-hidden="true" className="text-muted" />
              </Link>
            </li>
          ))}
        </ul>
      </main>
    </div>
  )
}
