/**
 * RxScanner — Phase 32 (enhanced with transcription)
 * =====================================================
 * Capture, manage, and intelligently transcribe physical prescription documents.
 *
 * Four tabs:
 *   🗂 Documents  — view, download, delete attached docs
 *   📷 Camera     — webcam capture with instant transcribe option
 *   📁 Upload     — file drag-and-drop (images + PDF)
 *   📝 Transcribe — OCR results, patient name match, prescriber council no.
 *
 * Transcription extracts:
 *   • Patient name   → compared against the visitor (who brought the Rx)
 *                      → if different person, enriches the family/relationship tree
 *   • Medications    → drug name, strength, dosage form, SIG, quantity, refills
 *   • Prescriber     → full name + medical council / license number
 *   • Rx date, Rx #
 *
 * API:
 *   POST /api/v1/rx-transcription/transcribe          — from saved doc_id
 *   POST /api/v1/rx-transcription/transcribe-base64   — from live camera frames
 *   POST /api/v1/rx-transcription/confirm             — staff confirms + links
 *   GET  /api/v1/rx-documents?rx_id=…                — list saved docs
 */
import { useState, useRef, useEffect, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../lib/api'

// ─── Types ────────────────────────────────────────────────────────────────────

type DocType = 'original_rx' | 'fax_confirmation' | 'prior_auth' |
               'prescriber_note' | 'insurance_letter' | 'compound_formula' | 'other'

interface DocumentMeta {
  id:          string
  rx_id:       string
  doc_type:    DocType
  filename:    string
  mime_type:   string
  file_size:   number
  uploaded_by: string
  notes:       string | null
  created_at:  string
  deleted:     boolean
}

interface MedicationResult {
  drug_name:   string
  strength:    string | null
  dosage_form: string | null
  sig:         string | null
  quantity:    string | null
  refills:     number | null
  confidence:  number
}

interface PrescriberResult {
  full_name:          string
  suffix:             string | null
  medical_council_no: string | null
  council_authority:  string | null
  confidence:         number
}

interface PatientCandidate {
  patient_id:     string
  full_name:      string
  national_id:    string | null
  similarity:     number
  classification: 'same' | 'likely' | 'possible' | 'different'
}

interface TranscribeResponse {
  doc_id:             string | null
  patient_name:       string | null
  patient_name_conf:  number
  patient_match:      string | null     // classification vs visitor
  patient_similarity: number | null
  patient_candidates: PatientCandidate[]
  prescriber:         PrescriberResult | null
  medications:        MedicationResult[]
  rx_date:            string | null
  rx_number:          string | null
  overall_confidence: number
  raw_text:           string
}

interface Props {
  rxId:              string
  rxNumber:          string
  visitorPatientId?: string    // the person who walked in with the Rx
  onClose?:          () => void
  inline?:           boolean
}

// ─── Constants ────────────────────────────────────────────────────────────────

const DOC_TYPE_LABELS: Record<DocType, string> = {
  original_rx:      '📄 Original Rx',
  fax_confirmation: '📠 Fax Confirmation',
  prior_auth:       '📋 Prior Auth',
  prescriber_note:  '🩺 Prescriber Note',
  insurance_letter: '💼 Insurance Letter',
  compound_formula: '🧪 Compound Formula',
  other:            '📎 Other',
}

const CLASSIFICATION_BADGE: Record<string, { label: string; colour: string }> = {
  same:      { label: 'Same person',   colour: 'bg-green-100 text-green-700' },
  likely:    { label: 'Likely same',   colour: 'bg-blue-100 text-blue-700'   },
  possible:  { label: 'Possibly different', colour: 'bg-amber-100 text-amber-700' },
  different: { label: 'Different person',   colour: 'bg-red-100 text-red-700'    },
  unknown:   { label: 'Visitor unknown',    colour: 'bg-gray-100 text-gray-500'  },
}

const RELATIONSHIP_OPTIONS = [
  'Parent / Guardian',
  'Spouse / Partner',
  'Child',
  'Sibling',
  'Caregiver / Aide',
  'Friend',
  'Other family member',
]

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1048576).toFixed(1)} MB`
}

function ConfBar({ value, label }: { value: number; label?: string }) {
  const pct  = Math.round(value * 100)
  const col  = pct >= 80 ? 'bg-green-500' : pct >= 60 ? 'bg-amber-400' : 'bg-red-400'
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
        <div className={`h-full ${col} rounded-full`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] text-gray-400 w-8 text-right">{pct}%</span>
      {label && <span className="text-[10px] text-gray-400">{label}</span>}
    </div>
  )
}

// ─── Webcam sub-component ─────────────────────────────────────────────────────

function WebcamCapture({ onCapture, onClose }: {
  onCapture: (b64: string) => void
  onClose:   () => void
}) {
  const videoRef  = useRef<HTMLVideoElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const [ready, setReady]       = useState(false)
  const [error, setError]       = useState<string | null>(null)
  const [captured, setCaptured] = useState<string | null>(null)

  useEffect(() => {
    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: 'environment', width: 1280, height: 720 } })
      .then(s => {
        streamRef.current = s
        if (videoRef.current) {
          videoRef.current.srcObject = s
          videoRef.current.onloadedmetadata = () => setReady(true)
        }
      })
      .catch(() => setError('Camera access denied'))
    return () => streamRef.current?.getTracks().forEach(t => t.stop())
  }, [])

  const capture = () => {
    if (!videoRef.current || !canvasRef.current) return
    const v = videoRef.current; const c = canvasRef.current
    c.width = v.videoWidth; c.height = v.videoHeight
    c.getContext('2d')?.drawImage(v, 0, 0)
    setCaptured(c.toDataURL('image/jpeg', 0.92))
  }

  if (error) return (
    <div className="text-center py-6 text-red-500 text-sm">
      📷 {error}
      <br /><button onClick={onClose} className="mt-2 text-xs underline text-gray-400">Close</button>
    </div>
  )

  return (
    <div className="space-y-2">
      <div className="bg-black rounded-lg overflow-hidden aspect-video relative">
        <video ref={videoRef} autoPlay playsInline muted
          className={`w-full h-full object-contain ${captured ? 'hidden' : ''}`} />
        {captured && <img src={captured} alt="Captured" className="w-full h-full object-contain" />}
        {!ready && !captured && (
          <div className="absolute inset-0 flex items-center justify-center text-white text-sm">
            Initialising camera…
          </div>
        )}
        <canvas ref={canvasRef} className="hidden" />
      </div>
      <div className="flex gap-2">
        {!captured ? (
          <button onClick={capture} disabled={!ready}
            className="flex-1 py-2 bg-blue-600 text-white rounded-lg text-sm disabled:opacity-40 hover:bg-blue-700">
            📸 Capture
          </button>
        ) : (
          <>
            <button onClick={() => setCaptured(null)}
              className="flex-1 py-2 border border-gray-200 text-gray-600 rounded-lg text-sm hover:bg-gray-50">
              🔄 Retake
            </button>
            <button onClick={() => onCapture(captured!)}
              className="flex-1 py-2 bg-green-600 text-white rounded-lg text-sm hover:bg-green-700">
              ✅ Use this
            </button>
          </>
        )}
        <button onClick={onClose} className="px-3 py-2 border border-gray-200 text-gray-500 text-sm rounded-lg hover:bg-gray-50">✕</button>
      </div>
    </div>
  )
}

// ─── Transcription Results Panel ──────────────────────────────────────────────

// Confirmed-transcription payload — shared by TranscriptionPanel.onConfirm and
// the confirm mutation so both sides stay in lockstep.
interface RxConfirmPayload {
  patientName:        string
  rxPatientId:        string | null
  relationship:       string | null
  prescriberName:     string
  prescriberSuffix:   string
  councilNo:          string
  councilAuthority:   string
  medications:        MedicationResult[]
  rxDate:             string
}

function TranscriptionPanel({
  result,
  onConfirm,
  isPending,
}: {
  result:    TranscribeResponse
  onConfirm: (data: RxConfirmPayload) => void
  isPending: boolean
}) {
  const [patientName,      setPatientName]      = useState(result.patient_name     || '')
  const [rxPatientId,      setRxPatientId]      = useState<string | null>(null)
  const [relationship,     setRelationship]     = useState<string | null>(null)
  const [, setShowRelDropdown] = useState(false)
  const [prescriberName,   setPrescriberName]   = useState(result.prescriber?.full_name    || '')
  const [prescriberSuffix, setPrescriberSuffix] = useState(result.prescriber?.suffix       || '')
  const [councilNo,        setCouncilNo]        = useState(result.prescriber?.medical_council_no || '')
  const [councilAuthority, setCouncilAuthority] = useState(result.prescriber?.council_authority || '')
  const [rxDate,           setRxDate]           = useState(result.rx_date || '')
  const [meds,             setMeds]             = useState<MedicationResult[]>(result.medications)

  const matchInfo  = result.patient_match ? (CLASSIFICATION_BADGE[result.patient_match] ?? CLASSIFICATION_BADGE.unknown) : null
  const isDifferent = result.patient_match && ['possible', 'different'].includes(result.patient_match)

  // Trigger relationship dropdown when visitor is different
  useEffect(() => { if (isDifferent) setShowRelDropdown(true) }, [isDifferent])

  const pct = Math.round(result.overall_confidence * 100)

  return (
    <div className="space-y-4">
      {/* OCR confidence header */}
      <div className="flex items-center gap-3 bg-gray-50 rounded-lg px-3 py-2">
        <div className={`w-10 h-10 rounded-full flex items-center justify-center text-sm font-bold ${
          pct >= 70 ? 'bg-green-100 text-green-700' : pct >= 45 ? 'bg-amber-100 text-amber-700' : 'bg-red-100 text-red-600'
        }`}>{pct}%</div>
        <div className="flex-1">
          <p className="text-sm font-medium text-gray-700">OCR Transcription</p>
          <ConfBar value={result.overall_confidence} />
        </div>
        <span className="text-xs text-gray-400">
          {result.medications.length} medication{result.medications.length !== 1 ? 's' : ''} found
        </span>
      </div>

      {/* ── Patient section ── */}
      <div className="border border-gray-100 rounded-lg p-3 space-y-2">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-sm font-semibold text-gray-700">👤 Patient on Rx</span>
          {matchInfo && (
            <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${matchInfo.colour}`}>
              {matchInfo.label}
            </span>
          )}
          {result.patient_similarity != null && (
            <span className="text-[10px] text-gray-400 ml-auto">
              similarity {Math.round(result.patient_similarity * 100)}%
            </span>
          )}
        </div>
        <div className="flex gap-2 items-center">
          <input
            value={patientName} onChange={e => setPatientName(e.target.value)}
            className="flex-1 border border-gray-200 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-purple-200 focus:outline-none"
            placeholder="Patient name from Rx"
          />
          <ConfBar value={result.patient_name_conf} />
        </div>

        {/* DB candidates */}
        {result.patient_candidates.length > 0 && (
          <div className="space-y-1">
            <p className="text-[10px] text-gray-400 uppercase tracking-wide">Database matches</p>
            {result.patient_candidates.slice(0, 4).map(c => (
              <button
                key={c.patient_id}
                onClick={() => { setPatientName(c.full_name); setRxPatientId(c.patient_id) }}
                className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-xs text-left transition-all ${
                  rxPatientId === c.patient_id
                    ? 'bg-purple-50 border border-purple-300 text-purple-800'
                    : 'border border-gray-100 hover:bg-gray-50 text-gray-700'
                }`}
              >
                <span className={`px-1.5 py-0.5 rounded text-[9px] font-semibold ${CLASSIFICATION_BADGE[c.classification]?.colour}`}>
                  {c.classification}
                </span>
                <span className="font-medium">{c.full_name}</span>
                {c.national_id && <span className="text-gray-400 ml-auto">ID: {c.national_id}</span>}
                <span className="text-gray-300">{Math.round(c.similarity * 100)}%</span>
              </button>
            ))}
          </div>
        )}

        {/* Relationship selector — shown when visitor ≠ Rx patient */}
        {isDifferent && (
          <div className="bg-amber-50 border border-amber-200 rounded-lg p-2 space-y-2">
            <p className="text-xs font-medium text-amber-800">
              ⚠ The person who brought this Rx appears to be <strong>different</strong> from
              the patient named on the prescription. A link will be added to the
              relationship tree. Optionally select a relationship label:
            </p>
            <select
              value={relationship || ''}
              onChange={e => setRelationship(e.target.value || null)}
              className="w-full border border-amber-200 rounded px-2 py-1.5 text-xs bg-white"
            >
              <option value="">— No label (untyped link) —</option>
              {RELATIONSHIP_OPTIONS.map(r => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
        )}
      </div>

      {/* ── Medications ── */}
      <div className="border border-gray-100 rounded-lg p-3 space-y-2">
        <div className="flex items-center justify-between mb-1">
          <span className="text-sm font-semibold text-gray-700">💊 Medications</span>
          <button
            onClick={() => setMeds(m => [...m, {
              drug_name: '', strength: null, dosage_form: null,
              sig: null, quantity: null, refills: null, confidence: 1.0,
            }])}
            className="text-xs text-purple-600 hover:underline"
          >
            + Add
          </button>
        </div>
        {meds.length === 0 && (
          <p className="text-xs text-gray-400 italic">No medications detected — add manually</p>
        )}
        {meds.map((m, i) => (
          <div key={i} className="grid grid-cols-3 gap-1.5 border border-gray-100 rounded p-2">
            <div className="col-span-3">
              <label className="text-[10px] text-gray-400 block mb-0.5">Drug name</label>
              <input
                value={m.drug_name}
                onChange={e => setMeds(prev => prev.map((p, j) => j === i ? { ...p, drug_name: e.target.value } : p))}
                className="w-full border border-gray-200 rounded px-2 py-1 text-xs"
                placeholder="e.g. Amoxicillin"
              />
            </div>
            <div>
              <label className="text-[10px] text-gray-400 block mb-0.5">Strength</label>
              <input
                value={m.strength || ''}
                onChange={e => setMeds(prev => prev.map((p, j) => j === i ? { ...p, strength: e.target.value || null } : p))}
                className="w-full border border-gray-200 rounded px-2 py-1 text-xs"
                placeholder="500mg"
              />
            </div>
            <div>
              <label className="text-[10px] text-gray-400 block mb-0.5">Form</label>
              <input
                value={m.dosage_form || ''}
                onChange={e => setMeds(prev => prev.map((p, j) => j === i ? { ...p, dosage_form: e.target.value || null } : p))}
                className="w-full border border-gray-200 rounded px-2 py-1 text-xs"
                placeholder="tablet"
              />
            </div>
            <div>
              <label className="text-[10px] text-gray-400 block mb-0.5">Qty</label>
              <input
                value={m.quantity || ''}
                onChange={e => setMeds(prev => prev.map((p, j) => j === i ? { ...p, quantity: e.target.value || null } : p))}
                className="w-full border border-gray-200 rounded px-2 py-1 text-xs"
                placeholder="30"
              />
            </div>
            <div className="col-span-2">
              <label className="text-[10px] text-gray-400 block mb-0.5">SIG (directions)</label>
              <input
                value={m.sig || ''}
                onChange={e => setMeds(prev => prev.map((p, j) => j === i ? { ...p, sig: e.target.value || null } : p))}
                className="w-full border border-gray-200 rounded px-2 py-1 text-xs"
                placeholder="1 tablet PO BID × 10 days"
              />
            </div>
            <div className="col-span-3 flex justify-between items-center">
              <ConfBar value={m.confidence} />
              <button onClick={() => setMeds(prev => prev.filter((_, j) => j !== i))}
                className="text-xs text-red-400 hover:text-red-600 ml-2">Remove</button>
            </div>
          </div>
        ))}
      </div>

      {/* ── Prescriber ── */}
      <div className="border border-gray-100 rounded-lg p-3 space-y-2">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-sm font-semibold text-gray-700">🩺 Prescriber</span>
          {result.prescriber && <ConfBar value={result.prescriber.confidence} />}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div className="col-span-2">
            <label className="text-[10px] text-gray-400 block mb-0.5">Full name</label>
            <input
              value={prescriberName}
              onChange={e => setPrescriberName(e.target.value)}
              className="w-full border border-gray-200 rounded-lg px-3 py-1.5 text-sm"
              placeholder="e.g. Ahmad Rezaei"
            />
          </div>
          <div>
            <label className="text-[10px] text-gray-400 block mb-0.5">Title / suffix</label>
            <input
              value={prescriberSuffix}
              onChange={e => setPrescriberSuffix(e.target.value)}
              className="w-full border border-gray-200 rounded-lg px-3 py-1.5 text-sm"
              placeholder="MD / MBBS / GP…"
            />
          </div>
          <div>
            <label className="text-[10px] text-gray-400 block mb-0.5">Rx date</label>
            <input
              value={rxDate}
              onChange={e => setRxDate(e.target.value)}
              className="w-full border border-gray-200 rounded-lg px-3 py-1.5 text-sm"
              placeholder="Date from Rx"
            />
          </div>
          {/* Medical council number — highlighted field */}
          <div className="col-span-2 bg-indigo-50 border border-indigo-200 rounded-lg p-2 space-y-1.5">
            <p className="text-[10px] font-semibold text-indigo-700 uppercase tracking-wide">
              🏥 Medical Council / License Registration
            </p>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-[10px] text-gray-500 block mb-0.5">Council No. / License No.</label>
                <input
                  value={councilNo}
                  onChange={e => setCouncilNo(e.target.value)}
                  className="w-full border border-indigo-200 rounded px-2 py-1.5 text-sm font-mono bg-white"
                  placeholder="e.g. 12345 or LIC-987654"
                />
              </div>
              <div>
                <label className="text-[10px] text-gray-500 block mb-0.5">Issuing authority</label>
                <input
                  value={councilAuthority}
                  onChange={e => setCouncilAuthority(e.target.value)}
                  className="w-full border border-indigo-200 rounded px-2 py-1.5 text-sm bg-white"
                  placeholder="e.g. Iranian Medical Council"
                />
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Confirm button */}
      <button
        onClick={() => onConfirm({
          patientName, rxPatientId, relationship,
          prescriberName, prescriberSuffix, councilNo, councilAuthority,
          medications: meds, rxDate,
        })}
        disabled={isPending}
        className="w-full py-2.5 bg-purple-600 text-white text-sm rounded-lg font-semibold hover:bg-purple-700 disabled:opacity-40"
      >
        {isPending ? '⌛ Saving…' : '✅ Confirm Transcription & Update Records'}
      </button>
    </div>
  )
}

// ─── Main Component ───────────────────────────────────────────────────────────

export default function RxScanner({ rxId, rxNumber, visitorPatientId, onClose, inline = false }: Props) {
  const qc      = useQueryClient()
  const staffId = localStorage.getItem('staff_id') || 'tech'

  type Tab = 'docs' | 'webcam' | 'upload' | 'transcribe'
  const [tab,           setTab]           = useState<Tab>('docs')
  const [docType,       setDocType]       = useState<DocType>('original_rx')
  const [notes,         setNotes]         = useState('')
  const [dragOver,      setDragOver]      = useState(false)
  const [uploadErr,     setUploadErr]     = useState<string | null>(null)
  const [deleteConf,    setDeleteConf]    = useState<string | null>(null)
  const [activeDocId,   setActiveDocId]   = useState<string | null>(null)
  const [transcription, setTranscription] = useState<TranscribeResponse | null>(null)
  const [confirmed,     setConfirmed]     = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // ── List documents ──
  const { data: rawDocs } = useQuery({
    queryKey: ['rx-documents', rxId],
    queryFn:  () => apiClient.get('/rx-documents', { params: { rx_id: rxId } })
                    .then(r => r.data.documents as DocumentMeta[]),
    enabled:  !!rxId,
  })
  const docs = rawDocs ?? []

  // ── Upload base64 (webcam) ──
  const uploadBase64Mutation = useMutation({
    mutationFn: (b64: string) => apiClient.post('/rx-documents/upload-base64', {
      rx_id: rxId, doc_type: docType,
      filename: `rx_scan_${Date.now()}.jpg`,
      base64_data: b64, mime_type: 'image/jpeg',
      notes: notes || undefined, uploaded_by: staffId,
    }).then(r => r.data as DocumentMeta),
    onSuccess: (doc) => {
      qc.invalidateQueries({ queryKey: ['rx-documents', rxId] })
      setNotes('')
      // Auto-kick off transcription for the saved doc
      transcribeMutation.mutate({ doc_id: doc.id })
      setActiveDocId(doc.id)
      setTab('transcribe')
    },
    onError: () => setUploadErr('Upload failed'),
  })

  // ── Upload file ──
  const uploadFileMutation = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData()
      form.append('rx_id', rxId); form.append('doc_type', docType)
      form.append('notes', notes); form.append('uploaded_by', staffId)
      form.append('file', file)
      return apiClient.post('/rx-documents/upload', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      }).then(r => r.data as DocumentMeta)
    },
    onSuccess: (doc) => {
      qc.invalidateQueries({ queryKey: ['rx-documents', rxId] })
      setNotes('')
      transcribeMutation.mutate({ doc_id: doc.id })
      setActiveDocId(doc.id)
      setTab('transcribe')
    },
    onError: () => setUploadErr('Upload failed'),
  })

  // ── Transcribe from doc_id ──
  const transcribeMutation = useMutation({
    mutationFn: (body: { doc_id: string }) =>
      apiClient.post('/rx-transcription/transcribe', {
        doc_id:             body.doc_id,
        visitor_patient_id: visitorPatientId || undefined,
      }).then(r => r.data as TranscribeResponse),
    onSuccess: (data) => { setTranscription(data); setConfirmed(false) },
  })

  // ── Confirm ──
  const confirmMutation = useMutation({
    mutationFn: (data: RxConfirmPayload) =>
      apiClient.post('/rx-transcription/confirm', {
        doc_id:                        activeDocId,
        rx_id:                         rxId,
        patient_name:                  data.patientName,
        rx_patient_id:                 data.rxPatientId,
        visitor_patient_id:            visitorPatientId,
        relationship_hint:             data.relationship,
        prescriber_full_name:          data.prescriberName,
        prescriber_suffix:             data.prescriberSuffix,
        prescriber_medical_council_no: data.councilNo,
        prescriber_council_authority:  data.councilAuthority,
        medications:                   data.medications,
        rx_date:                       data.rxDate,
        confirmed_by:                  staffId,
      }).then(r => r.data),
    onSuccess: () => setConfirmed(true),
  })

  // ── Delete ──
  const deleteMutation = useMutation({
    mutationFn: (id: string) => apiClient.delete(`/rx-documents/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['rx-documents', rxId] }); setDeleteConf(null) },
  })

  const handleFiles = useCallback((files: FileList | null) => {
    if (!files || !files.length) return
    setUploadErr(null)
    uploadFileMutation.mutate(files[0])
  }, [docType, notes, rxId])

  // ─── Inner content ──────────────────────────────────────────────────────────

  const inner = (
    <div className="space-y-3">
      {/* Rx strip */}
      <div className="flex items-center gap-2 text-sm text-gray-600 bg-gray-50 rounded-lg px-3 py-2">
        <span className="text-base">📋</span>
        <span className="font-mono font-medium">{rxNumber}</span>
        <span className="text-gray-300">·</span>
        <span className="text-gray-500">{docs.length} document{docs.length !== 1 ? 's' : ''} attached</span>
        {visitorPatientId && (
          <span className="ml-auto text-xs text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded-full">
            👤 Visitor ID linked
          </span>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-gray-100 rounded-lg p-1">
        {([
          ['docs',      '🗂 Docs'],
          ['webcam',    '📷 Camera'],
          ['upload',    '📁 Upload'],
          ['transcribe','📝 Transcribe'],
        ] as [Tab, string][]).map(([t, label]) => (
          <button key={t} onClick={() => setTab(t)}
            className={`flex-1 py-1.5 text-xs rounded-md transition-all ${
              tab === t ? 'bg-white shadow text-gray-800 font-medium' : 'text-gray-500 hover:text-gray-700'
            }`}>
            {label}
          </button>
        ))}
      </div>

      {/* ── Documents tab ── */}
      {tab === 'docs' && (
        <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
          {docs.length === 0 ? (
            <div className="text-center py-6 text-gray-400 text-sm">
              <p className="text-3xl mb-2">📭</p>No documents attached yet
            </div>
          ) : docs.map(doc => (
            <div key={doc.id}
              className={`flex items-center gap-3 border rounded-lg px-3 py-2 hover:bg-gray-50 transition-all ${
                activeDocId === doc.id ? 'border-purple-300 bg-purple-50' : 'border-gray-100'
              }`}
            >
              <span className="text-xl">{doc.mime_type === 'application/pdf' ? '📄' : '🖼'}</span>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium truncate text-gray-800">{doc.filename}</p>
                <p className="text-xs text-gray-400">
                  {DOC_TYPE_LABELS[doc.doc_type as DocType]} · {formatBytes(doc.file_size)}
                </p>
              </div>
              <div className="flex gap-1">
                <button
                  onClick={() => { setActiveDocId(doc.id); transcribeMutation.mutate({ doc_id: doc.id }); setTab('transcribe') }}
                  className="text-xs px-2 py-1 text-indigo-600 border border-indigo-200 rounded hover:bg-indigo-50"
                  title="Run OCR transcription"
                >
                  📝 Transcribe
                </button>
                <a href={`/api/v1/rx-documents/${doc.id}/file`} target="_blank" rel="noopener noreferrer"
                  className="text-xs px-2 py-1 text-blue-600 border border-blue-200 rounded hover:bg-blue-50">
                  View
                </a>
                {deleteConf === doc.id ? (
                  <>
                    <button onClick={() => deleteMutation.mutate(doc.id)}
                      className="text-xs px-1.5 py-1 text-red-600 border border-red-200 rounded hover:bg-red-50">Confirm</button>
                    <button onClick={() => setDeleteConf(null)}
                      className="text-xs px-1.5 py-1 border border-gray-200 rounded hover:bg-gray-50">No</button>
                  </>
                ) : (
                  <button onClick={() => setDeleteConf(doc.id)}
                    className="text-xs px-2 py-1 text-red-400 border border-red-100 rounded hover:bg-red-50">✕</button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── Camera tab ── */}
      {tab === 'webcam' && (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-xs text-gray-500 block mb-1">Document type</label>
              <select value={docType} onChange={e => setDocType(e.target.value as DocType)}
                className="w-full border border-gray-200 rounded px-2 py-1.5 text-sm">
                {(Object.keys(DOC_TYPE_LABELS) as DocType[]).map(k => (
                  <option key={k} value={k}>{DOC_TYPE_LABELS[k]}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-xs text-gray-500 block mb-1">Notes</label>
              <input value={notes} onChange={e => setNotes(e.target.value)}
                className="w-full border border-gray-200 rounded px-2 py-1.5 text-sm" placeholder="optional" />
            </div>
          </div>
          <WebcamCapture
            onCapture={b64 => uploadBase64Mutation.mutate(b64)}
            onClose={() => setTab('docs')}
          />
          {(uploadBase64Mutation.isPending || transcribeMutation.isPending) && (
            <p className="text-xs text-center text-gray-500">
              {uploadBase64Mutation.isPending ? '⌛ Uploading…' : '🔍 Transcribing…'}
            </p>
          )}
        </div>
      )}

      {/* ── Upload tab ── */}
      {tab === 'upload' && (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-xs text-gray-500 block mb-1">Document type</label>
              <select value={docType} onChange={e => setDocType(e.target.value as DocType)}
                className="w-full border border-gray-200 rounded px-2 py-1.5 text-sm">
                {(Object.keys(DOC_TYPE_LABELS) as DocType[]).map(k => (
                  <option key={k} value={k}>{DOC_TYPE_LABELS[k]}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-xs text-gray-500 block mb-1">Notes</label>
              <input value={notes} onChange={e => setNotes(e.target.value)}
                className="w-full border border-gray-200 rounded px-2 py-1.5 text-sm" placeholder="optional" />
            </div>
          </div>
          <div
            onDragOver={e => { e.preventDefault(); setDragOver(true) }}
            onDragLeave={() => setDragOver(false)}
            onDrop={e => { e.preventDefault(); setDragOver(false); handleFiles(e.dataTransfer.files) }}
            onClick={() => fileInputRef.current?.click()}
            className={`border-2 border-dashed rounded-xl py-8 flex flex-col items-center gap-2 cursor-pointer transition-colors ${
              dragOver ? 'border-purple-400 bg-purple-50' : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50'
            }`}
          >
            <span className="text-3xl">{uploadFileMutation.isPending ? '⌛' : '📁'}</span>
            <p className="text-sm text-gray-600 font-medium">
              {uploadFileMutation.isPending ? 'Uploading & transcribing…' : 'Drop file or click to browse'}
            </p>
            <p className="text-xs text-gray-400">JPG · PNG · PDF · up to 20 MB · transcription runs automatically</p>
          </div>
          <input ref={fileInputRef} type="file"
            accept="image/jpeg,image/png,image/webp,image/tiff,application/pdf"
            className="hidden" onChange={e => handleFiles(e.target.files)} />
          {uploadErr && <p className="text-xs text-red-500">{uploadErr}</p>}
        </div>
      )}

      {/* ── Transcribe tab ── */}
      {tab === 'transcribe' && (
        <div className="space-y-3">
          {/* Select a document to transcribe if none active */}
          {!activeDocId && docs.length > 0 && !transcribeMutation.isPending && (
            <div className="space-y-1">
              <p className="text-xs text-gray-500">Select a document to transcribe:</p>
              {docs.map(d => (
                <button key={d.id}
                  onClick={() => { setActiveDocId(d.id); transcribeMutation.mutate({ doc_id: d.id }) }}
                  className="w-full text-left px-3 py-2 border border-gray-100 rounded-lg text-sm hover:bg-gray-50 flex items-center gap-2">
                  🖼 <span>{d.filename}</span>
                </button>
              ))}
            </div>
          )}

          {!activeDocId && docs.length === 0 && !transcribeMutation.isPending && (
            <div className="text-center py-6 text-gray-400 text-sm">
              <p className="text-3xl mb-2">📷</p>
              Upload or capture a scan first, then transcribe
            </div>
          )}

          {transcribeMutation.isPending && (
            <div className="text-center py-8 space-y-2">
              <div className="text-3xl animate-pulse">🔍</div>
              <p className="text-sm text-gray-600">Running OCR…</p>
              <p className="text-xs text-gray-400">Extracting patient name, medications, prescriber…</p>
            </div>
          )}

          {transcribeMutation.isError && (
            <div className="text-center py-6 text-red-500 text-sm">
              ⚠ Transcription failed — check image quality
              <br />
              <button onClick={() => activeDocId && transcribeMutation.mutate({ doc_id: activeDocId })}
                className="mt-2 text-xs underline">Retry</button>
            </div>
          )}

          {transcription && !transcribeMutation.isPending && !confirmed && (
            <TranscriptionPanel
              result={transcription}
              onConfirm={data => confirmMutation.mutate(data)}
              isPending={confirmMutation.isPending}
            />
          )}

          {confirmed && (
            <div className="text-center py-6 space-y-2">
              <div className="text-4xl">✅</div>
              <p className="text-sm font-medium text-green-700">Transcription confirmed</p>
              <p className="text-xs text-gray-500">
                Patient record, prescriber registration, and relationship links updated.
              </p>
              <button onClick={() => { setConfirmed(false); setTranscription(null); setActiveDocId(null); setTab('docs') }}
                className="text-xs text-purple-600 hover:underline">
                Scan another document
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )

  if (inline) return <div>{inner}</div>

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-xl max-h-[94vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-3 border-b sticky top-0 bg-white z-10">
          <div className="flex items-center gap-2">
            <span className="text-lg">🗂</span>
            <span className="font-semibold text-gray-800">Rx Documents & Transcription</span>
            <span className="text-xs text-gray-400 font-mono">{rxNumber}</span>
          </div>
          {onClose && (
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl">×</button>
          )}
        </div>
        <div className="p-5">{inner}</div>
      </div>
    </div>
  )
}

// ─── Compact badge for toolbar ────────────────────────────────────────────────

export function RxDocumentsBadge({ rxId, onClick }: {
  rxId: string; rxNumber: string; onClick: () => void
}) {
  const { data: rawDocs } = useQuery({
    queryKey: ['rx-documents', rxId],
    queryFn:  () => apiClient.get('/rx-documents', { params: { rx_id: rxId } })
                    .then(r => r.data.documents as DocumentMeta[]),
    enabled:  !!rxId,
  })
  const count = (rawDocs ?? []).length
  return (
    <button onClick={onClick} title="View / scan Rx documents"
      className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs border border-gray-200 rounded-lg hover:bg-gray-50 text-gray-600">
      🗂 Docs
      {count > 0 && (
        <span className="bg-blue-500 text-white rounded-full px-1.5 py-0.5 text-[10px] font-semibold">{count}</span>
      )}
    </button>
  )
}
