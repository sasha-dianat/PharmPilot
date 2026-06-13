/**
 * Real-time Rx dispensing queue — the primary pharmacist workstation view.
 * Shows all active prescriptions color-coded by status and clinical risk.
 * Keyboard navigable: Arrow keys + Enter to select, C to claim, R to release.
 *
 * "Clinical Daylight" UI: rows lead with the short Rx token (#0196, the
 * rx_number tail) + status + patient anchor + lane colour, never the drug name
 * as the primary key. All store/keyboard/claim/grouping logic is unchanged.
 */
import { useEffect, useRef } from 'react'
import { useRxQueueStore, type Prescription, type RxStatus } from '../stores/rxQueue'
import { rxApi } from '../lib/api'

const STATUS_CONFIG: Record<RxStatus, { label: string; tone: string; lane: string; priority: number }> = {
  intake:                    { label: 'Intake',        tone: 'bg-surface2 text-ink2 border-line2',          lane: 'bg-line2',    priority: 5 },
  pending_dur:               { label: 'DUR pending',   tone: 'bg-counsel-soft text-counsel border-counsel/25', lane: 'bg-counsel', priority: 4 },
  dur_hold:                  { label: 'DUR hold',      tone: 'bg-blocker-soft text-blocker border-blocker/30', lane: 'bg-blocker', priority: 1 },
  pending_verification:      { label: 'Verify',        tone: 'bg-intel-soft text-intel border-intel/25',    lane: 'bg-intel',    priority: 2 },
  verification_in_progress:  { label: 'In progress',   tone: 'bg-intel-soft text-intel border-intel/25',    lane: 'bg-intel',    priority: 3 },
  pending_adjudication:      { label: 'Adjudicating',  tone: 'bg-warning-soft text-warning border-warning/25', lane: 'bg-warning', priority: 3 },
  adjudication_rejected:     { label: 'Rejected',      tone: 'bg-blocker-soft text-blocker border-blocker/30', lane: 'bg-blocker', priority: 1 },
  pending_pa:                { label: 'PA required',   tone: 'bg-caution-soft text-caution border-caution/30', lane: 'bg-caution', priority: 2 },
  ready_to_fill:             { label: 'Ready to fill', tone: 'bg-safe-soft text-safe border-safe/25',        lane: 'bg-safe',     priority: 2 },
  filling:                   { label: 'Filling',       tone: 'bg-safe-soft text-safe border-safe/25',        lane: 'bg-safe',     priority: 3 },
  filled:                    { label: 'Filled',        tone: 'bg-safe-soft text-safe border-safe/25',        lane: 'bg-safe',     priority: 4 },
  will_call:                 { label: 'Will call',     tone: 'bg-intel-soft text-intel border-intel/25',    lane: 'bg-intel',    priority: 5 },
  dispensed:                 { label: 'Dispensed',     tone: 'bg-surface2 text-ink3 border-line2',          lane: 'bg-line2',    priority: 9 },
  cancelled:                 { label: 'Cancelled',     tone: 'bg-surface2 text-ink3 border-line2',          lane: 'bg-line2',    priority: 9 },
  on_hold:                   { label: 'On hold',       tone: 'bg-warning-soft text-warning border-warning/25', lane: 'bg-warning', priority: 6 },
}

function rxToken(rxNumber: string | undefined): string {
  const n = rxNumber ?? ''
  return n.length > 4 ? `#${n.slice(-4)}` : (n ? `#${n}` : '#----')
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
  const patientName = (rx as any).patient_name as string | undefined

  return (
    <div
      onClick={onSelect}
      onDoubleClick={onClaim}
      tabIndex={0}
      className={`cd-hover relative overflow-hidden rounded-xl pl-3 pr-3 py-2.5 cursor-pointer select-none border ${
        isSelected ? 'border-intel bg-intel-soft' : 'border-line bg-surface hover:border-line2'
      } ${criticalAlerts > 0 ? 'ring-1 ring-blocker/40' : ''}`}
    >
      <span className={`absolute left-0 top-0 bottom-0 w-1 ${status.lane}`} aria-hidden="true" />

      <div className="flex items-center justify-between gap-2">
        <span className="cd-data text-sm font-semibold text-ink">{rxToken(rx.rx_number)}</span>
        <span className={`cd-data text-[10px] px-2 py-0.5 rounded-md font-medium border ${status.tone}`}>
          {status.label}
        </span>
      </div>

      {patientName && <div className="text-xs text-ink2 mt-0.5 truncate">{patientName}</div>}

      <div className="text-[11px] text-ink3 truncate mt-0.5">
        {rx.drug_name} {rx.drug_strength}
        {rx.is_controlled && (
          <span className="cd-data ml-1 text-[10px] bg-caution-soft text-caution px-1 rounded">{rx.dea_schedule}</span>
        )}
      </div>

      {(hasAlerts || aiRisk > 0.5) && (
        <div className="flex items-center gap-1.5 mt-2">
          {criticalAlerts > 0 && (
            <span className="cd-data text-[10px] bg-blocker-soft text-blocker px-1.5 py-0.5 rounded font-medium">🚫 {criticalAlerts}</span>
          )}
          {highAlerts > 0 && (
            <span className="cd-data text-[10px] bg-caution-soft text-caution px-1.5 py-0.5 rounded">⚠ {highAlerts}</span>
          )}
          {aiRisk > 0.7 && (
            <span className="cd-data text-[10px] bg-counsel-soft text-counsel px-1.5 py-0.5 rounded ml-auto">Risk {(aiRisk * 100).toFixed(0)}%</span>
          )}
        </div>
      )}

      {rx.status === 'verification_in_progress' && rx.claimed_by_staff_id && (
        <div className="mt-1 text-[10px] text-intel flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-intel" /> In verification</div>
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
      <div className="cd-section">
        <div className="flex items-center gap-2 mb-2 px-1">
          <span className={`w-2 h-2 rounded-full ${dot}`} />
          <span className="cd-ui text-[10px] font-semibold text-ink3 uppercase tracking-wider">
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
      className="cd-scope h-full overflow-y-auto focus:outline-none"
      tabIndex={-1}
    >
      <div className="p-3 space-y-4">
        {/* Queue header */}
        <div className="flex items-center justify-between">
          <h2 className="cd-ui font-semibold text-ink">Rx queue</h2>
          <span className="cd-data text-xs text-ink3">{activeRxs.length} active</span>
        </div>

        {activeRxs.length === 0 ? (
          <div className="text-center py-8 text-ink3 text-sm">Queue is empty</div>
        ) : (
          <>
            {renderGroup('Needs attention', urgentRxs, 'bg-blocker')}
            {renderGroup('In verification', workingRxs, 'bg-intel')}
            {renderGroup('Ready / filling', readyRxs, 'bg-safe')}
            {renderGroup('Other', otherRxs, 'bg-line2')}
          </>
        )}
      </div>
    </div>
  )
}
