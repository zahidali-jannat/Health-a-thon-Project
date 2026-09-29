import { ExternalLink } from 'lucide-react'
import { api, formatDate } from '../api.js'
import Modal from './Modal.jsx'

// The uploaded report shown on screen (photo or PDF) - same inline viewing as the review split screen.
// report: { id, test_label, upload_date, content_type }; `url` and `title` override the defaults
// (e.g. for a prescription photo, which is not an outside-lab report).
export default function DocumentPreview({ patientId, report, onClose, url: fileUrl, title: heading }) {
  const url = fileUrl ?? api.externalReportUrl(patientId, report.id)
  const pdf = report.content_type === 'application/pdf'
  const title = heading ?? `${report.test_label} report · uploaded ${formatDate(report.upload_date)}`
  return (
    <Modal title={title} width="60rem" onClose={onClose}
      footer={(close) => (<>
        <a href={url} target="_blank" rel="noreferrer" className="mr-auto inline-flex items-center gap-1 self-center text-sm text-brand-700 hover:underline">
          <ExternalLink size={14} aria-hidden="true" /> Open full size
        </a>
        <button type="button" onClick={close} className="inline-flex h-9 items-center rounded-md border border-line-strong bg-surface px-3.5 text-sm font-medium text-ink hover:bg-subtle">Close</button>
      </>)}>
      {pdf ? (
        <iframe src={url} title={title} className="block h-[70vh] w-full bg-subtle" />
      ) : (
        <div className="bg-subtle"><img src={url} alt={title} className="mx-auto block w-full max-w-4xl" /></div>
      )}
    </Modal>
  )
}
