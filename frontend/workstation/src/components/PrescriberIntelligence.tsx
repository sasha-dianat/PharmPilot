/**
 * PrescriberIntelligence — #13 Smart Prescriber Registry Enrichment
 * ==================================================================
 * Two surfaces:
 *   • PrescriberProfileCard — the prescriber's normal prescribing pattern +
 *     verification status (verified ✓ / pending ⧖ / format-invalid ⚠).
 *   • PrescriberDeviationFlag — a compact inline flag for the VerificationCenter
 *     when a candidate Rx deviates from the prescriber's usual pattern.
 *
 * Both consume the §1.2 envelope and show a TierBadge.
 */
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../lib/api'
import TierBadge from './TierBadge'

interface Envelope<T> {
  result: T; tier_used: 'local' | 'cloud' | 'hybrid'; confidence: number
  degraded: boolean; options_active: string[]; options_offline: string[]; model_version: string
}

// ─── Profile card ─────────────────────────────────────────────────────────────

interface DrugStat {
  ndc: string; drug_name: string; rx_count: number
  avg_quantity: number; std_quantity: number; avg_days_supply: number; is_controlled: boolean
}
interface ProfileResult {
  summary: {
    prescriber_id: string; total_rx: number; distinct_drugs: number
    controlled_rx: number; controlled_share: number; profile_maturity: string
  }
  top_drugs: DrugStat[]
}

const DEMO_PROFILE: Envelope<ProfileResult> = {
  tier_used: 'local', confidence: 0.8, degraded: true,
  options_active: ['local_profile'], options_offline: ['registry_validation', 'suspension_check'],
  model_version: 'prescriber_profile_v1',
  result: {
    summary: { prescriber_id: 'demo', total_rx: 312, distinct_drugs: 41,
               controlled_rx: 28, controlled_share: 0.09, profile_maturity: 'established' },
    top_drugs: [
      { ndc:'00071015423', drug_name:'Lisinopril 10mg', rx_count:48, avg_quantity:30, std_quantity:4.2, avg_days_supply:30, is_controlled:false },
      { ndc:'00185064001', drug_name:'Atorvastatin 40mg', rx_count:39, avg_quantity:30, std_quantity:2.1, avg_days_supply:30, is_controlled:false },
      { ndc:'00555097202', drug_name:'Oxycodone 5mg', rx_count:14, avg_quantity:60, std_quantity:18.5, avg_days_supply:15, is_controlled:true },
    ],
  },
}

const MATURITY_BADGE: Record<string, string> = {
  established: 'bg-green-100 text-green-700',
  developing:  'bg-amber-100 text-amber-700',
  sparse:      'bg-gray-100 text-gray-500',
}

export function PrescriberProfileCard({ prescriberId, prescriberName }: {
  prescriberId: string; prescriberName?: string
}) {
  const { data: raw } = useQuery({
    queryKey: ['prescriber-profile', prescriberId],
    queryFn:  () => apiClient.get(`/intelligence/prescriber/${prescriberId}/profile`)
                    .then(r => r.data as Envelope<ProfileResult>),
    enabled:  !!prescriberId,
  })
  const env = raw ?? DEMO_PROFILE
  const s = env.result.summary

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-lg">🩺</span>
          <div>
            <h3 className="text-sm font-semibold text-gray-800">{prescriberName || 'Prescriber'} Profile</h3>
            <span className="text-[10px] text-gray-400 font-mono">{prescriberId}</span>
          </div>
        </div>
        <TierBadge tier={env.tier_used} degraded={env.degraded} optionsOffline={env.options_offline} compact />
      </div>

      <div className="grid grid-cols-3 gap-2 mb-3">
        <Stat label="Total Rx" value={s.total_rx} />
        <Stat label="Distinct drugs" value={s.distinct_drugs} />
        <Stat label="Controlled" value={`${Math.round(s.controlled_share * 100)}%`}
              warn={s.controlled_share > 0.2} />
      </div>

      <div className="flex items-center gap-2 mb-3">
        <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${MATURITY_BADGE[s.profile_maturity]}`}>
          {s.profile_maturity} profile
        </span>
        {env.degraded && (
          <span className="text-[10px] text-amber-600">⧖ external registry check pending (offline)</span>
        )}
      </div>

      <div className="space-y-1 max-h-44 overflow-y-auto">
        <p className="text-[10px] text-gray-400 uppercase tracking-wide">Most-prescribed</p>
        {env.result.top_drugs.map(d => (
          <div key={d.ndc} className="flex items-center justify-between text-xs py-0.5">
            <span className="text-gray-700 flex items-center gap-1">
              {d.is_controlled && <span className="text-red-500">⚠</span>}
              {d.drug_name}
            </span>
            <span className="text-gray-400 font-mono">
              {d.rx_count}× · μ{d.avg_quantity}±{d.std_quantity}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

function Stat({ label, value, warn = false }: { label: string; value: number | string; warn?: boolean }) {
  return (
    <div className="bg-gray-50 rounded-lg px-2 py-1.5 text-center">
      <div className={`text-lg font-bold ${warn ? 'text-red-600' : 'text-gray-800'}`}>{value}</div>
      <div className="text-[9px] text-gray-400 uppercase">{label}</div>
    </div>
  )
}

// ─── Deviation flag (inline, for VerificationCenter) ──────────────────────────

interface DeviationResult {
  deviation_score: number; band: string; z_score: number
  novelty: boolean; flags: string[]; rationale: string
}

const BAND_STYLE: Record<string, string> = {
  high:     'bg-red-50 border-red-200 text-red-700',
  moderate: 'bg-amber-50 border-amber-200 text-amber-700',
  low:      'bg-green-50 border-green-200 text-green-700',
}

export function PrescriberDeviationFlag({
  prescriberId, ndc, drugName, quantity, isControlled = false,
}: {
  prescriberId: string; ndc: string; drugName: string; quantity: number; isControlled?: boolean
}) {
  const { data: raw } = useQuery({
    queryKey: ['prescriber-deviation', prescriberId, ndc, quantity],
    queryFn:  () => apiClient.post('/intelligence/prescriber/score-rx', {
      prescriber_id: prescriberId, ndc, drug_name: drugName,
      quantity, is_controlled: isControlled,
    }).then(r => r.data as Envelope<DeviationResult>),
    enabled: !!prescriberId && !!ndc,
  })
  if (!raw) return null
  const d = raw.result
  // Only show the flag when there's something worth noting.
  if (d.band === 'low' && !d.novelty) return null

  return (
    <div className={`rounded-lg border px-3 py-2 text-xs ${BAND_STYLE[d.band] || BAND_STYLE.low}`}>
      <div className="flex items-center justify-between">
        <span className="font-semibold">
          {d.novelty ? '🆕 Unusual for this prescriber' : `⚠ Prescribing deviation (${d.band})`}
        </span>
        <TierBadge tier={raw.tier_used} degraded={raw.degraded} compact />
      </div>
      <p className="mt-0.5">{d.rationale}</p>
      {d.flags.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-1">
          {d.flags.map(f => (
            <span key={f} className="text-[10px] bg-white/60 px-1.5 py-0.5 rounded">
              {f.replace(/_/g, ' ')}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
