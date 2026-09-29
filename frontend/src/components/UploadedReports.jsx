import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { api } from '../api.js'

// Every report this patient uploaded, fetched ONCE when the patient page opens and shared by every
// "link a report" picker on the page. It is fetched again only after the care team uploads or corrects a
// report (refresh). The server answers an unchanged list with 304 (ETag), so a refresh is cheap.
const Ctx = createContext(null)

export function UploadedReportsProvider({ patientId, children }) {
  const [state, setState] = useState({ reports: null, error: '' })
  const loadedFor = useRef(null)           // once per patient page, even when React runs effects twice in development
  const refresh = useCallback(async () => {
    try {
      const { reports } = await api.uploadedReports(patientId)
      setState({ reports, error: '' })
    } catch (e) {
      setState((s) => ({ ...s, error: e.message }))
    }
  }, [patientId])
  useEffect(() => {
    if (loadedFor.current === patientId) return
    loadedFor.current = patientId
    refresh()
  }, [refresh, patientId])
  return <Ctx.Provider value={{ ...state, patientId, refresh }}>{children}</Ctx.Provider>
}

export function useUploadedReports() {
  return useContext(Ctx)
}
