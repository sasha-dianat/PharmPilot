/**
 * Security Dashboard — real-time behavioral alerts and incident management.
 * Receives live events via WebSocket from the behavioral analysis engine.
 * Shows zone status, active alerts, event log, and duress activation.
 */
import { useEffect, useRef, useState } from 'react'
import { apiClient } from '../lib/api'

interface SecurityEvent {
  event_id: string
  event_type: string
  severity: 'critical' | 'high' | 'warning' | 'info'
  description: string
  detected_at: string
  resolved: boolean
  metadata?: Record<string, unknown>
}

interface SecuritySummary {
  critical_unresolved: number
  high_unresolved: number
  warnings_unresolved: number
  events_last_24h: number
  duress_incidents_30d: number
  status: 'normal' | 'alert'
}

const SEVERITY_CONFIG = {
  critical: { bg: 'bg-red-900',    border: 'border-red-500',    dot: 'bg-red-500',    label: 'CRITICAL', pulse: true },
  high:     { bg: 'bg-orange-900', border: 'border-orange-500', dot: 'bg-orange-500', label: 'HIGH',     pulse: false },
  warning:  { bg: 'bg-yellow-900', border: 'border-yellow-600', dot: 'bg-yellow-500', label: 'WARN',     pulse: false },
  info:     { bg: 'bg-slate-800',  border: 'border-slate-600',  dot: 'bg-slate-400',  label: 'INFO',     pulse: false },
}

const BEHAVIOR_ICONS: Record<string, string> = {
  loitering:              '⏱',
  counter_bypass_attempt: '🚨',
  vault_zone_intrusion:   '🔐',
  theft_gesture:          '🕵️',
  aggressive_posture:     '⚡',
  tailgating:             '👥',
  after_hours_presence:   '🌙',
  patient_distress:       '🆘',
  fall_detected:          '🏥',
  duress_incident:        '🚨',
  default:                '👁',
}

