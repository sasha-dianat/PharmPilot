import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { clinicalApi } from '../lib/api'

interface AuditRecord {
  id: string; type: 'ack' | 'letter'; at: string
  pharmacist: { name?: string; license?: string | null }
  patient: { id?: string | null; name?: string | null }
  physician: { name?: string | null; council_id?: string | null; medical_council_id?: string | null }
  severities: string[]
  letter_id: string | null; language: string | null; source: string | null; content_hash: string | null
}

export default function InteractionAuditView() {
  const [filters, setFilters] = useState<{ patient_name: string; council_id: string; from: string; to: string; type: '' | 'ack' | 'letter' }>(
    { patient_name: '', council_id: '', from: '', to: '', type: '' })
  const [applied, setApplied] = useState(filters)
  const [offset, setOffset] = useState(0)
  const [letter, setLetter] = useState<{ html: string; hash: string } | null>(null)

  const { data, isLoading } = useQuery<{ records: AuditRecord[]; count: number }>({
    queryKey: ['interaction-audit', applied, offset],
    queryFn: () => clinicalApi.getInteractionAudit({
      ...(applied.patient_name ? { patient_name: applied.patient_name } : {}),
      ...(applied.council_id ? { council_id: applied.council_id } : {}),
      ...(applied.from ? { from: applied.from } : {}),
      ...(applied.to ? { to: applied.to } : {}),
      ...(applied.type ? { type: applied.type } : {}),
      limit: 50, offset,
    }).then(r => r.data),
  })

  const viewLetter = async (id: string) => {
    const { data } = await clinicalApi.getPhysicianLetter(id)
    setLetter({ html: data.letter_html, hash: data.content_hash })
  }
  const printLetter = () => {
    if (!letter) return
    const w = window.open('', '_blank'); if (!w) return
    w.document.write(letter.html); w.document.close(); w.focus(); w.print()
  }

  const input = 'text-sm bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100'
  const records = data?.records ?? []

  return (
    <div className="p-4 space-y-4 text-slate-100">
      <h2 className="text-lg font-bold">Interaction Audit · Legal Trail</h2>

      <div className="flex flex-wrap gap-2 items-end">
        <input className={input} placeholder="Patient name" value={filters.patient_name}
          onChange={e => setFilters({ ...filters, patient_name: e.target.value })} />
        <input className={input} placeholder="Council ID" value={filters.council_id}
          onChange={e => setFilters({ ...filters, council_id: e.target.value })} />
        <input className={input} type="date" value={filters.from}
          onChange={e => setFilters({ ...filters, from: e.target.value })} />
        <input className={input} type="date" value={filters.to}
          onChange={e => setFilters({ ...filters, to: e.target.value })} />
        <select className={input} value={filters.type}
          onChange={e => setFilters({ ...filters, type: e.target.value as '' | 'ack' | 'letter' })}>
          <option value="">All</option><option value="ack">Acknowledgments</option><option value="letter">Letters</option>
        </select>
        <button onClick={() => { setOffset(0); setApplied(filters) }}
          className="px-4 py-2 bg-indigo-600 text-white text-sm rounded-lg">Apply</button>
        <button onClick={() => { const f = { patient_name: '', council_id: '', from: '', to: '', type: '' as const }; setFilters(f); setApplied(f); setOffset(0) }}
          className="px-4 py-2 bg-slate-700 text-white text-sm rounded-lg">Clear</button>
      </div>

      {isLoading && <p className="text-slate-400 text-sm">Loading…</p>}
      {!isLoading && records.length === 0 && <p className="text-slate-400 text-sm">No records match these filters.</p>}

      {records.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-slate-400 text-left">
            <tr><th className="py-1">Time</th><th>Type</th><th>Pharmacist</th><th>Patient</th><th>Physician</th><th>Severity</th><th></th></tr>
          </thead>
          <tbody>
            {records.map(r => (
              <tr key={r.type + r.id} className="border-t border-slate-800">
                <td className="py-1.5">{new Date(r.at).toLocaleString()}</td>
                <td><span className={`text-[11px] px-2 py-0.5 rounded ${r.type === 'letter' ? 'bg-red-500/20 text-red-300' : 'bg-amber-500/20 text-amber-300'}`}>{r.type === 'letter' ? 'Letter' : 'Ack'}</span></td>
                <td>{r.pharmacist?.name ?? '—'}</td>
                <td>{r.patient?.name ?? '—'}</td>
                <td>{(r.physician?.name ?? '—')}{(r.physician?.council_id || r.physician?.medical_council_id) ? ` · ${r.physician.council_id || r.physician.medical_council_id}` : ''}</td>
                <td>{r.severities.join(', ') || '—'}</td>
                <td>{r.letter_id && <button onClick={() => viewLetter(r.letter_id!)} className="text-indigo-400 text-xs underline">View letter</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="flex gap-2">
        <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}
          className="px-3 py-1 bg-slate-700 rounded text-sm disabled:opacity-40">Prev</button>
        <button disabled={records.length < 50} onClick={() => setOffset(offset + 50)}
          className="px-3 py-1 bg-slate-700 rounded text-sm disabled:opacity-40">Next</button>
      </div>

      {letter && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4" onClick={() => setLetter(null)}>
          <div className="bg-slate-900 max-w-2xl w-full p-4 rounded-lg space-y-2" onClick={e => e.stopPropagation()}>
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-bold">Physician letter</h3>
              <span className="text-[11px] text-slate-500">hash {letter.hash.slice(0, 12)}…</span>
              <button onClick={printLetter} className="ml-auto px-3 py-1 bg-emerald-600 text-white text-xs rounded">Print</button>
              <button onClick={() => setLetter(null)} className="text-slate-400">✕</button>
            </div>
            <iframe title="letter" srcDoc={letter.html} className="w-full h-[60vh] bg-white rounded" />
          </div>
        </div>
      )}
    </div>
  )
}
