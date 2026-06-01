/**
 * PharmPilot Workstation — main application layout.
 * Three-column: [Rx Queue | Verification Center | Patient Profile + ACB]
 * Optimized for 1920×1080. All critical actions keyboard-accessible.
 */
import { useEffect, useState } from 'react'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { useRxQueueStore, useRxQueueWebSocket } from './stores/rxQueue'
import RxQueue from './components/RxQueue'
import DURAlertPanel from './components/DURAlertPanel'
import { rxApi, patientApi, clinicalApi } from './lib/api'

const queryClient = new QueryClient()

function BiometricArrivalBanner() {
  const { incomingPatient, biometricMatchConfidence } = useRxQueueStore()
  if (!incomingPatient || biometricMatchConfidence < 0.8) return null
  return (
    <div className="fixed top-4 right-4 z-50 bg-blue-600 text-white rounded-lg shadow-xl p-4 w-80">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 bg-blue-500 rounded-full flex items-center justify-center text-lg">👤</div>
        <div>
          <div className="font-semibold">{incomingPatient.first_name} {incomingPatient.last_name} arriving</div>
          <div className="text-xs text-blue-200">Match: {(biometricMatchConfidence * 100).toFixed(0)}%</div>
          {incomingPatient.pending_rxs?.length ? (
            <div className="text-xs mt-1">{incomingPatient.pending_rxs.length} Rx(s) ready</div>
          ) : null}
        </div>
      </div>
    </div>
  )
}

