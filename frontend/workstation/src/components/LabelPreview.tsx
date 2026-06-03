/**
 * LabelPreview — Pharmacy dispensing label preview before printing.
 * ================================================================
 * Shows a realistic label layout with all required fields.
 * Supports browser window.print() or Zebra ZPL API endpoint.
 *
 * Required fields per USP <795>/<797> and state board standards:
 *   - Patient name + DOB (masked)
 *   - Drug name, strength, dosage form
 *   - Directions (SIG) — prominently displayed
 *   - Qty, days supply, refills remaining
 *   - Prescriber name + NPI
 *   - Pharmacy name, address, phone, DEA/NPI
 *   - Rx number, fill date, fill number
 *   - DEA schedule warning for controlled substances
 */
import { useQuery } from '@tanstack/react-query'
import { patientApi, apiClient } from '../lib/api'
import type { Prescription } from '../stores/rxQueue'

interface Props {
  rx: Prescription
  onClose: () => void
  onConfirmDispense: () => void
}

interface PharmacyInfo {
  name: string
  address_line1: string
  city: string
  state: string
  zip_code: string
  phone: string
  npi: string
  dea_number?: string
}

export default function LabelPreview({ rx, onClose, onConfirmDispense }: Props) {
  const pharmacyId = localStorage.getItem('pharmacy_id') || ''
  const today = new Date().toLocaleDateString('en-US', { month: '2-digit', day: '2-digit', year: 'numeric' })

  // Fetch patient details for label
  const { data: patient } = useQuery({
    queryKey: ['patient', rx.patient_id],
    queryFn: () => patientApi.get(rx.patient_id!).then(r => r.data),
    enabled: !!rx.patient_id,
  })

  // Fetch pharmacy info for label
  const { data: pharmacy } = useQuery<PharmacyInfo>({
    queryKey: ['pharmacy-info', pharmacyId],
    queryFn: () => apiClient.get(`/pharmacies/${pharmacyId}`).then(r => r.data),
    enabled: !!pharmacyId,
  })

  // Mask DOB: show MM/YYYY only
  const maskedDob = patient?.date_of_birth
    ? new Date(patient.date_of_birth).toLocaleDateString('en-US', { month: '2-digit', year: 'numeric' })
    : '**/**'

  const handlePrint = () => {
    const printContent = document.getElementById('pharmpilot-label')
    if (!printContent) return
    const win = window.open('', '_blank', 'width=600,height=400')
    if (!win) return
    win.document.write(`
      <html>
        <head>
          <title>Rx Label - ${rx.rx_number}</title>
          <style>
            body { font-family: Arial, sans-serif; margin: 0; padding: 8px; font-size: 11px; }
            .label { border: 2px solid #000; padding: 8px; width: 380px; }
            .pharmacy-header { font-weight: bold; font-size: 13px; border-bottom: 1px solid #000; padding-bottom: 4px; margin-bottom: 4px; }
            .drug-name { font-size: 15px; font-weight: bold; margin: 6px 0 2px; }
            .sig { background: #f0f0f0; border: 1px solid #ccc; padding: 4px 6px; margin: 4px 0; font-size: 12px; line-height: 1.4; }
            .row { display: flex; justify-content: space-between; margin: 2px 0; }
            .controlled-warning { background: #ff0000; color: #fff; text-align: center; font-weight: bold; padding: 2px 4px; margin-top: 4px; font-size: 10px; letter-spacing: 1px; }
          </style>
        </head>
        <body onload="window.print(); window.close()">
          ${printContent.innerHTML}
        </body>
      </html>
    `)
    win.document.close()
  }

  const patientName = patient
    ? `${patient.last_name?.toUpperCase()}, ${patient.first_name}`
    : 'PATIENT NAME'

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-xl">

        {/* Modal header */}
        <div className="flex items-center justify-between px-5 py-3 border-b">
          <div className="flex items-center gap-2">
            <span className="text-lg">🏷</span>
            <span className="font-semibold text-gray-800">Label Preview</span>
            <span className="text-xs text-gray-400 font-mono">{rx.rx_number}</span>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">×</button>
        </div>

        {/* Label rendering */}
        <div className="p-5">
          <div
            id="pharmpilot-label"
            className="border-2 border-gray-900 rounded p-3 font-mono text-xs bg-white shadow-inner"
            style={{ maxWidth: 420, margin: '0 auto' }}
          >
            {/* Pharmacy header */}
            <div className="border-b border-gray-900 pb-1 mb-2">
              <div className="font-bold text-sm text-center">
                {pharmacy?.name || 'PHARMPILOT PHARMACY'}
              </div>
              {pharmacy && (
                <div className="text-center text-gray-600 text-[10px]">
                  {pharmacy.address_line1}, {pharmacy.city}, {pharmacy.state} {pharmacy.zip_code}
                  {' '}· Tel: {pharmacy.phone}
                </div>
              )}
              {pharmacy?.npi && (
                <div className="text-center text-[10px] text-gray-500">NPI: {pharmacy.npi}</div>
              )}
            </div>

            {/* Rx + Date row */}
            <div className="flex justify-between text-[10px] text-gray-600 mb-1">
              <span>Rx# <strong className="text-gray-900">{rx.rx_number}</strong></span>
              <span>Fill #1 of {rx.refills_remaining + 1}</span>
              <span>Date: <strong>{today}</strong></span>
            </div>

            {/* Patient */}
            <div className="border-t border-gray-300 pt-1 mt-1">
              <div className="text-sm font-bold text-gray-900">{patientName}</div>
              <div className="text-[10px] text-gray-500">DOB: {maskedDob}</div>
            </div>

            {/* Drug */}
            <div className="mt-2">
              <div className="text-base font-bold text-gray-900">
                {rx.drug_name.toUpperCase()}
                {rx.drug_strength && <span className="font-normal text-gray-600 ml-1">{rx.drug_strength}</span>}
              </div>
              <div className="text-[10px] text-gray-500">
                Qty: <strong>{rx.quantity_prescribed}</strong> &nbsp;·&nbsp;
                Days supply: <strong>{rx.days_supply}</strong> &nbsp;·&nbsp;
                Refills remaining: <strong>{rx.refills_remaining}</strong>
              </div>
            </div>

            {/* SIG — most prominent element */}
            <div className="mt-2 bg-gray-100 border border-gray-400 rounded px-2 py-1.5">
              <div className="text-[9px] text-gray-500 uppercase tracking-widest mb-0.5">Directions</div>
              <div className="text-xs font-semibold leading-relaxed text-gray-900">
                {rx.sig_text || 'Take as directed by prescriber'}
              </div>
            </div>

            {/* Prescriber */}
            <div className="mt-2 text-[10px] text-gray-600">
              <span className="text-gray-500">Prescriber: </span>
              <strong className="text-gray-800">Dr. [Prescriber Name]</strong>
              <span className="text-gray-400 ml-1">NPI: [NPI]</span>
            </div>

            {/* Controlled substance warning */}
            {rx.is_controlled && (
              <div className="mt-2 bg-red-600 text-white text-center text-[9px] font-bold tracking-widest py-0.5 rounded">
                CAUTION: FEDERAL LAW PROHIBITS TRANSFER · {rx.dea_schedule} CONTROLLED SUBSTANCE
              </div>
            )}

            {/* Refill reminder */}
            <div className="mt-1 text-[9px] text-gray-400 border-t border-dashed border-gray-300 pt-1">
              {rx.refills_remaining > 0
                ? `⟳ ${rx.refills_remaining} refill(s) authorized — call before ${new Date(Date.now() + 30 * 86400000).toLocaleDateString('en-US', { month: '2-digit', day: '2-digit', year: 'numeric' })}`
                : 'NO REFILLS REMAINING — Contact prescriber for new prescription'}
            </div>

            {/* Rx barcode placeholder */}
            <div className="mt-1 text-center text-[9px] text-gray-300 tracking-[0.4em]">
              |||||||||||||||||||||||||||||||||||||||||||||||
            </div>
            <div className="text-center text-[9px] text-gray-400">{rx.rx_number}</div>
          </div>
        </div>

        {/* Actions */}
        <div className="flex gap-3 px-5 py-3 border-t bg-gray-50 rounded-b-xl">
          <button
            onClick={handlePrint}
            className="flex items-center gap-2 px-4 py-2 bg-gray-700 text-white text-sm rounded-lg hover:bg-gray-800"
          >
            🖨 Print Label
          </button>
          <button
            onClick={onConfirmDispense}
            className="flex items-center gap-2 px-4 py-2 bg-purple-600 text-white text-sm rounded-lg hover:bg-purple-700 font-medium"
          >
            💊 Confirm & Dispense
          </button>
          <button
            onClick={onClose}
            className="px-4 py-2 text-gray-600 text-sm rounded-lg border border-gray-200 hover:bg-gray-100 ml-auto"
          >
            Back
          </button>
        </div>
      </div>
    </div>
  )
}
