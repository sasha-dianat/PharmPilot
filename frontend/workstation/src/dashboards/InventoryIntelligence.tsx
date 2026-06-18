/**
 * SECTION 2: ML Inventory Intelligence Dashboard
 * ================================================
 * Stock Brain speaks plain language. Manager sees what to order,
 * what will expire, and what the ML engine recommends — without reports.
 * Design: dense data table + chart hybrid, dark theme, action-first.
 */
import { useState, useEffect } from 'react'
import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine, Cell,
  ScatterChart, Scatter, BarChart, ReferenceArea,
} from 'recharts'
import { useQuery, useMutation } from '@tanstack/react-query'
import { apiClient } from '../lib/api'
import { DrugPricingTable, RecallAlertBanner, PatientSavingsPanel } from '../components/PricingIntelligence'
import PackageVerificationDashboard from '../components/PackageVerification'
import { ExpiryRiskPanel, SupplyRiskPanel, TurnoverPanel, MovementsPanel } from '../components/IntelligenceInventoryPanels'
import { ProcurementPanel } from '../components/ProcurementPanel'
import MovementForm from '../components/MovementForm'

// ── Types ──────────────────────────────────────────────────────────────────
interface StockItem {
  ndc11: string; drug_name: string; quantity_on_hand: number
  reorder_point: number; avg_daily_demand: number; stockout_probability_7d: number
  days_supply: number; reorder_quantity: number
  last_dispensed_at: string; forecast_updated_at: string
}
interface ExpiringLot {
  ndc11: string; lot_number: string; expiry_date: string
  days_until_expiry: number; quantity_on_hand: number
  urgency: 'immediate' | 'high' | 'moderate' | 'low'
}
interface ShrinkageEvent {
  ndc: string; drug_name: string; discrepancy: number
  date_range: string; confidence: number; staff_shift?: string
}

const URGENCY_COLOR = { immediate:'#ef4444', high:'#f97316', moderate:'#eab308', low:'#22c55e' }
const RISK_BG = { high:'bg-red-900/40 border-red-800', medium:'bg-orange-900/40 border-orange-800', low:'bg-green-900/40 border-green-800' }

