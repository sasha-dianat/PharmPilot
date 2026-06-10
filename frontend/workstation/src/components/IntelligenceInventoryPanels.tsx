/**
 * IntelligenceInventoryPanels — #12 Expiry Waste + #17 Supply Warning
 * ====================================================================
 * Two panels that drop into the EXISTING InventoryIntelligence (stock-ML) board.
 * Both consume the universal §1.2 intelligence envelope and show a TierBadge so
 * the user always knows whether they're on Full or Local intelligence.
 *
 * Dark slate theme to match the surrounding dashboard.
 */
import { useQuery, useMutation } from '@tanstack/react-query'
import { apiClient } from '../lib/api'
import TierBadge, { DegradedNote } from './TierBadge'

// ─── Envelope typing ──────────────────────────────────────────────────────────

interface Envelope<T> {
  result:          T
  tier_used:       'local' | 'cloud' | 'hybrid'
  confidence:      number
  degraded:        boolean
  options_active:  string[]
  options_offline: string[]
  model_version:   string
}

// ─── #12 Expiry types ─────────────────────────────────────────────────────────

interface LotRisk {
  lot_id: string; ndc11: string; drug_name: string; lot_number: string
  expiry_date: string; quantity_on_hand: number; unit_cost: number
  daily_dispense_rate: number; days_to_expiry: number
  days_to_depletion: number | null; projected_waste_qty: number
  waste_value: number; risk_score: number; risk_band: string
  recommended_action: string; rationale: string; is_controlled: boolean
  return_window_days?: number | null; return_eligible?: boolean | null
}
interface ExpiryResult {
  at_risk: LotRisk[]
  summary: { at_risk_lots: number; critical_lots: number; total_waste_value: number; lots_evaluated: number }
}

const DEMO_EXPIRY: Envelope<ExpiryResult> = {
  tier_used: 'local', confidence: 0.82, degraded: true,
  options_active: ['local_survival_model', 'fefo_ordering'],
  options_offline: ['wholesaler_returns', 'branch_transfer'],
  model_version: 'expiry_prevention_v1',
  result: {
    summary: { at_risk_lots: 3, critical_lots: 1, total_waste_value: 412.5, lots_evaluated: 142 },
    at_risk: [
      { lot_id:'l1', ndc11:'00071015423', drug_name:'Metoprolol 50mg', lot_number:'LOT7742',
        expiry_date:'2026-07-08', quantity_on_hand:240, unit_cost:0.42, daily_dispense_rate:3.2,
        days_to_expiry:32, days_to_depletion:75, projected_waste_qty:138, waste_value:57.96,
        risk_band:'critical', risk_score:0.78, recommended_action:'dispense_first',
        rationale:'At 3.2/day depletes in ~75d but expires in 32d. Dispense first (FEFO); return ~138 units.',
        is_controlled:false },
      { lot_id:'l2', ndc11:'00185064001', drug_name:'Amlodipine 5mg', lot_number:'LOT5521',
        expiry_date:'2026-08-15', quantity_on_hand:90, unit_cost:0.18, daily_dispense_rate:0.4,
        days_to_expiry:70, days_to_depletion:225, projected_waste_qty:62, waste_value:11.16,
        risk_band:'warning', risk_score:0.44, recommended_action:'dispense_first',
        rationale:'Trending toward partial expiry — prioritise this lot.', is_controlled:false },
      { lot_id:'l3', ndc11:'00781100101', drug_name:'Cefuroxime 250mg', lot_number:'LOT3390',
        expiry_date:'2026-06-22', quantity_on_hand:60, unit_cost:1.95, daily_dispense_rate:0,
        days_to_expiry:16, days_to_depletion:null, projected_waste_qty:60, waste_value:117.0,
        risk_band:'critical', risk_score:0.92, recommended_action:'return',
        rationale:'No dispensing activity — return surplus before expiry.', is_controlled:false },
    ],
  },
}

// ─── #17 Supply types ─────────────────────────────────────────────────────────

interface SupplyRisk {
  ndc11: string; drug_name: string; fill_rate: number; fill_rate_trend: string
  cusum_signal: number; on_hand: number; avg_daily_demand: number
  days_of_stock: number | null; disruption_risk: number; risk_band: string
  suggested_buffer_qty: number; rationale: string; is_controlled: boolean
  national_shortage?: boolean | null; recall_surge?: boolean | null
}
interface SupplyResult {
  at_risk: SupplyRisk[]
  summary: { at_risk_ndcs: number; critical_ndcs: number; ndcs_evaluated: number }
}

