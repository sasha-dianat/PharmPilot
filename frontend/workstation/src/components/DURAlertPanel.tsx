/**
 * DUR Alert Panel — displays drug utilization review alerts with severity tiers.
 * Critical alerts = hard stop (no dismiss). High = override with reason required.
 * Designed to interrupt without inducing alert fatigue on moderate/informational.
 */
import { useState } from 'react'
import type { DURAlert, AlertSeverity } from '../stores/rxQueue'
import { rxApi } from '../lib/api'
import DictateNote from './DictateNote'

interface Props {
  alerts: DURAlert[]
  rxId: string
  onAlertResolved: (alertId: string) => void
}

const SEVERITY_CONFIG: Record<AlertSeverity, {
  bg: string; border: string; icon: string; label: string; canDismiss: boolean
}> = {
  critical: {
    bg: 'bg-red-50', border: 'border-red-600', icon: '🚫',
    label: 'CRITICAL — HARD STOP', canDismiss: false,
  },
  high: {
    bg: 'bg-orange-50', border: 'border-orange-500', icon: '⚠️',
    label: 'HIGH — Override Required', canDismiss: true,
  },
  moderate: {
    bg: 'bg-yellow-50', border: 'border-yellow-400', icon: '⚡',
    label: 'MODERATE — Review', canDismiss: true,
  },
  informational: {
    bg: 'bg-blue-50', border: 'border-blue-300', icon: 'ℹ️',
    label: 'Informational', canDismiss: true,
  },
}

export default function DURAlertPanel({ alerts, rxId, onAlertResolved }: Props) {
  const [overrideMode, setOverrideMode] = useState<string | null>(null)
  const [overrideReason, setOverrideReason] = useState('')
  const [dismissedIds, setDismissedIds] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(false)

  const visibleAlerts = alerts.filter(
    (a) => !dismissedIds.has(a.id) && !a.was_overridden
  )

  const criticalCount = visibleAlerts.filter((a) => a.severity === 'critical').length
  const highCount = visibleAlerts.filter((a) => a.severity === 'high').length

  const handleOverride = async (alert: DURAlert) => {
    if (!overrideReason.trim() || overrideReason.length < 10) return
    setLoading(true)
    try {
      await rxApi.overrideDur(rxId, alert.id, overrideReason)
      setOverrideMode(null)
      setOverrideReason('')
      onAlertResolved(alert.id)
    } catch (err) {
      console.error('Override failed:', err)
    } finally {
      setLoading(false)
    }
  }

  const handleDismiss = (alertId: string) => {
    setDismissedIds((prev) => new Set([...prev, alertId]))
  }

  if (visibleAlerts.length === 0) {
    return (
      <div className="flex items-center gap-2 px-3 py-2 bg-green-50 border border-green-300 rounded text-green-700 text-sm">
        <span>✅</span>
        <span>No DUR alerts — all checks passed</span>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {/* Summary bar */}
      {(criticalCount > 0 || highCount > 0) && (
        <div className="flex items-center gap-3 px-3 py-2 bg-red-100 border border-red-400 rounded font-semibold text-red-800 text-sm">
          {criticalCount > 0 && <span>🚫 {criticalCount} HARD STOP{criticalCount > 1 ? 'S' : ''}</span>}
          {highCount > 0 && <span>⚠️ {highCount} HIGH PRIORITY</span>}
          <span className="ml-auto">Must resolve before dispensing</span>
        </div>
      )}

      {/* Individual alerts — sorted by severity */}
      {[...visibleAlerts]
        .sort((a, b) => {
          const order: AlertSeverity[] = ['critical', 'high', 'moderate', 'informational']
          return order.indexOf(a.severity) - order.indexOf(b.severity)
        })
        .map((alert) => {
          const config = SEVERITY_CONFIG[alert.severity] || SEVERITY_CONFIG.informational
          const isOverriding = overrideMode === alert.id

          return (
            <div
              key={alert.id}
              className={`${config.bg} border-l-4 ${config.border} rounded p-3 space-y-2`}
            >
              {/* Header row */}
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2 font-semibold text-sm">
                  <span>{config.icon}</span>
                  <span className="uppercase text-xs tracking-wide">{config.label}</span>
                  {alert.evidence_grade && (
                    <span className="bg-white border border-gray-300 text-gray-600 text-xs px-1.5 py-0.5 rounded">
                      Grade {alert.evidence_grade}
                    </span>
                  )}
                </div>
                <div className="flex gap-1">
                  {config.canDismiss && !isOverriding && (
                    <>
                      {alert.severity !== 'informational' && (
                        <button
                          onClick={() => setOverrideMode(alert.id)}
                          className="text-xs px-2 py-1 bg-white border border-gray-300 rounded hover:bg-gray-50"
                        >
                          Override
                        </button>
                      )}
                      <button
                        onClick={() => handleDismiss(alert.id)}
                        className="text-xs px-2 py-1 bg-white border border-gray-300 rounded hover:bg-gray-50"
                      >
                        Dismiss
                      </button>
                    </>
                  )}
                </div>
              </div>

              {/* Alert description */}
              <p className="text-sm text-gray-800">{alert.description}</p>
              {alert.interacting_drug_name && (
                <p className="text-xs text-gray-500">
                  Interacting drug: <strong>{alert.interacting_drug_name}</strong>
                </p>
              )}

              {/* Override reason input */}
              {isOverriding && (
                <div className="space-y-2 pt-2 border-t border-gray-200">
                  <div className="flex items-center justify-between">
                    <label className="text-xs font-medium text-gray-700">
                      Override reason (required, min 10 chars):
                    </label>
                    {/* 🎙 Dictate the override reason hands-free */}
                    <DictateNote
                      context="dur_override"
                      language="fa"
                      compact
                      onConfirm={(text) => setOverrideReason(prev => prev ? `${prev} ${text}` : text)}
                    />
                  </div>
                  <textarea
                    autoFocus
                    value={overrideReason}
                    onChange={(e) => setOverrideReason(e.target.value)}
                    rows={2}
                    className="w-full text-sm border border-gray-300 rounded p-2 resize-none focus:outline-none focus:ring-2 focus:ring-orange-400"
                    placeholder="Document clinical reasoning (or tap 🎙 to dictate)..."
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleOverride(alert)}
                      disabled={overrideReason.length < 10 || loading}
                      className="text-xs px-3 py-1.5 bg-orange-500 text-white rounded hover:bg-orange-600 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {loading ? 'Saving…' : 'Confirm Override'}
                    </button>
                    <button
                      onClick={() => { setOverrideMode(null); setOverrideReason('') }}
                      className="text-xs px-3 py-1.5 bg-white border border-gray-300 rounded hover:bg-gray-50"
                    >
                      Cancel
                    </button>
                    <span className="text-xs text-gray-400 self-center">
                      {overrideReason.length}/10 chars min
                    </span>
                  </div>
                </div>
              )}
            </div>
          )
        })}
    </div>
  )
}
