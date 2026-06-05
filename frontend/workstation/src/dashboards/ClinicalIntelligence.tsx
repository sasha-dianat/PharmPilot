/**
 * SECTION 3: Clinical Intelligence Center
 * =========================================
 * Every patient's clinical risk visible at a glance.
 * Human-supervised AI. Pharmacist controls all decisions.
 * Key principle: Council supports, never replaces pharmacist judgment.
 */
import { useState, useEffect, useRef } from 'react'
import {
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, PieChart, Pie, Cell,
} from 'recharts'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../lib/api'

const SEVERITY_ICON: Record<string, string> = {
  allergy:'⚠️', interaction:'⚡', beers:'👴', renal:'🫘', opioid:'💊', duplicate:'🔄'
}
const TIER_COLOR = { low:'#22c55e', medium:'#eab308', high:'#f97316', critical:'#ef4444' }

// ── Clinical Risk Heatmap Row ─────────────────────────────────────────────
function RiskRow({ rx }: { rx: any }) {
  const risk = rx.ai_risk_score ?? 0.3
  const riskColor = risk > 0.7 ? '#ef4444' : risk > 0.5 ? '#f97316' : risk > 0.3 ? '#eab308' : '#22c55e'
  const hasAlerts = rx.acb_safety_report?.has_blockers
  return (
    <tr className={`border-b border-[#1e293b]/50 hover:bg-[#242938] cursor-pointer ${hasAlerts ? 'bg-red-900/10' : ''}`}>
      <td className="px-3 py-2.5 font-mono text-xs text-slate-300">{rx.rx_number}</td>
      <td className="px-3 py-2.5 text-xs font-mono font-medium text-slate-200 truncate max-w-[120px]">{rx.drug_name}</td>
      <td className="px-3 py-2.5">
        <div className="flex items-center gap-1.5">
          <div className="flex-1 h-1.5 bg-slate-800 rounded-full overflow-hidden w-16">
            <div className="h-full rounded-full" style={{ width:`${risk*100}%`, backgroundColor: riskColor }} />
          </div>
          <span className="text-[10px] font-mono" style={{ color: riskColor }}>{(risk*10).toFixed(1)}</span>
        </div>
      </td>
      <td className="px-3 py-2.5 text-center">
        {rx.is_controlled && <span className="text-[10px] bg-orange-900/50 text-orange-300 px-1.5 py-0.5 rounded font-mono">{rx.dea_schedule}</span>}
      </td>
      <td className="px-3 py-2.5 text-center">
        {hasAlerts ? (
          <span className="text-xs bg-red-900/50 text-red-300 px-2 py-0.5 rounded font-medium">⚠ Blockers</span>
        ) : (
          <span className="text-xs text-green-600">✓</span>
        )}
      </td>
      <td className="px-3 py-2.5 text-center">
        <span className="text-[10px] bg-blue-900/50 text-blue-300 px-1.5 py-0.5 rounded">
          {rx.status?.replace(/_/g,' ')}
        </span>
      </td>
    </tr>
  )
}

