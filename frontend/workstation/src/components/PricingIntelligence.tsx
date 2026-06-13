/**
 * Pricing Intelligence Components — Phase 21
 * ============================================
 * DrugPricingTable   — AWP / WAC / AAC / margin per drug (30-day fill basis)
 * RecallAlertBanner  — Active FDA recall strip with severity colour-coding
 * PatientSavingsPanel — Insurance vs discount-card comparison for pharmacist
 *
 * All three render from demo data when the API is unreachable (preview / offline).
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../lib/api'

// ── Types ─────────────────────────────────────────────────────────────────────

interface DrugPricingSummary {
  ndc: string
  drug_name: string
  is_generic: boolean
  unit_awp: number
  unit_wac: number
  unit_aac: number
  reimbursement: number
  cogs: number
  gross_margin: number
  gross_margin_pct: number
  dir_fee: number
  net_margin: number
  net_margin_pct: number
  cash_uc_price: number
}

interface RecallInfo {
  recall_number: string
  recall_class: string
  severity_level: 'critical' | 'high' | 'low' | 'unknown'
  reason: string
  product_description: string
  recalling_firm: string
  report_date: string
  lot_numbers: string
}

interface DiscountCard {
  card_name: string
  estimated_price: number
  savings_vs_retail: number
  savings_pct: number
  url: string
}

interface PatientSavings {
  drug_name: string
  retail_cash_price: number
  best_card: DiscountCard | null
  insurance_copay: number | null
  recommendation: string
  recommendation_reason: string
}

// ── Demo Data ─────────────────────────────────────────────────────────────────

const DEMO_PRICING: DrugPricingSummary[] = [
  { ndc:'00378-0001-01', drug_name:'Metformin 500mg',       is_generic:true,  unit_awp:0.089, unit_wac:0.083, unit_aac:0.072, reimbursement:4.68,  cogs:2.16, gross_margin:2.52, gross_margin_pct:53.8, dir_fee:0.33, net_margin:2.19, net_margin_pct:46.8, cash_uc_price:6.05 },
  { ndc:'00781-0001-01', drug_name:'Atorvastatin 40mg',     is_generic:true,  unit_awp:0.126, unit_wac:0.118, unit_aac:0.102, reimbursement:5.82,  cogs:3.06, gross_margin:2.76, gross_margin_pct:47.4, dir_fee:0.41, net_margin:2.35, net_margin_pct:40.4, cash_uc_price:8.58 },
  { ndc:'00185-0001-01', drug_name:'Amlodipine 5mg',        is_generic:true,  unit_awp:0.193, unit_wac:0.180, unit_aac:0.155, reimbursement:7.74,  cogs:4.65, gross_margin:3.09, gross_margin_pct:39.9, dir_fee:0.54, net_margin:2.55, net_margin_pct:32.9, cash_uc_price:13.02 },
  { ndc:'00603-0001-01', drug_name:'Gabapentin 300mg',      is_generic:true,  unit_awp:0.265, unit_wac:0.248, unit_aac:0.214, reimbursement:9.82,  cogs:6.42, gross_margin:3.40, gross_margin_pct:34.6, dir_fee:0.69, net_margin:2.71, net_margin_pct:27.6, cash_uc_price:18.00 },
  { ndc:'16714-0001-01', drug_name:'Losartan 50mg',         is_generic:true,  unit_awp:0.158, unit_wac:0.148, unit_aac:0.128, reimbursement:6.74,  cogs:3.84, gross_margin:2.90, gross_margin_pct:43.0, dir_fee:0.47, net_margin:2.43, net_margin_pct:36.1, cash_uc_price:10.75 },
  { ndc:'00228-0001-01', drug_name:'Omeprazole 20mg',       is_generic:true,  unit_awp:0.098, unit_wac:0.092, unit_aac:0.079, reimbursement:4.95,  cogs:2.37, gross_margin:2.58, gross_margin_pct:52.1, dir_fee:0.35, net_margin:2.23, net_margin_pct:45.1, cash_uc_price:6.65 },
  { ndc:'00554-0001-01', drug_name:'Metoprolol Succ 50mg',  is_generic:true,  unit_awp:0.230, unit_wac:0.215, unit_aac:0.186, reimbursement:8.70,  cogs:5.58, gross_margin:3.12, gross_margin_pct:35.9, dir_fee:0.61, net_margin:2.51, net_margin_pct:28.9, cash_uc_price:15.65 },
  { ndc:'00054-0001-01', drug_name:'Warfarin 5mg',          is_generic:true,  unit_awp:1.278, unit_wac:1.195, unit_aac:1.032, reimbursement:40.34, cogs:30.96,gross_margin:9.38, gross_margin_pct:23.3, dir_fee:2.82, net_margin:6.56, net_margin_pct:16.3, cash_uc_price:87.22 },
]

const DEMO_RECALLS: RecallInfo[] = [
  { recall_number:'Z-0001-2025', recall_class:'Class I', severity_level:'critical', reason:'Presence of nitrosamine impurity (NDMA) above acceptable limit', product_description:'Metformin Extended-Release Tablets, 500mg, 750mg, 1000mg', recalling_firm:'Apotex Corp.', report_date:'20250510', lot_numbers:'Lots: 2024001 through 2024045' },
  { recall_number:'Z-0002-2025', recall_class:'Class II', severity_level:'high', reason:'Labeling error — incorrect dosage instructions on carton', product_description:'Lisinopril Tablets, USP, 5mg, 10mg, 20mg', recalling_firm:'Aurobindo Pharma', report_date:'20250428', lot_numbers:'Lot: 2023B887' },
  { recall_number:'Z-0003-2025', recall_class:'Class III', severity_level:'low', reason:'Sub-potency — product fails dissolution specification', product_description:'Atorvastatin Calcium Tablets, 10mg', recalling_firm:'Mylan Pharmaceuticals', report_date:'20250415', lot_numbers:'Lots: A-4450, A-4451' },
]

const DEMO_SAVINGS: PatientSavings = {
  drug_name: 'Atorvastatin 40mg',
  retail_cash_price: 87.40,
  insurance_copay: 45.00,
  best_card: { card_name:'GoodRx Gold', estimated_price:8.42, savings_vs_retail:78.98, savings_pct:90.4, url:'https://www.goodrx.com' },
  recommendation: 'discount_card',
  recommendation_reason: 'GoodRx Gold ($8.42) saves $36.58 vs insurance copay ($45.00)',
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function recallSeverityStyle(level: string) {
  switch (level) {
    case 'critical': return { dot: 'bg-red-500',    badge: 'bg-red-900/50 text-red-300 border-red-700',    label: 'CLASS I' }
    case 'high':     return { dot: 'bg-orange-500', badge: 'bg-orange-900/50 text-orange-300 border-orange-700', label: 'CLASS II' }
    default:         return { dot: 'bg-yellow-500', badge: 'bg-yellow-900/50 text-yellow-300 border-yellow-700', label: 'CLASS III' }
  }
}

function marginFlag(pct: number) {
  if (pct < 0)  return { color:'text-red-400',    label:'Loss',    bg:'bg-red-900/30' }
  if (pct < 10) return { color:'text-orange-400', label:'Thin',    bg:'bg-orange-900/30' }
  if (pct < 25) return { color:'text-yellow-400', label:'OK',      bg:'bg-yellow-900/30' }
  return              { color:'text-green-400',  label:'Healthy', bg:'bg-green-900/30' }
}

function fmtDate(yyyymmdd: string) {
  if (!yyyymmdd || yyyymmdd.length < 8) return yyyymmdd
  return `${yyyymmdd.slice(0,4)}-${yyyymmdd.slice(4,6)}-${yyyymmdd.slice(6,8)}`
}

// ── Drug Pricing Table ────────────────────────────────────────────────────────

export function DrugPricingTable({ items }: { items: { ndc11: string; drug_name: string }[] }) {
  const [dirTier, setDirTier] = useState<'zero'|'low'|'medium'|'high'>('medium')

  const { data: rawData } = useQuery({
    queryKey: ['drug-pricing-batch', dirTier, items.map(i => i.ndc11).join(',')],
    queryFn: () =>
      apiClient.post('/drug-database/pricing/batch', {
        drugs: items.slice(0, 20).map(i => ({ ndc: i.ndc11, name: i.drug_name })),
        qty: 30,
        dir_tier: dirTier,
      }).then(r => r.data.drugs as DrugPricingSummary[]),
    enabled: items.length > 0,
  })

  const pricing = rawData ?? DEMO_PRICING

  const dirLabels = { zero:'No DIR', low:'Low DIR (3%)', medium:'Avg DIR (7%)', high:'High DIR (12%)' }

  return (
    <div className="bg-[#1a1f2e] rounded-xl border border-[#1e293b] overflow-hidden">
      {/* Header */}
      <div className="px-4 py-3 border-b border-[#1e293b] flex items-center justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-slate-500">
            Drug Pricing & Margin
          </p>
          <p className="text-[10px] text-slate-600 mt-0.5">AWP / WAC / AAC · 30-day fill basis</p>
        </div>
        <select
          value={dirTier}
          onChange={e => setDirTier(e.target.value as typeof dirTier)}
          className="text-[10px] bg-[#0f1117] border border-[#1e293b] text-slate-400 rounded px-2 py-1 cursor-pointer"
        >
          {Object.entries(dirLabels).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-[#1e293b] text-slate-500">
              <th className="text-left px-3 py-2 font-medium">Drug</th>
              <th className="text-right px-3 py-2 font-medium whitespace-nowrap">AWP/unit</th>
              <th className="text-right px-3 py-2 font-medium whitespace-nowrap">AAC/unit</th>
              <th className="text-right px-3 py-2 font-medium whitespace-nowrap">Reimb.</th>
              <th className="text-right px-3 py-2 font-medium whitespace-nowrap">COGS</th>
              <th className="text-right px-3 py-2 font-medium whitespace-nowrap">DIR</th>
              <th className="text-right px-3 py-2 font-medium whitespace-nowrap">Net Margin</th>
              <th className="text-center px-3 py-2 font-medium">Flag</th>
            </tr>
          </thead>
          <tbody>
            {pricing.slice(0, 12).map((drug, i) => {
              const flag = marginFlag(drug.net_margin_pct)
              return (
                <tr key={i} className="border-b border-[#1e293b]/50 hover:bg-white/[0.02] transition-colors">
                  <td className="px-3 py-2">
                    <div className="font-medium text-slate-200 truncate max-w-[160px]">{drug.drug_name}</div>
                    <div className="text-[10px] text-slate-600 font-mono">{drug.ndc}</div>
                  </td>
                  <td className="px-3 py-2 text-right text-slate-400 font-mono">
                    ${drug.unit_awp.toFixed(3)}
                  </td>
                  <td className="px-3 py-2 text-right text-slate-400 font-mono">
                    ${drug.unit_aac.toFixed(3)}
                  </td>
                  <td className="px-3 py-2 text-right text-slate-300 font-mono font-medium">
                    ${drug.reimbursement.toFixed(2)}
                  </td>
                  <td className="px-3 py-2 text-right text-slate-500 font-mono">
                    ${drug.cogs.toFixed(2)}
                  </td>
                  <td className="px-3 py-2 text-right text-orange-400/80 font-mono">
                    −${drug.dir_fee.toFixed(2)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono font-semibold">
                    <span className={drug.net_margin >= 0 ? 'text-green-400' : 'text-red-400'}>
                      ${drug.net_margin.toFixed(2)}
                    </span>
                    <span className="text-slate-600 text-[9px] ml-1">
                      ({drug.net_margin_pct.toFixed(1)}%)
                    </span>
                  </td>
                  <td className="px-3 py-2 text-center">
                    <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${flag.bg} ${flag.color}`}>
                      {flag.label}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Footer summary */}
      <div className="px-4 py-2 border-t border-[#1e293b] flex gap-6 text-[10px] text-slate-500">
        <span>
          Avg net margin:{' '}
          <span className="text-green-400 font-semibold">
            {(pricing.reduce((s,d) => s + d.net_margin_pct, 0) / pricing.length).toFixed(1)}%
          </span>
        </span>
        <span>
          Loss drugs:{' '}
          <span className="text-red-400 font-semibold">
            {pricing.filter(d => d.net_margin < 0).length}
          </span>
        </span>
        <span>
          Thin margin (&lt;10%):{' '}
          <span className="text-orange-400 font-semibold">
            {pricing.filter(d => d.net_margin_pct > 0 && d.net_margin_pct < 10).length}
          </span>
        </span>
      </div>
    </div>
  )
}

// ── Recall Alert Banner ───────────────────────────────────────────────────────

export function RecallAlertBanner() {
  const [expanded, setExpanded] = useState(false)

  const { data: rawData } = useQuery({
    queryKey: ['active-recalls'],
    queryFn: () =>
      apiClient.get('/drug-database/recalls/active?limit=30')
        .then(r => r.data as { total: number; critical_count: number; recalls: RecallInfo[] }),
    refetchInterval: 5 * 60_000,   // refresh every 5 min
    staleTime:       3 * 60_000,
  })

  // Always have renderable data
  const recalls  = rawData?.recalls     ?? DEMO_RECALLS
  const critical = rawData?.critical_count ?? DEMO_RECALLS.filter(r => r.severity_level === 'critical').length
  const total    = rawData?.total       ?? DEMO_RECALLS.length

  if (total === 0) return null

  return (
    <div className="bg-[#1a1f2e] rounded-xl border border-[#1e293b] overflow-hidden">
      {/* Summary bar */}
      <button
        onClick={() => setExpanded(e => !e)}
        className="w-full px-4 py-3 flex items-center justify-between hover:bg-white/[0.02] transition-colors"
      >
        <div className="flex items-center gap-3">
          <span className="text-lg">⚠️</span>
          <div className="text-left">
            <p className="text-xs font-semibold text-slate-200">
              {total} Active FDA Recall{total !== 1 ? 's' : ''}
            </p>
            <p className="text-[10px] text-slate-500 mt-0.5">
              {critical > 0
                ? <span className="text-red-400 font-semibold">{critical} Class I (critical)</span>
                : 'No Class I recalls'}{' '}
              · Click to {expanded ? 'collapse' : 'expand'}
            </p>
          </div>
        </div>
        <div className="flex gap-2 items-center">
          {critical > 0 && (
            <span className="px-2 py-0.5 bg-red-900/60 text-red-300 text-[10px] font-bold rounded-full border border-red-700 animate-pulse">
              URGENT
            </span>
          )}
          <span className="text-slate-500 text-xs">{expanded ? '▲' : '▼'}</span>
        </div>
      </button>

      {/* Expanded recall list */}
      {expanded && (
        <div className="border-t border-[#1e293b] max-h-64 overflow-y-auto">
          {recalls.slice(0, 20).map((recall, i) => {
            const style = recallSeverityStyle(recall.severity_level)
            return (
              <div key={i} className="px-4 py-3 border-b border-[#1e293b]/50 hover:bg-white/[0.02] flex gap-3">
                <div className="flex-shrink-0 mt-1">
                  <span className={`inline-block w-2 h-2 rounded-full ${style.dot}`} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded border ${style.badge}`}>
                      {style.label}
                    </span>
                    <span className="text-[10px] text-slate-500 font-mono">{recall.recall_number}</span>
                    <span className="text-[10px] text-slate-600">{fmtDate(recall.report_date)}</span>
                  </div>
                  <p className="text-xs text-slate-300 font-medium truncate">{recall.product_description}</p>
                  <p className="text-[10px] text-slate-500 mt-0.5 line-clamp-2">{recall.reason}</p>
                  <p className="text-[10px] text-slate-600 mt-0.5">{recall.recalling_firm} · {recall.lot_numbers}</p>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Patient Savings Panel ─────────────────────────────────────────────────────

export function PatientSavingsPanel({ ndc, drugName, insuranceCopay }: {
  ndc: string
  drugName: string
  insuranceCopay?: number
}) {
  const { data: rawData } = useQuery({
    queryKey: ['patient-savings', ndc, insuranceCopay],
    queryFn: () =>
      apiClient.get(`/drug-database/${encodeURIComponent(ndc)}/patient-savings`, {
        params: {
          drug_name: drugName,
          qty: 30,
          insurance_copay: insuranceCopay,
        },
      }).then(r => r.data as PatientSavings),
    enabled: !!ndc,
  })

  const savings = rawData ?? DEMO_SAVINGS
  const bestCard = savings.best_card

  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-3">
        Patient Savings
      </p>

      {/* Drug name */}
      <p className="text-sm font-semibold text-slate-200 mb-3">{savings.drug_name}</p>

      {/* Price comparison */}
      <div className="space-y-2 mb-3">
        {savings.insurance_copay != null && (
          <div className="flex items-center justify-between py-1.5 px-2 rounded-lg bg-slate-800/60">
            <span className="text-xs text-slate-400">Insurance copay</span>
            <span className="text-sm font-semibold text-slate-200">
              ${savings.insurance_copay.toFixed(2)}
            </span>
          </div>
        )}
        {bestCard && (
          <div className="flex items-center justify-between py-1.5 px-2 rounded-lg bg-green-900/20 border border-green-800/50">
            <div>
              <p className="text-xs text-green-400 font-medium">{bestCard.card_name}</p>
              <p className="text-[10px] text-green-600">−{bestCard.savings_pct}% off retail</p>
            </div>
            <span className="text-sm font-bold text-green-300">
              ${bestCard.estimated_price.toFixed(2)}
            </span>
          </div>
        )}
        <div className="flex items-center justify-between py-1.5 px-2 rounded-lg">
          <span className="text-xs text-slate-600">Retail cash</span>
          <span className="text-xs text-slate-600">${savings.retail_cash_price.toFixed(2)}</span>
        </div>
      </div>

      {/* Recommendation */}
      <div className={`rounded-lg px-3 py-2 text-[10px] leading-relaxed ${
        savings.recommendation === 'discount_card'
          ? 'bg-green-900/20 border border-green-800/50 text-green-300'
          : savings.recommendation === 'use_insurance'
          ? 'bg-blue-900/20 border border-blue-800/50 text-blue-300'
          : 'bg-slate-800 text-slate-400'
      }`}>
        <span className="font-bold">Pharmacist note: </span>
        {savings.recommendation_reason}
      </div>

      {/* Savings summary */}
      {bestCard && savings.insurance_copay != null &&
        savings.insurance_copay > bestCard.estimated_price && (
        <div className="mt-2 text-center">
          <p className="text-[10px] text-slate-600">Patient saves</p>
          <p className="text-lg font-bold text-green-400">
            ${(savings.insurance_copay - bestCard.estimated_price).toFixed(2)}
          </p>
          <p className="text-[10px] text-slate-600">by using discount card</p>
        </div>
      )}
    </div>
  )
}

// ── Compact Recall Badge (for Command Center) ─────────────────────────────────

export function RecallCountBadge() {
  const { data } = useQuery({
    queryKey: ['recall-count'],
    queryFn: () =>
      apiClient.get('/drug-database/recalls/active?limit=50')
        .then(r => r.data as { total: number; critical_count: number }),
    refetchInterval: 10 * 60_000,
    staleTime: 5 * 60_000,
  })

  const total    = data?.total          ?? 3
  const critical = data?.critical_count ?? 1

  return (
    <div className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-xs ${
      critical > 0
        ? 'bg-red-900/20 border-red-800/60 text-red-300'
        : 'bg-orange-900/20 border-orange-800/60 text-orange-300'
    }`}>
      <span className={`w-2 h-2 rounded-full ${critical > 0 ? 'bg-red-500 animate-pulse' : 'bg-orange-500'}`} />
      <span className="font-semibold">{total}</span>
      <span className="text-slate-500">active recall{total !== 1 ? 's' : ''}</span>
      {critical > 0 && (
        <span className="font-bold text-red-400">{critical} Class I</span>
      )}
    </div>
  )
}
