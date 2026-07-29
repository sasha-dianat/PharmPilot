/**
 * Rx queue Zustand store — real-time queue state managed via WebSocket.
 * Single source of truth for all prescriptions on the workstation screen.
 */
import { create } from 'zustand'
import { wsUrl } from '../lib/api'

export type RxStatus =
  | 'intake' | 'pending_dur' | 'dur_hold' | 'pending_verification'
  | 'verification_in_progress' | 'pending_adjudication' | 'adjudication_rejected'
  | 'pending_pa' | 'ready_to_fill' | 'filling' | 'filled' | 'will_call'
  | 'dispensed' | 'cancelled' | 'on_hold'

export type AlertSeverity = 'critical' | 'high' | 'moderate' | 'informational'

export interface DURAlert {
  id: string
  alert_type: string
  severity: AlertSeverity
  description: string
  is_hard_stop: boolean
  was_overridden: boolean
  interacting_drug_name?: string
  evidence_grade?: string
}

export interface Prescription {
  id: string
  rx_number: string
  patient_id: string
  drug_name: string
  drug_strength?: string
  sig_text: string
  quantity_prescribed: number
  days_supply: number
  refills_remaining: number
  dea_schedule?: string
  is_controlled: boolean
  status: RxStatus
  source: string
  written_date: string
  claimed_by_staff_id?: string
  ai_risk_score?: number
  acb_safety_report?: {
    severity: string
    primary_finding: string
  }
  dur_alerts?: DURAlert[]
}

export interface PatientProfile {
  id: string
  first_name: string
  last_name: string
  date_of_birth: string
  gender: string
  phone_primary?: string
  allergies: Array<{ allergen_name: string; severity: string; reaction?: string }>
  insurance: Array<{ bin_number: string; member_id: string; priority: number }>
  pending_rxs?: Prescription[]
  active_alerts?: DURAlert[]
}

interface RxQueueState {
  // Queue items
  queue: Prescription[]
  selectedRxId: string | null
  selectedRx: Prescription | null
  patientProfile: PatientProfile | null

  // Biometric pre-load
  incomingPatient: PatientProfile | null
  biometricMatchConfidence: number

  // WebSocket
  wsConnected: boolean
  wsLastUpdate: Date | null

  // Actions
  setQueue: (queue: Prescription[]) => void
  selectRx: (id: string | null) => void
  setSelectedRx: (rx: Prescription | null) => void
  setPatientProfile: (profile: PatientProfile | null) => void
  setIncomingPatient: (patient: PatientProfile | null, confidence: number) => void
  updateRxInQueue: (id: string, updates: Partial<Prescription>) => void
  removeFromQueue: (id: string) => void
  setWsConnected: (connected: boolean) => void
}

export const useRxQueueStore = create<RxQueueState>((set) => ({
  queue: [],
  selectedRxId: null,
  selectedRx: null,
  patientProfile: null,
  incomingPatient: null,
  biometricMatchConfidence: 0,
  wsConnected: false,
  wsLastUpdate: null,

  setQueue: (queue) => set((state) => {
    const nextState: Partial<RxQueueState> = { queue, wsLastUpdate: new Date() }

    if (state.selectedRxId) {
      const refreshedSelection = queue.find((rx) => rx.id === state.selectedRxId) || null
      nextState.selectedRxId = refreshedSelection?.id || null
      nextState.selectedRx = refreshedSelection
    }

    return nextState
  }),

  selectRx: (id) => set((state) => ({
    selectedRxId: id,
    selectedRx: id ? (state.queue.find((rx) => rx.id === id) || null) : null,
  })),

  setSelectedRx: (rx) => set({ selectedRx: rx, selectedRxId: rx?.id || null }),

  setPatientProfile: (profile) => set({ patientProfile: profile }),

  setIncomingPatient: (patient, confidence) =>
    set({ incomingPatient: patient, biometricMatchConfidence: confidence }),

  updateRxInQueue: (id, updates) =>
    set((state) => {
      const queue = state.queue.map((rx) => rx.id === id ? { ...rx, ...updates } : rx)
      const nextState: Partial<RxQueueState> = { queue }

      if (state.selectedRxId) {
        const refreshedSelection = queue.find((rx) => rx.id === state.selectedRxId) || null
        nextState.selectedRxId = refreshedSelection?.id || null
        nextState.selectedRx = refreshedSelection
      }

      return nextState
    }),

  removeFromQueue: (id) =>
    set((state) => ({
      queue: state.queue.filter((rx) => rx.id !== id),
      selectedRxId: state.selectedRxId === id ? null : state.selectedRxId,
      selectedRx: state.selectedRxId === id ? null : state.selectedRx,
    })),

  setWsConnected: (connected) => set({ wsConnected: connected }),
}))


/**
 * WebSocket hook — connects to real-time Rx queue and biometric events.
 */
export function useRxQueueWebSocket(pharmacyId: string) {
  const { setQueue, setWsConnected, setIncomingPatient, updateRxInQueue } = useRxQueueStore()

  // Both sockets are authenticated: the server closes an unticketed handshake
  // with 1008, which the badge reported forever as «Reconnecting…». A ticket is
  // short-lived, so a FRESH one is minted for every attempt, reconnects
  // included.
  const connect = () => {
    let ws: WebSocket | null = null
    let bioWs: WebSocket | null = null
    let closed = false
    let retry: ReturnType<typeof setTimeout> | undefined

    const open = async () => {
      if (closed) return
      const [queueUrl, bioUrl] = await Promise.all([
        wsUrl(`/api/v1/prescriptions/queue/ws/${pharmacyId}`),
        wsUrl(`/api/v1/biometric/stream/${pharmacyId}/counter`),
      ])
      if (closed || !queueUrl || !bioUrl) {
        // no ticket — signed out or the API is down; try again rather than
        // spinning up a socket that is certain to be refused
        setWsConnected(false)
        retry = setTimeout(open, 3000)
        return
      }
      ws = new WebSocket(queueUrl)
      bioWs = new WebSocket(bioUrl)
      wire(ws, bioWs)
    }

    const wire = (ws: WebSocket, bioWs: WebSocket) => {
      ws.onopen = () => setWsConnected(true)
      ws.onclose = () => {
        setWsConnected(false)
        if (!closed) retry = setTimeout(open, 3000) // Auto-reconnect
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          if (data.event === 'queue_update') {
            setQueue(data.items || [])
          } else if (data.event === 'rx_status_change') {
            updateRxInQueue(data.rx_id, { status: data.new_status })
          }
        } catch { /* ignore parse errors */ }
      }

      bioWs.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          if (data.event_type === 'identity_resolved' && data.patient_id) {
            setIncomingPatient(data.patient_profile || null, data.confidence || 0)
          }
        } catch { /* ignore */ }
      }
    }

    void open()
    return () => {
      closed = true
      if (retry) clearTimeout(retry)
      ws?.close()
      bioWs?.close()
    }
  }

  return { connect }
}
