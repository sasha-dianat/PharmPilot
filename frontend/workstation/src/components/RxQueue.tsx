/**
 * Real-time Rx dispensing queue — the primary pharmacist workstation view.
 * Shows all active prescriptions color-coded by status and clinical risk.
 * Keyboard navigable: Arrow keys + Enter to select, C to claim, R to release.
 */
import { useEffect, useRef } from 'react'
import { useRxQueueStore, type Prescription, type RxStatus } from '../stores/rxQueue'
import { rxApi } from '../lib/api'

const STATUS_CONFIG: Record<RxStatus, { label: string; color: string; priority: number }> = {
  intake:                    { label: 'Intake',          color: 'bg-gray-100 text-gray-700',   priority: 5 },
  pending_dur:               { label: 'DUR Pending',     color: 'bg-purple-100 text-purple-700', priority: 4 },
  dur_hold:                  { label: 'DUR Hold',        color: 'bg-red-100 text-red-700',     priority: 1 },
  pending_verification:      { label: 'Verify',          color: 'bg-blue-100 text-blue-700',   priority: 2 },
  verification_in_progress:  { label: 'In Progress',     color: 'bg-indigo-100 text-indigo-700', priority: 3 },
  pending_adjudication:      { label: 'Adjudicating',    color: 'bg-yellow-100 text-yellow-700', priority: 3 },
  adjudication_rejected:     { label: 'Rejected',        color: 'bg-red-200 text-red-800',     priority: 1 },
  pending_pa:                { label: 'PA Required',     color: 'bg-orange-100 text-orange-700', priority: 2 },
  ready_to_fill:             { label: 'Ready to Fill',   color: 'bg-green-100 text-green-700', priority: 2 },
  filling:                   { label: 'Filling',         color: 'bg-teal-100 text-teal-700',   priority: 3 },
  filled:                    { label: 'Filled',          color: 'bg-emerald-100 text-emerald-700', priority: 4 },
  will_call:                 { label: 'Will Call',       color: 'bg-sky-100 text-sky-700',     priority: 5 },
  dispensed:                 { label: 'Dispensed',       color: 'bg-gray-100 text-gray-500',   priority: 9 },
  cancelled:                 { label: 'Cancelled',       color: 'bg-gray-100 text-gray-400',   priority: 9 },
  on_hold:                   { label: 'On Hold',         color: 'bg-amber-100 text-amber-700', priority: 6 },
}

interface RxCardProps {
  rx: Prescription
  isSelected: boolean
  onSelect: () => void
  onClaim: () => void
}

