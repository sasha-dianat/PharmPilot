/**
 * PackageVerification — Phase 24
 * ================================
 * Three panels:
 *
 *   PackageEnrollmentPanel  — Multi-step wizard at RECEIVING:
 *     Step 1  Capture        — Take 5–10 photos of the packaging
 *     Step 2  Auto-Extract   — OCR reads drug name, NDC, lot, expiry,
 *                              manufacturer, dosage form automatically
 *     Step 3  Verify & PO    — Staff confirms OCR data; system shows the
 *                              matching Purchase Order from the distributor
 *                              for cross-reference (qty ordered, cost, etc.)
 *     Step 4  Enroll         — One-click confirm; visual ML embedding saved
 *
 *   PackageVerificationPanel — At DISPENSING: camera verifies the pulled
 *     item matches the prescription's expected NDC before pharmacist review.
 *
 *   EnrolledProductsTable   — Catalogue of all enrolled products.
 */
import { useState, useRef, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../lib/api'

// ─── Types ───────────────────────────────────────────────────────────────────

interface ExtractedInfo {
  ndc11:         string | null
  ndc_raw:       string | null
  drug_name:     string | null
  brand_name:    string | null
  generic_name:  string | null
  strength:      string | null
  dosage_form:   string | null
  manufacturer:  string | null
  lot_number:    string | null
  expiry_date:   string | null
  package_qty:   string | null
  storage_notes: string | null
}

interface POMatch {
  po_id:               string
  po_number:           string
  wholesaler:          string
  po_status:           string
  line_status:         string
  ordered_at:          string | null
  expected_delivery:   string | null
  quantity_ordered:    number
  quantity_received:   number
  quantity_outstanding: number
  unit_cost:           number | null
  wholesaler_item_no:  string | null
  drug_from_catalog: {
    generic_name: string | null
    brand_name:   string | null
    strength:     string | null
    dosage_form:  string | null
    manufacturer: string | null
  }
}

interface EnrolledProduct {
  ndc11:        string
  drug_name:    string
  manufacturer: string
  dosage_form:  string
  photo_count:  number
  enrolled_by:  string
  enrolled_at:  string
  last_updated: string | null
  notes:        string | null
}

interface VerifyCandidate {
  ndc11:       string
  drug_name:   string
  similarity:  number
  is_accepted: boolean
}

interface VerifyResult {
  status:         'pass' | 'warn' | 'fail' | 'unenrolled' | 'error'
  similarity:     number
  confidence_pct: number
  expected_ndc:   string
  expected_name:  string
  message:        string
  is_pass:        boolean
  needs_alert:    boolean
  top_candidates: VerifyCandidate[]
  latency_ms:     number
}

interface EngineStatus {
  backend:          string
  embed_dim:        number
  enrolled_count:   number
  accept_threshold: number
  warn_threshold:   number
  ocr_backend:      string
}

// ─── Demo/fallback data ───────────────────────────────────────────────────────

const DEMO_PRODUCTS: EnrolledProduct[] = [
  {
    ndc11: '00093314905', drug_name: 'Amoxicillin 500 mg Capsules',
    manufacturer: 'Teva', dosage_form: 'bottle', photo_count: 7,
    enrolled_by: 'tech_01', enrolled_at: '2026-05-10T09:00:00Z',
    last_updated: null, notes: 'Lot: TM240501 | Exp: 2027-05-01 | PO: PO-2026-001',
  },
  {
    ndc11: '00071015523', drug_name: 'Lipitor 20 mg Tablets',
    manufacturer: 'Pfizer', dosage_form: 'blister', photo_count: 5,
    enrolled_by: 'tech_01', enrolled_at: '2026-05-12T11:30:00Z',
    last_updated: null, notes: 'Blue blister | Lot: PFZ24B | Exp: 2028-03-01',
  },
  {
    ndc11: '00169368412', drug_name: 'Metformin 500 mg Tablets',
    manufacturer: 'Bristol-Myers Squibb', dosage_form: 'bottle', photo_count: 6,
    enrolled_by: 'pharmacist', enrolled_at: '2026-05-14T08:15:00Z',
    last_updated: '2026-05-20T14:00:00Z', notes: null,
  },
]

const DEMO_STATUS: EngineStatus = {
  backend: 'mobilenet_v3', embed_dim: 960, enrolled_count: 3,
  accept_threshold: 0.80, warn_threshold: 0.60, ocr_backend: 'easyocr',
}

const DOSAGE_FORMS = ['sachet', 'blister', 'bottle', 'box', 'vial', 'tablet',
                      'capsule', 'injection', 'ampoule', 'syrup', 'cream',
                      'ointment', 'patch', 'inhaler', 'drops', 'powder', 'other']

// ─── Helpers ─────────────────────────────────────────────────────────────────

function statusColors(status: string) {
  switch (status) {
    case 'pass':       return { bg: 'bg-green-50',  border: 'border-green-400', text: 'text-green-700',  icon: '✅' }
    case 'warn':       return { bg: 'bg-yellow-50', border: 'border-yellow-400',text: 'text-yellow-700', icon: '⚠️' }
    case 'fail':       return { bg: 'bg-red-50',    border: 'border-red-400',   text: 'text-red-700',    icon: '❌' }
    case 'unenrolled': return { bg: 'bg-blue-50',   border: 'border-blue-300',  text: 'text-blue-700',   icon: '📋' }
    default:           return { bg: 'bg-gray-50',   border: 'border-gray-300',  text: 'text-gray-700',   icon: '⚙️' }
  }
}

function confBar(pct: number, status: string) {
  const color = status === 'pass' ? 'bg-green-500'
    : status === 'warn' ? 'bg-yellow-400' : 'bg-red-500'
  return (
    <div className="w-full bg-gray-100 rounded-full h-2 mt-1">
      <div className={`${color} h-2 rounded-full transition-all`} style={{ width: `${Math.min(100, pct)}%` }} />
    </div>
  )
}

// ─── Camera hook ─────────────────────────────────────────────────────────────

function useCamera() {
  const videoRef  = useRef<HTMLVideoElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const [active, setActive] = useState(false)
  const [error,  setError]  = useState('')
  const [photos, setPhotos] = useState<string[]>([])

  const start = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } })
      streamRef.current = stream
      if (videoRef.current) { videoRef.current.srcObject = stream; videoRef.current.play() }
      setActive(true); setError('')
    } catch (e: any) { setError(e.message || 'Camera access denied') }
  }, [])

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach(t => t.stop())
    streamRef.current = null; setActive(false)
  }, [])

  const capture = useCallback((): string | null => {
    if (!videoRef.current || !canvasRef.current) return null
    const v = videoRef.current, c = canvasRef.current
    c.width = v.videoWidth || 640; c.height = v.videoHeight || 480
    c.getContext('2d')!.drawImage(v, 0, 0)
    return c.toDataURL('image/jpeg', 0.90)
  }, [])

  const captureAndAdd = useCallback(() => {
    const b64 = capture(); if (b64) setPhotos(p => [...p, b64]); return b64
  }, [capture])

  const clearPhotos = useCallback(() => setPhotos([]), [])
  return { videoRef, canvasRef, active, error, photos, start, stop, capture, captureAndAdd, clearPhotos }
}

