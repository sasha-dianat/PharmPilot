/**
 * MovementForm — Record Stock Movement
 * ====================================================================
 * Compact dark-slate card that posts to POST /api/v1/inventory/movements.
 *
 * Two quantity semantics share one number input:
 *   • ADJUSTMENT / CORRECTION → absolute "New on-hand count" → body.new_quantity
 *   • removal types           → "Units to remove"           → body.quantity
 *
 * On success shows a green confirmation (type · before→after · delta).
 * On 422 shows the server `detail` message in red.
 *
 * Palette mirrors MovementsPanel in IntelligenceInventoryPanels.tsx.
 */
import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { AxiosError } from 'axios'
import { apiClient } from '../lib/api'

// ─── Types ───────────────────────────────────────────────────────────────────

type MovementType =
  | 'ADJUSTMENT' | 'CORRECTION' | 'RETURN' | 'DAMAGE' | 'EXPIRY_REMOVAL' | 'RECALL_REMOVAL'

interface MovementBody {
  inventory_lot_id: string
  movement_type: MovementType
  reason: string
  new_quantity?: number
  quantity?: number
  reference?: string
  notes?: string
}

interface MovementResult {
  id: string
  ndc11: string
  movement_type: string
  quantity_before: number
  quantity_after: number
  quantity_delta: number
  reason: string
  reference: string | null
  created_at: string | null
}

// ─── Shared palette (mirrors MovementsPanel) ─────────────────────────────────

const MOVE_COLOR: Record<MovementType, string> = {
  ADJUSTMENT:     'bg-slate-800 text-slate-300',
  CORRECTION:     'bg-slate-800 text-slate-300',
  RETURN:         'bg-blue-900/50 text-blue-300',
  DAMAGE:         'bg-red-900/50 text-red-300',
  EXPIRY_REMOVAL: 'bg-amber-900/50 text-amber-300',
  RECALL_REMOVAL: 'bg-purple-900/50 text-purple-300',
}

const MOVE_TYPES: MovementType[] = [
  'ADJUSTMENT', 'CORRECTION', 'RETURN', 'DAMAGE', 'EXPIRY_REMOVAL', 'RECALL_REMOVAL',
]

const isAbsolute = (t: MovementType) => t === 'ADJUSTMENT' || t === 'CORRECTION'

// ─── Component ───────────────────────────────────────────────────────────────

