/**
 * DURConsistencyCard — #5 DUR Override Pattern Intelligence (QA view)
 * ===================================================================
 * Per-pharmacist override-rate outlier report for peer-review / QA.
 * Surfaces pharmacists whose override volume is a statistical outlier (z≥2)
 * vs peers — for quality assurance, not punishment.
 *
 * Consumes the §1.2 envelope; shows a TierBadge.
 */
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../lib/api'
import TierBadge from './TierBadge'

interface Envelope<T> {
  result: T; tier_used: 'local' | 'cloud' | 'hybrid'; confidence: number
  degraded: boolean; options_active: string[]; options_offline: string[]; model_version: string
}

interface PharmacistRow {
  pharmacist: string; total_overrides: number; by_alert: Record<string, number>
  override_share: number; z_score: number; flag: string; note: string
}
interface ConsistencyResult {
  pharmacists: PharmacistRow[]
  summary: { total_overrides: number; pharmacists: number; outliers: number; peer_mean: number }
}

const DEMO: Envelope<ConsistencyResult> = {
  tier_used: 'local', confidence: 0.75, degraded: true,
  options_active: ['local_zscore'], options_offline: ['national_benchmarks'],
  model_version: 'dur_consistency_v1',
  result: {
    summary: { total_overrides: 184, pharmacists: 4, outliers: 1, peer_mean: 46 },
    pharmacists: [
      { pharmacist:'pharmacist_jdoe', total_overrides:92, by_alert:{ DDI:54, ALLERGY:20, DUPLICATE:18 },
        override_share:0.50, z_score:2.4, flag:'outlier_high',
        note:'Overrides 92 vs peer avg 46 (z=2.4). Suggest peer review of override rationale consistency.' },
      { pharmacist:'pharmacist_asmith', total_overrides:48, by_alert:{ DDI:30, DOSE_HIGH:18 },
        override_share:0.26, z_score:0.1, flag:'normal', note:'Within normal range vs peers.' },
      { pharmacist:'pharmacist_rlee', total_overrides:34, by_alert:{ DDI:20, ALLERGY:14 },
        override_share:0.18, z_score:-0.6, flag:'normal', note:'Within normal range vs peers.' },
      { pharmacist:'pharmacist_intern', total_overrides:10, by_alert:{ DDI:10 },
        override_share:0.05, z_score:-1.9, flag:'normal', note:'Within normal range vs peers.' },
    ],
  },
}

const FLAG_STYLE: Record<string, string> = {
  outlier_high: 'bg-red-50 border-red-200',
  normal:       'bg-white border-gray-100',
  low_volume:   'bg-gray-50 border-gray-100',
}

export default function DURConsistencyCard() {
  const { data: raw } = useQuery({
    queryKey: ['dur-consistency'],
    queryFn:  () => apiClient.get('/intelligence/dur/consistency-report')
                    .then(r => r.data as Envelope<ConsistencyResult>),
    refetchInterval: 600_000,
  })
  const env = raw ?? DEMO
  const { result: d } = env

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-lg">⚖️</span>
          <h3 className="text-sm font-semibold text-gray-800">DUR Override Consistency</h3>
          <TierBadge tier={env.tier_used} degraded={env.degraded} optionsOffline={env.options_offline} compact />
        </div>
        <div className="text-right">
          <div className={`text-lg font-bold ${d.summary.outliers > 0 ? 'text-red-600' : 'text-green-600'}`}>
            {d.summary.outliers}
          </div>
          <div className="text-[10px] text-gray-400">outlier{d.summary.outliers !== 1 ? 's' : ''} · peer avg {d.summary.peer_mean}</div>
        </div>
      </div>

      <div className="space-y-2 max-h-72 overflow-y-auto">
        {d.pharmacists.map(p => (
          <div key={p.pharmacist} className={`rounded-lg border px-3 py-2 ${FLAG_STYLE[p.flag] || FLAG_STYLE.normal}`}>
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-gray-800">{p.pharmacist.replace('pharmacist_', '')}</span>
              <div className="flex items-center gap-2">
                {p.flag === 'outlier_high' && (
                  <span className="text-[10px] bg-red-600 text-white px-1.5 py-0.5 rounded-full">review</span>
                )}
                <span className="text-xs font-mono text-gray-500">
                  {p.total_overrides} · z={p.z_score}
                </span>
              </div>
            </div>
            <div className="flex flex-wrap gap-1 mt-1">
              {Object.entries(p.by_alert).map(([alert, n]) => (
                <span key={alert} className="text-[10px] bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded">
                  {alert} {n}
                </span>
              ))}
            </div>
            {p.flag === 'outlier_high' && (
              <p className="text-[11px] text-red-600 mt-1">{p.note}</p>
            )}
          </div>
        ))}
      </div>
      <p className="text-[10px] text-gray-400 mt-2">
        Quality assurance only — high override rates may reflect complex caseload, not error.
      </p>
    </div>
  )
}