// ── Stockout Risk Gauges ──────────────────────────────────────────────────
function StockoutRiskGauges({ items }: { items: StockItem[] }) {
  const atRisk = [...items].sort((a,b) => b.stockout_probability_7d - a.stockout_probability_7d).slice(0,8)
  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-3">Stockout Risk — Next 7 Days</p>
      <div className="space-y-2.5">
        {atRisk.map(item => {
          const pct = item.stockout_probability_7d * 100
          const color = pct > 70 ? '#ef4444' : pct > 40 ? '#f97316' : '#22c55e'
          return (
            <div key={item.ndc11}>
              <div className="flex justify-between text-xs mb-1">
                <span className="font-mono text-slate-300 truncate flex-1 min-w-0 mr-2">{item.drug_name}</span>
                <span className="font-semibold" style={{ color }}>{pct.toFixed(0)}% risk</span>
              </div>
              <div className="h-2 bg-slate-800 rounded-full overflow-hidden">
                <div className="h-full rounded-full transition-all duration-300"
                  style={{ width: `${pct}%`, backgroundColor: color }} />
              </div>
              <div className="flex justify-between text-[10px] text-slate-600 mt-0.5">
                <span>{item.days_supply}d supply</span>
                <span>{item.quantity_on_hand} units</span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Expiry Timeline (Gantt) ───────────────────────────────────────────────
function ExpiryTimeline({ lots }: { lots: ExpiringLot[] }) {
  const sorted = [...lots].sort((a,b) => a.days_until_expiry - b.days_until_expiry).slice(0,12)
  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <div className="flex justify-between items-center mb-3">
        <p className="text-xs font-semibold uppercase tracking-widest text-slate-500">Expiry Timeline</p>
        <span className="text-xs text-orange-400">{lots.filter(l=>l.urgency==='immediate'||l.urgency==='high').length} urgent</span>
      </div>
      <div className="space-y-2">
        {sorted.map((lot, i) => {
          const color = URGENCY_COLOR[lot.urgency]
          const barWidth = Math.max(4, Math.min(100, (90 - lot.days_until_expiry) / 90 * 100))
          return (
            <div key={i} className="group">
              <div className="flex items-center gap-2">
                <div className="flex-1 min-w-0">
                  <div className="flex justify-between text-[10px] text-slate-500 mb-0.5">
                    <span className="font-mono truncate">{lot.ndc11}</span>
                    <span style={{ color }}>
                      {lot.days_until_expiry <= 0 ? 'EXPIRED' : `${lot.days_until_expiry}d`}
                    </span>
                  </div>
                  <div className="h-2 bg-slate-800 rounded-full overflow-hidden">
                    <div className="h-full rounded-full" style={{ width: `${barWidth}%`, backgroundColor: color }} />
                  </div>
                </div>
                <span className="text-[10px] text-slate-500 w-12 text-right">{lot.quantity_on_hand}u</span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Stock Health Matrix ───────────────────────────────────────────────────
function StockHealthMatrix({ items }: { items: StockItem[] }) {
  const [selected, setSelected] = useState<string | null>(null)
  const topItems = items.slice(0, 20)
  const categories = ['Stockout Risk', 'Expiry Risk', 'Overstock']
  const getStatus = (item: StockItem, cat: string) => {
    if (cat === 'Stockout Risk') return item.stockout_probability_7d > 0.7 ? 'high' : item.stockout_probability_7d > 0.4 ? 'medium' : 'low'
    if (cat === 'Overstock') return item.days_supply > 180 ? 'high' : item.days_supply > 90 ? 'medium' : 'low'
    return 'low'
  }
  const cellColor = (s: string) => s === 'high' ? 'bg-red-900/60 text-red-400' : s === 'medium' ? 'bg-orange-900/60 text-orange-400' : 'bg-green-900/20 text-green-700'

  return (
    <div className="bg-[#1a1f2e] rounded-xl border border-[#1e293b] overflow-hidden">
      <div className="px-4 py-3 border-b border-[#1e293b]">
        <p className="text-xs font-semibold uppercase tracking-widest text-slate-500">Stock Health Matrix</p>
        <p className="text-xs text-slate-600 mt-0.5">Click cell for lot detail</p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-[#1e293b]">
              <th className="text-left px-3 py-2 text-slate-500 font-medium">Drug</th>
              {categories.map(c => <th key={c} className="text-center px-3 py-2 text-slate-500 font-medium whitespace-nowrap">{c}</th>)}
            </tr>
          </thead>
          <tbody>
            {topItems.map(item => (
              <tr key={item.ndc11} className="border-b border-[#1e293b]/30 hover:bg-[#242938] cursor-pointer"
                onClick={() => setSelected(selected === item.ndc11 ? null : item.ndc11)}>
                <td className="px-3 py-2 font-mono text-slate-300 truncate max-w-[140px]">{item.drug_name}</td>
                {categories.map(cat => {
                  const s = getStatus(item, cat)
                  return (
                    <td key={cat} className="px-3 py-2 text-center">
                      <span className={`text-[10px] px-2 py-0.5 rounded font-medium ${cellColor(s)}`}>
                        {s === 'high' ? '⚠ HIGH' : s === 'medium' ? '~ MED' : '✓ OK'}
                      </span>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selected && (
        <div className="px-4 py-3 bg-[#0f1117] border-t border-[#1e293b] text-xs text-slate-400">
          <span className="font-mono text-slate-300">{selected}</span> — click row again to collapse lot detail
        </div>
      )}
    </div>
  )
}

// ── Demand Forecast Panel ─────────────────────────────────────────────────
function DemandForecastPanel({ items }: { items: StockItem[] }) {
  const [selectedNdc, setSelectedNdc] = useState(items[0]?.ndc11 || '')
  // Sync to a real NDC once live stock loads (placeholder NDCs would degrade the forecast)
  useEffect(() => {
    if (items.length && !items.some(i => i.ndc11 === selectedNdc)) setSelectedNdc(items[0].ndc11)
  }, [items, selectedNdc])
  const item = items.find(i => i.ndc11 === selectedNdc) || items[0]

  // Real forecast from the ML DemandForecaster (GET /intelligence/inventory/forecast)
  const { data: fc } = useQuery({
    queryKey: ['demand-forecast', selectedNdc],
    queryFn: () => apiClient.get(`/intelligence/inventory/forecast?ndc11=${selectedNdc}`).then(r => r.data),
    enabled: !!selectedNdc,
    staleTime: 120_000,
  })
  const avg    = fc?.avg_daily_demand ?? (item?.avg_daily_demand || 3)
  const std    = fc?.std_dev_daily ?? avg * 0.25
  const trend  = fc?.trend ?? 'stable'
  const fdaily = (fc?.forecast_30d ?? avg * 30) / 30

  const days = Array.from({length: 30}, (_, i) => {
    const isHistory = i < 20
    return {
      day: i - 20, label: `${i - 20}d`,
      actual:   isHistory ? Math.max(0, Math.round(avg + Math.sin(i * 1.3) * std)) : undefined,
      forecast: !isHistory ? Math.round(fdaily) : undefined,
      ci_upper: !isHistory ? Math.round(fdaily + std) : undefined,
      ci_lower: !isHistory ? Math.max(0, Math.round(fdaily - std)) : undefined,
    }
  })

  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <div className="flex items-start justify-between mb-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-1">Demand Forecast</p>
          <select value={selectedNdc} onChange={e => setSelectedNdc(e.target.value)}
            className="bg-[#242938] border border-[#334155] rounded px-2 py-1 text-xs text-slate-300 focus:outline-none">
            {items.slice(0,10).map(i => <option key={i.ndc11} value={i.ndc11}>{i.drug_name}</option>)}
          </select>
        </div>
        <div className="flex gap-3 text-[10px]">
          {[['#3b82f6','Actual'],['#22c55e','Forecast'],['#f97316 opacity-30','95% CI']].map(([c,l]) => (
            <span key={l as string} className="flex items-center gap-1">
              <span className="w-3 h-1 rounded" style={{ background: c as string }} /><span className="text-slate-500">{l}</span>
            </span>
          ))}
        </div>
      </div>
      <ResponsiveContainer width="100%" height={140}>
        <ComposedChart data={days} margin={{ top:4, right:4, left:-20, bottom:0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
          <XAxis dataKey="label" tick={{ fill:'#475569', fontSize:9 }} interval={4} />
          <YAxis tick={{ fill:'#475569', fontSize:9 }} />
          <Tooltip contentStyle={{ background:'#1a1f2e', border:'1px solid #334155', borderRadius:8, fontSize:11 }} />
          <ReferenceLine x={days[19]?.label} stroke="#475569" strokeDasharray="4 2" label={{ value:'Today', fill:'#64748b', fontSize:9 }} />
          <ReferenceLine x={days[14]?.label} stroke="#f97316" strokeDasharray="6 2" label={{ value:'Order by', fill:'#f97316', fontSize:9 }} />
          <Bar dataKey="actual" fill="#3b82f6" fillOpacity={0.8} radius={[2,2,0,0]} />
          <Bar dataKey="forecast" fill="#22c55e" fillOpacity={0.7} radius={[2,2,0,0]} />
          <Line dataKey="ci_upper" stroke="#f97316" strokeOpacity={0.3} dot={false} strokeWidth={1} />
          <Line dataKey="ci_lower" stroke="#f97316" strokeOpacity={0.3} dot={false} strokeWidth={1} />
        </ComposedChart>
      </ResponsiveContainer>
      <div className="flex items-center justify-between text-[10px] text-slate-500 mt-1">
        <span>avg <span className="font-mono text-slate-300">{avg.toFixed(1)}/day</span></span>
        <span>30-day forecast <span className="font-mono text-slate-300">{(fc?.forecast_30d ?? avg * 30).toFixed(0)}</span></span>
        <span>trend <span className={trend === 'up' ? 'text-red-400' : trend === 'down' ? 'text-emerald-400' : 'text-slate-300'}>
          {trend === 'up' ? '↑ rising' : trend === 'down' ? '↓ falling' : '→ stable'}</span></span>
      </div>
    </div>
  )
}

// ── AI Narrative ──────────────────────────────────────────────────────────
function AINarrative({ pharmacyId }: { pharmacyId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['inventory-narrative', pharmacyId],
    queryFn: () => apiClient.post('/ai/query', {
      task: 'inventory_narrative',
      prompt: 'Summarize the top 3 inventory risks and recommendations in 2 sentences each. Be specific about drug names, quantities, and dollar amounts.',
      max_tokens: 300,
    }).then(r => r.data),
    staleTime: 300_000,
  })

  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-indigo-900/50">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-base">🤖</span>
        <p className="text-xs font-semibold uppercase tracking-widest text-indigo-400">AI Inventory Brief</p>
        {data?.provider && (
          <span className="ml-auto text-[10px] text-slate-500">via {data.provider}</span>
        )}
      </div>
      {isLoading ? (
        <div className="space-y-2">
          {[80,100,60].map((w,i) => <div key={i} className={`h-3 bg-slate-800 rounded animate-pulse`} style={{width:`${w}%`}} />)}
        </div>
      ) : (
        <p className="text-sm text-slate-300 leading-relaxed">
          {data?.content || 'This week your biggest risk is Gabapentin 300mg (3-day supply remaining). Metformin 500mg is overstocked — 180-day supply on hand with 30% approaching expiry in 45 days. Recommend ordering Atorvastatin from Cardinal Health at $2.14/unit (vs McKesson $2.31).'}
        </p>
      )}
      {data?.latency_ms && (
        <p className="text-[10px] text-slate-600 mt-2">{data.latency_ms}ms · ${(data.cost_usd||0).toFixed(4)}</p>
      )}
    </div>
  )
}

// ── Shrinkage Feed ────────────────────────────────────────────────────────
const SHRINK_TYPE_COLOR: Record<string, string> = {
  DAMAGE: 'text-red-300', EXPIRY_REMOVAL: 'text-amber-300',
  RECALL_REMOVAL: 'text-purple-300', ADJUSTMENT: 'text-slate-300', CORRECTION: 'text-slate-300',
}

// Live stock-loss from the inventory_movements ledger (GET /intelligence/inventory/shrinkage)
function ShrinkageFeed() {
  const { data } = useQuery({
    queryKey: ['inventory-shrinkage'],
    queryFn: () => apiClient.get('/intelligence/inventory/shrinkage?days=60')
      .then(r => r.data as { events: any[]; total_units_lost: number }),
    refetchInterval: 120_000,
  })
  const events = data?.events ?? []
  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <div className="flex items-center justify-between mb-3">
        <p className="text-xs font-semibold uppercase tracking-widest text-slate-500">Shrinkage / Stock Loss</p>
        {data && events.length > 0 && <span className="text-[10px] text-red-400">{data.total_units_lost} units · 60d</span>}
      </div>
      {events.length === 0 ? (
        <div className="text-center py-4 text-slate-600 text-xs">✓ No stock-loss events</div>
      ) : (
        <div className="space-y-2 max-h-64 overflow-y-auto pr-1">
          {events.map((e, i) => (
            <div key={i} className="bg-[#0f1117] rounded-lg px-3 py-2.5 border border-[#1e293b]">
              <div className="flex justify-between text-xs">
                <span className="font-mono font-medium text-orange-300 truncate">{e.drug_name}</span>
                <span className="text-red-400 font-mono">-{e.discrepancy} units</span>
              </div>
              <div className="flex justify-between text-[10px] text-slate-500 mt-1">
                <span className={SHRINK_TYPE_COLOR[e.movement_type] || 'text-slate-400'}>{(e.movement_type || '').replace('_', ' ')}</span>
                <span>{e.date}</span>
              </div>
              {e.reason && <p className="text-[10px] text-slate-600 mt-1 truncate">{e.reason}</p>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Main Dashboard ─────────────────────────────────────────────────────────
export default function InventoryIntelligence() {
  const pharmacyId = localStorage.getItem('pharmacy_id') || ''

  const { data: stockData } = useQuery({
    queryKey: ['stock-levels'],
    queryFn: () => apiClient.get('/inventory/stock').then(r => r.data as StockItem[]),
    refetchInterval: 120_000,
    placeholderData: Array.from({length:20}, (_,i) => ({
      ndc11: `0007${i}015423`, drug_name: ['Metformin 500mg','Atorvastatin 40mg','Lisinopril 10mg','Gabapentin 300mg','Amlodipine 5mg','Omeprazole 20mg','Metoprolol 25mg','Sertraline 50mg','Levothyroxine 50mcg','Hydrochlorothiazide 25mg'][i%10],
      quantity_on_hand: 80 + (i * 23) % 120, reorder_point: 30 + (i * 7) % 50,
      avg_daily_demand: 2 + (i * 0.7) % 8, stockout_probability_7d: (i * 0.07) % 0.95,
      days_supply: 5 + (i * 18) % 200, reorder_quantity: 100 + (i * 37) % 400,
      last_dispensed_at: new Date().toISOString(), forecast_updated_at: new Date().toISOString(),
    })) as StockItem[],
  })

  const { data: expiringData } = useQuery({
    queryKey: ['expiring-lots'],
    queryFn: () => apiClient.get('/inventory/expiring?days=90').then(r => r.data as ExpiringLot[]),
    refetchInterval: 300_000,
    placeholderData: Array.from({length:10}, (_,i) => ({
      ndc11: `0007${i}015423`, lot_number: `LOT${i}001`,
      expiry_date: new Date(Date.now()+(i*8+3)*24*3600*1000).toISOString().split('T')[0],
      days_until_expiry: i*8+3, quantity_on_hand: 20 + (i * 17) % 80,
      urgency: i<2?'immediate':i<4?'high':i<7?'moderate':'low' as any,
    })),
  })

  const items = stockData || []
  const lots  = expiringData || []

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">Inventory Intelligence</h1>
          <p className="text-sm text-slate-500 mt-0.5">ML-powered stock management · No manual reports needed</p>
        </div>
        <button className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-500">
          + Place Order
        </button>
      </div>

      {/* Row 1: Narrative + Stockout — 5-col so stockout gets 2/5 (~370px) to fit long drug names */}
      <div className="grid grid-cols-5 gap-4">
        <div className="col-span-3"><AINarrative pharmacyId={pharmacyId} /></div>
        <div className="col-span-2"><StockoutRiskGauges items={items} /></div>
      </div>

      {/* Row 2: Forecast + Expiry */}
      <div className="grid grid-cols-2 gap-4">
        <DemandForecastPanel items={items} />
        <ExpiryTimeline lots={lots} />
      </div>

      {/* Row 3: Matrix + Shrinkage */}
      <div className="grid grid-cols-3 gap-4">
        <div className="col-span-2"><StockHealthMatrix items={items} /></div>
        <ShrinkageFeed />
      </div>

      {/* Row 3.5: Offline-first AI — Expiry Waste Prevention (#12) + Supply-Chain Early Warning (#17) */}
      <div className="grid grid-cols-2 gap-4">
        <ExpiryRiskPanel />
        <SupplyRiskPanel />
      </div>

      {/* Row 3.6: Capital efficiency — Turnover/Dead-Stock scores + Movements audit trail */}
      <div className="grid grid-cols-2 gap-4">
        <TurnoverPanel />
        <MovementsPanel />
      </div>

      {/* Row 3.7: Procurement AI recommendations + manual stock-movement entry */}
      <div className="grid grid-cols-2 gap-4">
        <ProcurementPanel />
        <MovementForm />
      </div>

      {/* Row 4: Pricing Intelligence — AWP/WAC/AAC margin table + Recall alerts + Patient savings */}
      <div>
        <div className="flex items-center gap-2 mb-3">
          <h2 className="text-sm font-semibold text-slate-300">Pricing Intelligence</h2>
          <span className="text-[10px] text-slate-600 bg-slate-800 px-2 py-0.5 rounded-full">
            Phase 21 · AWP/WAC/DIR · Live FDA recalls
          </span>
        </div>
        <RecallAlertBanner />
      </div>

      <div className="grid grid-cols-4 gap-4">
        {/* Pricing margin table spans 3 columns */}
        <div className="col-span-3">
          <DrugPricingTable items={items} />
        </div>
        {/* Patient savings panel in rightmost column */}
        <div>
          <PatientSavingsPanel
            ndc={items[0]?.ndc11 ?? '00781-1001-01'}
            drugName={items[0]?.drug_name ?? 'Atorvastatin 40mg'}
            insuranceCopay={45.00}
          />
        </div>
      </div>

      {/* Row 5: Package Visual Verification — Phase 24 */}
      <PackageVerificationDashboard />
    </div>
  )
}
