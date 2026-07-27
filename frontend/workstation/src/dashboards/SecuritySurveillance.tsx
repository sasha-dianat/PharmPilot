/**
 * SECTION 4: Surveillance & Security Intelligence
 * =================================================
 * Calm when clear. Immediately alarming when active.
 * Operator principle: no false urgency. Red means now.
 */
import { useState } from 'react'
import { RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis, ResponsiveContainer, Tooltip } from 'recharts'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../lib/api'

// ── Pharmacy Floor Live Map ───────────────────────────────────────────────
function PharmacyFloorMap(_props: { events: any[] }) {
  const persons = [
    { id:1, x:20, y:30, zone:'waiting_area', dwell:180, identity:'patient', alert:null },
    { id:2, x:55, y:75, zone:'vault_room', dwell:45, identity:'staff', alert:'vault_zone_intrusion' },
    { id:3, x:80, y:25, zone:'otc_shelves', dwell:520, identity:'unknown', alert:'loitering' },
  ]
  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <div className="flex justify-between items-center mb-3">
        <p className="text-xs font-semibold uppercase tracking-widest text-slate-500">Live Floor Map</p>
        <span className="flex items-center gap-1.5 text-xs text-green-400">
          <span className="w-2 h-2 rounded-full bg-green-500" />Live
        </span>
      </div>
      {/* SVG floor plan */}
      <div className="relative bg-[#0f1117] rounded-lg overflow-hidden" style={{ height: 200 }}>
        <svg viewBox="0 0 100 100" className="w-full h-full">
          {/* Zone overlays */}
          <rect x="0" y="0" width="50" height="60" fill="#1e293b" stroke="#334155" strokeWidth="0.5" opacity="0.8" />
          <text x="2" y="5" fontSize="3" fill="#64748b">Waiting Area</text>
          <rect x="0" y="60" width="50" height="20" fill="#1d4ed820" stroke="#1d4ed8" strokeWidth="0.5" />
          <text x="2" y="65" fontSize="3" fill="#3b82f6">Counter</text>
          <rect x="0" y="80" width="50" height="20" fill="#7c3aed20" stroke="#7c3aed" strokeWidth="0.5" />
          <text x="2" y="85" fontSize="3" fill="#a78bfa">Counter Interior ⛔</text>
          {/* Vault — hatched */}
          <rect x="50" y="70" width="50" height="30" fill="#7f1d1d20" stroke="#ef4444" strokeWidth="1" strokeDasharray="3 1" />
          <text x="52" y="80" fontSize="3" fill="#ef4444">VAULT ⛔</text>
          <rect x="50" y="50" width="50" height="20" fill="#4c1d9520" stroke="#7c3aed" strokeWidth="0.5" />
          <text x="52" y="58" fontSize="3" fill="#a78bfa">Pharm Only</text>
          <rect x="50" y="0" width="50" height="50" fill="#1e3a5f20" stroke="#334155" strokeWidth="0.5" />
          <text x="52" y="6" fontSize="3" fill="#64748b">OTC Shelves</text>

          {/* Person icons */}
          {persons.map(p => (
            <g key={p.id}>
              {/* Dwell circle grows with time */}
              <circle cx={p.x} cy={p.y} r={Math.min(8, 2+p.dwell/100)}
                fill={p.alert ? '#ef444430' : '#3b82f610'}
                stroke={p.alert ? '#ef4444' : '#3b82f6'}
                strokeWidth="0.5" strokeDasharray={p.alert ? "2 1" : "none"} />
              {/* Person silhouette */}
              <circle cx={p.x} cy={p.y-1} r="2"
                fill={p.identity==='staff'?'#3b82f6':p.identity==='unknown'?'#f97316':'#94a3b8'} />
              <rect x={p.x-1.5} y={p.y+1} width="3" height="3.5" rx="0.5"
                fill={p.identity==='staff'?'#3b82f6':p.identity==='unknown'?'#f97316':'#94a3b8'} />
              {/* Alert icon */}
              {p.alert && (
                <text x={p.x+3} y={p.y-2} fontSize="4">⚠</text>
              )}
            </g>
          ))}
        </svg>
        {/* Legend */}
        <div className="absolute bottom-2 left-2 flex gap-2 text-[8px]">
          {[['#94a3b8','Patient'],['#3b82f6','Staff'],['#f97316','Unknown']].map(([c,l]) => (
            <span key={l} className="flex items-center gap-0.5">
              <span className="w-2 h-2 rounded-full" style={{background:c}} /><span className="text-slate-500">{l}</span>
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── Behavioral Event Timeline ─────────────────────────────────────────────
function BehavioralTimeline({ events }: { events: any[] }) {
  const ICONS: Record<string, string> = { loitering:'⏱', vault_zone_intrusion:'🔐', theft_gesture:'🕵️', fall_detected:'🏥', aggressive_posture:'⚡', after_hours_presence:'🌙' }
  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-3">Behavioral Events</p>
      <div className="space-y-2 max-h-56 overflow-y-auto" aria-live="polite">
        {events.length === 0 ? (
          <div className="text-center py-6">
            <p className="text-3xl mb-1">🛡️</p>
            <p className="text-xs text-green-400">All clear — no active alerts</p>
          </div>
        ) : events.map((e, i) => (
          <div key={i} className={`rounded-lg p-3 border ${e.severity==='critical'?'bg-red-900/20 border-red-800':e.severity==='high'?'bg-orange-900/20 border-orange-800':'bg-[#242938] border-[#334155]'}`}>
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="text-base">{ICONS[e.event_type] || '👁'}</span>
                <div>
                  <p className="text-xs font-semibold text-slate-200">{e.event_type?.replace(/_/g,' ')}</p>
                  <p className="text-[10px] text-slate-500">{e.detected_at} · Zone: {e.metadata?.camera_zone}</p>
                </div>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <span className="text-[10px] text-slate-400">{((e.metadata?.confidence||0.85)*100).toFixed(0)}%</span>
                {!e.resolved && (
                  <span className="text-[10px] bg-red-900/50 text-red-300 px-1.5 py-0.5 rounded">Active</span>
                )}
              </div>
            </div>
            {e.description && <p className="text-[10px] text-slate-400 mt-1 leading-relaxed">{e.description}</p>}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Duress Protocol Panel ─────────────────────────────────────────────────
function DuressPanel() {
  const [confirm, setConfirm] = useState(false)

  return (
    <div className={`rounded-xl p-4 border ${confirm ? 'bg-red-900/30 border-red-600' : 'bg-[#1a1f2e] border-[#1e293b]'}`}>
      <div className="flex items-center gap-2 mb-3">
        <span className="text-xl">🛡️</span>
        <p className="text-xs font-semibold uppercase tracking-widest text-green-400">Duress Protocol</p>
        <span className="ml-auto text-xs text-green-400">● All clear</span>
      </div>
      <div className="space-y-2 text-xs mb-3">
        {[['Vault Status','🔒 Locked'],['Silent Alarm','Not active'],['Last Incident','Never']].map(([k,v]) => (
          <div key={k} className="flex justify-between">
            <span className="text-slate-500">{k}</span>
            <span className="text-slate-300">{v}</span>
          </div>
        ))}
      </div>
      {!confirm ? (
        <button onClick={() => setConfirm(true)}
          className="w-full py-2 bg-red-900/50 border border-red-800 text-red-300 text-xs font-semibold rounded-lg hover:bg-red-900/70">
          🚨 Activate Duress
        </button>
      ) : (
        <div className="space-y-2">
          <p className="text-xs text-red-300 text-center font-semibold">Confirm — 911 + vault lock</p>
          <div className="flex gap-2">
            <button className="flex-1 py-2 bg-red-600 text-white text-xs font-bold rounded-lg">CONFIRM</button>
            <button onClick={() => setConfirm(false)} className="flex-1 py-2 bg-slate-700 text-slate-300 text-xs rounded-lg">Cancel</button>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Rx Shopping Radar ─────────────────────────────────────────────────────
function RxShoppingRadar({ profile }: { profile?: any }) {
  const data = [
    { axis:'Pharmacy Count\n(24h)', patient:profile?.biometric_pharmacy_visits_24h||1, avg:1.2 },
    { axis:'Prescriber Count\n(30d)', patient:profile?.pdmp_prescriber_count_30d||1, avg:1.5 },
    { axis:'MME Aggregate', patient:Math.min(10,(profile?.daily_mme_estimate||40)/100*10), avg:3 },
    { axis:'Gap Patterns', patient:3, avg:2 },
    { axis:'Cash Pay Rate', patient:2, avg:1.8 },
  ]
  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-2">Rx Shopping Risk Radar</p>
      <ResponsiveContainer width="100%" height={160}>
        <RadarChart data={data} margin={{top:10,right:20,left:20,bottom:10}}>
          <PolarGrid stroke="#1e293b" />
          <PolarAngleAxis dataKey="axis" tick={{ fill:'#475569', fontSize:8 }} />
          <PolarRadiusAxis domain={[0,10]} tick={{ fill:'#475569', fontSize:7 }} />
          <Radar name="Patient" dataKey="patient" stroke="#ef4444" fill="#ef4444" fillOpacity={0.2} />
          <Radar name="Avg" dataKey="avg" stroke="#64748b" fill="#64748b" fillOpacity={0.1} strokeDasharray="4 2" />
          <Tooltip contentStyle={{ background:'#1a1f2e', border:'1px solid #334155', borderRadius:8, fontSize:10 }} />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────
export default function SecuritySurveillance() {
  const { data: securityData } = useQuery({
    queryKey: ['security-events'],
    queryFn: () => apiClient.get('/security/events?resolved=false&limit=20').then(r => r.data as any[]),
    refetchInterval: 10_000,
    placeholderData: [
      { event_type:'vault_zone_intrusion', severity:'critical', detected_at:'10:42:15', resolved:false, description:'Unauthorized entry detected in vault room', metadata:{camera_zone:'vault_room',confidence:0.94} },
      { event_type:'loitering', severity:'warning', detected_at:'10:38:02', resolved:false, description:'Person stationary 9 minutes in waiting area', metadata:{camera_zone:'waiting_area',confidence:0.78} },
    ],
  })
  const { data: summary } = useQuery({
    queryKey: ['security-summary'],
    queryFn: () => apiClient.get('/security/summary').then(r => r.data),
    refetchInterval: 15_000,
  })

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">Security & Surveillance</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            {summary?.critical_unresolved > 0
              ? <span className="text-red-400 font-semibold">⚠ {summary.critical_unresolved} critical alerts active</span>
              : <span className="text-green-400">✓ No active critical alerts</span>}
          </p>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <div className="col-span-2"><PharmacyFloorMap events={securityData || []} /></div>
        <DuressPanel />
      </div>
      <div className="grid grid-cols-2 gap-4">
        <BehavioralTimeline events={securityData || []} />
        <RxShoppingRadar />
      </div>
    </div>
  )
}