export default function SecurityDashboard() {
  const [events, setEvents] = useState<SecurityEvent[]>([])
  const [summary, setSummary] = useState<SecuritySummary | null>(null)
  const [wsConnected, setWsConnected] = useState(false)
  const [duressConfirmOpen, setDuressConfirmOpen] = useState(false)
  const [activatingDuress, setActivatingDuress] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const pharmacyId = localStorage.getItem('pharmacy_id') || ''

  // Load initial event list
  useEffect(() => {
    apiClient.get('/security/events?resolved=false&limit=30')
      .then(r => setEvents(r.data))
      .catch(() => {})

    apiClient.get('/security/summary')
      .then(r => setSummary(r.data))
      .catch(() => {})
  }, [])

  // WebSocket for real-time alerts
  useEffect(() => {
    if (!pharmacyId) return
    const WS = import.meta.env.VITE_WS_URL || 'ws://localhost:8001'
    const ws = new WebSocket(`${WS}/api/v1/security/stream/${pharmacyId}`)
    wsRef.current = ws

    ws.onopen  = () => setWsConnected(true)
    ws.onclose = () => {
      setWsConnected(false)
      setTimeout(() => wsRef.current?.close(), 3000)
    }
    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.type === 'behavior_detection' || data.type === 'duress_activated') {
          const newEvent: SecurityEvent = {
            event_id: data.event_id || crypto.randomUUID(),
            event_type: data.behavior || data.type,
            severity: data.severity as SecurityEvent['severity'],
            description: data.description || data.message || '',
            detected_at: data.timestamp || new Date().toISOString(),
            resolved: false,
            metadata: data,
          }
          setEvents(prev => [newEvent, ...prev].slice(0, 50))

          // Update summary counts
          setSummary(prev => prev ? {
            ...prev,
            critical_unresolved: prev.critical_unresolved + (data.severity === 'critical' ? 1 : 0),
            high_unresolved:     prev.high_unresolved     + (data.severity === 'high' ? 1 : 0),
            events_last_24h:     prev.events_last_24h + 1,
            status: data.severity === 'critical' ? 'alert' : prev.status,
          } : null)
        }

        if (data.type === 'duress_resolved') {
          setSummary(prev => prev ? { ...prev, status: 'normal' } : null)
        }
      } catch { /* ignore */ }
    }

    return () => ws.close()
  }, [pharmacyId])

  const handleResolve = async (eventId: string) => {
    try {
      await apiClient.patch(`/security/events/${eventId}/resolve`)
      setEvents(prev => prev.map(e =>
        e.event_id === eventId ? { ...e, resolved: true } : e
      ))
    } catch { /* ignore */ }
  }

  const handleDuressActivate = async () => {
    setActivatingDuress(true)
    try {
      await apiClient.post('/security/duress/activate', {
        trigger_method: 'manual_staff',
        biometric_ids_present: [],
      })
      setDuressConfirmOpen(false)
      // The WebSocket will push the duress event to all workstations
    } catch (err) {
      console.error('Duress activation failed:', err)
    } finally {
      setActivatingDuress(false)
    }
  }

  const unresolvedEvents = events.filter(e => !e.resolved)
  const criticalEvents   = unresolvedEvents.filter(e => e.severity === 'critical')

  return (
    <div className="h-full flex flex-col bg-slate-900 text-slate-100 overflow-hidden">
      {/* Header */}
      <div className="px-4 py-3 border-b border-slate-700 flex items-center justify-between flex-shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-lg">🛡️</span>
          <span className="font-semibold text-slate-100">Security</span>
          <span className={`w-2 h-2 rounded-full ${wsConnected ? 'bg-green-500' : 'bg-red-500'}`} />
        </div>
        {unresolvedEvents.length > 0 && (
          <span className="text-xs bg-red-600 text-white px-2 py-0.5 rounded-full font-medium">
            {unresolvedEvents.length} active
          </span>
        )}
      </div>

      {/* Summary badges */}
      {summary && (
        <div className="px-3 py-2 flex gap-2 flex-shrink-0 border-b border-slate-700">
          {summary.critical_unresolved > 0 && (
            <span className="text-xs bg-red-800 text-red-200 px-2 py-1 rounded font-mono">
              🚨 {summary.critical_unresolved} CRITICAL
            </span>
          )}
          {summary.high_unresolved > 0 && (
            <span className="text-xs bg-orange-900 text-orange-200 px-2 py-1 rounded font-mono">
              ⚠ {summary.high_unresolved} HIGH
            </span>
          )}
          <span className="text-xs text-slate-400 self-center ml-auto">
            {summary.events_last_24h} events / 24h
          </span>
        </div>
      )}

      {/* Alert list */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-2">
        {unresolvedEvents.length === 0 ? (
          <div className="text-center py-8 text-slate-500">
            <div className="text-3xl mb-2">✓</div>
            <div className="text-sm">No active security alerts</div>
          </div>
        ) : (
          unresolvedEvents.map(event => {
            const cfg  = SEVERITY_CONFIG[event.severity] || SEVERITY_CONFIG.info
            const icon = BEHAVIOR_ICONS[event.event_type] || BEHAVIOR_ICONS.default
            const time = new Date(event.detected_at).toLocaleTimeString([], {
              hour: '2-digit', minute: '2-digit', second: '2-digit',
            })
            return (
              <div
                key={event.event_id}
                className={`${cfg.bg} border ${cfg.border} rounded-lg p-3 text-xs`}
              >
                <div className="flex items-start justify-between gap-2 mb-1">
                  <div className="flex items-center gap-1.5">
                    <span>{icon}</span>
                    <span className={`font-mono font-bold ${cfg.pulse ? 'animate-pulse' : ''}`}>
                      {cfg.label}
                    </span>
                    <span className="text-slate-400">{time}</span>
                  </div>
                  <button
                    onClick={() => handleResolve(event.event_id)}
                    className="text-slate-400 hover:text-slate-200 text-xs"
                  >
                    Resolve
                  </button>
                </div>
                <p className="text-slate-200 leading-relaxed">{event.description}</p>
                {event.metadata?.camera_zone && (
                  <p className="text-slate-400 mt-1">Zone: {String(event.metadata.camera_zone)}</p>
                )}
              </div>
            )
          })
        )}

        {/* Resolved events (collapsed) */}
        {events.filter(e => e.resolved).length > 0 && (
          <details className="mt-2">
            <summary className="text-xs text-slate-500 cursor-pointer">
              {events.filter(e => e.resolved).length} resolved events
            </summary>
            <div className="space-y-1 mt-1">
              {events.filter(e => e.resolved).slice(0, 5).map(event => (
                <div key={event.event_id} className="text-xs text-slate-600 px-2 py-1 bg-slate-800 rounded">
                  ✓ {event.event_type} — {new Date(event.detected_at).toLocaleTimeString()}
                </div>
              ))}
            </div>
          </details>
        )}
      </div>

      {/* Duress button */}
      <div className="px-3 py-3 border-t border-slate-700 flex-shrink-0">
        {!duressConfirmOpen ? (
          <button
            onClick={() => setDuressConfirmOpen(true)}
            className="w-full bg-red-900 hover:bg-red-800 border border-red-700 text-red-200
                       rounded-lg py-2.5 text-sm font-semibold transition-colors"
          >
            🚨 Activate Duress Protocol
          </button>
        ) : (
          <div className="bg-red-900 border border-red-600 rounded-lg p-3 space-y-2">
            <p className="text-red-200 text-xs font-semibold text-center">
              CONFIRM DURESS ACTIVATION
            </p>
            <p className="text-red-300 text-xs text-center">
              This will: call 911 silently, lock the vault, boost cameras, preserve evidence.
            </p>
            <div className="flex gap-2">
              <button
                onClick={handleDuressActivate}
                disabled={activatingDuress}
                className="flex-1 bg-red-600 hover:bg-red-500 text-white rounded py-2 text-xs
                           font-bold disabled:opacity-50"
              >
                {activatingDuress ? 'Activating…' : 'CONFIRM'}
              </button>
              <button
                onClick={() => setDuressConfirmOpen(false)}
                className="flex-1 bg-slate-700 hover:bg-slate-600 text-slate-200 rounded py-2 text-xs"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
