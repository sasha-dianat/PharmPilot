/**
 * ProcurementPanel — Procurement Recommendations
 * ==============================================
 * Drops into the InventoryIntelligence board. Consumes the
 * GET /intelligence/inventory/procurement-recommendations endpoint and renders
 * urgency-banded reorder cards. Falls back to DEMO_PROCUREMENT so it renders
 * without a backend.
 *
 * Dark slate theme to match IntelligenceInventoryPanels.
 */
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../lib/api'

// ─── Types ────────────────────────────────────────────────────────────────────

type Urgency = 'critical' | 'high' | 'normal'

interface ProcurementRec {
  ndc11: string
  on_hand: number
  avg_daily_demand: number
  days_of_stock: number | null
  reorder_point: number
  lead_time_days: number
  recommended_order_qty: number
  order_by_date: string | null // "YYYY-MM-DD"
  urgency: Urgency
  est_cost: number
  rationale: string
}

interface ProcurementSummary {
  total_skus: number
  total_est_cost: number
  critical_count: number
  budget: number | null
  within_budget: boolean
}

interface ProcurementResult {
  recommendations: ProcurementRec[]
  summary: ProcurementSummary
}

// ─── Demo fallback ────────────────────────────────────────────────────────────

const DEMO_PROCUREMENT: ProcurementResult = {
  summary: { total_skus: 4, total_est_cost: 1240.32, critical_count: 1, budget: 2500, within_budget: true },
  recommendations: [
    { ndc11: '00093721098', on_hand: 120, avg_daily_demand: 14, days_of_stock: 8.6,
      reorder_point: 210, lead_time_days: 5, recommended_order_qty: 196, order_by_date: '2026-06-18',
      urgency: 'critical', est_cost: 612.5,
      rationale: 'Below reorder point with 9d stock vs 5d lead time — order now to avoid stockout.' },
    { ndc11: '00071015423', on_hand: 240, avg_daily_demand: 3.2, days_of_stock: 75,
      reorder_point: 64, lead_time_days: 7, recommended_order_qty: 120, order_by_date: '2026-07-02',
      urgency: 'high', est_cost: 50.4,
      rationale: 'Approaching reorder point; demand steady at 3.2/day. Replenish within two weeks.' },
    { ndc11: '00054852199', on_hand: 60, avg_daily_demand: 5, days_of_stock: 12,
      reorder_point: 70, lead_time_days: 4, recommended_order_qty: 90, order_by_date: '2026-06-22',
      urgency: 'high', est_cost: 446.4,
      rationale: 'Stock dips under reorder point in ~3d; 4d lead time leaves little slack.' },
    { ndc11: '00185064001', on_hand: 90, avg_daily_demand: 0.4, days_of_stock: 225,
      reorder_point: 6, lead_time_days: 6, recommended_order_qty: 40, order_by_date: null,
      urgency: 'normal', est_cost: 131.02,
      rationale: 'Comfortable cover; top-up batch to consolidate a wholesaler order.' },
  ],
}

// ─── Urgency styling ──────────────────────────────────────────────────────────

const URGENCY_COLOR: Record<Urgency, string> = {
  critical: 'text-red-400 bg-red-950/40 border-red-900',
  high:     'text-amber-400 bg-amber-950/40 border-amber-900',
  normal:   'text-emerald-400 bg-emerald-950/40 border-emerald-900',
}

const URGENCY_CHIP: Record<Urgency, string> = {
  critical: 'bg-red-900/50 text-red-300',
  high:     'bg-amber-900/50 text-amber-300',
  normal:   'bg-slate-800 text-slate-400',
}

// ─── Panel ────────────────────────────────────────────────────────────────────

export function ProcurementPanel() {
  const { data } = useQuery({
    queryKey: ['intel-procurement'],
    queryFn:  () => apiClient.get('/intelligence/inventory/recommendations')
                    .then(r => r.data as ProcurementResult),
    refetchInterval: 300_000,
  })
  const d = data ?? DEMO_PROCUREMENT
  const { recommendations: recs, summary } = d

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-slate-200">🛒 Procurement Recommendations</h2>
        <div className="text-right">
          <div className="text-sm font-bold text-slate-100">
            ${Math.round(summary.total_est_cost).toLocaleString()} · {summary.total_skus} SKUs
          </div>
          <div className="text-[10px] text-slate-500">
            {summary.critical_count > 0
              ? <span className="text-red-400">{summary.critical_count} critical</span>
              : <span>no critical orders</span>}
            {summary.budget != null && (
              <span className={summary.within_budget ? ' text-emerald-400' : ' text-red-400'}>
                {' '}· {summary.within_budget ? 'within budget' : 'over budget'}
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
        {recs.length === 0 && (
          <p className="text-xs text-slate-500 py-4 text-center">Nothing to reorder ✓</p>
        )}
        {recs.map(r => (
          <div key={r.ndc11} className={`rounded-lg border px-3 py-2 ${URGENCY_COLOR[r.urgency] || URGENCY_COLOR.normal}`}>
            <div className="flex items-center justify-between">
              <span className="font-mono text-[11px] text-slate-300">{r.ndc11}</span>
              <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${URGENCY_CHIP[r.urgency] || URGENCY_CHIP.normal}`}>
                {r.urgency}
              </span>
            </div>
            <div className="flex items-center gap-3 mt-1.5 text-[11px] text-slate-400">
              <span className="text-sm font-bold text-slate-100">order {r.recommended_order_qty}u</span>
              <span className="text-slate-600">·</span>
              <span>${r.est_cost.toFixed(2)}</span>
              <span className="text-slate-600">·</span>
              <span className={r.urgency === 'critical' ? 'text-red-300' : ''}>
                by {r.order_by_date ?? '—'}
              </span>
            </div>
            <div className="flex items-center gap-3 mt-1 text-[10px] text-slate-500">
              <span>{r.on_hand}u on hand</span>
              <span className="text-slate-600">·</span>
              <span>{r.avg_daily_demand}/day</span>
              <span className="text-slate-600">·</span>
              <span className={r.days_of_stock != null && r.days_of_stock < r.lead_time_days ? 'text-amber-300' : ''}>
                {r.days_of_stock != null ? `${r.days_of_stock}d` : '—'} stock vs {r.lead_time_days}d lead
              </span>
            </div>
            <p className="text-[11px] text-slate-400 mt-1.5">{r.rationale}</p>
          </div>
        ))}
      </div>
    </div>
  )
}
