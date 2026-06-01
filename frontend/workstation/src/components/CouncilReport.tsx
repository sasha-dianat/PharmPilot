/**
 * Specialist Council Report — auto-displays when pharmacist opens Rx review.
 * Streams findings progressively via Server-Sent Events so results appear
 * as each specialist finishes (Nephrology might return in 50ms, Hereditary
 * takes longer — both show the moment they're ready).
 */
import { useEffect, useState } from 'react'

interface CouncilFinding {
  specialist: string
  severity: 'blocker' | 'caution' | 'counseling' | 'monitoring' | 'clarification'
  message: string
  drug_name?: string
  evidence_grade?: string
  evidence_source?: string
}

interface StreamEvent {
  event: string
  specialist?: string
  findings?: CouncilFinding[]
  findings_count?: number
}

interface Props {
  prescriptionId: string
  patientId: string
  pharmacyId: string
}

const SEVERITY_STYLE: Record<string, { bg: string; border: string; icon: string; label: string }> = {
  blocker:       { bg: 'bg-red-50',    border: 'border-red-500',    icon: '🚫', label: 'BLOCKER' },
  caution:       { bg: 'bg-orange-50', border: 'border-orange-400', icon: '⚠️', label: 'Caution' },
  counseling:    { bg: 'bg-blue-50',   border: 'border-blue-400',   icon: '💬', label: 'Counseling' },
  monitoring:    { bg: 'bg-yellow-50', border: 'border-yellow-400', icon: '📋', label: 'Monitor' },
  clarification: { bg: 'bg-purple-50', border: 'border-purple-400', icon: '❓', label: 'Clarify' },
}

export default function CouncilReport({ prescriptionId, patientId, pharmacyId }: Props) {
  const [findings, setFindings] = useState<CouncilFinding[]>([])
  const [completedSpecialists, setCompletedSpecialists] = useState<string[]>([])
  const [status, setStatus] = useState<'idle' | 'loading' | 'complete' | 'error'>('idle')
  const [errorMsg, setErrorMsg] = useState('')

  useEffect(() => {
    if (!prescriptionId || !patientId) return
    setFindings([])
    setCompletedSpecialists([])
    setStatus('loading')

    const token = localStorage.getItem('access_token') || ''
    const API = import.meta.env.VITE_API_URL || 'http://localhost:8001/api/v1'
    const url = `${API}/pharmacy/council/stream?prescription_id=${prescriptionId}&patient_id=${patientId}`

    const es = new EventSource(url)

    es.onmessage = (e) => {
      try {
        const data: StreamEvent = JSON.parse(e.data)

        if (data.event === 'council_started') {
          setStatus('loading')
        } else if (data.event === 'specialist_complete' && data.specialist) {
          setCompletedSpecialists((prev) => [...prev, data.specialist!])
          if (data.findings && data.findings.length > 0) {
            setFindings((prev) => [...prev, ...data.findings!])
          }
        } else if (data.event === 'council_complete') {
          setStatus('complete')
          es.close()
        }
      } catch { /* ignore parse errors */ }
    }

    es.onerror = () => {
      setStatus('error')
      setErrorMsg('Council connection lost. Reload to retry.')
      es.close()
    }

    return () => es.close()
  }, [prescriptionId, patientId])

  const blockers     = findings.filter(f => f.severity === 'blocker')
  const cautions     = findings.filter(f => f.severity === 'caution')
  const counseling   = findings.filter(f => f.severity === 'counseling')
  const monitoring   = findings.filter(f => f.severity === 'monitoring')
  const clarification = findings.filter(f => f.severity === 'clarification')

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-lg">🏛️</span>
          <span className="font-semibold text-gray-800">Specialist Council</span>
          {status === 'loading' && (
            <span className="text-xs text-blue-500 animate-pulse">
              Consulting specialists…
            </span>
          )}
          {status === 'complete' && (
            <span className="text-xs text-green-600">
              ✓ {completedSpecialists.length} specialists consulted
            </span>
          )}
        </div>
        {findings.length > 0 && (
          <div className="flex gap-1.5 text-xs">
            {blockers.length > 0 && (
              <span className="bg-red-100 text-red-700 px-1.5 py-0.5 rounded font-medium">
                🚫 {blockers.length}
              </span>
            )}
            {cautions.length > 0 && (
              <span className="bg-orange-100 text-orange-700 px-1.5 py-0.5 rounded">
                ⚠️ {cautions.length}
              </span>
            )}
            {counseling.length > 0 && (
              <span className="bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded">
                💬 {counseling.length}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Loading skeleton */}
      {status === 'loading' && findings.length === 0 && (
        <div className="space-y-2">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="h-12 bg-gray-100 rounded animate-pulse" />
          ))}
        </div>
      )}

      {/* Error */}
      {status === 'error' && (
        <div className="text-xs text-red-600 bg-red-50 border border-red-200 rounded p-2">
          {errorMsg}
        </div>
      )}

      {/* Findings — each category */}
      {[
        { list: blockers,      label: 'Matters Requiring Attention' },
        { list: cautions,      label: 'Cautions' },
        { list: clarification, label: 'Consider Prescriber Clarification' },
        { list: counseling,    label: 'Patient Counseling Points' },
        { list: monitoring,    label: 'Monitoring Parameters' },
      ].map(({ list, label }) =>
        list.length > 0 ? (
          <div key={label}>
            <div className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">
              {label} ({list.length})
            </div>
            <div className="space-y-1.5">
              {list.map((f, i) => {
                const style = SEVERITY_STYLE[f.severity] || SEVERITY_STYLE.caution
                return (
                  <div key={i} className={`${style.bg} border-l-4 ${style.border} rounded p-2.5 text-xs`}>
                    <div className="flex items-center gap-1.5 mb-1">
                      <span>{style.icon}</span>
                      <span className="font-semibold text-gray-700">{f.specialist}</span>
                      {f.evidence_grade && (
                        <span className="bg-white border text-gray-500 px-1 rounded text-xs">
                          Grade {f.evidence_grade}
                        </span>
                      )}
                    </div>
                    <p className="text-gray-800 leading-relaxed">{f.message}</p>
                    {f.evidence_source && (
                      <p className="text-gray-400 mt-1 italic">Source: {f.evidence_source}</p>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        ) : null
      )}

      {/* All clear */}
      {status === 'complete' && findings.length === 0 && (
        <div className="flex items-center gap-2 px-3 py-2 bg-green-50 border border-green-300 rounded text-green-700 text-sm">
          <span>✅</span>
          <span>No significant clinical considerations identified by the council.</span>
        </div>
      )}

      {/* Safe language disclaimer */}
      {findings.length > 0 && (
        <p className="text-xs text-gray-400 italic border-t pt-2">
          Council findings use review prompts and possible considerations.
          The council does not diagnose, prescribe, or approve dispensing.
          Clinical judgment rests with the licensed pharmacist.
        </p>
      )}
    </div>
  )
}
