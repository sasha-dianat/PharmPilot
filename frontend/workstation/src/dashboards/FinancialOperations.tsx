/**
 * SECTION 5: Financial Operations Dashboard
 * ===========================================
 * Bloomberg terminal × pharmacy P&L. Live, dense, actionable.
 * Owner sees: revenue trajectory, claim performance, DIR impact, Star Ratings.
 */
import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, BarChart, Cell, ReferenceLine, PieChart, Pie,
} from 'recharts'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../lib/api'

const formatDollar = (v: number) => v >= 1000 ? `$${(v/1000).toFixed(0)}K` : `$${v}`

function RevenueWaterfall() {
  const data = [
    { name:'Gross Revenue', value:48200, cumulative:48200, fill:'#3b82f6' },
    { name:'PBM Paid',      value:-8400, cumulative:39800, fill:'#22c55e' },
    { name:'Copays',        value:-6200, cumulative:33600, fill:'#22c55e' },
    { name:'DIR Posted',    value:2100,  cumulative:31500, fill:'#ef4444' },
    { name:'Est. Future DIR',value:800,  cumulative:30700, fill:'#f97316' },
    { name:'Net Realized',  value:30700, cumulative:30700, fill:'#a855f7' },
  ]
  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-3">Revenue Waterfall (MTD)</p>
      <ResponsiveContainer width="100%" height={160}>
        <BarChart data={data} margin={{top:4,right:4,left:0,bottom:0}}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
          <XAxis dataKey="name" tick={{fill:'#475569',fontSize:8}} angle={-20} textAnchor="end" height={40} />
          <YAxis tickFormatter={formatDollar} tick={{fill:'#475569',fontSize:9}} />
          <Tooltip formatter={(v:number) => [`$${Math.abs(v).toLocaleString()}`]}
            contentStyle={{background:'#1a1f2e',border:'1px solid #334155',borderRadius:8,fontSize:11}} />
          <Bar dataKey="value" radius={[3,3,0,0]}>
            {data.map((d,i) => <Cell key={i} fill={d.fill} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

function AdjudicationHistogram() {
  const data = Array.from({length:20}, (_,i) => ({
    ms: i*20, count: i < 10 ? (20 + i * 2) : Math.max(5, 20 - (i-10)*3),
    fill: i*20 < 200 ? '#22c55e' : '#ef4444',
  }))
  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-3">Adjudication Response Time</p>
      <ResponsiveContainer width="100%" height={120}>
        <BarChart data={data} margin={{top:4,right:4,left:-20,bottom:0}}>
          <XAxis dataKey="ms" tick={{fill:'#475569',fontSize:8}} tickFormatter={v=>`${v}ms`} interval={4} />
          <YAxis tick={{fill:'#475569',fontSize:8}} />
          <ReferenceLine x={200} stroke="#ef4444" strokeDasharray="4 2" label={{value:'SLA 200ms',fill:'#ef4444',fontSize:8}} />
          <Bar dataKey="count" radius={[2,2,0,0]}>
            {data.map((d,i) => <Cell key={i} fill={d.fill} />)}
          </Bar>
          <Tooltip contentStyle={{background:'#1a1f2e',border:'1px solid #334155',borderRadius:8,fontSize:11}} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

function CMSStarGauges() {
  const metrics = [
    { name:'Diabetes PDC', score:0.84, stars:4, label:'Statin Adherence' },
    { name:'Hypertension PDC', score:0.79, stars:3, label:'RAS Adherence' },
    { name:'Cholesterol PDC', score:0.91, stars:5, label:'Statin+DM' },
  ]
  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-3">CMS Star Ratings — Live PDC</p>
      <div className="space-y-3">
        {metrics.map(m => {
          const pct = m.score * 100
          const color = m.score >= 0.89 ? '#22c55e' : m.score >= 0.83 ? '#3b82f6' : '#eab308'
          const stars = '★'.repeat(m.stars) + '☆'.repeat(5-m.stars)
          return (
            <div key={m.name}>
              <div className="flex justify-between text-xs mb-1">
                <span className="text-slate-300">{m.name}</span>
                <div className="flex items-center gap-2">
                  <span style={{color}} className="font-mono font-semibold">{pct.toFixed(1)}%</span>
                  <span className="text-yellow-400 text-[11px]">{stars}</span>
                </div>
              </div>
              <div className="relative h-2.5 bg-slate-800 rounded-full">
                <div className="h-full rounded-full" style={{width:`${pct}%`,backgroundColor:color}} />
                {[75,83,89].map(t => (
                  <div key={t} className="absolute top-0 bottom-0 w-px bg-slate-600" style={{left:`${t}%`}} />
                ))}
              </div>
              <div className="flex justify-between text-[9px] text-slate-600 mt-0.5">
                <span>75%=3★</span><span>83%=4★</span><span>89%=5★</span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function RejectPareto() {
  const data = [
    {code:'75',label:'Prior Auth',count:23,fill:'#ef4444'},
    {code:'76',label:'Plan Limit',count:18,fill:'#f97316'},
    {code:'70',label:'Days Supply',count:12,fill:'#eab308'},
    {code:'19',label:'NDC Invalid',count:9, fill:'#3b82f6'},
    {code:'33',label:'Early Refill',count:7, fill:'#a855f7'},
    {code:'07',label:'Member ID',count:5, fill:'#22c55e'},
  ]
  const total = data.reduce((s,d)=>s+d.count,0)
  let cum = 0
  const withCum = data.map(d => { cum += d.count; return {...d, cumPct: cum/total*100} })
  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-3">Reject Code Pareto</p>
      <ResponsiveContainer width="100%" height={130}>
        <ComposedChart data={withCum} margin={{top:4,right:20,left:-20,bottom:0}}>
          <XAxis dataKey="code" tick={{fill:'#475569',fontSize:9}} />
          <YAxis yAxisId="left" tick={{fill:'#475569',fontSize:9}} />
          <YAxis yAxisId="right" orientation="right" domain={[0,100]} tickFormatter={v=>`${v}%`} tick={{fill:'#475569',fontSize:9}} />
          <Tooltip contentStyle={{background:'#1a1f2e',border:'1px solid #334155',borderRadius:8,fontSize:11}}
            formatter={(v: any, n: string) => [n==='cumPct'?`${(v as number).toFixed(0)}%`:v, n==='cumPct'?'Cumulative':'Count']} />
          <Bar yAxisId="left" dataKey="count" radius={[3,3,0,0]}>
            {withCum.map((d,i)=><Cell key={i} fill={d.fill} />)}
          </Bar>
          <Line yAxisId="right" type="monotone" dataKey="cumPct" stroke="#94a3b8" strokeWidth={1.5} dot={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}

export default function FinancialOperations() {
  const { data: financialData } = useQuery({
    queryKey: ['financial-summary'],
    queryFn: () => apiClient.get('/analytics/financial/summary').then(r => r.data),
    refetchInterval: 300_000,
  })

  const kpis = [
    { label:'MTD Revenue', value:`$${((financialData?.gross_revenue||48200)/1000).toFixed(1)}K`, color:'#3b82f6' },
    { label:'PBM Paid', value:`$${((financialData?.pbm_paid||39800)/1000).toFixed(1)}K`, color:'#22c55e' },
    { label:'DIR Exposure', value:`$${((financialData?.dir_fees_posted||2100)).toLocaleString()}`, color:'#ef4444' },
    { label:'Net Realized', value:`$${((financialData?.net_after_known_dir||30700)/1000).toFixed(1)}K`, color:'#a855f7' },
  ]

  return (
    <div className="p-6 space-y-4">
      <h1 className="text-2xl font-bold text-slate-100">Financial Operations</h1>
      <div className="grid grid-cols-4 gap-3">
        {kpis.map(k => (
          <div key={k.label} className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
            <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-1">{k.label}</p>
            <p className="text-3xl font-bold tabular-nums" style={{color:k.color}}>{k.value}</p>
          </div>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-4">
        <RevenueWaterfall />
        <AdjudicationHistogram />
      </div>
      <div className="grid grid-cols-2 gap-4">
        <RejectPareto />
        <CMSStarGauges />
      </div>
    </div>
  )
}
