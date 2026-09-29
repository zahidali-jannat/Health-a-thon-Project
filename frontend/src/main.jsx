import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import './index.css'
import { RequireClinician } from './auth.jsx'
import ClinicianLogin from './pages/ClinicianLogin.jsx'
import ConsentLog from './pages/ConsentLog.jsx'
import Overview from './pages/Overview.jsx'
import Patients from './pages/Patients.jsx'
import Home from './pages/Home.jsx'
import PatientDetail from './pages/PatientDetail.jsx'
import PendingReports from './pages/PendingReports.jsx'
import MedicineBills from './pages/MedicineBills.jsx'
import BillReview from './pages/BillReview.jsx'
import Portal from './pages/Portal.jsx'
import ReportReview from './pages/ReportReview.jsx'
import LabReportReview from './pages/LabReportReview.jsx'
import Schedule from './pages/Schedule.jsx'
import Settings from './pages/Settings.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/care-team/login" element={<ClinicianLogin />} />
        <Route path="/care-team" element={<RequireClinician><Overview /></RequireClinician>} />
        <Route path="/care-team/patients" element={<RequireClinician><Patients /></RequireClinician>} />
        <Route path="/care-team/patients/:id" element={<RequireClinician><PatientDetail /></RequireClinician>} />
        <Route path="/care-team/reports" element={<RequireClinician><PendingReports /></RequireClinician>} />
        <Route path="/care-team/reports/:patientId/:reportId" element={<RequireClinician><ReportReview /></RequireClinician>} />
        <Route path="/care-team/lab-reports/:patientId/:reportId" element={<RequireClinician><LabReportReview /></RequireClinician>} />
        <Route path="/care-team/bills" element={<RequireClinician><MedicineBills /></RequireClinician>} />
        <Route path="/care-team/bills/:patientId/:billId" element={<RequireClinician><BillReview /></RequireClinician>} />
        <Route path="/care-team/consent-log" element={<RequireClinician><ConsentLog /></RequireClinician>} />
        <Route path="/care-team/schedule" element={<RequireClinician><Schedule /></RequireClinician>} />
        <Route path="/care-team/settings/:section?" element={<RequireClinician><Settings /></RequireClinician>} />
        <Route path="/patient" element={<Portal />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
