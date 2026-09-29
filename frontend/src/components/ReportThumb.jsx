import { FileText } from 'lucide-react'
import { api } from '../api.js'

// A small preview of an uploaded outside-lab report: the image itself, or a PDF marker.
export default function ReportThumb({ report, size = 'h-14 w-11' }) {
  if (report.content_type === 'application/pdf') {
    return (
      <span className={`${size} flex shrink-0 flex-col items-center justify-center rounded border border-line bg-subtle text-muted`}>
        <FileText size={16} aria-hidden="true" /><span className="text-[10px] font-semibold">PDF</span>
      </span>
    )
  }
  return (
    <img src={api.externalReportUrl(report.patient_id, report.id, true)} alt="" loading="lazy"
      className={`${size} shrink-0 rounded border border-line bg-subtle object-cover object-top`} />
  )
}
