import { createContext, useContext, useEffect, useState } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { api } from './api.js'

const ClinicianContext = createContext(null)

export function useClinician() {
  return useContext(ClinicianContext)
}

// Care-team pages render only for a signed-in clinician. The server enforces this on every request too;
// this just sends people to the sign-in page instead of showing empty screens.
export function RequireClinician({ children }) {
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
  return <ClinicianContext.Provider value={{ user, setUser }}>{children}</ClinicianContext.Provider>
}