const DEMO_SUPPLY: Envelope<SupplyResult> = {
  tier_used: 'local', confidence: 0.70, degraded: true,
  options_active: ['local_fillrate_cusum', 'stock_pressure'],
  options_offline: ['national_shortage_feed', 'recall_surge', 'alternatives'],
  model_version: 'supply_warning_v1',
  result: {
    summary: { at_risk_ndcs: 2, critical_ndcs: 1, ndcs_evaluated: 88 },
    at_risk: [
      { ndc11:'00093721098', drug_name:'Amoxicillin 500mg', fill_rate:0.62, fill_rate_trend:'declining',
        cusum_signal:0.41, on_hand:120, avg_daily_demand:14, days_of_stock:8.6, disruption_risk:0.74,
        risk_band:'critical', suggested_buffer_qty:196,
        rationale:'Supply pressure: wholesaler fill-rate 62%, fill-rate declining, only 9d of stock.',
        is_controlled:false },
      { ndc11:'00054852199', drug_name:'Prednisone 20mg', fill_rate:0.85, fill_rate_trend:'stable',
        cusum_signal:0.12, on_hand:60, avg_daily_demand:5, days_of_stock:12, disruption_risk:0.42,
        risk_band:'warning', suggested_buffer_qty:70,
        rationale:'Supply pressure: wholesaler fill-rate 85%, only 12d of stock.', is_controlled:false },
    ],
  },
}

// ─── Shared bits ──────────────────────────────────────────────────────────────

const BAND_COLOR: Record<string, string> = {
  critical: 'text-red-400 bg-red-950/40 border-red-900',
  warning:  'text-amber-400 bg-amber-950/40 border-amber-900',
  ok:       'text-emerald-400 bg-emerald-950/40 border-emerald-900',
}

function RiskBar({ value, band }: { value: number; band: string }) {
  const pct = Math.round(value * 100)
  const col = band === 'critical' ? 'bg-red-500' : band === 'warning' ? 'bg-amber-500' : 'bg-emerald-500'
  return (
    <div className="flex items-center gap-2 w-full">
      <div className="flex-1 h-1.5 bg-slate-800 rounded-full overflow-hidden">
        <div className={`h-full ${col} rounded-full`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] text-slate-500 w-7 text-right">{pct}%</span>
    </div>
  )
}

// ─── #12 Expiry Risk Panel ────────────────────────────────────────────────────

export function ExpiryRiskPanel() {
  const { data: raw } = useQuery({
    queryKey: ['intel-expiry-risk'],
    queryFn:  () => apiClient.get('/intelligence/inventory/expiry-risk')
                    .then(r => r.data as Envelope<ExpiryResult>),
    refetchInterval: 300_000,
  })
  const env = raw ?? DEMO_EXPIRY
  const { result: d } = env

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-slate-200">⏳ Expiry Waste Prevention</h2>
          <TierBadge tier={env.tier_used} degraded={env.degraded} optionsOffline={env.options_offline} />
        </div>
        <div className="text-right">
          <div className="text-lg font-bold text-red-400">${d.summary.total_waste_value.toFixed(2)}</div>
          <div className="text-[10px] text-slate-500">projected waste · {d.summary.at_risk_lots} lots</div>
        </div>
      </div>

      <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
        {d.at_risk.length === 0 && (
          <p className="text-xs text-slate-500 py-4 text-center">No lots at risk in the horizon ✓</p>
        )}
        {d.at_risk.map(lot => (
          <div key={lot.lot_id} className={`rounded-lg border px-3 py-2 ${BAND_COLOR[lot.risk_band] || BAND_COLOR.ok}`}>
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-slate-100">{lot.drug_name}</span>
              <span className="text-[10px] font-mono text-slate-400">{lot.lot_number}</span>
            </div>
            <div className="flex items-center gap-3 mt-1 text-[11px] text-slate-400">
              <span>exp {lot.expiry_date}</span>
              <span className="text-slate-600">·</span>
              <span>{lot.days_to_expiry}d left</span>
              <span className="text-slate-600">·</span>
              <span>{lot.quantity_on_hand} on hand</span>
              <span className="text-slate-600">·</span>
              <span className="text-red-300">~{lot.projected_waste_qty} waste (${lot.waste_value.toFixed(2)})</span>
            </div>
            <div className="mt-1.5"><RiskBar value={lot.risk_score} band={lot.risk_band} /></div>
            <div className="flex items-center justify-between mt-1.5">
              <span className="text-[11px] text-slate-400">{lot.rationale}</span>
              <span className={`text-[10px] px-2 py-0.5 rounded-full ml-2 whitespace-nowrap ${
                lot.recommended_action === 'return' ? 'bg-blue-900/50 text-blue-300'
                : lot.recommended_action === 'dispense_first' ? 'bg-purple-900/50 text-purple-300'
                : 'bg-slate-800 text-slate-400'}`}>
                {lot.recommended_action === 'dispense_first' ? '↑ FEFO dispense'
                  : lot.recommended_action === 'return' ? '↩ return' : 'monitor'}
              </span>
            </div>
          </div>
        ))}
      </div>
      <DegradedNote optionsOffline={env.degraded ? env.options_offline : undefined} />
    </div>
  )
}

