/**
 * SECTION 6: Patient & Adherence Intelligence
 * ============================================
 * Reveal which patients need intervention BEFORE they miss a refill.
 * MTM pipeline as Kanban. Adherence funnel. Language-aware outreach.
 */
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  LineChart, Line,
} from 'recharts'

// ── Adherence Funnel ───────────────────────────────────────────────────────
function AdherenceFunnel() {
  const stages = [
    { label:'Total Active', count:847, pct:100 },
    { label:'Screened', count:712, pct:84 },
    { label:'Low Risk', count:445, pct:52 },
    { label:'Medium Risk', count:168, pct:20 },
    { label:'High Risk', count:72, pct:9 },
    { label:'Critical', count:27, pct:3 },
  ]
  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-3">Adherence Risk Funnel</p>
      <div className="space-y-1.5">
        {stages.map((s, i) => {
          const color = i===0?'#3b82f6':i===1?'#64748b':i===2?'#22c55e':i===3?'#eab308':i===4?'#f97316':'#ef4444'
          return (
            <div key={s.label}>
              <div className="flex justify-between text-xs mb-0.5">
                <span className="text-slate-300">{s.label}</span>
                <span className="font-mono font-semibold" style={{color}}>{s.count}</span>
              </div>
              <div className="h-3 bg-slate-800 rounded-full overflow-hidden" style={{width:`${100-i*5}%`,marginLeft:`${i*2.5}%`}}>
                <div className="h-full rounded-full" style={{width:`${s.pct}%`,backgroundColor:color}} />
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── MTM Kanban Pipeline ─────────────────────────────────────────────────────
function MTMKanban() {
  const columns = [
    { id:'eligible', label:'Eligible', color:'#3b82f6',
      cards:[{id:'E1',name:'M.J.',cost:'$8,400',cpt:'99605'},{id:'E2',name:'D.R.',cost:'$6,200',cpt:'99605'}] },
    { id:'contacted', label:'Contacted', color:'#f97316',
      cards:[{id:'C1',name:'S.B.',cost:'$9,100',cpt:'99606'}] },
    { id:'scheduled', label:'Scheduled', color:'#eab308',
      cards:[{id:'SC1',name:'A.T.',cost:'$7,800',cpt:'99606'},{id:'SC2',name:'P.M.',cost:'$5,500',cpt:'99605'}] },
    { id:'complete', label:'Complete', color:'#22c55e',
      cards:[{id:'CM1',name:'L.K.',cost:'$11,200',cpt:'99607'}] },
    { id:'billed', label:'Billed', color:'#a855f7',
      cards:[{id:'B1',name:'R.N.',cost:'$6,800',cpt:'99606'}] },
  ]
  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-3">MTM Pipeline</p>
      <div className="flex gap-2 overflow-x-auto pb-1">
        {columns.map(col => (
          <div key={col.id} className="flex-shrink-0 w-28">
            <div className="flex items-center gap-1 mb-2">
              <span className="w-2 h-2 rounded-full" style={{background:col.color}} />
              <span className="text-[10px] font-semibold text-slate-400">{col.label}</span>
              <span className="ml-auto text-[10px] text-slate-600">{col.cards.length}</span>
            </div>
            <div className="space-y-1.5">
              {col.cards.map(card => (
                <div key={card.id} className="bg-[#242938] rounded p-2 border border-[#334155] cursor-pointer hover:border-blue-500">
                  <p className="text-xs font-semibold text-slate-200">{card.name}</p>
                  <p className="text-[10px] text-slate-500">{card.cost}/yr</p>
                  <span className="text-[9px] bg-blue-900/50 text-blue-300 px-1 py-0.5 rounded font-mono">{card.cpt}</span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Intervention Effectiveness ─────────────────────────────────────────────
function InterventionEffectiveness() {
  const data = [
    { intervention:'SMS', before:0.71, after:0.79 },
    { intervention:'Phone call', before:0.65, after:0.83 },
    { intervention:'Med Sync', before:0.68, after:0.91 },
    { intervention:'MTM', before:0.61, after:0.87 },
  ]
  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-3">Intervention Effectiveness (PDC)</p>
      <ResponsiveContainer width="100%" height={140}>
        <BarChart data={data} margin={{top:4,right:4,left:-20,bottom:0}}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
          <XAxis dataKey="intervention" tick={{fill:'#475569',fontSize:9}} />
          <YAxis domain={[0.5,1]} tickFormatter={v=>`${(v*100).toFixed(0)}%`} tick={{fill:'#475569',fontSize:9}} />
          <Tooltip formatter={(v) => [`${(Number(v ?? 0)*100).toFixed(1)}%`]}
            contentStyle={{background:'#1a1f2e',border:'1px solid #334155',borderRadius:8,fontSize:11}} />
          <Bar dataKey="before" name="Before" fill="#ef4444" fillOpacity={0.7} radius={[2,2,0,0]} />
          <Bar dataKey="after"  name="After"  fill="#22c55e" fillOpacity={0.9} radius={[2,2,0,0]} />
        </BarChart>
      </ResponsiveContainer>
      <div className="flex gap-4 mt-2 text-[10px] justify-center">
        {[['#ef4444','Before'],['#22c55e','After intervention']].map(([c,l]) => (
          <span key={l} className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-sm" style={{background:c}} /><span className="text-slate-500">{l}</span>
          </span>
        ))}
      </div>
    </div>
  )
}

// ── Refill Reminder Performance ────────────────────────────────────────────
function RefillReminderLine() {
  const data = Array.from({length:12}, (_,i) => ({
    week:`W${i+1}`, sendRate: 0.65 + (i * 0.04) % 0.25, completionRate: 0.60 + (i * 0.035) % 0.3,
  }))
  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-3">Refill Reminder Performance (90d)</p>
      <ResponsiveContainer width="100%" height={110}>
        <LineChart data={data} margin={{top:4,right:4,left:-20,bottom:0}}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
          <XAxis dataKey="week" tick={{fill:'#475569',fontSize:8}} interval={2} />
          <YAxis domain={[0,1]} tickFormatter={v=>`${(v*100).toFixed(0)}%`} tick={{fill:'#475569',fontSize:9}} />
          <Tooltip formatter={(v) => [`${(Number(v ?? 0)*100).toFixed(1)}%`]}
            contentStyle={{background:'#1a1f2e',border:'1px solid #334155',borderRadius:8,fontSize:11}} />
          <Line dataKey="sendRate" stroke="#3b82f6" strokeWidth={2} dot={false} name="Send Rate" />
          <Line dataKey="completionRate" stroke="#22c55e" strokeWidth={2} dot={false} name="Completion Rate" />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

export default function PatientAdherence() {
  return (
    <div className="p-6 space-y-4">
      <div>
        <h1 className="text-2xl font-bold text-slate-100">Patient & Adherence Intelligence</h1>
        <p className="text-sm text-slate-500 mt-0.5">Population health · MTM pipeline · Intervention ROI</p>
      </div>
      <div className="grid grid-cols-3 gap-4">
        <AdherenceFunnel />
        <div className="col-span-2"><MTMKanban /></div>
      </div>
      <div className="grid grid-cols-2 gap-4">
        <InterventionEffectiveness />
        <RefillReminderLine />
      </div>
    </div>
  )
}
