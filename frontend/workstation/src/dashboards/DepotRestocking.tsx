/**
 * Depot → Shelf Restocking (dual-verification)
 * ============================================
 * Multi-step replenishment: build FEFO pick list → depot checkpoint (barcode +
 * count) → shelf placement (barcode + AI verify) → finalize. Plus the owner
 * surveillance feed. Wired to /api/v1/inventory (Phase-1 backend). The 10 wall
 * cameras post into the surveillance feed; they never block a transfer.
 */
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { depotApi } from '../lib/api'

interface Shelf {
  id: string; label: string; zone: string | null
  capacity_units: number; current_units: number; storage_condition: string
}
interface SurveillanceEvent {
  id: string; camera_id: string | null; event_type: string; severity: string
  detected_at: string; owner_notified: boolean; reviewed_by: string | null
}

const STEPS = ['Pick list', 'Depot checkpoint', 'Shelf placement', 'Done'] as const

export default function DepotRestocking() {
  const qc = useQueryClient()
  const { data: shelves = [] } = useQuery<Shelf[]>({
    queryKey: ['depot-shelves'],
    queryFn: () => depotApi.listShelves().then(r => r.data),
  })

  const [step, setStep] = useState(0)
  const [shelfId, setShelfId] = useState('')
  const [ndcs, setNdcs] = useState('')
  const [sessionId, setSessionId] = useState('')
  const [pickList, setPickList] = useState<any[]>([])

  // Staged item (the lot being moved) — drives both checkpoints
  const [staged, setStaged] = useState({ ndc11: '', lot_number: '', expiry_date: '', serial: '' })
  const [count, setCount] = useState(0)
  const [aiResult, setAiResult] = useState<any>(null)
  const [temp, setTemp] = useState('')
  const [attestBy, setAttestBy] = useState('')
  const [attestPin, setAttestPin] = useState('')
  const [overrideReason, setOverrideReason] = useState('')
  const [result, setResult] = useState<any>(null)
  const [err, setErr] = useState('')

  const createSession = useMutation({
    mutationFn: () => depotApi.createSession({ shelf_id: shelfId, ndc11s: ndcs.split(',').map(s => s.trim()).filter(Boolean) }),
    onSuccess: (r) => { setSessionId(r.data.id); setPickList(r.data.pick_list || []); setStep(1); setErr('') },
    onError: (e: any) => setErr(e?.response?.data?.detail || 'Failed to build pick list'),
  })

  const depotCollect = useMutation({
    mutationFn: () => depotApi.depotCollect(sessionId, {
      counted_units: count, inventory_lot_id: staged.lot_number || '00000000-0000-0000-0000-000000000000',
      staged: { ...staged, expiry_date: staged.expiry_date },
      scans: [{ ...staged }],
    }),
    onSuccess: () => { setStep(2); setErr('') },
    onError: (e: any) => setErr(e?.response?.data?.detail || 'Depot checkpoint failed'),
  })

  const verify = useMutation({
    mutationFn: () => depotApi.shelfVerify({
      image_base64: null, staged_ndc: staged.ndc11, staged_lot: staged.lot_number,
      staged_quantity: count, expected_drug_name: '', expected_drug_form: 'tablet',
    }),
    onSuccess: (r) => setAiResult(r.data),
  })

  const place = useMutation({
    mutationFn: () => depotApi.shelfPlace(sessionId, {
      session_id: sessionId, shelf_id: shelfId,
      inventory_lot_id: staged.lot_number || '00000000-0000-0000-0000-000000000000',
      ndc11: staged.ndc11, quantity: count,
      barcode_scans: [{ ...staged }], ai_verification: aiResult?.result ?? {},
      temperature_logged_c: temp ? Number(temp) : null,
      pharmacist_attestation_by: attestBy || null, pharmacist_attestation_pin: attestPin || null,
      override_reason: overrideReason || null,
    }),
    onSuccess: (r) => { setResult(r.data); setStep(3); setErr(''); qc.invalidateQueries({ queryKey: ['depot-shelves'] }) },
    onError: (e: any) => setErr(e?.response?.data?.detail || 'Shelf placement blocked'),
  })

  const input = 'text-sm bg-[#0f1420] border border-[#243042] rounded-md px-3 py-1.5 text-slate-100 placeholder:text-slate-600 focus:outline-none focus:ring-2 focus:ring-blue-500/40'
  const card = 'bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]'

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-100">Depot → shelf restocking</h1>
        <p className="text-sm text-slate-500 mt-0.5">Dual-verification: barcode + count at depot, barcode + AI at shelf — reconciled.</p>
      </div>

      {/* Stepper */}
      <div className="flex items-center gap-2 text-xs">
        {STEPS.map((s, i) => (
          <div key={s} className="flex items-center gap-2">
            <span className={`px-2.5 py-1 rounded-md ${i === step ? 'bg-blue-600 text-white' : i < step ? 'text-emerald-400' : 'text-slate-500'}`}>
              {i < step ? '✓ ' : ''}{s}
            </span>
            {i < STEPS.length - 1 && <span className="text-slate-700">→</span>}
          </div>
        ))}
      </div>

      {err && <div className="bg-red-950/40 border border-red-900/50 text-red-300 rounded-lg p-3 text-sm">⚠ {err}</div>}

      <div className="grid grid-cols-3 gap-6">
        <div className="col-span-2 space-y-4">
          {step === 0 && (
            <div className={card + ' space-y-3'}>
              <p className="text-sm font-semibold text-slate-200">1 · Build FEFO pick list</p>
              <label className="block text-xs text-slate-400">Shelf
                <select value={shelfId} onChange={e => setShelfId(e.target.value)} className={input + ' w-full mt-1'}>
                  <option value="">Select a shelf…</option>
                  {shelves.map(s => <option key={s.id} value={s.id}>{s.label} ({s.current_units}/{s.capacity_units})</option>)}
                </select>
              </label>
              <label className="block text-xs text-slate-400">NDC(s) to replenish (comma-separated)
                <input value={ndcs} onChange={e => setNdcs(e.target.value)} placeholder="00185-0127-01, …" className={input + ' w-full mt-1'} />
              </label>
              <button onClick={() => createSession.mutate()} disabled={!shelfId || createSession.isPending}
                className="text-sm px-4 py-2 rounded-md bg-blue-600 text-white hover:bg-blue-500 disabled:opacity-40">
                Build pick list (FEFO)
              </button>
            </div>
          )}

          {step === 1 && (
            <div className={card + ' space-y-3'}>
              <p className="text-sm font-semibold text-slate-200">2 · Depot checkpoint — scan + count</p>
              <div className="grid grid-cols-2 gap-2">
                <input value={staged.ndc11} onChange={e => setStaged({ ...staged, ndc11: e.target.value })} placeholder="Scanned NDC" className={input} />
                <input value={staged.lot_number} onChange={e => setStaged({ ...staged, lot_number: e.target.value })} placeholder="Lot #" className={input} />
                <input type="date" value={staged.expiry_date} onChange={e => setStaged({ ...staged, expiry_date: e.target.value })} className={input} />
                <input value={staged.serial} onChange={e => setStaged({ ...staged, serial: e.target.value })} placeholder="Serial (DSCSA)" className={input} />
              </div>
              <label className="block text-xs text-slate-400">Counted units
                <input type="number" value={count} onChange={e => setCount(Number(e.target.value))} className={input + ' w-32 mt-1 block'} />
              </label>
              <button onClick={() => depotCollect.mutate()} disabled={!staged.ndc11 || depotCollect.isPending}
                className="text-sm px-4 py-2 rounded-md bg-blue-600 text-white hover:bg-blue-500 disabled:opacity-40">
                Confirm depot pick → transport
              </button>
            </div>
          )}

          {step === 2 && (
            <div className={card + ' space-y-3'}>
              <p className="text-sm font-semibold text-slate-200">3 · Shelf placement — AI verify + finalize</p>
              <button onClick={() => verify.mutate()} disabled={verify.isPending}
                className="text-sm px-3 py-1.5 rounded-md border border-slate-600 text-slate-200 hover:bg-slate-700/40">
                📷 Run AI shelf-verify
              </button>
              {aiResult && (
                <div className="text-xs bg-[#0f1420] border border-[#243042] rounded-lg p-2.5 text-slate-300">
                  Verdict: <b className={aiResult.result.count_verdict === 'pass' ? 'text-emerald-400' : aiResult.result.count_verdict === 'block' ? 'text-red-400' : 'text-amber-400'}>{aiResult.result.count_verdict}</b>
                  {aiResult.degraded && <span className="text-amber-400"> · degraded (manual confirm)</span>}
                  <div className="text-slate-500 mt-0.5">{aiResult.result.advisory_notes?.join(' ')}</div>
                </div>
              )}
              <div className="grid grid-cols-2 gap-2">
                <input value={temp} onChange={e => setTemp(e.target.value)} placeholder="Temp °C (cold chain)" className={input} />
                <input value={overrideReason} onChange={e => setOverrideReason(e.target.value)} placeholder="Override reason (if AI block)" className={input} />
                <input value={attestBy} onChange={e => setAttestBy(e.target.value)} placeholder="Pharmacist ID (high-risk)" className={input} />
                <input type="password" value={attestPin} onChange={e => setAttestPin(e.target.value)} placeholder="Pharmacist PIN" className={input} />
              </div>
              <button onClick={() => place.mutate()} disabled={place.isPending}
                className="text-sm px-4 py-2 rounded-md bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-40">
                ✓ Finalize & commit to shelf
              </button>
            </div>
          )}

          {step === 3 && result && (
            <div className={card + ' space-y-2'}>
              <p className="text-sm font-semibold text-emerald-400">✓ Transfer committed</p>
              <p className="text-xs text-slate-400">Reconciliation: depot {result.reconciliation?.depot_out} vs shelf {result.reconciliation?.shelf_in} — {result.reconciliation?.match ? 'matched' : `Δ ${result.reconciliation?.delta}`}</p>
              <p className="text-xs text-slate-400">Shelf now at <b className="text-slate-200">{result.shelf_current_units}</b> units</p>
              {result.flags?.capacity_warning && <p className="text-xs text-amber-400">⚠ {result.flags.capacity_warning}</p>}
              {result.primary_location_prompt && <p className="text-xs text-blue-400">Update primary dispensing location for {result.primary_location_prompt.drug}?</p>}
              <button onClick={() => { setStep(0); setResult(null); setAiResult(null); setStaged({ ndc11: '', lot_number: '', expiry_date: '', serial: '' }); setCount(0) }}
                className="text-sm px-3 py-1.5 rounded-md border border-slate-600 text-slate-200 hover:bg-slate-700/40 mt-1">
                Start another transfer
              </button>
            </div>
          )}

          {pickList.length > 0 && step < 3 && (
            <div className={card}>
              <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-2">FEFO pick list</p>
              <div className="space-y-1">
                {pickList.map((l, i) => (
                  <div key={i} className="flex justify-between text-xs text-slate-300 border-b border-[#1e293b] py-1">
                    <span className="font-mono">{l.ndc11} · lot {l.lot_number}</span>
                    <span className="text-slate-500">exp {l.expiry_date} · {l.quantity_on_hand}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Surveillance feed (owner) */}
        <SurveillancePanel cardClass={card} />
      </div>
    </div>
  )
}

function SurveillancePanel({ cardClass }: { cardClass: string }) {
  const { data: events = [] } = useQuery<SurveillanceEvent[]>({
    queryKey: ['depot-surveillance'],
    queryFn: () => depotApi.listSurveillance().then(r => r.data),
    refetchInterval: 30_000,
  })
  const sev = (s: string) => s === 'high' ? 'text-red-400' : s === 'medium' ? 'text-amber-400' : 'text-slate-400'
  return (
    <div className={cardClass}>
      <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-2">🎥 Surveillance feed (advisory)</p>
      {events.length === 0 ? (
        <p className="text-xs text-slate-600 italic">No anomaly events. The 10 wall cameras flag wrong-bin / extra-item / behavior here — review only, never blocks a transfer.</p>
      ) : (
        <div className="space-y-1.5">
          {events.map(e => (
            <div key={e.id} className="text-xs border-b border-[#1e293b] py-1.5">
              <div className="flex justify-between">
                <span className={sev(e.severity)}>{e.event_type}</span>
                {e.owner_notified && <span className="text-red-400">owner notified</span>}
              </div>
              <div className="text-slate-600">{new Date(e.detected_at).toLocaleString()} · {e.camera_id ?? 'cam ?'} · {e.reviewed_by ? 'reviewed' : 'unreviewed'}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