// ─── #17 Supply Risk Panel ────────────────────────────────────────────────────

export function SupplyRiskPanel() {
  const { data: raw } = useQuery({
    queryKey: ['intel-supply-risk'],
    queryFn:  () => apiClient.get('/intelligence/inventory/supply-risk')
                    .then(r => r.data as Envelope<SupplyResult>),
    refetchInterval: 300_000,
  })
  const bufferMutation = useMutation({
    mutationFn: (v: { ndc11: string; quantity: number }) =>
      apiClient.post('/intelligence/inventory/queue-buffer-order', null, { params: v })
        .then(r => r.data),
  })

  const env = raw ?? DEMO_SUPPLY
  const { result: d } = env

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-slate-200">🚚 Supply-Chain Early Warning</h2>
          <TierBadge tier={env.tier_used} degraded={env.degraded} optionsOffline={env.options_offline} />
        </div>
        <div className="text-right">
          <div className="text-lg font-bold text-amber-400">{d.summary.at_risk_ndcs}</div>
          <div className="text-[10px] text-slate-500">at-risk NDCs · {d.summary.critical_ndcs} critical</div>
        </div>
      </div>

      <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
        {d.at_risk.length === 0 && (
          <p className="text-xs text-slate-500 py-4 text-center">No supply disruptions detected ✓</p>
        )}
        {d.at_risk.map(s => (
          <div key={s.ndc11} className={`rounded-lg border px-3 py-2 ${BAND_COLOR[s.risk_band] || BAND_COLOR.ok}`}>
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-slate-100">{s.drug_name}</span>
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                s.fill_rate_trend === 'declining' ? 'bg-red-900/50 text-red-300' : 'bg-slate-800 text-slate-400'}`}>
                fill {Math.round(s.fill_rate * 100)}% {s.fill_rate_trend === 'declining' ? '↓' : ''}
              </span>
            </div>
            <div className="flex items-center gap-3 mt-1 text-[11px] text-slate-400">
              <span>{s.on_hand} on hand</span>
              <span className="text-slate-600">·</span>
              <span>{s.avg_daily_demand}/day</span>
              {s.days_of_stock != null && (<><span className="text-slate-600">·</span>
                <span className={s.days_of_stock < 14 ? 'text-amber-300' : ''}>{s.days_of_stock}d stock</span></>)}
            </div>
            <div className="mt-1.5"><RiskBar value={s.disruption_risk} band={s.risk_band} /></div>
            <div className="flex items-center justify-between mt-1.5">
              <span className="text-[11px] text-slate-400">{s.rationale}</span>
              <button
                onClick={() => bufferMutation.mutate({ ndc11: s.ndc11, quantity: s.suggested_buffer_qty })}
                disabled={bufferMutation.isPending}
                className="text-[10px] px-2 py-0.5 rounded-full ml-2 whitespace-nowrap bg-blue-900/50 text-blue-300 hover:bg-blue-800/50 disabled:opacity-50">
                + buffer {s.suggested_buffer_qty}
              </button>
            </div>
          </div>
        ))}
      </div>
      {bufferMutation.isSuccess && (
        <p className="text-[11px] text-emerald-400 mt-1">
          {bufferMutation.data?.submitted_now ? '✓ Buffer order submitted.' : '⧖ Buffer order queued — will submit when online.'}
        </p>
      )}
      <DegradedNote optionsOffline={env.degraded ? env.options_offline : undefined} />
    </div>
  )
}
