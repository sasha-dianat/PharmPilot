/**
 * SECTION 7: Audio & Conversation Intelligence
 * ==============================================
 * QA + knowledge extraction. What did patients say? What needs review?
 * Pharmacist approves all AI-extracted clinical facts before they are saved.
 */
import { useState, useEffect, useRef } from 'react'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../lib/api'
import { CounselingScorecard } from '../components/IntelligenceWorkflowBits'

// ── Entity colors for transcript highlighting ─────────────────────────────
const ENTITY_STYLE: Record<string, string> = {
  drug:      'bg-blue-900/50 text-blue-300 px-1 rounded',
  condition: 'bg-orange-900/50 text-orange-300 px-1 rounded',
  allergy:   'bg-red-900/50 text-red-300 px-1 rounded',
  lab_value: 'bg-green-900/50 text-green-300 px-1 rounded',
  default:   'text-slate-200',
}

// ── Live Transcript Feed ───────────────────────────────────────────────────
function LiveTranscriptFeed() {
  const [segments, setSegments] = useState([
    { speaker:'PHARMACIST', text:'Good morning! I see you\'re here to pick up your ', entities:[] },
    { speaker:'PATIENT', text:'Yes, also I wanted to mention I started taking a ', entities:[{word:'new supplement',type:'drug'}], suffix:' for my joints.' },
    { speaker:'PHARMACIST', text:'Which supplement? Any ', entities:[{word:'allergies',type:'allergy'}], suffix:' I should know about?' },
    { speaker:'PATIENT', text:'Fish oil, 3 grams daily. And yes — I\'m allergic to ', entities:[{word:'penicillin',type:'allergy'}], suffix:'. Caused a rash.' },
    { speaker:'PHARMACIST', text:'Thank you. Your last ', entities:[{word:'A1C',type:'lab_value'}], suffix:' from Dr. Smith was what, do you remember?' },
    { speaker:'PATIENT', text:'I think it was ', entities:[{word:'7.8%',type:'lab_value'}], suffix:' about 3 months ago.' },
  ])
  const endRef = useRef<HTMLDivElement>(null)
  useEffect(() => { endRef.current?.scrollIntoView({ behavior:'smooth' }) }, [segments])

  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <div className="flex items-center justify-between mb-3">
        <p className="text-xs font-semibold uppercase tracking-widest text-slate-500">Live Transcript — Counter Zone</p>
        <div className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
          <span className="text-xs text-red-400">Recording</span>
        </div>
      </div>
      <div className="space-y-2 max-h-56 overflow-y-auto text-xs">
        {segments.map((seg, i) => (
          <div key={i} className={`flex gap-2 ${seg.speaker==='PHARMACIST' ? 'flex-row-reverse' : ''}`}>
            <span className={`flex-shrink-0 text-[9px] font-semibold px-1.5 py-0.5 rounded self-start mt-0.5 ${
              seg.speaker==='PHARMACIST' ? 'bg-blue-900/50 text-blue-300' : 'bg-slate-700 text-slate-400'}`}>
              {seg.speaker === 'PHARMACIST' ? 'RPh' : 'Pt'}
            </span>
            <div className={`rounded-lg px-3 py-2 max-w-[75%] ${seg.speaker==='PHARMACIST' ? 'bg-blue-900/20' : 'bg-[#242938]'}`}>
              <span className="text-slate-200">{seg.text}</span>
              {seg.entities?.map((e: any, j: number) => (
                <span key={j}>
                  <span className={ENTITY_STYLE[e.type] || ENTITY_STYLE.default} title={`${e.type} detected`}>{e.word}</span>
                </span>
              ))}
              {(seg as any).suffix && <span className="text-slate-200">{(seg as any).suffix}</span>}
            </div>
          </div>
        ))}
        <div ref={endRef} />
      </div>
      <div className="flex gap-2 mt-2 text-[9px]">
        {['drug','condition','allergy','lab_value'].map(t => (
          <span key={t} className={`px-1.5 py-0.5 rounded ${ENTITY_STYLE[t]}`}>{t}</span>
        ))}
      </div>
    </div>
  )
}

