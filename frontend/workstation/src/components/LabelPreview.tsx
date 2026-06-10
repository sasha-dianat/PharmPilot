/**
 * LabelPreview — Phase 23
 * ========================
 * Dispensing label modal with three output modes:
 *
 *   🖨 Print PDF    — Opens browser print dialog (for PDF / thermal via OS driver)
 *   📡 ZPL Thermal — Sends ZPL II to a Zebra printer over TCP/9100
 *   ✍ Handwritten  — Pharmacist writes the label manually; platform records the
 *                    audit entry, no print job is created. Shows a large-type
 *                    reference sheet the pharmacist reads while writing.
 *
 * Auxiliary warning labels are auto-resolved from SIG/drug class.
 * Up to 6 are displayed; the pharmacist can add/remove before confirming.
 */
import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { patientApi, apiClient } from '../lib/api'
import type { Prescription } from '../stores/rxQueue'
import LabelSimplificationPanel from './LabelSimplificationPanel'

// ─── Types ───────────────────────────────────────────────────────────────────

type PrintMode = 'thermal_pdf' | 'thermal_zpl' | 'handwritten'

interface AuxLabel {
  code:      string
  text:      string
  color_hex: string
  icon:      string
}

interface LabelData {
  rx_number:               string
  fill_number:             number
  fill_date:               string
  drug_name:               string
  drug_strength:           string
  dosage_form:             string
  ndc11:                   string
  quantity:                number
  days_supply:             number
  refills_remaining:       number
  sig_text:                string
  patient_name:            string
  patient_dob_masked:      string
  prescriber_name:         string
  prescriber_npi:          string
  pharmacy_name:           string
  pharmacy_address:        string
  pharmacy_city_state_zip: string
  pharmacy_phone:          string
  pharmacy_npi:            string
  is_controlled:           boolean
  dea_schedule:            string | null
  print_mode:              PrintMode
  auxiliary_labels:        AuxLabel[]
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

interface Props {
  rx:                Prescription
  onClose:           () => void
  onConfirmDispense: () => void
}

// ─── Demo data (preview/offline) ────────────────────────────────────────────

const DEMO_AUX: AuxLabel[] = [
  { code: 'take_food',     text: 'Take with food or milk',          color_hex: '#FFA500', icon: '🍽' },
  { code: 'avoid_alcohol', text: 'Avoid alcohol',                   color_hex: '#FF4444', icon: '🚫' },
  { code: 'drowsiness',    text: 'May cause drowsiness — use care', color_hex: '#FFCC00', icon: '😴' },
]

// ─── Mode icons / labels ─────────────────────────────────────────────────────

const MODE_META: Record<PrintMode, { icon: string; label: string; desc: string }> = {
  thermal_pdf: {
    icon:  '🖨',
    label: 'Print PDF',
    desc:  'Opens browser print dialog (PDF or thermal via OS printer driver)',
  },
  thermal_zpl: {
    icon:  '📡',
    label: 'ZPL Thermal',
    desc:  'Sends ZPL II directly to Zebra printer (LP2844, GK420d, ZD420)',
  },
  handwritten: {
    icon:  '✍',
    label: 'Handwritten',
    desc:  'Pharmacist writes label manually. Platform records audit entry — no print job created.',
  },
}

// ─── Component ───────────────────────────────────────────────────────────────

export default function LabelPreview({ rx, onClose, onConfirmDispense }: Props) {
  const pharmacyId = localStorage.getItem('pharmacy_id') || ''
  const staffId    = localStorage.getItem('staff_id')    || 'pharmacist'
  const today = new Date().toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit', year: 'numeric' })

  const [printMode,       setPrintMode]       = useState<PrintMode>('thermal_pdf')
  const [printerIp,       setPrinterIp]       = useState('192.168.1.50')
  const [showPrinterConf, setShowPrinterConf] = useState(false)
  const [handwrittenNote, setHandwrittenNote] = useState('')
  const [labelData,       setLabelData]       = useState<LabelData | null>(null)
  const [auxOverrides,    setAuxOverrides]     = useState<AuxLabel[] | null>(null)  // null = use server-resolved
  const [sigOverride,     setSigOverride]      = useState<string | null>(null)      // #8 simplified directions
  const [step, setStep] = useState<'choose' | 'preview' | 'done'>('choose')

  // ── Fetch patient / pharmacy for local PDF rendering ──
  const { data: patient } = useQuery({
    queryKey: ['patient', rx.patient_id],
    queryFn:  () => patientApi.get(rx.patient_id!).then(r => r.data),
    enabled:  !!rx.patient_id,
  })
  const { data: pharmacy } = useQuery<PharmacyInfo>({
    queryKey: ['pharmacy-info', pharmacyId],
    queryFn:  () => apiClient.get(`/pharmacies/${pharmacyId}`).then(r => r.data),
    enabled:  !!pharmacyId,
  })

  // ── Generate label data from API ──
  const generateMutation = useMutation({
    mutationFn: () =>
      apiClient.post(`/labels/${rx.id}/generate`, {
        print_mode:     printMode,
        handwritten_by: printMode === 'handwritten' ? staffId : undefined,
      }).then(r => r.data.label as LabelData),
    onSuccess: (data) => {
      setLabelData(data)
      setAuxOverrides(data.auxiliary_labels)
      setStep('preview')
    },
  })

  // ── Send ZPL to printer ──
  const printZplMutation = useMutation({
    mutationFn: () =>
      apiClient.post(`/labels/${rx.id}/print`, {
        printer_ip:  printerIp,
        printer_port: 9100,
        include_aux: true,
      }).then(r => r.data),
    onSuccess: () => setStep('done'),
  })

  // ── Record handwritten audit ──
  const handwrittenMutation = useMutation({
    mutationFn: () =>
      apiClient.post(`/labels/${rx.id}/handwritten`, {
        staff_id: staffId,
        notes:    handwrittenNote || undefined,
      }).then(r => r.data),
    onSuccess: () => setStep('done'),
  })

  // ── PDF print (browser) ──
  const handlePdfPrint = () => {
    const el = document.getElementById('pharmpilot-label')
    if (!el) return
    const win = window.open('', '_blank', 'width=620,height=460')
    if (!win) return
    win.document.write(`<html><head><title>Rx Label</title>
      <style>
        body{font-family:Arial,sans-serif;margin:0;padding:8px;font-size:11px}
        .label{border:2px solid #000;padding:8px;width:400px}
        .ph{font-weight:bold;font-size:13px;text-align:center;border-bottom:1px solid #000;padding-bottom:4px;margin-bottom:4px}
        .drug{font-size:15px;font-weight:bold;margin:6px 0 2px}
        .sig{background:#f0f0f0;border:1px solid #ccc;padding:4px 6px;margin:4px 0;font-size:12px;line-height:1.4}
        .row{display:flex;justify-content:space-between;margin:2px 0}
        .cii{background:#ff0000;color:#fff;text-align:center;font-weight:bold;padding:2px 4px;margin-top:4px;font-size:10px;letter-spacing:1px}
        .aux{display:flex;flex-wrap:wrap;gap:3px;margin-top:4px}
        .aux span{border:1px solid #ccc;border-radius:3px;padding:1px 4px;font-size:9px}
        .bc{text-align:center;letter-spacing:.4em;color:#555;margin-top:4px;font-size:10px}
      </style>
    </head><body onload="window.print();window.close()">
      ${el.innerHTML}
    </body></html>`)
    win.document.close()
    setStep('done')
  }

  const activeAux: AuxLabel[] = auxOverrides ?? labelData?.auxiliary_labels ?? DEMO_AUX

  const maskedDob = patient?.date_of_birth
    ? new Date(patient.date_of_birth).toLocaleDateString('en-GB', { month: '2-digit', year: 'numeric' })
    : '**/**'

  const patientName = patient
    ? `${(patient.last_name || '').toUpperCase()}, ${patient.first_name}`
    : 'PATIENT NAME'

  // ─────────────────────────────────────────────────────
  // Render: step = 'choose'
  // ─────────────────────────────────────────────────────
  if (step === 'choose') {
    return (
      <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4">
        <div className="bg-white rounded-xl shadow-2xl w-full max-w-md">
          <div className="flex items-center justify-between px-5 py-3 border-b">
            <div className="flex items-center gap-2">
              <span className="text-lg">🏷</span>
              <span className="font-semibold text-gray-800">Label Output Mode</span>
              <span className="text-xs text-gray-400 font-mono">{rx.rx_number}</span>
            </div>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl">×</button>
          </div>

          <div className="p-5 space-y-3">
            <p className="text-sm text-gray-500">How should the label be produced for this prescription?</p>

            {(Object.entries(MODE_META) as [PrintMode, typeof MODE_META[PrintMode]][]).map(([mode, meta]) => (
              <button
                key={mode}
                onClick={() => setPrintMode(mode)}
                className={`w-full text-left border rounded-lg px-4 py-3 transition-all ${
                  printMode === mode
                    ? 'border-purple-500 bg-purple-50 ring-2 ring-purple-200'
                    : 'border-gray-200 hover:border-gray-300'
                }`}
              >
                <div className="flex items-center gap-3">
                  <span className="text-2xl">{meta.icon}</span>
                  <div>
                    <div className="font-medium text-gray-800 text-sm">{meta.label}</div>
                    <div className="text-xs text-gray-500 mt-0.5">{meta.desc}</div>
                  </div>
                  {printMode === mode && (
                    <span className="ml-auto text-purple-600 font-bold">✓</span>
                  )}
                </div>
              </button>
            ))}

            {printMode === 'thermal_zpl' && (
              <div className="mt-1">
                <button
                  onClick={() => setShowPrinterConf(!showPrinterConf)}
                  className="text-xs text-purple-600 underline"
                >
                  {showPrinterConf ? 'Hide' : 'Configure'} printer IP
                </button>
                {showPrinterConf && (
                  <div className="mt-2 flex gap-2 items-center">
                    <label className="text-xs text-gray-600 w-20">Printer IP</label>
                    <input
                      type="text"
                      value={printerIp}
                      onChange={e => setPrinterIp(e.target.value)}
                      className="border border-gray-200 rounded px-2 py-1 text-xs font-mono w-40"
                      placeholder="192.168.1.50"
                    />
                    <span className="text-xs text-gray-400">port 9100</span>
                  </div>
                )}
              </div>
            )}

            {printMode === 'handwritten' && (
              <div className="mt-1">
                <label className="text-xs text-gray-600 block mb-1">Note (optional)</label>
                <textarea
                  value={handwrittenNote}
                  onChange={e => setHandwrittenNote(e.target.value)}
                  className="w-full border border-gray-200 rounded px-2 py-1 text-xs"
                  rows={2}
                  placeholder="e.g. Printer offline, patient requested handwritten"
                />
              </div>
            )}
          </div>

          <div className="flex gap-3 px-5 py-3 border-t bg-gray-50 rounded-b-xl">
            <button
              onClick={() => generateMutation.mutate()}
              disabled={generateMutation.isPending}
              className="flex items-center gap-2 px-4 py-2 bg-purple-600 text-white text-sm rounded-lg hover:bg-purple-700 disabled:opacity-50 font-medium"
            >
              {generateMutation.isPending ? '⌛ Loading…' : `${MODE_META[printMode].icon} Continue`}
            </button>
            <button onClick={onClose} className="px-4 py-2 text-gray-600 text-sm rounded-lg border border-gray-200 hover:bg-gray-100 ml-auto">
              Cancel
            </button>
          </div>
          {generateMutation.isError && (
            <p className="text-red-500 text-xs px-5 pb-3">Failed to load label data. Using local preview.</p>
          )}
        </div>
      </div>
    )
  }

  // ─────────────────────────────────────────────────────
  // Render: step = 'done'
  // ─────────────────────────────────────────────────────
  if (step === 'done') {
    return (
      <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4">
        <div className="bg-white rounded-xl shadow-2xl w-full max-w-sm p-8 text-center">
          <div className="text-5xl mb-3">✅</div>
          <h2 className="font-semibold text-lg text-gray-800 mb-1">Label Complete</h2>
          <p className="text-sm text-gray-500 mb-6">
            {printMode === 'handwritten'
              ? 'Handwritten label recorded. Proceed to pharmacist dispensing review.'
              : 'Label sent. Proceed to pharmacist dispensing review.'}
          </p>
          <button
            onClick={onConfirmDispense}
            className="w-full px-4 py-2 bg-purple-600 text-white text-sm rounded-lg hover:bg-purple-700 font-medium"
          >
            💊 Confirm & Dispense
          </button>
          <button onClick={onClose} className="mt-2 w-full px-4 py-2 text-gray-500 text-sm rounded-lg hover:bg-gray-50">
            Close
          </button>
        </div>
      </div>
    )
  }

  // ─────────────────────────────────────────────────────
  // Render: step = 'preview'  (label preview)
  // ─────────────────────────────────────────────────────
  const ld = labelData  // May be null if API call failed; fallback to rx props
  const drugName     = ld?.drug_name     || rx.drug_name     || 'DRUG NAME'
  const drugStrength = ld?.drug_strength || rx.drug_strength || ''
  const sigText      = sigOverride ?? (ld?.sig_text || rx.sig_text || 'Take as directed')
  const quantity     = ld?.quantity      ?? rx.quantity_prescribed  ?? 0
  const daysSupply   = ld?.days_supply   ?? rx.days_supply          ?? 0
  const refills      = ld?.refills_remaining ?? rx.refills_remaining ?? 0
  const pharName     = ld?.pharmacy_name         || pharmacy?.name         || 'PHARMPILOT PHARMACY'
  const pharAddr     = ld?.pharmacy_address      || pharmacy?.address_line1|| ''
  const pharCityZip  = ld?.pharmacy_city_state_zip
    || `${pharmacy?.city || ''}, ${pharmacy?.state || ''} ${pharmacy?.zip_code || ''}`
  const pharPhone    = ld?.pharmacy_phone || pharmacy?.phone || ''
  const pharNpi      = ld?.pharmacy_npi   || pharmacy?.npi   || ''

  const isHandwritten = printMode === 'handwritten'

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[92vh] overflow-y-auto">

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b sticky top-0 bg-white z-10">
          <div className="flex items-center gap-2">
            <span className="text-lg">{MODE_META[printMode].icon}</span>
            <span className="font-semibold text-gray-800">
              {isHandwritten ? 'Handwriting Reference Sheet' : 'Label Preview'}
            </span>
            <span className="text-xs text-gray-400 font-mono">{rx.rx_number}</span>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl">×</button>
        </div>

        {isHandwritten && (
          <div className="mx-5 mt-4 bg-amber-50 border border-amber-200 rounded-lg px-4 py-3 text-sm text-amber-800">
            <strong>✍ Handwritten mode</strong> — Write the information below onto the label by hand.
            No print job will be created. The platform will record this as a handwritten label.
          </div>
        )}

        {/* Label rendering */}
        <div className="p-5">
          <div
            id="pharmpilot-label"
            className={`border-2 border-gray-900 rounded p-3 bg-white shadow-inner ${isHandwritten ? 'text-base' : 'font-mono text-xs'}`}
            style={{ maxWidth: 460, margin: '0 auto' }}
          >
            {/* Pharmacy header */}
            <div className="border-b border-gray-900 pb-1 mb-2">
              <div className={`font-bold text-center ${isHandwritten ? 'text-lg' : 'text-sm'}`}>
                {pharName}
              </div>
              <div className={`text-center text-gray-600 ${isHandwritten ? 'text-sm' : 'text-[10px]'}`}>
                {pharAddr} {pharCityZip} · Tel: {pharPhone}
              </div>
              {pharNpi && (
                <div className={`text-center text-gray-500 ${isHandwritten ? 'text-xs' : 'text-[10px]'}`}>
                  NPI: {pharNpi}
                </div>
              )}
            </div>

            {/* Rx + Date */}
            <div className={`flex justify-between text-gray-600 mb-1 ${isHandwritten ? 'text-sm' : 'text-[10px]'}`}>
              <span>Rx# <strong className="text-gray-900">{rx.rx_number}</strong></span>
              <span>Fill #{ld?.fill_number ?? 1}</span>
              <span>Date: <strong>{today}</strong></span>
            </div>

            {/* Patient */}
            <div className="border-t border-gray-300 pt-1 mt-1">
              <div className={`font-bold text-gray-900 ${isHandwritten ? 'text-xl' : 'text-sm'}`}>
                {ld?.patient_name || patientName}
              </div>
              <div className={`text-gray-500 ${isHandwritten ? 'text-sm' : 'text-[10px]'}`}>
                DOB: {ld?.patient_dob_masked || maskedDob}
              </div>
            </div>

            {/* Drug */}
            <div className="mt-2">
              <div className={`font-bold text-gray-900 ${isHandwritten ? 'text-2xl' : 'text-base'}`}>
                {drugName.toUpperCase()}
                {drugStrength && <span className="font-normal text-gray-600 ml-2">{drugStrength}</span>}
              </div>
              <div className={`text-gray-500 ${isHandwritten ? 'text-sm' : 'text-[10px]'}`}>
                Qty: <strong>{quantity}</strong> &nbsp;·&nbsp;
                Days supply: <strong>{daysSupply}</strong> &nbsp;·&nbsp;
                Refills remaining: <strong>{refills}</strong>
              </div>
            </div>

            {/* SIG */}
            <div className={`mt-2 bg-gray-100 border border-gray-400 rounded px-2 py-1.5 ${isHandwritten ? 'text-base' : ''}`}>
              <div className={`text-gray-500 uppercase tracking-widest mb-0.5 ${isHandwritten ? 'text-xs' : 'text-[9px]'}`}>
                Directions
              </div>
              <div className={`font-semibold leading-relaxed text-gray-900 ${isHandwritten ? 'text-lg' : 'text-xs'}`}>
                {sigText}
              </div>
            </div>

            {/* Prescriber */}
            <div className={`mt-2 text-gray-600 ${isHandwritten ? 'text-sm' : 'text-[10px]'}`}>
              <span className="text-gray-500">Prescriber: </span>
              <strong className="text-gray-800">{ld?.prescriber_name || 'Dr. [Name]'}</strong>
              {ld?.prescriber_npi && <span className="text-gray-400 ml-1">NPI: {ld.prescriber_npi}</span>}
            </div>

            {/* Auxiliary labels */}
            {activeAux.length > 0 && (
              <div className="mt-2">
                <div className={`text-gray-400 uppercase tracking-widest mb-1 ${isHandwritten ? 'text-xs' : 'text-[9px]'}`}>
                  Warning Labels
                </div>
                <div className="flex flex-wrap gap-1">
                  {activeAux.map(a => (
                    <span
                      key={a.code}
                      className={`flex items-center gap-1 rounded px-1.5 py-0.5 border ${isHandwritten ? 'text-sm' : 'text-[9px]'}`}
                      style={{ borderColor: a.color_hex, backgroundColor: a.color_hex + '22' }}
                    >
                      <span>{a.icon}</span>
                      <span>{a.text}</span>
                      {auxOverrides && (
                        <button
                          onClick={() => setAuxOverrides(auxOverrides.filter(x => x.code !== a.code))}
                          className="ml-1 text-gray-400 hover:text-red-500 text-xs leading-none"
                          title="Remove"
                        >×</button>
                      )}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Controlled substance banner */}
            {rx.is_controlled && (
              <div className={`mt-2 bg-red-600 text-white text-center font-bold tracking-widest py-0.5 rounded ${isHandwritten ? 'text-xs' : 'text-[9px]'}`}>
                CAUTION: FEDERAL LAW PROHIBITS TRANSFER · {rx.dea_schedule || 'CII'} CONTROLLED SUBSTANCE
              </div>
            )}

            {/* Barcode placeholder (hidden in handwritten mode) */}
            {!isHandwritten && (
              <>
                <div className="mt-1 text-center text-[9px] text-gray-300 tracking-[0.4em]">
                  |||||||||||||||||||||||||||||||||||||||||||||||
                </div>
                <div className="text-center text-[9px] text-gray-400">{rx.rx_number}</div>
              </>
            )}
          </div>
        </div>

        {/* #8 — Personalized Label Simplification: patient plain-language directions
            + pharmacist reference dosing (not printed; a verification aid) */}
        <div className="px-5 pb-3">
          <LabelSimplificationPanel
            sig={ld?.sig_text || rx.sig_text || ''}
            drugName={drugName}
            language={(ld as any)?.patient_language || 'en'}
            onUseInstructions={(textVal) => setSigOverride(textVal)}
          />
        </div>

        {/* Actions */}
        <div className="flex gap-3 px-5 py-3 border-t bg-gray-50 rounded-b-xl sticky bottom-0">
          {printMode === 'thermal_pdf' && (
            <button
              onClick={handlePdfPrint}
              className="flex items-center gap-2 px-4 py-2 bg-gray-700 text-white text-sm rounded-lg hover:bg-gray-800"
            >
              🖨 Print PDF
            </button>
          )}

          {printMode === 'thermal_zpl' && (
            <button
              onClick={() => printZplMutation.mutate()}
              disabled={printZplMutation.isPending}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 disabled:opacity-50"
            >
              {printZplMutation.isPending ? '⌛ Sending…' : '📡 Send to Zebra Printer'}
            </button>
          )}

          {printMode === 'handwritten' && (
            <button
              onClick={() => handwrittenMutation.mutate()}
              disabled={handwrittenMutation.isPending}
              className="flex items-center gap-2 px-4 py-2 bg-amber-600 text-white text-sm rounded-lg hover:bg-amber-700 disabled:opacity-50"
            >
              {handwrittenMutation.isPending ? '⌛ Saving…' : '✍ Mark as Hand-Labeled'}
            </button>
          )}

          <button
            onClick={onConfirmDispense}
            className="flex items-center gap-2 px-4 py-2 bg-purple-600 text-white text-sm rounded-lg hover:bg-purple-700 font-medium"
          >
            💊 Confirm & Dispense
          </button>

          <button
            onClick={() => setStep('choose')}
            className="px-4 py-2 text-gray-600 text-sm rounded-lg border border-gray-200 hover:bg-gray-100 ml-auto"
          >
            ← Change mode
          </button>
        </div>

        {(printZplMutation.isError) && (
          <p className="text-red-500 text-xs px-5 pb-3">
            ⚠ Printer unreachable. Check IP {printerIp} and try again, or switch to PDF mode.
          </p>
        )}
      </div>
    </div>
  )
}
