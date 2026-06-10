import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../lib/api'

export type CancellationReason =
  | 'customer_declined'
  | 'prescriber_cancelled'
  | 'duplicate'
  | 'expired'
  | 'insurance_issue'
  | 'other'

export const CANCELLATION_REASON_LABELS: Record<CancellationReason, string> = {
  customer_declined: 'Customer declined pickup',
  prescriber_cancelled: 'Prescriber cancelled',
  duplicate: 'Duplicate entry',
  expired: 'Expired',
  insurance_issue: 'Insurance issue',
  other: 'Other',
}

const REASONS = Object.entries(CANCELLATION_REASON_LABELS) as Array<[CancellationReason, string]>

function errorDetail(err: unknown) {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (Array.isArray(detail)) return detail.map(d => d?.msg || String(d)).join(', ')
  if (typeof detail === 'string') return detail
  return 'Cancellation failed. Check permissions and Rx state.'
}

export default function CancelRxModal({
  rxId,
  rxNumber,
  drugName,
  onClose,
  onCancelled,
}: {
  rxId: string
  rxNumber: string
  drugName: string
  onClose: () => void
  onCancelled: () => void | Promise<void>
}) {
  const queryClient = useQueryClient()
  const [reason, setReason] = useState<CancellationReason>('customer_declined')
  const [error, setError] = useState('')

  const cancelMutation = useMutation({
    mutationFn: () => apiClient.post(`/prescriptions/${rxId}/transition`, {
      to_status: 'cancelled',
      reason,
    }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['prescriptions'] }),
        queryClient.invalidateQueries({ queryKey: ['rx-copilot', rxId] }),
        queryClient.invalidateQueries({ queryKey: ['analysis', rxId] }),
        queryClient.invalidateQueries({ queryKey: ['dur', rxId] }),
      ])
      await onCancelled()
      onClose()
    },
    onError: (err) => setError(errorDetail(err)),
  })

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-xl bg-white shadow-xl border border-red-200">
        <div className="border-b border-gray-200 px-4 py-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="text-base font-semibold text-gray-900">Cancel Rx</h2>
              <p className="text-xs text-gray-500 mt-0.5">
                {rxNumber} · {drugName}
              </p>
            </div>
            <button
              onClick={onClose}
              disabled={cancelMutation.isPending}
              className="text-gray-400 hover:text-gray-600 disabled:opacity-50"
              aria-label="Close cancellation modal">
              ×
            </button>
          </div>
        </div>

        <div className="px-4 py-3 space-y-3">
          <div className="space-y-2">
            {REASONS.map(([code, label]) => (
              <label
                key={code}
                className={`flex items-center gap-3 rounded-lg border px-3 py-2 text-sm cursor-pointer ${
                  reason === code
                    ? 'border-red-300 bg-red-50 text-red-900'
                    : 'border-gray-200 bg-white text-gray-700 hover:bg-gray-50'
                }`}>
                <input
                  type="radio"
                  name="cancellation-reason"
                  value={code}
                  checked={reason === code}
                  onChange={() => {
                    setReason(code)
                    setError('')
                  }}
                  className="text-red-600 focus:ring-red-500"
                />
                <span className="font-medium">{label}</span>
              </label>
            ))}
          </div>

          {reason === 'other' && (
            <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              Use clinical notes to document additional details. Analytics receives only the canonical reason code.
            </p>
          )}

          {error && (
            <div className="rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-gray-200 px-4 py-3">
          <button
            onClick={onClose}
            disabled={cancelMutation.isPending}
            className="px-3 py-2 text-sm rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 disabled:opacity-50">
            Keep Rx
          </button>
          <button
            onClick={() => cancelMutation.mutate()}
            disabled={cancelMutation.isPending}
            className="px-3 py-2 text-sm rounded-lg bg-red-600 text-white font-medium hover:bg-red-700 disabled:opacity-50">
            {cancelMutation.isPending ? 'Cancelling...' : 'Cancel Rx'}
          </button>
        </div>
      </div>
    </div>
  )
}