// ── Pending Profile Updates ────────────────────────────────────────────────
function PendingProfileUpdates() {
  const [items, setItems] = useState([
    { id:'1', type:'allergy', description:'New allergy: Penicillin → rash', quote:'"I\'m allergic to penicillin"', confidence:0.92, ts:'10:42' },
    { id:'2', type:'lab_value', description:'A1C: 7.8% (3 months ago)', quote:'"A1C was 7.8% about 3 months ago"', confidence:0.88, ts:'10:43' },
    { id:'3', type:'medication', description:'New supplement: Fish oil 3g daily', quote:'"I started taking fish oil"', confidence:0.79, ts:'10:41' },
  ])

  const approve = (id: string) => {
    setItems(prev => prev.filter(i => i.id !== id))
    apiClient.post(`/audio/transcripts/demo/approve-enrichment/${id}`, null, { params:{ pharmacist_id: localStorage.getItem('staff_id') } }).catch(()=>{})
  }

  const TYPE_ICON: Record<string,string> = { allergy:'⚠️', lab_value:'🔬', medication:'💊' }

  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-orange-900/50">
      <div className="flex justify-between items-center mb-3">
        <p className="text-xs font-semibold uppercase tracking-widest text-orange-400">Pending Profile Updates</p>
        <span className="text-xs bg-orange-900/50 text-orange-300 px-2 py-0.5 rounded">{items.length} awaiting review</span>
      </div>
      {items.length === 0 ? (
        <p className="text-xs text-slate-500 text-center py-4">✓ All extractions reviewed</p>
      ) : (
        <div className="space-y-2" aria-live="polite">
          {items.map(item => (
            <div key={item.id} className="bg-[#0f1117] rounded-lg p-3 border border-[#334155]">
              <div className="flex items-start justify-between gap-2 mb-1">
                <div className="flex items-center gap-1.5">
                  <span>{TYPE_ICON[item.type] || '📋'}</span>
                  <span className="text-xs font-semibold text-slate-200">{item.description}</span>
                </div>
                <span className="text-[10px] text-slate-500 flex-shrink-0">{item.ts}</span>
              </div>
              <p className="text-[10px] text-slate-500 italic mb-2">"{item.quote}"</p>
              <div className="flex items-center justify-between">
                <span className="text-[10px] text-slate-500">
                  Confidence: <span className={item.confidence > 0.85 ? 'text-green-400' : 'text-yellow-400'}>{(item.confidence*100).toFixed(0)}%</span>
                </span>
                <div className="flex gap-1.5">
                  <button onClick={() => approve(item.id)}
                    className="text-[10px] px-2 py-1 bg-green-900/50 text-green-300 rounded hover:bg-green-900/70">
                    ✓ Save
                  </button>
                  <button onClick={() => setItems(prev => prev.filter(i => i.id !== item.id))}
                    className="text-[10px] px-2 py-1 bg-slate-700 text-slate-400 rounded hover:bg-slate-600">
                    ✗ Skip
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Urgency Event Log ──────────────────────────────────────────────────────
function UrgencyEventLog() {
  const events = [
    { ts:'09:15', zone:'Counter', excerpt:'"I have chest pain and shortness of breath"', action:'Pharmacist alerted immediately', type:'cardiac' },
    { ts:'08:42', zone:'Counseling', excerpt:'"I\'ve been having thoughts of hurting myself"', action:'Crisis resources provided, pharmacist notified', type:'mental_health' },
  ]
  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-red-900/50">
      <p className="text-xs font-semibold uppercase tracking-widest text-red-400 mb-3">⚠ Urgency Events</p>
      {events.length === 0 ? (
        <p className="text-xs text-slate-500 text-center py-2">No urgency events today</p>
      ) : (
        <div className="space-y-2">
          {events.map((e, i) => (
            <div key={i} className="bg-red-900/20 rounded-lg p-3 border border-red-800">
              <div className="flex justify-between text-[10px] mb-1">
                <span className="font-mono text-slate-400">{e.ts}</span>
                <span className="text-slate-500">{e.zone}</span>
              </div>
              <p className="text-xs text-red-200 italic mb-1">"{e.excerpt}"</p>
              <p className="text-[10px] text-slate-400">Action: {e.action}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Zone Activity Heatmap (simple) ─────────────────────────────────────────
function ZoneActivityHeatmap() {
  const zones = ['Counter', 'Counseling', 'Drive-Through', 'Waiting']
  const hours = Array.from({length:12}, (_,i) => i+8)
  const data: Record<string, number[]> = {
    Counter: [2,5,8,12,9,7,11,14,10,8,6,3],
    Counseling: [0,1,2,3,4,3,2,4,3,2,1,0],
    'Drive-Through': [1,3,6,8,7,5,9,10,8,7,4,2],
    Waiting: [3,7,10,15,12,9,13,16,12,10,7,4],
  }
  const maxVal = Math.max(...Object.values(data).flat())
  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-3">Zone Conversation Activity</p>
      <div className="space-y-1.5">
        <div className="flex gap-1 ml-20">
          {hours.map(h => (
            <div key={h} className="flex-1 text-center text-[8px] text-slate-600">{h}h</div>
          ))}
        </div>
        {zones.map(zone => (
          <div key={zone} className="flex items-center gap-1">
            <span className="text-[10px] text-slate-400 w-20 flex-shrink-0 text-right pr-2">{zone}</span>
            {(data[zone] || []).map((v, i) => {
              const intensity = v / maxVal
              return (
                <div key={i} className="flex-1 h-4 rounded-sm"
                  style={{ backgroundColor: `rgba(59,130,246,${0.1+intensity*0.9})` }}
                  title={`${zone} ${hours[i]}h: ${v} conversations`} />
              )
            })}
          </div>
        ))}
      </div>
    </div>
  )
}

export default function AudioIntelligence() {
  return (
    <div className="p-6 space-y-4">
      <div>
        <h1 className="text-2xl font-bold text-slate-100">Audio & Conversation Intelligence</h1>
        <p className="text-sm text-slate-500 mt-0.5">Clinical extraction · Pharmacist-reviewed · QA monitoring</p>
      </div>
      <div className="grid grid-cols-2 gap-4">
        <LiveTranscriptFeed />
        <PendingProfileUpdates />
      </div>
      <div className="grid grid-cols-2 gap-4">
        <ZoneActivityHeatmap />
        <UrgencyEventLog />
      </div>

      {/* #11 Counseling Quality — demo transcript scorecard (offline-first) */}
      <div className="grid grid-cols-2 gap-4">
        <CounselingScorecard transcriptText="Pharmacist: This is lisinopril for your blood pressure. Take one tablet once a day with food. You might feel dizzy at first — watch for that. Store it at room temperature. If you miss a dose, take it when you remember unless it's nearly time for the next one. Call us with any questions. Patient: Okay, got it. Thank you, that's clear." />
      </div>
    </div>
  )
}
