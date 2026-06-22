import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { clinicalApi } from '../lib/api'
import { SEVERITY, toSeverity } from '../design/severity'

interface Finding {
  rule_id: string; type: string; severity: string; direction: string
  predicted_magnitude: string | null; mechanism: string; suggested_actions: string[]
  evidence_grade: string; source: string; recency_note: string | null
  participants: { name: string; kind: string }[]
}
interface ReportPayload {
  report: { summary: Record<string, number>; degraded: boolean; findings: Finding[] }
  findings_hash: string; cached: boolean; computed_at: string
}

const ORDER = ['Contraindicated', 'Major', 'Moderate', 'Minor']

export default function InteractionReportPanel({
  patientId, onSerious,
}: { patientId?: string; onSerious: (hash: string | null) => void }) {
  const { data, isLoading, isError } = useQuery<ReportPayload>({
    queryKey: ['interaction-report', patientId],
    enabled: !!patientId,
    queryFn: () => clinicalApi.getInteractionReport(patientId!).then(r => r.data),
  })

  const report = data?.report
  const findings = report?.findings ?? []
  const serious = findings.filter(f => f.severity === 'Contraindicated' || f.severity === 'Major')

  // tell the parent whether a serious sign-off is required (and for which hash)
  useEffect(() => {
    if (report && !report.degraded) onSerious(serious.length ? (data!.findings_hash) : null)
    else onSerious(null)
  }, [data, report, serious.length, onSerious])

  if (!patientId) return null

  return (
    <div className="cd-card p-3 space-y-2">
      <div className="flex items-center gap-1.5 text-sm font-semibold text-ink cd-ui">
        <span className="w-5 h-5 rounded-md bg-intel-soft text-intel flex items-center justify-center text-xs">🧬</span>
        <span>Interaction report</span>
        {data && <span className="ml-auto text-[11px] text-ink3">{data.cached ? 'cached' : 'fresh'}</span>}
      </div>

      {isLoading && <p className="cd-narr text-sm text-ink3">Computing interactions…</p>}
      {isError && <p className="cd-narr text-sm text-[#ef4444]">Interaction check unavailable — manual review required.</p>}
      {report?.degraded && (
        <p className="cd-narr text-sm text-[#fb923c]">Could not compute interactions — manual review required.</p>
      )}

      {report && !report.degraded && (
        <>
          <div className="flex flex-wrap gap-1.5">
            {ORDER.map(sev => {
              const n = report.summary[sev] ?? 0
              const tok = SEVERITY[toSeverity(sev)]
              return (
                <span key={sev}
                  className={`cd-ui text-[11px] font-semibold px-2 py-0.5 rounded-md ${tok.bg} ${tok.text} border ${tok.border} ${n === 0 ? 'opacity-40' : ''}`}>
                  {n} {sev}
                </span>
              )
            })}
          </div>

          {findings.length === 0 && (
            <p className="cd-narr text-sm text-[#34d399]">No interactions detected for the current basket.</p>
          )}

          <div className="space-y-2 max-h-80 overflow-y-auto">
            {[...findings].sort((a, b) => ORDER.indexOf(a.severity) - ORDER.indexOf(b.severity)).map((f, i) => {
              const tok = SEVERITY[toSeverity(f.severity)]
              return (
                <div key={i} className={`border-l-2 pl-3 ${tok.border}`}>
                  <div className="flex items-center gap-2">
                    <span className={`cd-ui text-[11px] font-bold ${tok.text}`}>{f.severity}</span>
                    <span className="cd-ui text-[11px] text-ink3">{f.direction.replace('_', ' ')}</span>
                    {f.source === 'inferred_mechanistic'
                      ? <span className="cd-ui text-[10px] text-ink3 italic">predicted</span>
                      : <span className="cd-ui text-[10px] text-ink3">{f.evidence_grade}</span>}
                  </div>
                  <p className="cd-ui text-[13px] font-bold text-ink leading-snug mt-0.5">
                    {f.participants.map(p => p.name).join(' × ')}
                  </p>
                  <p className="cd-narr text-sm text-ink leading-[1.7] mt-0.5">{f.mechanism}</p>
                  {f.predicted_magnitude && <p className="cd-narr text-[12px] text-ink2 mt-0.5">{f.predicted_magnitude}</p>}
                  {f.suggested_actions?.[0] && <p className="cd-narr text-[12px] text-intel mt-0.5">→ {f.suggested_actions[0]}</p>}
                  {f.recency_note && <p className="cd-narr text-[11px] italic text-ink3 mt-0.5">{f.recency_note}</p>}
                </div>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
