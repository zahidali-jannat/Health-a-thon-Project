import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
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
import Briefs from './pages/Briefs.jsx'
import BriefComposer from './pages/BriefComposer.jsx'
import DoctorQueue from './pages/DoctorQueue.jsx'
import Settings from './pages/Settings.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/care-team/login" element={<ClinicianLogin />} />
        <Route path="/care-team" element={<RequireClinician role="care_team"><Overview /></RequireClinician>} />
        <Route path="/care-team/patients" element={<RequireClinician role="care_team"><Patients /></RequireClinician>} />
        <Route path="/care-team/patients/:id" element={<RequireClinician role="care_team"><PatientDetail /></RequireClinician>} />
        <Route path="/care-team/reports" element={<RequireClinician role="care_team"><PendingReports /></RequireClinician>} />
        <Route path="/care-team/reports/:patientId/:reportId" element={<RequireClinician role="care_team"><ReportReview /></RequireClinician>} />
        <Route path="/care-team/lab-reports/:patientId/:reportId" element={<RequireClinician role="care_team"><LabReportReview /></RequireClinician>} />
        <Route path="/care-team/bills" element={<RequireClinician role="care_team"><MedicineBills /></RequireClinician>} />
        <Route path="/care-team/bills/:patientId/:billId" element={<RequireClinician role="care_team"><BillReview /></RequireClinician>} />
        <Route path="/care-team/consent-log" element={<RequireClinician role="care_team"><ConsentLog /></RequireClinician>} />
        <Route path="/care-team/schedule" element={<RequireClinician role="care_team"><Schedule /></RequireClinician>} />
        <Route path="/care-team/briefs" element={<RequireClinician role="care_team"><Briefs /></RequireClinician>} />
        <Route path="/care-team/briefs/:briefId" element={<RequireClinician role="care_team"><BriefComposer /></RequireClinician>} />
        <Route path="/care-team/doctor" element={<Navigate to="/doctor" replace />} />
        <Route path="/doctor" element={<RequireClinician role="doctor"><DoctorQueue /></RequireClinician>} />
        <Route path="/doctor/settings/:section?" element={<RequireClinician role="doctor"><Settings /></RequireClinician>} />
        <Route path="/care-team/settings/:section?" element={<RequireClinician role="care_team"><Settings /></RequireClinician>} />
        <Route path="/patient" element={<Portal />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