function PatientPanel({ patientId }: { patientId: string }) {
  const { data: patient } = useQuery({ queryKey: ['patient', patientId], queryFn: () => patientApi.get(patientId).then(r => r.data), enabled: !!patientId })
  const { data: allergies } = useQuery({ queryKey: ['allergies', patientId], queryFn: () => patientApi.getAllergies(patientId).then(r => r.data), enabled: !!patientId })
  const { data: insurance } = useQuery({ queryKey: ['insurance', patientId], queryFn: () => patientApi.getInsurance(patientId).then(r => r.data), enabled: !!patientId })
  if (!patient) return <div className="p-4 text-gray-400 text-sm">No patient selected</div>
  const age = Math.floor((Date.now() - new Date(patient.date_of_birth).getTime()) / (365.25 * 24 * 3600 * 1000))
  return (
    <div className="p-3 space-y-4 text-sm">
      <div className="border-b pb-3">
        <div className="font-bold text-lg text-gray-900">{patient.first_name} {patient.last_name}</div>
        <div className="text-gray-500 text-xs mt-1">DOB: {patient.date_of_birth} ({age}y) · {patient.gender}</div>
        {patient.biometric_enrolled && <span className="text-xs bg-green-100 text-green-700 px-1.5 py-0.5 rounded mt-1 inline-block">✓ Biometric</span>}
      </div>
      <div>
        <div className="font-semibold text-gray-700 mb-1.5">Allergies</div>
        {!allergies?.length ? <span className="text-xs text-gray-400">NKDA</span> : (
          <div className="space-y-1">
            {allergies.map((a: { allergen_name: string; reaction?: string; severity: string }, i: number) => (
              <div key={i} className="flex items-start gap-2">
                <span className="text-red-500">⚠</span>
                <div>
                  <span className="font-medium text-red-700">{a.allergen_name}</span>
                  {a.reaction && <span className="text-gray-500"> — {a.reaction}</span>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      <div>
        <div className="font-semibold text-gray-700 mb-1.5">Insurance</div>
        {!insurance?.length ? <span className="text-xs text-gray-400">Cash pay</span> : (
          <div className="space-y-1">
            {insurance.sort((a: { priority: number }, b: { priority: number }) => a.priority - b.priority).map((ins: { priority: number; bin_number: string; pcn?: string; member_id: string }, i: number) => (
              <div key={i} className="text-xs bg-gray-50 border rounded p-2">
                <span className="text-gray-400">{ins.priority}° </span><span className="font-medium">BIN {ins.bin_number}</span>
                <div className="text-gray-500">ID: {ins.member_id}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function VerificationPanel() {
  const { selectedRx, setSelectedRx } = useRxQueueStore()
  const [aclQuestion, setAclQuestion] = useState('')
  const [aclAnswer, setAclAnswer] = useState('')
  const [aclLoading, setAclLoading] = useState(false)
  const { data: durAlerts, refetch: refetchAlerts } = useQuery({
    queryKey: ['dur', selectedRx?.id],
    queryFn: () => rxApi.durAlerts(selectedRx!.id).then(r => r.data),
    enabled: !!selectedRx,
  })
  const handleAskACB = async () => {
    if (!aclQuestion.trim()) return
    setAclLoading(true)
    try {
      const { data } = await clinicalApi.queryKnowledge(aclQuestion, selectedRx?.patient_id)
      setAclAnswer(data.answer)
    } catch { setAclAnswer('Knowledge base unavailable.') }
    finally { setAclLoading(false) }
  }
  const handleTransition = async (toStatus: string) => {
    if (!selectedRx) return
    await rxApi.transition(selectedRx.id, toStatus)
    const { data } = await rxApi.get(selectedRx.id)
    setSelectedRx(data)
  }
  const criticalHardStops = (durAlerts || []).filter((a: { is_hard_stop: boolean; was_overridden: boolean }) => a.is_hard_stop && !a.was_overridden).length
  if (!selectedRx) return (
    <div className="h-full flex items-center justify-center text-gray-400">
      <div className="text-center"><div className="text-4xl mb-3">💊</div><div className="text-sm">Select or claim an Rx to begin</div></div>
    </div>
  )
  return (
    <div className="h-full overflow-y-auto p-4 space-y-4">
      <div className="bg-white border rounded-lg p-4">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm text-gray-500">{selectedRx.rx_number}</span>
          {selectedRx.is_controlled && <span className="text-xs bg-orange-100 text-orange-700 px-2 py-0.5 rounded-full font-medium">CONTROLLED {selectedRx.dea_schedule}</span>}
        </div>
        <div className="text-xl font-bold text-gray-900 mt-1">{selectedRx.drug_name} {selectedRx.drug_strength}</div>
        <div className="text-sm text-gray-600 mt-1">Qty: {selectedRx.quantity_prescribed} · Days: {selectedRx.days_supply} · Refills: {selectedRx.refills_remaining}</div>
        <div className="text-sm text-gray-700 mt-2 bg-gray-50 rounded p-2 font-mono">{selectedRx.sig_text}</div>
        {selectedRx.acb_safety_report && (
          <div className={`mt-3 p-2 rounded text-sm ${selectedRx.acb_safety_report.severity === 'critical' ? 'bg-red-50 border border-red-300' : 'bg-blue-50 border border-blue-200'}`}>
            <span className="font-semibold">ACB: </span>{selectedRx.acb_safety_report.primary_finding}
          </div>
        )}
      </div>
      {durAlerts?.length > 0 && (
        <div><h3 className="font-semibold text-gray-800 mb-2">DUR Alerts</h3><DURAlertPanel alerts={durAlerts} rxId={selectedRx.id} onAlertResolved={() => refetchAlerts()} /></div>
      )}
      <div className="flex gap-2 flex-wrap">
        {selectedRx.status === 'pending_verification' && <button onClick={() => handleTransition('verification_in_progress')} className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-medium text-sm">Claim for Verification</button>}
        {selectedRx.status === 'verification_in_progress' && (<>
          <button onClick={() => handleTransition('pending_adjudication')} disabled={criticalHardStops > 0} className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 font-medium text-sm disabled:opacity-40 disabled:cursor-not-allowed">Verify & Adjudicate</button>
          <button onClick={() => handleTransition('dur_hold')} className="px-4 py-2 bg-orange-500 text-white rounded-lg hover:bg-orange-600 font-medium text-sm">Clinical Hold</button>
        </>)}
        {selectedRx.status === 'ready_to_fill' && <button onClick={() => handleTransition('filling')} className="px-4 py-2 bg-teal-600 text-white rounded-lg font-medium text-sm">Begin Filling</button>}
        {selectedRx.status === 'filling' && <button onClick={() => handleTransition('filled')} className="px-4 py-2 bg-emerald-600 text-white rounded-lg font-medium text-sm">Filled → Will Call</button>}
        {selectedRx.status === 'will_call' && <button onClick={() => handleTransition('dispensed')} className="px-4 py-2 bg-purple-600 text-white rounded-lg font-medium text-sm">Dispense to Patient</button>}
        <button onClick={() => handleTransition('on_hold')} className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg font-medium text-sm border">Hold</button>
      </div>
      <div className="border rounded-lg p-3 space-y-2">
        <div className="text-sm font-semibold text-gray-700">🧠 Ask Clinical Brain</div>
        <div className="flex gap-2">
          <input type="text" value={aclQuestion} onChange={e => setAclQuestion(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleAskACB()} placeholder={`Ask about ${selectedRx.drug_name}…`} className="flex-1 text-sm border rounded px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-400" />
          <button onClick={handleAskACB} disabled={aclLoading} className="px-3 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 disabled:opacity-50">{aclLoading ? '…' : 'Ask'}</button>
        </div>
        {aclAnswer && <div className="text-xs text-gray-700 bg-gray-50 rounded p-2 max-h-32 overflow-y-auto whitespace-pre-wrap">{aclAnswer}</div>}
      </div>
    </div>
  )
}

function WorkstationApp() {
  const { selectedRx, wsConnected } = useRxQueueStore()
  const { connect } = useRxQueueWebSocket('demo-pharmacy-id')
  useEffect(() => { const cleanup = connect(); return cleanup }, [])
  return (
    <div className="h-screen bg-gray-100 flex flex-col overflow-hidden">
      <div className="bg-white border-b px-4 py-2 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="font-bold text-blue-700 text-lg">💊 PharmPilot</span>
          <span className="text-gray-300">|</span>
          <span className="text-sm text-gray-600">Dispensing Workstation</span>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <span className={`flex items-center gap-1 ${wsConnected ? 'text-green-600' : 'text-red-500'}`}>
            <span className={`w-2 h-2 rounded-full ${wsConnected ? 'bg-green-500' : 'bg-red-500'}`} />
            {wsConnected ? 'Live' : 'Reconnecting…'}
          </span>
        </div>
      </div>
      <div className="flex-1 flex overflow-hidden">
        <div className="w-72 flex-shrink-0 bg-white border-r overflow-hidden"><RxQueue /></div>
        <div className="flex-1 overflow-hidden bg-gray-50"><VerificationPanel /></div>
        <div className="w-72 flex-shrink-0 bg-white border-l overflow-y-auto">
          {selectedRx?.patient_id ? <PatientPanel patientId={selectedRx.patient_id} /> : <div className="p-4 text-sm text-gray-400">Patient profile appears here</div>}
        </div>
      </div>
      <BiometricArrivalBanner />
    </div>
  )
}

export default function App() {
  return <QueryClientProvider client={queryClient}><WorkstationApp /></QueryClientProvider>
}