// ─────────────────────────────────────────────────────────────────────────────
// PackageEnrollmentPanel  (4-step wizard)
// ─────────────────────────────────────────────────────────────────────────────

interface EnrollmentProps { onEnrolled?: (ndc11: string) => void }

export function PackageEnrollmentPanel({ onEnrolled }: EnrollmentProps) {
  const qc     = useQueryClient()
  const camera = useCamera()
  const staffId = localStorage.getItem('staff_id') || 'tech_01'

  const [step,   setStep]   = useState<1 | 2 | 3 | 4>(1)
  const [ocr,    setOcr]    = useState<ExtractedInfo | null>(null)
  const [ocrMeta, setOcrMeta] = useState<{ confidence: number; warnings: string[]; backend: string } | null>(null)
  const [poData, setPoData] = useState<{ found: boolean; orders: POMatch[] } | null>(null)
  const [selectedPo, setSelectedPo] = useState<POMatch | null>(null)
  const [replace, setReplace] = useState(false)
  const [enrollResult, setEnrollResult] = useState<string | null>(null)

  // Editable form state (pre-filled from OCR, editable by staff)
  const [form, setForm] = useState({
    ndc11: '', drug_name: '', manufacturer: '', dosage_form: 'other',
    lot_number: '', expiry_date: '', notes: '',
  })

  // ── Step 1: Extract via OCR ──
  const extractMutation = useMutation({
    mutationFn: () =>
      apiClient.post('/package-verification/extract-from-package', {
        photos_b64: camera.photos,
        use_all_frames: true,
      }).then(r => r.data),
    onSuccess: (data) => {
      const ex: ExtractedInfo = data.extracted
      setOcr(ex)
      setOcrMeta({ confidence: data.confidence, warnings: data.warnings, backend: data.ocr_backend })
      // Pre-fill form from OCR
      setForm({
        ndc11:        ex.ndc11         || '',
        drug_name:    ex.drug_name     || (ex.generic_name || ex.brand_name || ''),
        manufacturer: ex.manufacturer  || '',
        dosage_form:  ex.dosage_form   || 'other',
        lot_number:   ex.lot_number    || '',
        expiry_date:  ex.expiry_date   || '',
        notes:        ex.storage_notes || '',
      })
      setStep(2)
    },
    onError: () => {
      // OCR failed — skip to manual entry
      setOcr(null)
      setOcrMeta({ confidence: 0, warnings: ['OCR unavailable — please enter details manually'], backend: 'none' })
      setStep(2)
    },
  })

  // ── Step 2→3: PO lookup ──
  const poMutation = useMutation({
    mutationFn: () =>
      apiClient.post('/package-verification/po-lookup', {
        ndc11: form.ndc11,
        pharmacy_id: localStorage.getItem('pharmacy_id') || undefined,
      }).then(r => r.data),
    onSuccess: (data) => {
      setPoData(data)
      if (data.found && data.orders.length > 0) setSelectedPo(data.orders[0])
      setStep(3)
    },
    onError: () => {
      setPoData({ found: false, orders: [] })
      setStep(3)
    },
  })

  // ── Step 3→4: Final enrollment ──
  const enrollMutation = useMutation({
    mutationFn: () =>
      apiClient.post('/package-verification/enroll', {
        ndc11:        form.ndc11,
        drug_name:    form.drug_name,
        manufacturer: form.manufacturer,
        dosage_form:  form.dosage_form,
        lot_number:   form.lot_number || undefined,
        expiry_date:  form.expiry_date || undefined,
        photos_b64:   camera.photos,
        enrolled_by:  staffId,
        notes:        form.notes || undefined,
        replace,
        po_id:        selectedPo?.po_id || undefined,
      }).then(r => r.data),
    onSuccess: (data) => {
      setEnrollResult(`✅ ${data.drug_name} (${data.ndc11}) enrolled with ${data.photo_count} photos.`)
      qc.invalidateQueries({ queryKey: ['enrolled-products'] })
      if (onEnrolled) onEnrolled(form.ndc11)
      setStep(4)
    },
    onError: (e: any) => {
      setEnrollResult(`❌ ${e.response?.data?.detail || e.message}`)
      setStep(4)
    },
  })

  const f = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm(prev => ({ ...prev, [key]: e.target.value }))

  const reset = () => {
    setStep(1); setOcr(null); setOcrMeta(null); setPoData(null)
    setSelectedPo(null); setEnrollResult(null); setReplace(false)
    setForm({ ndc11: '', drug_name: '', manufacturer: '', dosage_form: 'other', lot_number: '', expiry_date: '', notes: '' })
    camera.clearPhotos()
  }

  // ── Step indicators ──
  const STEPS = ['1. Capture', '2. Auto-Extract', '3. Verify & PO', '4. Enroll']

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-2">
        <span className="text-lg">📦</span>
        <h3 className="font-semibold text-gray-800">Enroll Package Appearance</h3>
        <span className="text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full">At Receiving</span>
      </div>

      {/* Step progress bar */}
      <div className="flex items-center gap-1">
        {STEPS.map((label, i) => (
          <div key={i} className="flex items-center flex-1">
            <div className={`flex-1 h-1 rounded-full ${i + 1 <= step ? 'bg-purple-500' : 'bg-gray-200'}`} />
            <span className={`text-[10px] whitespace-nowrap px-1 ${i + 1 === step ? 'text-purple-700 font-semibold' : i + 1 < step ? 'text-purple-500' : 'text-gray-400'}`}>
              {label}
            </span>
          </div>
        ))}
      </div>

      {/* ── STEP 1: Capture ── */}
      {step === 1 && (
        <div className="space-y-3">
          <p className="text-xs text-gray-500">
            Point the camera at the medication packaging and take <strong>5–10 photos</strong> from
            different angles. The system will automatically read all drug details from the packaging —
            no manual typing required.
          </p>
          <p className="text-[10px] text-gray-400">
            Recommended angles: front label, back panel, side (with NDC barcode), close-up of text,
            individual unit (sachet/blister/tablet).
          </p>

          {/* Camera */}
          <div className="border border-gray-200 rounded-lg p-3 bg-gray-50">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium text-gray-700">
                📸 {camera.photos.length} photo{camera.photos.length !== 1 ? 's' : ''} captured
              </span>
              <div className="flex gap-2">
                {!camera.active
                  ? <button onClick={camera.start} className="text-xs px-3 py-1 bg-blue-600 text-white rounded hover:bg-blue-700">📷 Start</button>
                  : <button onClick={camera.stop}  className="text-xs px-3 py-1 bg-gray-600 text-white rounded">⏹ Stop</button>
                }
                {camera.active && (
                  <button onClick={camera.captureAndAdd} className="text-xs px-3 py-1 bg-green-600 text-white rounded hover:bg-green-700">
                    📸 Capture
                  </button>
                )}
                {camera.photos.length > 0 && (
                  <button onClick={camera.clearPhotos} className="text-xs px-2 py-1 bg-red-100 text-red-600 rounded hover:bg-red-200">🗑</button>
                )}
              </div>
            </div>
            {camera.error && <p className="text-red-500 text-xs mb-2">{camera.error}</p>}
            {camera.active && (
              <video ref={camera.videoRef} className="w-full rounded border border-gray-300 max-h-44 object-cover" />
            )}
            <canvas ref={camera.canvasRef} className="hidden" />
            {camera.photos.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-2">
                {camera.photos.map((p, i) => (
                  <div key={i} className="relative">
                    <img src={p} alt="" className="w-12 h-12 object-cover rounded border border-gray-300" />
                    <span className="absolute bottom-0 right-0 bg-black/50 text-white text-[8px] px-0.5 rounded-tl">{i+1}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          <button
            onClick={() => extractMutation.mutate()}
            disabled={camera.photos.length < 1 || extractMutation.isPending}
            className="w-full px-4 py-2 bg-purple-600 text-white text-sm rounded-lg hover:bg-purple-700 disabled:opacity-40 font-medium"
          >
            {extractMutation.isPending
              ? '⌛ Reading packaging…'
              : `🔍 Extract Drug Details from ${camera.photos.length} Photo${camera.photos.length !== 1 ? 's' : ''}`}
          </button>
        </div>
      )}

      {/* ── STEP 2: Review OCR extracted data ── */}
      {step === 2 && (
        <div className="space-y-3">
          {/* OCR confidence banner */}
          {ocrMeta && (
            <div className={`rounded-lg px-3 py-2 text-xs flex items-start gap-2 ${
              ocrMeta.backend === 'none' ? 'bg-amber-50 border border-amber-200 text-amber-800'
              : ocrMeta.confidence >= 0.7 ? 'bg-green-50 border border-green-200 text-green-800'
              : 'bg-yellow-50 border border-yellow-200 text-yellow-800'
            }`}>
              <span className="text-base mt-0.5">
                {ocrMeta.backend === 'none' ? '⚠️' : ocrMeta.confidence >= 0.7 ? '✅' : '🔍'}
              </span>
              <div>
                <div className="font-medium">
                  {ocrMeta.backend === 'none'
                    ? 'Manual entry required — OCR engine not installed'
                    : `OCR extracted data · ${(ocrMeta.confidence * 100).toFixed(0)}% confidence · ${ocrMeta.backend}`}
                </div>
                {ocrMeta.warnings.length > 0 && (
                  <ul className="mt-0.5 list-disc list-inside">
                    {ocrMeta.warnings.map((w, i) => <li key={i}>{w}</li>)}
                  </ul>
                )}
                {ocrMeta.backend !== 'none' && (
                  <div className="mt-0.5 opacity-70">
                    Review all fields below. Correct anything the OCR misread. Fields in
                    <span className="text-purple-700 font-semibold"> purple</span> were auto-filled.
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Form — pre-filled from OCR, all editable */}
          <div className="grid grid-cols-2 gap-3">
            <ExtractedField
              label="NDC-11 *" value={form.ndc11} onChange={f('ndc11')}
              autoFilled={!!ocr?.ndc11} placeholder="00093314905" mono
              hint={ocr?.ndc_raw ? `Printed: ${ocr.ndc_raw}` : undefined}
            />
            <div>
              <label className="text-xs text-gray-600 block mb-1">Dosage Form</label>
              <select
                value={form.dosage_form} onChange={f('dosage_form')}
                className={`w-full border rounded px-2 py-1.5 text-sm ${ocr?.dosage_form ? 'border-purple-300 bg-purple-50' : 'border-gray-200'}`}
              >
                {DOSAGE_FORMS.map(d => <option key={d} value={d}>{d.charAt(0).toUpperCase() + d.slice(1)}</option>)}
              </select>
            </div>
            <div className="col-span-2">
              <ExtractedField
                label="Drug Name + Strength *" value={form.drug_name} onChange={f('drug_name')}
                autoFilled={!!(ocr?.drug_name || ocr?.generic_name)}
                placeholder="e.g. Amoxicillin 500 mg Capsules"
                hint={ocr?.brand_name ? `Brand: ${ocr.brand_name}` : undefined}
              />
            </div>
            <ExtractedField
              label="Manufacturer / Labeler" value={form.manufacturer} onChange={f('manufacturer')}
              autoFilled={!!ocr?.manufacturer} placeholder="e.g. Teva"
            />
            <ExtractedField
              label="Lot / Batch Number" value={form.lot_number} onChange={f('lot_number')}
              autoFilled={!!ocr?.lot_number} placeholder="e.g. TM240501"
            />
            <ExtractedField
              label="Expiry Date" value={form.expiry_date} onChange={f('expiry_date')}
              autoFilled={!!ocr?.expiry_date} placeholder="YYYY-MM-DD"
              hint={ocr?.expiry_date ? `Parsed: ${ocr.expiry_date}` : undefined}
            />
            <div>
              <label className="text-xs text-gray-600 block mb-1">Notes (optional)</label>
              <input
                value={form.notes} onChange={f('notes')}
                className="w-full border border-gray-200 rounded px-2 py-1.5 text-xs"
                placeholder={ocr?.storage_notes || 'Colour, markings, special conditions…'}
              />
            </div>
          </div>

          <label className="flex items-center gap-2 text-xs text-gray-600">
            <input type="checkbox" checked={replace} onChange={e => setReplace(e.target.checked)} />
            Replace existing enrollment for this NDC
          </label>

          <div className="flex gap-2 pt-1">
            <button onClick={() => setStep(1)} className="px-3 py-2 text-xs text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50">
              ← Retake Photos
            </button>
            <button
              onClick={() => poMutation.mutate()}
              disabled={!form.ndc11 || !form.drug_name || poMutation.isPending}
              className="flex-1 px-4 py-2 bg-purple-600 text-white text-sm rounded-lg hover:bg-purple-700 disabled:opacity-40 font-medium"
            >
              {poMutation.isPending ? '⌛ Looking up PO…' : '📋 Continue → Cross-check Purchase Order'}
            </button>
          </div>
        </div>
      )}

      {/* ── STEP 3: PO Cross-reference + Final confirm ── */}
      {step === 3 && (
        <div className="space-y-3">
          <h4 className="text-sm font-semibold text-gray-700">Purchase Order Cross-Reference</h4>
          <p className="text-xs text-gray-500">
            Verify the received item matches what was ordered. Select the matching PO from the distributor.
          </p>

          {/* PO match results */}
          {poData?.found && poData.orders.length > 0 ? (
            <div className="space-y-2">
              {poData.orders.map(po => (
                <button
                  key={po.po_id}
                  onClick={() => setSelectedPo(po)}
                  className={`w-full text-left border rounded-lg px-3 py-2.5 transition-all text-sm ${
                    selectedPo?.po_id === po.po_id
                      ? 'border-purple-400 bg-purple-50 ring-1 ring-purple-200'
                      : 'border-gray-200 hover:border-gray-300'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-gray-800">PO# {po.po_number}</span>
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                      po.po_status === 'acknowledged' ? 'bg-green-100 text-green-700' :
                      po.po_status === 'partial'      ? 'bg-yellow-100 text-yellow-700' :
                      'bg-gray-100 text-gray-600'
                    }`}>{po.po_status}</span>
                  </div>
                  <div className="grid grid-cols-3 gap-2 mt-1.5 text-xs text-gray-600">
                    <span>🏢 {po.wholesaler}</span>
                    <span>📦 Ordered: <strong>{po.quantity_ordered}</strong></span>
                    <span>📬 Outstanding: <strong className={po.quantity_outstanding > 0 ? 'text-orange-600' : 'text-green-600'}>{po.quantity_outstanding}</strong></span>
                    {po.unit_cost && <span>💲 {po.unit_cost.toFixed(2)}/unit</span>}
                    {po.expected_delivery && <span>📅 Exp. {po.expected_delivery}</span>}
                    {po.wholesaler_item_no && <span className="font-mono text-[10px]">{po.wholesaler_item_no}</span>}
                  </div>

                  {/* Drug from PO catalog vs OCR/extracted */}
                  {po.drug_from_catalog.generic_name && (
                    <div className="mt-1.5 text-xs">
                      <span className="text-gray-400">PO catalog: </span>
                      <span className="text-gray-700">
                        {po.drug_from_catalog.generic_name}
                        {po.drug_from_catalog.strength ? ` ${po.drug_from_catalog.strength}` : ''}
                        {po.drug_from_catalog.dosage_form ? ` · ${po.drug_from_catalog.dosage_form}` : ''}
                      </span>
                      {/* Match check */}
                      {form.drug_name.toLowerCase().includes(
                        (po.drug_from_catalog.generic_name || '').toLowerCase().split(' ')[0]
                      ) ? (
                        <span className="ml-2 text-green-600 font-medium">✓ Matches</span>
                      ) : (
                        <span className="ml-2 text-amber-600 font-medium">⚠ Check name</span>
                      )}
                    </div>
                  )}
                </button>
              ))}
              <p className="text-[10px] text-gray-400">
                Select the PO that matches the current delivery. The lot number and expiry will be
                linked to this order for full traceability.
              </p>
            </div>
          ) : (
            <div className="bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 text-sm text-amber-800">
              <div className="font-medium">⚠ No open purchase order found for NDC {form.ndc11}</div>
              <div className="text-xs mt-0.5">
                This item may not have been formally ordered. Continue enrollment without a PO, or
                create a purchase order first.
              </div>
            </div>
          )}

          {/* Summary */}
          <div className="bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 space-y-1 text-xs">
            <div className="font-medium text-gray-700 mb-1">Enrollment Summary</div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-gray-600">
              <span className="text-gray-400">NDC</span>       <span className="font-mono font-medium">{form.ndc11}</span>
              <span className="text-gray-400">Drug</span>      <span>{form.drug_name}</span>
              <span className="text-gray-400">Form</span>      <span>{form.dosage_form}</span>
              <span className="text-gray-400">Manufacturer</span><span>{form.manufacturer || '—'}</span>
              <span className="text-gray-400">Lot</span>       <span>{form.lot_number || '—'}</span>
              <span className="text-gray-400">Expiry</span>    <span>{form.expiry_date || '—'}</span>
              <span className="text-gray-400">Photos</span>    <span>{camera.photos.length}</span>
              <span className="text-gray-400">PO</span>        <span>{selectedPo?.po_number || 'Not linked'}</span>
            </div>
          </div>

          <div className="flex gap-2">
            <button onClick={() => setStep(2)} className="px-3 py-2 text-xs text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50">
              ← Back
            </button>
            <button
              onClick={() => enrollMutation.mutate()}
              disabled={!form.ndc11 || !form.drug_name || camera.photos.length < 1 || enrollMutation.isPending}
              className="flex-1 px-4 py-2 bg-green-600 text-white text-sm rounded-lg hover:bg-green-700 disabled:opacity-40 font-medium"
            >
              {enrollMutation.isPending ? '⌛ Enrolling…' : '✅ Confirm & Enroll Product'}
            </button>
          </div>
        </div>
      )}

      {/* ── STEP 4: Done ── */}
      {step === 4 && (
        <div className="text-center py-4 space-y-3">
          <div className="text-4xl">{enrollResult?.startsWith('✅') ? '✅' : '❌'}</div>
          <p className="text-sm text-gray-700">{enrollResult}</p>
          <button onClick={reset} className="px-4 py-2 bg-purple-600 text-white text-sm rounded-lg hover:bg-purple-700">
            Enroll Another Product
          </button>
        </div>
      )}
    </div>
  )
}

// ── Extracted field component (shows purple highlight when auto-filled) ──
interface FieldProps {
  label:      string
  value:      string
  onChange:   (e: React.ChangeEvent<HTMLInputElement>) => void
  autoFilled: boolean
  placeholder?: string
  hint?:      string
  mono?:      boolean
}

function ExtractedField({ label, value, onChange, autoFilled, placeholder, hint, mono }: FieldProps) {
  return (
    <div>
      <div className="flex items-center gap-1 mb-1">
        <label className="text-xs text-gray-600">{label}</label>
        {autoFilled && (
          <span className="text-[9px] bg-purple-100 text-purple-600 px-1.5 py-0.5 rounded-full font-medium">
            AUTO
          </span>
        )}
      </div>
      <input
        value={value} onChange={onChange}
        className={`w-full border rounded px-2 py-1.5 text-sm ${mono ? 'font-mono' : ''} ${
          autoFilled ? 'border-purple-300 bg-purple-50 focus:ring-purple-300' : 'border-gray-200'
        } focus:outline-none focus:ring-2`}
        placeholder={placeholder}
      />
      {hint && <p className="text-[10px] text-gray-400 mt-0.5">{hint}</p>}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// PackageVerificationPanel  (at dispensing)
// ─────────────────────────────────────────────────────────────────────────────

interface VerificationProps {
  expectedNdc?:  string
  expectedName?: string
  onPass?:       () => void
}

export function PackageVerificationPanel({ expectedNdc, expectedName, onPass }: VerificationProps) {
  const camera = useCamera()
  const [ndc,    setNdc]    = useState(expectedNdc || '')
  const [result, setResult] = useState<VerifyResult | null>(null)

  const verifyMutation = useMutation({
    mutationFn: (frame: string) =>
      apiClient.post('/package-verification/verify', {
        frame_b64: frame, expected_ndc: ndc, top_k: 3,
      }).then(r => r.data as VerifyResult),
    onSuccess: (data) => {
      setResult(data)
      if (data.is_pass && onPass) onPass()
    },
  })

  const handleCapture = () => {
    const frame = camera.capture()
    if (frame) verifyMutation.mutate(frame)
  }

  const colors = result ? statusColors(result.status) : null

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <span className="text-lg">🔍</span>
        <h3 className="font-semibold text-gray-800">Visual Package Verification</h3>
        <span className="text-xs bg-purple-100 text-purple-700 px-2 py-0.5 rounded-full">At Dispensing</span>
      </div>
      <p className="text-xs text-gray-500">
        Point camera at the pulled medication packaging. The ML engine confirms it visually matches
        the expected product before it reaches the pharmacist review stage.
      </p>

      {expectedName && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg px-3 py-2 text-sm">
          <span className="text-blue-600 font-medium">Expected: </span>
          <span className="text-blue-800">{expectedName}</span>
          <span className="text-blue-400 text-xs ml-2 font-mono">{expectedNdc}</span>
        </div>
      )}

      {!expectedNdc && (
        <div>
          <label className="text-xs text-gray-600 block mb-1">Expected NDC for this Rx line</label>
          <input
            value={ndc} onChange={e => setNdc(e.target.value)}
            className="w-full border border-gray-200 rounded px-2 py-1.5 text-sm font-mono"
            placeholder="00093314905"
          />
        </div>
      )}

      <div className="border border-gray-200 rounded-lg p-3 bg-gray-50">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm font-medium text-gray-700">Camera</span>
          <div className="flex gap-2">
            {!camera.active
              ? <button onClick={camera.start} className="text-xs px-3 py-1 bg-blue-600 text-white rounded hover:bg-blue-700">📷 Start</button>
              : <button onClick={camera.stop}  className="text-xs px-3 py-1 bg-gray-600 text-white rounded">⏹ Stop</button>
            }
          </div>
        </div>
        {camera.error && <p className="text-red-500 text-xs mb-2">{camera.error}</p>}
        {camera.active && <video ref={camera.videoRef} className="w-full rounded border border-gray-300 max-h-48 object-cover" />}
        <canvas ref={camera.canvasRef} className="hidden" />
      </div>

      <button
        onClick={handleCapture}
        disabled={!camera.active || !ndc || verifyMutation.isPending}
        className="w-full px-4 py-2 bg-purple-600 text-white text-sm rounded-lg hover:bg-purple-700 disabled:opacity-40 font-medium"
      >
        {verifyMutation.isPending ? '⌛ Verifying…' : '🔍 Capture & Verify'}
      </button>

      {result && colors && (
        <div className={`rounded-lg border px-4 py-3 ${colors.bg} ${colors.border}`}>
          <div className={`flex items-center gap-2 font-semibold ${colors.text}`}>
            <span className="text-xl">{colors.icon}</span>
            <span>{result.status.toUpperCase()} — {result.confidence_pct.toFixed(1)}% match</span>
            <span className="ml-auto text-xs font-normal opacity-60">{result.latency_ms.toFixed(0)}ms</span>
          </div>
          {confBar(result.confidence_pct, result.status)}
          <p className={`text-sm mt-2 ${colors.text}`}>{result.message}</p>
          {result.top_candidates.length > 0 && (
            <div className="mt-2 border-t border-current/20 pt-2">
              <p className="text-xs opacity-70 mb-1">Top matches:</p>
              {result.top_candidates.map((c, i) => (
                <div key={i} className="flex items-center justify-between text-xs py-0.5">
                  <span className="font-mono">{c.ndc11}</span>
                  <span className="flex-1 mx-2 text-gray-600 truncate">{c.drug_name}</span>
                  <span className={c.is_accepted ? 'text-green-600 font-medium' : 'text-gray-500'}>
                    {(c.similarity * 100).toFixed(1)}%
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// EnrolledProductsTable
// ─────────────────────────────────────────────────────────────────────────────

export function EnrolledProductsTable() {
  const qc      = useQueryClient()
  const staffId = localStorage.getItem('staff_id') || 'pharmacist'
  const [filter, setFilter] = useState('')

  const { data: raw } = useQuery({
    queryKey: ['enrolled-products'],
    queryFn:  () => apiClient.get('/package-verification/products').then(r => r.data),
    select:   d => (d?.products ?? DEMO_PRODUCTS) as EnrolledProduct[],
    placeholderData: { products: DEMO_PRODUCTS },
  })

  const { data: st } = useQuery({
    queryKey: ['package-verify-status'],
    queryFn:  () => apiClient.get('/package-verification/status').then(r => r.data as EngineStatus),
    placeholderData: DEMO_STATUS,
  })

  const deleteMutation = useMutation({
    mutationFn: (ndc: string) =>
      apiClient.delete(`/package-verification/products/${ndc}`, { params: { staff_id: staffId } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['enrolled-products'] }),
  })

  const products = (raw ?? DEMO_PRODUCTS).filter(p =>
    !filter || p.drug_name.toLowerCase().includes(filter.toLowerCase()) || p.ndc11.includes(filter)
  )

  const formIcon = (f: string) =>
    ({ sachet:'📩', blister:'💊', bottle:'🧴', box:'📦', vial:'💉', tablet:'⬜', capsule:'🔵',
       injection:'💉', ampoule:'🔬', syrup:'🍶', cream:'🧴', other:'📋' }[f] || '📋')

  return (
    <div className="space-y-3">
      {st && (
        <div className="flex flex-wrap items-center gap-3 bg-gray-50 rounded-lg px-3 py-2 text-xs text-gray-600">
          <span>🧠 Vision: <strong>{st.backend}</strong></span>
          <span>OCR: <strong>{st.ocr_backend}</strong></span>
          <span>Dim: {st.embed_dim}</span>
          <span>Enrolled: <strong>{st.enrolled_count}</strong></span>
          <span>Accept ≥ {(st.accept_threshold * 100).toFixed(0)}%</span>
        </div>
      )}

      <div className="flex items-center gap-2">
        <input
          value={filter} onChange={e => setFilter(e.target.value)}
          placeholder="Filter by name or NDC…"
          className="flex-1 border border-gray-200 rounded px-3 py-1.5 text-sm"
        />
        <span className="text-xs text-gray-400">{products.length} product{products.length !== 1 ? 's' : ''}</span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 text-xs text-gray-500 uppercase tracking-wide">
              <th className="text-left pb-2 pr-3">Product</th>
              <th className="text-left pb-2 pr-3">NDC-11</th>
              <th className="text-left pb-2 pr-3">Form</th>
              <th className="text-center pb-2 pr-3">Photos</th>
              <th className="text-left pb-2 pr-3">Enrolled</th>
              <th className="pb-2"></th>
            </tr>
          </thead>
          <tbody>
            {products.map(p => (
              <tr key={p.ndc11} className="border-b border-gray-100 hover:bg-gray-50">
                <td className="py-2 pr-3">
                  <div className="font-medium text-gray-800">{p.drug_name}</div>
                  {p.manufacturer && <div className="text-xs text-gray-400">{p.manufacturer}</div>}
                  {p.notes && <div className="text-[10px] text-blue-500 italic truncate max-w-[200px]">{p.notes}</div>}
                </td>
                <td className="py-2 pr-3 font-mono text-xs text-gray-600">{p.ndc11}</td>
                <td className="py-2 pr-3">
                  <span className="text-base" title={p.dosage_form}>{formIcon(p.dosage_form)}</span>
                  <span className="text-xs text-gray-500 ml-1">{p.dosage_form}</span>
                </td>
                <td className="py-2 pr-3 text-center">
                  <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${
                    p.photo_count >= 5 ? 'bg-green-100 text-green-700'
                    : p.photo_count >= 3 ? 'bg-yellow-100 text-yellow-700'
                    : 'bg-red-100 text-red-700'
                  }`}>{p.photo_count}</span>
                </td>
                <td className="py-2 pr-3 text-xs text-gray-500">
                  <div>{new Date(p.enrolled_at).toLocaleDateString()}</div>
                  <div className="text-[10px]">{p.enrolled_by}</div>
                </td>
                <td className="py-2 text-right">
                  <button
                    onClick={() => deleteMutation.mutate(p.ndc11)}
                    disabled={deleteMutation.isPending}
                    className="text-xs text-red-400 hover:text-red-600 px-2 py-1 rounded hover:bg-red-50"
                    title="Remove enrollment"
                  >🗑</button>
                </td>
              </tr>
            ))}
            {products.length === 0 && (
              <tr>
                <td colSpan={6} className="py-8 text-center text-gray-400 text-sm">
                  No products enrolled yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// PackageVerificationDashboard  (combined view)
// ─────────────────────────────────────────────────────────────────────────────

export default function PackageVerificationDashboard() {
  const [activeTab, setActiveTab] = useState<'verify' | 'enroll' | 'catalog'>('verify')

  const tabs = [
    { id: 'verify'  as const, label: '🔍 Verify Item',    desc: 'At dispensing' },
    { id: 'enroll'  as const, label: '📦 Enroll Product', desc: 'At receiving'  },
    { id: 'catalog' as const, label: '📋 Catalog',        desc: 'All products'  },
  ]

  return (
    <div className="bg-white border border-gray-200 rounded-xl shadow-sm">
      <div className="px-5 py-3 border-b">
        <h2 className="font-semibold text-gray-800">Package Visual Verification</h2>
        <p className="text-xs text-gray-500">
          Phase 24 · MobileNetV3 offline ML · OCR auto-extraction · PO cross-reference
        </p>
      </div>
      <div className="flex border-b border-gray-100">
        {tabs.map(t => (
          <button
            key={t.id}
            onClick={() => setActiveTab(t.id)}
            className={`flex-1 px-4 py-3 text-sm transition-colors ${
              activeTab === t.id
                ? 'border-b-2 border-purple-500 text-purple-700 font-medium bg-purple-50/50'
                : 'text-gray-500 hover:text-gray-700 hover:bg-gray-50'
            }`}
          >
            <div>{t.label}</div>
            <div className="text-[10px] opacity-60">{t.desc}</div>
          </button>
        ))}
      </div>
      <div className="p-5">
        {activeTab === 'verify'  && <PackageVerificationPanel />}
        {activeTab === 'enroll'  && <PackageEnrollmentPanel />}
        {activeTab === 'catalog' && <EnrolledProductsTable />}
      </div>
    </div>
  )
}
