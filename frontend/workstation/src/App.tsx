/**
 * PharmPilot Workstation — main application layout.
 * Three-column: [Rx Queue | Verification Center | Patient Profile + ACB]
 * Optimized for 1920×1080. All critical actions keyboard-accessible.
 */
import { useEffect, useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useRxQueueStore, useRxQueueWebSocket } from './stores/rxQueue'
import RxQueue from './components/RxQueue'
import LoginPage from './components/LoginPage'
import DashboardShell from './DashboardShell'
import VerificationCenter from './components/VerificationCenter'
import PatientPanel from './components/PatientPanel'
import { ErrorBoundary } from './components/ErrorBoundary'

const queryClient = new QueryClient()

// ── Role badge colours ──────────────────────────────────────────────────────
const ROLE_COLORS: Record<string, string> = {
  super_admin: 'bg-purple-100 text-purple-800',
  pharmacy_manager: 'bg-blue-100 text-blue-800',
  pharmacist: 'bg-green-100 text-green-800',
  pharmacy_technician: 'bg-yellow-100 text-yellow-700',
  cashier: 'bg-gray-100 text-gray-700',
}
const ROLE_LABELS: Record<string, string> = {
  super_admin: 'Admin', pharmacy_manager: 'Manager',
  pharmacist: 'Pharmacist', pharmacy_technician: 'Technician', cashier: 'Cashier',
}

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

// VerificationCenter and PatientPanel are imported from their own files above

function WorkstationApp() {
  const { selectedRx, wsConnected } = useRxQueueStore()
  const pharmacyId = localStorage.getItem('pharmacy_id') || 'demo-pharmacy-id'
  const userRole   = localStorage.getItem('user_role')   || ''
  const { connect } = useRxQueueWebSocket(pharmacyId)
  const [showDashboard, setShowDashboard] = useState(false)
  useEffect(() => { const cleanup = connect(); return cleanup }, [])

  const handleLogout = () => {
    localStorage.clear()
    window.location.reload()
  }

  // Show dashboard shell if requested
  if (showDashboard) {
    return <DashboardShell onExitDashboard={() => setShowDashboard(false)} />
  }
  return (
    <div className="h-screen bg-gray-100 flex flex-col overflow-hidden">
      <div className="bg-white border-b px-4 py-2 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="font-bold text-blue-700 text-lg">💊 PharmPilot</span>
          <span className="text-gray-300">|</span>
          <span className="text-sm text-gray-600">Dispensing Workstation</span>
        </div>
        <div className="flex items-center gap-3 text-sm">
          {userRole && (
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${ROLE_COLORS[userRole] || 'bg-gray-100 text-gray-600'}`}>
              {ROLE_LABELS[userRole] || userRole}
            </span>
          )}
          <span className={`flex items-center gap-1 ${wsConnected ? 'text-green-600' : 'text-red-500'}`}>
            <span className={`w-2 h-2 rounded-full ${wsConnected ? 'bg-green-500' : 'bg-red-500'}`} />
            {wsConnected ? 'Live' : 'Reconnecting…'}
          </span>
          <button
            onClick={() => setShowDashboard(true)}
            className="text-xs bg-blue-600 text-white px-3 py-1 rounded hover:bg-blue-500 font-medium"
          >
            📊 Dashboards
          </button>
          <button
            onClick={handleLogout}
            className="text-xs text-gray-400 hover:text-red-500 px-2 py-1 rounded hover:bg-gray-100"
          >
            Sign out
          </button>
        </div>
      </div>
      <div className="flex-1 flex overflow-hidden">
        <div className="w-72 flex-shrink-0 bg-white border-r overflow-hidden"><RxQueue /></div>
        <div className="flex-1 overflow-hidden bg-gray-50">
          <ErrorBoundary label="Verification Center">
            <VerificationCenter />
          </ErrorBoundary>
        </div>
        <div className="w-72 flex-shrink-0 bg-white border-l overflow-hidden">
          <ErrorBoundary label="Patient Panel">
            <PatientPanel patientId={selectedRx?.patient_id || ''} />
          </ErrorBoundary>
        </div>
      </div>
      <BiometricArrivalBanner />
    </div>
  )
}

// ── Root: auth gate ─────────────────────────────────────────────────────────
export default function App() {
  const [isLoggedIn, setIsLoggedIn] = useState<boolean>(
    () => !!localStorage.getItem('access_token')
  )

  if (!isLoggedIn) {
    return (
      <LoginPage
        onLoginSuccess={() => setIsLoggedIn(true)}
      />
    )
  }

  return (
    <QueryClientProvider client={queryClient}>
      <ErrorBoundary label="Workstation">
        <WorkstationApp />
      </ErrorBoundary>
    </QueryClientProvider>
  )
}