export default function MovementForm() {
  const [movementType, setMovementType] = useState<MovementType>('ADJUSTMENT')
  const [lotId, setLotId] = useState('')
  const [qty, setQty] = useState('')
  const [reason, setReason] = useState('')
  const [reference, setReference] = useState('')
  const [notes, setNotes] = useState('')

  const mutation = useMutation<MovementResult, AxiosError<{ detail?: string }>, MovementBody>({
    mutationFn: (body) =>
      apiClient.post('/inventory/movements', body).then((r) => r.data as MovementResult),
    onSuccess: () => {
      // Keep lot context, clear the per-movement entry fields.
      setQty('')
      setReason('')
      setReference('')
      setNotes('')
    },
  })

  const absolute = isAbsolute(movementType)
  const qtyNum = Number(qty)
  const qtyValid = qty.trim() !== '' && Number.isFinite(qtyNum) && qtyNum >= 0
  const canSubmit =
    lotId.trim() !== '' && reason.trim() !== '' && qtyValid && !mutation.isPending

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!canSubmit) return
    const body: MovementBody = {
      inventory_lot_id: lotId.trim(),
      movement_type: movementType,
      reason: reason.trim(),
      ...(absolute ? { new_quantity: qtyNum } : { quantity: qtyNum }),
      ...(reference.trim() ? { reference: reference.trim() } : {}),
      ...(notes.trim() ? { notes: notes.trim() } : {}),
    }
    mutation.mutate(body)
  }

  const inputCls =
    'w-full rounded-lg bg-slate-800/60 border border-slate-700 px-3 py-1.5 text-sm ' +
    'text-slate-200 placeholder-slate-500 focus:outline-none focus:border-slate-500'
  const labelCls = 'block text-[10px] text-slate-500 uppercase tracking-wider mb-1'

  const errorDetail =
    mutation.error?.response?.data?.detail ?? mutation.error?.message ?? null

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-slate-200">✏️ Record Stock Movement</h2>
        <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${MOVE_COLOR[movementType]}`}>
          {movementType.replace('_', ' ')}
        </span>
      </div>

      <form onSubmit={handleSubmit} className="space-y-3">
        {/* Movement type */}
        <div>
          <label className={labelCls}>Movement type</label>
          <select
            value={movementType}
            onChange={(e) => setMovementType(e.target.value as MovementType)}
            className={inputCls}
          >
            {MOVE_TYPES.map((t) => (
              <option key={t} value={t}>{t.replace('_', ' ')}</option>
            ))}
          </select>
        </div>

        {/* Lot id */}
        <div>
          <label className={labelCls}>Inventory lot ID (UUID)</label>
          <input
            value={lotId}
            onChange={(e) => setLotId(e.target.value)}
            placeholder="00000000-0000-0000-0000-000000000000"
            className={`${inputCls} font-mono text-[12px]`}
          />
        </div>

        {/* Quantity — label switches on type */}
        <div>
          <label className={labelCls}>
            {absolute ? 'New on-hand count' : 'Units to remove'}
          </label>
          <input
            type="number"
            min={0}
            value={qty}
            onChange={(e) => setQty(e.target.value)}
            placeholder={absolute ? 'absolute new count' : 'units to remove'}
            className={inputCls}
          />
          <p className="text-[10px] text-slate-600 mt-1">
            {absolute
              ? 'Sets stock to this absolute count.'
              : 'Subtracts this many units from on-hand.'}
          </p>
        </div>

        {/* Reason */}
        <div>
          <label className={labelCls}>Reason <span className="text-red-400">*</span></label>
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. cycle count correction"
            className={inputCls}
          />
        </div>

        {/* Reference + notes */}
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className={labelCls}>Reference</label>
            <input
              value={reference}
              onChange={(e) => setReference(e.target.value)}
              placeholder="RMA / recall ref"
              className={inputCls}
            />
          </div>
          <div>
            <label className={labelCls}>Notes</label>
            <input
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="optional"
              className={inputCls}
            />
          </div>
        </div>

        <button
          type="submit"
          disabled={!canSubmit}
          className="w-full rounded-lg bg-blue-900/50 text-blue-200 text-sm font-medium py-2
                     hover:bg-blue-800/50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {mutation.isPending ? 'Recording…' : 'Record movement'}
        </button>
      </form>

      {/* Success confirmation */}
      {mutation.isSuccess && mutation.data && (
        <div className="mt-3 rounded-lg border border-emerald-900 bg-emerald-950/40 px-3 py-2">
          <div className="flex items-center justify-between">
            <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
              MOVE_COLOR[(mutation.data.movement_type as MovementType)] || 'bg-slate-800 text-slate-300'}`}>
              {mutation.data.movement_type.replace('_', ' ')}
            </span>
            <span className="font-mono text-[11px] text-slate-400">{mutation.data.ndc11}</span>
            <span className={`text-[11px] font-semibold ${
              mutation.data.quantity_delta < 0 ? 'text-red-300' : 'text-emerald-300'}`}>
              {mutation.data.quantity_delta > 0 ? '+' : ''}{mutation.data.quantity_delta}
            </span>
          </div>
          <p className="text-[11px] text-emerald-300 mt-1">
            ✓ Recorded · {mutation.data.quantity_before}→{mutation.data.quantity_after}
            {mutation.data.reference ? ` · ${mutation.data.reference}` : ''}
          </p>
        </div>
      )}

      {/* Error (422 detail) */}
      {mutation.isError && (
        <div className="mt-3 rounded-lg border border-red-900 bg-red-950/40 px-3 py-2">
          <p className="text-[11px] text-red-300">⚠ {errorDetail}</p>
        </div>
      )}
    </div>
  )
}