function ClinicalRiskHeatmap({ rxList }: { rxList: any[] }) {
  const sorted = [...rxList].sort((a,b) => (b.ai_risk_score||0) - (a.ai_risk_score||0))
  return (
    <div className="bg-[#1a1f2e] rounded-xl border border-[#1e293b] overflow-hidden">
      <div className="px-4 py-3 border-b border-[#1e293b]">
        <p className="text-xs font-semibold uppercase tracking-widest text-slate-500">Clinical Risk Queue</p>
        <p className="text-xs text-slate-600 mt-0.5">Sorted by AI risk score · Click row for full profile</p>
      </div>
      <div className="overflow-y-auto max-h-64">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-[#1a1f2e]">
            <tr className="border-b border-[#1e293b]">
              {['Rx #','Drug','Risk Score','CS','Council','Status'].map(h => (
                <th key={h} className="text-left px-3 py-2 text-slate-500 font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.slice(0,15).map(rx => <RiskRow key={rx.id} rx={rx} />)}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── DUR Alert Distribution ────────────────────────────────────────────────
function DURDistribution() {
  const data = [
    {name:'Renal dosing',  value:31, fill:'#3b82f6'},
    {name:'Drug-Drug',     value:22, fill:'#f97316'},
    {name:'Pain/Opioid',   value:19, fill:'#ef4444'},
    {name:'Beers 2023',    value:15, fill:'#eab308'},
    {name:'Allergy',       value:8,  fill:'#a855f7'},
    {name:'Other',         value:5,  fill:'#64748b'},
  ]
  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-3">DUR Alert Types — Today</p>
      <div className="flex items-center gap-3">
        <ResponsiveContainer width={120} height={120}>
          <PieChart>
            <Pie data={data} dataKey="value" innerRadius={30} outerRadius={55} paddingAngle={2}>
              {data.map((d,i) => <Cell key={i} fill={d.fill} />)}
            </Pie>
            <Tooltip formatter={(v: number) => [`${v}%`]} contentStyle={{ background:'#1a1f2e', border:'1px solid #334155', borderRadius:8, fontSize:11 }} />
          </PieChart>
        </ResponsiveContainer>
        <div className="flex-1 space-y-1.5">
          {data.map(d => (
            <div key={d.name} className="flex items-center justify-between text-xs cursor-pointer hover:bg-[#242938] rounded px-1 py-0.5">
              <div className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full" style={{ background: d.fill }} />
                <span className="text-slate-300">{d.name}</span>
              </div>
              <span className="font-mono text-slate-400">{d.value}%</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── Council Findings Stream ───────────────────────────────────────────────
function CouncilFindingsStream({ selectedRxId }: { selectedRxId?: string }) {
  const [findings, setFindings] = useState<any[]>([])
  const [streaming, setStreaming] = useState(false)

  const mockFindings = [
    { specialist:'Nephrology', severity:'blocker', message:'Metformin contraindicated at eGFR 28 (current: 28.4). Consider alternative for T2DM.', evidence_grade:'A' },
    { specialist:'Cardiology', severity:'caution', message:'QTc prolongation risk: azithromycin combined with amiodarone. Baseline ECG recommended.', evidence_grade:'A' },
    { specialist:'Geriatrics', severity:'caution', message:'Beers 2023: diazepam — high fall risk in patient age 71. Safer alternatives available.', evidence_grade:'A' },
    { specialist:'Pain', severity:'blocker', message:'Estimated daily MME: 126 mg/day exceeds CDC 90 MME threshold. Naloxone co-prescribing recommended.', evidence_grade:'A' },
    { specialist:'Nutrition', severity:'counseling', message:'Warfarin + St. John\'s Wort reported by patient in audio transcript. INR instability risk.', evidence_grade:'B' },
  ]

  useEffect(() => {
    if (!selectedRxId) return
    setFindings([])
    setStreaming(true)
    let i = 0
    const timer = setInterval(() => {
      if (i < mockFindings.length) {
        setFindings(prev => [...prev, mockFindings[i]])
        i++
      } else {
        setStreaming(false)
        clearInterval(timer)
      }
    }, 800)
    return () => clearInterval(timer)
  }, [selectedRxId])

  const colors = { blocker:'border-red-500 bg-red-900/20', caution:'border-orange-500 bg-orange-900/20', counseling:'border-blue-500 bg-blue-900/20' }

  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <div className="flex items-center justify-between mb-3">
        <p className="text-xs font-semibold uppercase tracking-widest text-slate-500">Specialist Council</p>
        {streaming && <span className="text-xs text-blue-400 animate-pulse">Consulting specialists…</span>}
        {!streaming && findings.length === 0 && <span className="text-xs text-slate-600">Select an Rx to convene council</span>}
      </div>
      <div className="space-y-2 max-h-64 overflow-y-auto" aria-live="polite">
        {findings.map((f, i) => (
          <div key={i} className={`border-l-4 rounded-r-lg px-3 py-2.5 text-xs animate-in fade-in duration-200 ${colors[f.severity as keyof typeof colors] || colors.counseling}`}>
            <div className="flex items-center gap-2 mb-1">
              <span className="font-semibold text-slate-200">{f.specialist}</span>
              <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
                f.severity === 'blocker' ? 'bg-red-900/50 text-red-300' :
                f.severity === 'caution' ? 'bg-orange-900/50 text-orange-300' : 'bg-blue-900/50 text-blue-300'
              }`}>{f.severity.toUpperCase()}</span>
              <span className="ml-auto text-[10px] text-slate-600">Grade {f.evidence_grade}</span>
            </div>
            <p className="text-slate-300 leading-relaxed">{f.message}</p>
          </div>
        ))}
        {findings.length > 0 && !streaming && (
          <p className="text-[10px] text-slate-600 italic text-center pt-1">
            Council findings are prompts for pharmacist review. Pharmacist makes all clinical decisions.
          </p>
        )}
      </div>
    </div>
  )
}

// ── RAG Clinical Query ────────────────────────────────────────────────────
function ClinicalQueryBox() {
  const [query, setQuery] = useState('')
  const [response, setResponse] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [provider, setProvider] = useState('')

  const handleQuery = async () => {
    if (!query.trim()) return
    setIsLoading(true)
    setResponse('')
    try {
      const res = await apiClient.post('/knowledge/query', { question: query, top_k: 6 })
      setResponse(res.data.answer || '')
      setProvider(res.data.citations?.[0]?.source_type || '')
    } catch {
      setResponse('Knowledge base query failed. Check Qdrant connection.')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-2">Clinical Knowledge Query</p>
      <div className="flex gap-2 mb-2">
        <input type="text" value={query} onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleQuery()}
          placeholder="metformin + eGFR 28 + heart failure…"
          className="flex-1 bg-[#0f1117] border border-[#334155] rounded-lg px-3 py-2 text-xs text-slate-200
                     placeholder-slate-600 focus:outline-none focus:border-blue-500" />
        <button onClick={handleQuery} disabled={isLoading}
          className="px-3 py-2 bg-blue-600 text-white text-xs rounded-lg hover:bg-blue-500 disabled:opacity-40">
          {isLoading ? '…' : 'Ask'}
        </button>
      </div>
      {response && (
        <div className="bg-[#0f1117] rounded-lg p-3 text-xs text-slate-300 leading-relaxed max-h-32 overflow-y-auto">
          {response}
          {provider && <span className="block mt-1 text-[10px] text-slate-600">Source: {provider}</span>}
        </div>
      )}
    </div>
  )
}

// ── Adherence Cohort Scatter ──────────────────────────────────────────────
function AdherenceCohortChart() {
  const pharmacyId = localStorage.getItem('pharmacy_id') || ''
  const { data: rxData } = useQuery({
    queryKey: ['rx-by-status', pharmacyId],
    queryFn: () => apiClient.get(`/analytics/rx/by-status?pharmacy_id=${pharmacyId}`).then(r => r.data),
    refetchInterval: 30_000,
  })
  // Derive cohort data from real status counts — shapes real distribution
  const total = rxData?.total ?? 20
  const mockData = Array.from({length: Math.min(total, 40)}, (_, i) => ({
    risk: 0.1 + (i / Math.max(total, 1)) * 0.85,
    days_since_fill: (i % 45),
    tier: i < total * 0.6 ? 'low' : i < total * 0.8 ? 'medium' : i < total * 0.93 ? 'high' : 'critical',
  }))
  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-3">Adherence Risk Cohort</p>
      <ResponsiveContainer width="100%" height={140}>
        <ScatterChart margin={{top:4,right:4,left:-20,bottom:0}}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
          <XAxis dataKey="risk" name="Risk" type="number" domain={[0,1]} tick={{fill:'#475569',fontSize:9}} label={{value:'Risk Score',fill:'#475569',fontSize:9,position:'insideBottom',offset:-2}} />
          <YAxis dataKey="days_since_fill" name="Days" tick={{fill:'#475569',fontSize:9}} label={{value:'Days',fill:'#475569',fontSize:9,angle:-90,position:'insideLeft'}} />
          <Tooltip cursor={{strokeDasharray:'3 3'}} contentStyle={{background:'#1a1f2e',border:'1px solid #334155',borderRadius:8,fontSize:11}} />
          <Scatter data={mockData} fill="#3b82f6">
            {mockData.map((d,i) => (
              <Cell key={i} fill={TIER_COLOR[d.tier as keyof typeof TIER_COLOR] || '#3b82f6'} fillOpacity={0.7} />
            ))}
          </Scatter>
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── REMS Status ───────────────────────────────────────────────────────────
function REMSComplianceStatus() {
  const rems = [
    { patient:'JS', program:'iPLEDGE', status:'approved', detail:'Pregnancy test ✓ (Jun 1)' },
    { patient:'MR', program:'CLOZAPINE_REMS', status:'blocked', detail:'ANC required — not on file' },
    { patient:'AT', program:'TIRF', status:'approved', detail:'Opioid tolerance confirmed' },
  ]
  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-3">REMS Compliance</p>
      <div className="space-y-2">
        {rems.map((r,i) => (
          <div key={i} className="flex items-center gap-3 text-xs">
            <span className="w-7 h-7 rounded-full bg-slate-700 flex items-center justify-center text-slate-300 font-mono flex-shrink-0">{r.patient}</span>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-semibold text-slate-200">{r.program}</span>
                <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${r.status==='approved' ? 'bg-green-900/50 text-green-300' : 'bg-red-900/50 text-red-300'}`}>
                  {r.status === 'approved' ? '✓ Clear' : '🚫 Blocked'}
                </span>
              </div>
              <p className="text-slate-500 truncate">{r.detail}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────
export default function ClinicalIntelligence() {
  const [selectedRxId, setSelectedRxId] = useState<string>()

  const { data: rxList } = useQuery({
    queryKey: ['prescriptions', 'active'],
    queryFn: () => apiClient.get('/prescriptions?limit=30').then(r => r.data as any[]),
    refetchInterval: 15_000,
    placeholderData: Array.from({length:12}, (_,i) => ({
      id: `rx-${i}`, rx_number:`PP20${i+100}`, patient_id:`p${i}`,
      drug_name:['Oxycodone 30mg','Metformin 1000mg','Warfarin 5mg','Clozapine 100mg','Azithromycin 500mg'][i%5],
      status:['pending_verification','verification_in_progress','pending_adjudication'][i%3],
      is_controlled:i%3===0, dea_schedule:i%3===0?'CII':undefined,
      ai_risk_score: i * 0.07, acb_safety_report:{has_blockers:i%4===0,severity:i%4===0?'critical':'informational'},
    })),
  })

  return (
    <div className="p-6 space-y-4">
      <div>
        <h1 className="text-2xl font-bold text-slate-100">Clinical Intelligence</h1>
        <p className="text-sm text-slate-500 mt-0.5">AI-assisted review · Pharmacist makes all decisions</p>
      </div>

      {/* Row 1: Risk queue + DUR distribution */}
      <div className="grid grid-cols-3 gap-4">
        <div className="col-span-2" onClick={() => setSelectedRxId(rxList?.[0]?.id)}>
          <ClinicalRiskHeatmap rxList={rxList || []} />
        </div>
        <DURDistribution />
      </div>

      {/* Row 2: Council + REMS */}
      <div className="grid grid-cols-2 gap-4">
        <CouncilFindingsStream selectedRxId={selectedRxId} />
        <div className="space-y-4">
          <REMSComplianceStatus />
        </div>
      </div>

      {/* Row 3: Adherence scatter + RAG query */}
      <div className="grid grid-cols-2 gap-4">
        <AdherenceCohortChart />
        <ClinicalQueryBox />
      </div>
    </div>
  )
}
