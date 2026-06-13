/**
 * MarginOptimizerPanel — #19 Financial Intelligence & Margin Optimization
 * ========================================================================
 * Drops into FinancialOperations. Shows below-cost fills, generic-substitution
 * upside, and DIR exposure projection from the §1.2 intelligence envelope.
 * Local-complete on cached pricing; cloud-enriched online.
 */
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../lib/api'
import TierBadge, { DegradedNote } from './TierBadge'

interface Envelope<T> {
  result: T; tier_used: 'local' | 'cloud' | 'hybrid'; confidence: number
  degraded: boolean; options_active: string[]; options_offline: string[]; model_version: string
}

interface BelowCost {
  claim_id: string; ndc: string; drug_name: string; quantity: number
  amount_paid: number; acquisition_cost: number; margin: number; bin_number: string
}
interface Substitution {
  brand_ndc: string; brand_name: string; brand_margin: number
  generic_ndc: string; generic_name: string; generic_margin: number
  margin_uplift: number; patient_cost_delta: number; fills_in_window: number
  projected_monthly_uplift: number; rationale: string
}
interface MarginResult {
  summary: {
    claims_evaluated: number; total_margin: number; total_revenue: number; margin_pct: number
    below_cost_count: number; below_cost_loss: number; substitution_opportunities: number
    projected_monthly_uplift: number; dir_exposure_window: number
    dir_exposure_quarterly: number; prices_as_of: string | null
  }
  below_cost: BelowCost[]
  substitutions: Substitution[]
}

const DEMO: Envelope<MarginResult> = {
  tier_used: 'local', confidence: 0.80, degraded: true,
  options_active: ['local_margin', 'generic_optimizer', 'dir_projection'],
  options_offline: ['live_pricing', 'live_mac', 'benchmarks'],
  model_version: 'margin_optimizer_v1',
  result: {
    summary: {
      claims_evaluated: 1284, total_margin: 8420.55, total_revenue: 61200.0, margin_pct: 13.76,
      below_cost_count: 14, below_cost_loss: -312.40, substitution_opportunities: 6,
      projected_monthly_uplift: 1840.20, dir_exposure_window: 1420.0,
      dir_exposure_quarterly: 4260.0, prices_as_of: '2026-05-30',
    },
    below_cost: [
      { claim_id:'c1', ndc:'00071015423', drug_name:'Rosuvastatin 20mg', quantity:30, amount_paid:14.20, acquisition_cost:32.60, margin:-18.40, bin_number:'610502' },
      { claim_id:'c2', ndc:'00185064001', drug_name:'Duloxetine 60mg', quantity:30, amount_paid:9.10, acquisition_cost:21.30, margin:-12.20, bin_number:'004336' },
      { claim_id:'c3', ndc:'00781100101', drug_name:'Pantoprazole 40mg', quantity:90, amount_paid:11.05, acquisition_cost:18.90, margin:-7.85, bin_number:'610502' },
    ],
    substitutions: [
      { brand_ndc:'00071015423', brand_name:'Crestor 20mg', brand_margin:2.10, generic_ndc:'00378395293',
        generic_name:'Rosuvastatin 20mg', generic_margin:18.40, margin_uplift:16.30, patient_cost_delta:-13.50,
        fills_in_window:23, projected_monthly_uplift:1249.5,
        rationale:'23 brand fills in 90d. Switching to Rosuvastatin adds ~$16.30/fill and lowers patient cost ~$13.50.' },
      { brand_ndc:'00002314030', brand_name:'Cymbalta 60mg', brand_margin:3.40, generic_ndc:'00378618893',
        generic_name:'Duloxetine 60mg', generic_margin:14.90, margin_uplift:11.50, patient_cost_delta:-9.80,
        fills_in_window:11, projected_monthly_uplift:421.7,
        rationale:'11 brand fills in 90d. Switching to Duloxetine adds ~$11.50/fill and lowers patient cost ~$9.80.' },
    ],
  },
}

function Money({ v, className = '' }: { v: number; className?: string }) {
  const neg = v < 0
  return <span className={`tabular-nums ${neg ? 'text-red-400' : ''} ${className}`}>
    {neg ? '-' : ''}${Math.abs(v).toFixed(2)}</span>
}

