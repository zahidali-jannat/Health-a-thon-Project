import { createContext, useContext, useEffect, useState } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { api } from './api.js'

const ClinicianContext = createContext(null)

export function useClinician() {
  return useContext(ClinicianContext)
}

// Doctors and the clinic team have separate dashboards. An account from a backend without roles yet acts as the
// clinic team (it used to do everything), so nothing loops or locks out while the backend is being upgraded.
export const roleOf = (user) => user?.role ?? 'care_team'
export const homeFor = (user) => (roleOf(user) === 'doctor' ? '/doctor' : '/care-team')
export const settingsBase = (user) => (user?.role === 'doctor' ? '/doctor/settings' : '/care-team/settings')

// Care-team pages render only for a signed-in clinician. The server enforces this on every request too;
// this just sends people to the sign-in page instead of showing empty screens. `role` keeps a doctor out of the
// clinic team's pages (and the other way round) by sending them to their own dashboard.
export function RequireClinician({ children, role }) {
  const [user, setUser] = useState(undefined)
  const location = useLocation()

  useEffect(() => {
    api.clinicianMe().then(setUser).catch(() => setUser(null))
    const onSignedOut = (e) => e.detail === 'clinician' && setUser(null)
    window.addEventListener('uc2:signed-out', onSignedOut)
    return () => window.removeEventListener('uc2:signed-out', onSignedOut)
  }, [])

  // The clinician's saved theme applies to the care-team pages only; the patient app stays light.
  useEffect(() => {
    const root = document.documentElement
    if (user?.theme && user.theme !== 'light') root.dataset.theme = user.theme
    else delete root.dataset.theme
    return () => delete root.dataset.theme
  }, [user?.theme])

  if (user === undefined) return <p className="p-8 text-base text-muted">Loading…</p>
  if (user === null) return <Navigate to="/care-team/login" replace state={{ from: location.pathname }} />
  if (role && roleOf(user) !== role) return <Navigate to={homeFor(user)} replace />
  return <ClinicianContext.Provider value={{ user, setUser }}>{children}</ClinicianContext.Provider>
}