function RxCard({ rx, isSelected, onSelect, onClaim }: RxCardProps) {
  const status = STATUS_CONFIG[rx.status] || STATUS_CONFIG.intake
  const hasAlerts = (rx.dur_alerts?.length ?? 0) > 0
  const criticalAlerts = rx.dur_alerts?.filter((a) => a.severity === 'critical').length ?? 0
  const highAlerts = rx.dur_alerts?.filter((a) => a.severity === 'high').length ?? 0
  const aiRisk = rx.ai_risk_score ?? 0

  return (
    <div
      onClick={onSelect}
      onDoubleClick={onClaim}
      tabIndex={0}
      className={`
        border rounded-lg p-3 cursor-pointer transition-all select-none
        ${isSelected
          ? 'border-blue-500 bg-blue-50 shadow-md'
          : 'border-gray-200 bg-white hover:border-gray-400 hover:shadow-sm'
        }
        ${criticalAlerts > 0 ? 'ring-2 ring-red-400' : ''}
      `}
    >
      {/* Top row: Rx number + status badge */}
      <div className="flex items-center justify-between gap-2 mb-1">
        <span className="font-mono text-sm font-semibold text-gray-900">{rx.rx_number}</span>
        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${status.color}`}>
          {status.label}
        </span>
      </div>

      {/* Drug name */}
      <div className="text-sm font-medium text-gray-800 truncate">
        {rx.drug_name} {rx.drug_strength}
        {rx.is_controlled && (
          <span className="ml-1 text-xs bg-orange-100 text-orange-700 px-1 rounded">
            {rx.dea_schedule}
          </span>
        )}
      </div>

      {/* Sig summary */}
      <div className="text-xs text-gray-500 truncate mt-0.5">{rx.sig_text}</div>

      {/* Bottom row: alerts + AI risk */}
      {(hasAlerts || aiRisk > 0.5) && (
        <div className="flex items-center gap-2 mt-2">
          {criticalAlerts > 0 && (
            <span className="text-xs bg-red-100 text-red-700 px-1.5 py-0.5 rounded font-medium">
              🚫 {criticalAlerts} CRITICAL
            </span>
          )}
          {highAlerts > 0 && (
            <span className="text-xs bg-orange-100 text-orange-700 px-1.5 py-0.5 rounded">
              ⚠️ {highAlerts} high
            </span>
          )}
          {aiRisk > 0.7 && (
            <span className="text-xs bg-purple-100 text-purple-700 px-1.5 py-0.5 rounded ml-auto">
              AI Risk: {(aiRisk * 100).toFixed(0)}%
            </span>
          )}
        </div>
      )}

      {/* Claimed indicator */}
      {rx.status === 'verification_in_progress' && rx.claimed_by_staff_id && (
        <div className="mt-1 text-xs text-indigo-600">● In verification</div>
      )}
    </div>
  )
}

export default function RxQueue() {
  const { queue, selectedRxId, selectRx, setSelectedRx } = useRxQueueStore()
  const containerRef = useRef<HTMLDivElement>(null)

  // Keyboard navigation
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (!queue.length) return
      const currentIdx = queue.findIndex((rx) => rx.id === selectedRxId)

      if (e.key === 'ArrowDown') {
        e.preventDefault()
        const nextIdx = Math.min(currentIdx + 1, queue.length - 1)
        selectRx(queue[nextIdx].id)
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        const prevIdx = Math.max(currentIdx - 1, 0)
        selectRx(queue[prevIdx].id)
      }
    }

    containerRef.current?.addEventListener('keydown', handleKeyDown)
    return () => containerRef.current?.removeEventListener('keydown', handleKeyDown)
  }, [queue, selectedRxId, selectRx])

  const handleClaim = async (rxId: string) => {
    try {
      const { data } = await rxApi.claim(rxId)
      setSelectedRx(data.rx)
    } catch (err: unknown) {
      const axiosError = err as { response?: { status: number; data: { detail: string } } }
      if (axiosError.response?.status === 409) {
        alert(`Cannot claim: ${axiosError.response.data.detail}`)
      }
    }
  }

  // Group queue by priority
  const activeRxs = queue.filter((rx) =>
    !['dispensed', 'cancelled', 'returned_to_stock'].includes(rx.status)
  )

  const urgentRxs = activeRxs.filter((rx) =>
    ['dur_hold', 'adjudication_rejected', 'pending_pa'].includes(rx.status)
  )
  const workingRxs = activeRxs.filter((rx) =>
    ['pending_verification', 'verification_in_progress', 'pending_adjudication'].includes(rx.status)
  )
  const readyRxs = activeRxs.filter((rx) =>
    ['ready_to_fill', 'filling', 'filled', 'will_call'].includes(rx.status)
  )
  const otherRxs = activeRxs.filter((rx) =>
    !urgentRxs.find((u) => u.id === rx.id) &&
    !workingRxs.find((w) => w.id === rx.id) &&
    !readyRxs.find((r) => r.id === rx.id)
  )

  const renderGroup = (title: string, rxs: Prescription[], dot: string) => {
    if (!rxs.length) return null
    return (
      <div>
        <div className="flex items-center gap-2 mb-2 px-1">
          <span className={`w-2 h-2 rounded-full ${dot}`} />
          <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
            {title} ({rxs.length})
          </span>
        </div>
        <div className="space-y-2">
          {rxs.map((rx) => (
            <RxCard
              key={rx.id}
              rx={rx}
              isSelected={rx.id === selectedRxId}
              onSelect={() => selectRx(rx.id)}
              onClaim={() => handleClaim(rx.id)}
            />
          ))}
        </div>
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      className="h-full overflow-y-auto focus:outline-none"
      tabIndex={-1}
    >
      <div className="p-3 space-y-4">
        {/* Queue header */}
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-gray-900">Rx Queue</h2>
          <span className="text-sm text-gray-500">{activeRxs.length} active</span>
        </div>

        {activeRxs.length === 0 ? (
          <div className="text-center py-8 text-gray-400 text-sm">Queue is empty</div>
        ) : (
          <>
            {renderGroup('Needs Attention', urgentRxs, 'bg-red-500')}
            {renderGroup('In Verification', workingRxs, 'bg-blue-500')}
            {renderGroup('Ready / Filling', readyRxs, 'bg-green-500')}
            {renderGroup('Other', otherRxs, 'bg-gray-400')}
          </>
        )}
      </div>
    </div>
  )
}