export default function MarginOptimizerPanel() {
  const { data: raw } = useQuery({
    queryKey: ['intel-margin-insights'],
    queryFn:  () => apiClient.get('/intelligence/finance/margin-insights')
                    .then(r => r.data as Envelope<MarginResult>),
    refetchInterval: 300_000,
  })
  const env = raw ?? DEMO
  const { result: d } = env

  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-slate-200">💹 Margin Optimizer</h2>
          <TierBadge tier={env.tier_used} degraded={env.degraded} optionsOffline={env.options_offline} />
        </div>
        {d.summary.prices_as_of && (
          <span className="text-[10px] text-slate-500">prices as of {d.summary.prices_as_of}</span>
        )}
      </div>

      {/* KPI strip */}
      <div className="grid grid-cols-4 gap-2 mb-4">
        <Kpi label="Gross margin" value={`$${(d.summary.total_margin/1000).toFixed(1)}K`} sub={`${d.summary.margin_pct}%`} color="#22c55e" />
        <Kpi label="Below-cost" value={`${d.summary.below_cost_count}`} sub={`$${Math.abs(d.summary.below_cost_loss).toFixed(0)} loss`} color="#ef4444" />
        <Kpi label="Sub. upside/mo" value={`$${(d.summary.projected_monthly_uplift/1000).toFixed(1)}K`} sub={`${d.summary.substitution_opportunities} drugs`} color="#a855f7" />
        <Kpi label="DIR /qtr" value={`$${(d.summary.dir_exposure_quarterly/1000).toFixed(1)}K`} sub="projected" color="#f59e0b" />
      </div>

      <div className="grid grid-cols-2 gap-4">
        {/* Below-cost fills */}
        <div>
          <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500 mb-2">⚠ Below-cost fills</h3>
          <div className="space-y-1.5 max-h-56 overflow-y-auto pr-1">
            {d.below_cost.length === 0 && <p className="text-xs text-slate-600">None — all fills profitable ✓</p>}
            {d.below_cost.map(b => (
              <div key={b.claim_id} className="flex items-center justify-between rounded-lg border border-red-950 bg-red-950/30 px-2.5 py-1.5">
                <div className="min-w-0">
                  <div className="text-xs text-slate-200 truncate">{b.drug_name}</div>
                  <div className="text-[10px] text-slate-500">qty {b.quantity} · BIN {b.bin_number} · paid <Money v={b.amount_paid} /> vs cost <Money v={b.acquisition_cost} /></div>
                </div>
                <Money v={b.margin} className="text-sm font-semibold" />
              </div>
            ))}
          </div>
        </div>

        {/* Substitution opportunities */}
        <div>
          <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500 mb-2">↑ Generic substitution upside</h3>
          <div className="space-y-1.5 max-h-56 overflow-y-auto pr-1">
            {d.substitutions.length === 0 && <p className="text-xs text-slate-600">No brand-to-generic opportunities found</p>}
            {d.substitutions.map(s => (
              <div key={s.brand_ndc} className="rounded-lg border border-purple-950 bg-purple-950/30 px-2.5 py-1.5">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-slate-200 truncate">{s.brand_name} → {s.generic_name}</span>
                  <span className="text-sm font-semibold text-emerald-400 whitespace-nowrap ml-2">+<Money v={s.margin_uplift} />/fill</span>
                </div>
                <div className="flex items-center justify-between mt-0.5 text-[10px] text-slate-500">
                  <span>{s.fills_in_window} fills · patient saves <Money v={Math.abs(s.patient_cost_delta)} /></span>
                  <span className="text-purple-300">~${s.projected_monthly_uplift.toFixed(0)}/mo</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
      <DegradedNote optionsOffline={env.degraded ? env.options_offline : undefined} />
    </div>
  )
}

function Kpi({ label, value, sub, color }: { label: string; value: string; sub: string; color: string }) {
  return (
    <div className="bg-slate-900/50 rounded-lg px-2.5 py-2 border border-slate-800">
      <p className="text-[9px] font-semibold uppercase tracking-widest text-slate-500">{label}</p>
      <p className="text-lg font-bold tabular-nums" style={{ color }}>{value}</p>
      <p className="text-[10px] text-slate-500">{sub}</p>
    </div>
  )
}
