/**
 * PharmPilot API client — typed wrappers for all backend endpoints.
 */
import axios from 'axios'
import type { AxiosInstance, AxiosResponse } from 'axios'

const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8001/api/v1'

interface PharmPilotApiClient extends AxiosInstance {
  medReconcile: (payload: MedReconcilePayload) => Promise<AxiosResponse<MedReconcileResponse>>
}

export const apiClient = axios.create({
  baseURL: BASE_URL,
  headers: { 'Content-Type': 'application/json' },
}) as PharmPilotApiClient

apiClient.medReconcile = (payload: MedReconcilePayload) =>
  apiClient.post('/med-reconciliation/reconcile', payload)

// Inject auth token on every request
apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// Auto-refresh on 401
apiClient.interceptors.response.use(
  (res) => res,
  async (error) => {
    if (error.response?.status === 401) {
      const refreshToken = localStorage.getItem('refresh_token')
      if (refreshToken) {
        try {
          const { data } = await axios.post(`${BASE_URL}/auth/refresh`, {
            refresh_token: refreshToken,
          })
          localStorage.setItem('access_token', data.access_token)
          localStorage.setItem('refresh_token', data.refresh_token)
          error.config.headers.Authorization = `Bearer ${data.access_token}`
          return apiClient.request(error.config)
        } catch {
          localStorage.clear()
          window.location.href = '/login'
        }
      }
    }
    return Promise.reject(error)
  }
)

/**
 * Coerce any API error into a display string. FastAPI `detail` can be a string,
 * a request-validation array `[{type,loc,msg,input}]`, or an object (e.g. the
 * probe's `{message, diagnostics}`) — rendering any non-string as a React child
 * throws "Objects are not valid as a React child". Always run errors through this.
 */
export function apiErrorText(e: unknown, fallback = 'خطای ناشناخته'): string {
  const d = (e as any)?.response?.data?.detail
  if (d == null) return (e as any)?.message || fallback
  if (typeof d === 'string') return d
  if (Array.isArray(d)) {
    const msgs = d.map((x: any) => (x?.loc ? `${x.loc.slice(-1)[0]}: ` : '') + (x?.msg || JSON.stringify(x)))
    return msgs.join(' · ') || fallback
  }
  if (typeof d === 'object') {
    if (d.diagnostics?.summary) {
      const s = d.diagnostics.summary
      return `[${s.worst_category || '—'}] ${s.top_hint || d.message || fallback}`
    }
    return d.message || JSON.stringify(d)
  }
  return String(d) || fallback
}

// ── Auth ──────────────────────────────────────────────────────────────────
export const authApi = {
  login: (username: string, password: string, workstationId?: string) =>
    apiClient.post('/auth/login', { username, password, workstation_id: workstationId }),
  me: () => apiClient.get('/auth/me'),
  logout: () => apiClient.post('/auth/logout'),
}

// ── Patients ──────────────────────────────────────────────────────────────
export const patientApi = {
  search: (q: string) => apiClient.get('/patients', { params: { q } }),
  get: (id: string) => apiClient.get(`/patients/${id}`),
  create: (data: PatientCreateData) => apiClient.post('/patients', data),
  update: (id: string, data: Partial<PatientCreateData>) => apiClient.patch(`/patients/${id}`, data),
  getAllergies: (id: string) => apiClient.get(`/patients/${id}/allergies`),
  addAllergy: (id: string, data: AllergyData) => apiClient.post(`/patients/${id}/allergies`, data),
  getInsurance: (id: string) => apiClient.get(`/patients/${id}/insurance`),
  getLabs: (id: string) => apiClient.get(`/patients/${id}/labs`),
  getNotes: (id: string) => apiClient.get(`/patients/${id}/notes`),
}

// ── Prescriptions ─────────────────────────────────────────────────────────
export const rxApi = {
  queue: (status?: string) => apiClient.get('/prescriptions', { params: { status } }),
  // Patient-scoped Rx history — reuses GET /prescriptions?patient_id=… (see
  // get_queue in services/platform/routers/prescriptions.py), which already
  // returns drug_name/sig_text/status/fill_date/etc. per row. Powers the
  // PatientPanel's "active meds" and "recent fills" tabs without standing up
  // a parallel per-patient endpoint.
  byPatient: (patientId: string, limit = 20) =>
    apiClient.get('/prescriptions', { params: { patient_id: patientId, limit } }),
  get: (id: string) => apiClient.get(`/prescriptions/${id}`),
  intake: (data: RxIntakeData) => apiClient.post('/prescriptions', data),
  claim: (id: string) => apiClient.post(`/prescriptions/${id}/claim`),
  release: (id: string) => apiClient.post(`/prescriptions/${id}/release`),
  transition: (id: string, toStatus: string, reason?: string) =>
    apiClient.post(`/prescriptions/${id}/transition`, { to_status: toStatus, reason }),
  durAlerts: (id: string) => apiClient.get(`/prescriptions/${id}/dur-alerts`),
  overrideDur: (rxId: string, alertId: string, reason: string) =>
    apiClient.post(`/prescriptions/${rxId}/dur-override`, { alert_id: alertId, override_reason: reason }),
  history: (id: string) => apiClient.get(`/prescriptions/${id}/history`),
}

// ── Claims ────────────────────────────────────────────────────────────────
export const claimsApi = {
  submit: (data: ClaimSubmitData) => apiClient.post('/claims/submit', data),
  reverse: (claimId: string, reason: string) =>
    apiClient.post(`/claims/reverse/${claimId}`, null, { params: { reason } }),
  history: (pharmacyId: string) => apiClient.get(`/claims/history/${pharmacyId}`),
}

// ── Pricing & affordability (reception) ───────────────────────────────────────
export interface QuoteLine { irc?: string | null; drug_name?: string | null; quantity: number; removed?: boolean }
export interface QuotePayload {
  insurer: string; setting: string; technical_fee?: number; lines: QuoteLine[]
}
export const pricingApi = {
  quote: (payload: QuotePayload) => apiClient.post('/pricing/quote', payload),
  listProposals: (status = 'pending') => apiClient.get('/pricing/proposals', { params: { status } }),
  decideProposals: (ids: string[], approve: boolean) =>
    apiClient.post('/pricing/proposals/decide', { ids, approve }),
  runSync: () => apiClient.post('/pricing/sync/run', {}),
  importCatalog: (file: File) => {
    const fd = new FormData(); fd.append('file', file)
    // Content-Type: undefined lets axios set multipart/form-data + boundary,
    // overriding the instance's default application/json (else the file field
    // is dropped and FastAPI returns a 422 "file required").
    return apiClient.post('/pricing/catalog/import', fd, { headers: { 'Content-Type': undefined } })
  },
  catalogStats: () => apiClient.get('/pricing/catalog/stats'),
  importCoverage: (file: File, insurer: string) => {
    const fd = new FormData(); fd.append('file', file)
    return apiClient.post('/pricing/coverage/import', fd,
      { params: { insurer }, headers: { 'Content-Type': undefined } })
  },
  nfiStart: (body: { start_id: number; end_id: number; delay: number; proxy?: string }) =>
    apiClient.post('/pricing/catalog/nfi/start', body),
  nfiStatus: () => apiClient.get('/pricing/catalog/nfi/status'),
  nfiStop: () => apiClient.post('/pricing/catalog/nfi/stop', {}),
  // دارونامه coverage sources & staged runs
  coverageSources: () => apiClient.get('/pricing/coverage/sources'),
  coverageSourceSave: (id: string | null, body: Record<string, unknown>) =>
    id ? apiClient.put(`/pricing/coverage/sources/${id}`, body)
       : apiClient.post('/pricing/coverage/sources', body),
  coverageSourceDelete: (id: string) => apiClient.delete(`/pricing/coverage/sources/${id}`),
  coverageProbe: (id: string) => apiClient.post(`/pricing/coverage/sources/${id}/probe`, {}),
  coverageHarvest: (id: string) => apiClient.post(`/pricing/coverage/sources/${id}/harvest`, {}),
  coverageHarvestStatus: () => apiClient.get('/pricing/coverage/harvest/status'),
  coverageRuns: (sourceId?: string) =>
    apiClient.get('/pricing/coverage/runs', { params: sourceId ? { source_id: sourceId } : {} }),
  coverageRun: (id: string) => apiClient.get(`/pricing/coverage/runs/${id}`),
  coverageApprove: (id: string, body: { remove_missing: boolean; accepted_review_ids: number[] }) =>
    apiClient.post(`/pricing/coverage/runs/${id}/approve`, body),
  coverageReject: (id: string) => apiClient.post(`/pricing/coverage/runs/${id}/reject`, {}),
  inconsistencies: (insurer: string, threshold: number) =>
    apiClient.get('/pricing/inconsistencies', { params: { insurer, price_threshold_pct: threshold } }),
  inconsistencyDrug: (irc: string, insurer: string) =>
    apiClient.get(`/pricing/inconsistencies/drug/${irc}`, { params: { insurer } }),
  // هوش‌یار دارو — drug enrichment intelligence
  enrichWorklist: (insurer?: string) =>
    apiClient.get('/pricing/enrichment/worklist', { params: insurer ? { insurer } : {} }),
  enrichRun: (body: { limit: number; min_confidence: number }) =>
    apiClient.post('/pricing/enrichment/run', body),
  enrichRunStatus: () => apiClient.get('/pricing/enrichment/run/status'),
  enrichSuggestions: (status = 'suggested') =>
    apiClient.get('/pricing/enrichment/suggestions', { params: { status } }),
  enrichDecide: (ids: string[], approve: boolean) =>
    apiClient.post('/pricing/enrichment/decide', { ids, approve }),
  enrichExport: () => apiClient.post('/pricing/enrichment/export', {}),
  enrichImport: () => apiClient.post('/pricing/enrichment/import', {}),
}

// ── Inventory ─────────────────────────────────────────────────────────────
export const inventoryApi = {
  searchDrugs: (q: string) => apiClient.get('/inventory/drugs/search', { params: { q } }),
  getDrug: (ndc: string) => apiClient.get(`/inventory/drugs/${ndc}`),
  getStock: () => apiClient.get('/inventory/stock'),
  getStockForNdc: (ndc: string) => apiClient.get(`/inventory/stock/${ndc}`),
  getExpiring: (days?: number) => apiClient.get('/inventory/expiring', { params: { days } }),
  createOrder: (data: PurchaseOrderData) => apiClient.post('/inventory/orders', data),
}

// ── Depot → shelf dual-verification replenishment ─────────────────────────
export const depotApi = {
  listShelves: () => apiClient.get('/inventory/shelves'),
  createSession: (data: { shelf_id: string; ndc11s: string[] }) =>
    apiClient.post('/inventory/replenishment/session', data),
  getSession: (sid: string) => apiClient.get(`/inventory/replenishment/${sid}`),
  depotCollect: (sid: string, data: Record<string, unknown>) =>
    apiClient.post(`/inventory/replenishment/${sid}/depot-collect`, data),
  shelfVerify: (data: Record<string, unknown>) =>
    apiClient.post('/inventory/ai/shelf-verify', data),
  shelfPlace: (sid: string, data: Record<string, unknown>) =>
    apiClient.post(`/inventory/replenishment/${sid}/shelf-place`, data),
  listSurveillance: () => apiClient.get('/inventory/surveillance/events'),
  createSurveillance: (data: Record<string, unknown>) =>
    apiClient.post('/inventory/surveillance/events', data),
}

// ── Clinical ──────────────────────────────────────────────────────────────
export const clinicalApi = {
  reviewRx: (prescriptionId: string, patientId: string, pharmacyId: string) =>
    apiClient.post('/clinical/rx-review', { prescription_id: prescriptionId, patient_id: patientId, pharmacy_id: pharmacyId }),
  evaluateCDS: (patientId: string, medications?: CDSMedicationInput[]) =>
    apiClient.post('/cds/evaluate', { patient_id: patientId, ...(medications ? { medications } : {}) }),
  assessADR: (patientId: string, complaint: string, onsetDate?: string, medications?: ADRMedicationInput[]) =>
    apiClient.post('/adr/assess', {
      patient_id: patientId,
      complaint,
      ...(onsetDate ? { onset_date: onsetDate } : {}),
      ...(medications ? { medications } : {}),
    }),
  generateCounselling: (params: CounsellingGenerateParams) =>
    apiClient.post('/counselling/generate', params),
  generatePhysicianMessage: (params: PhysicianMessageGenerateParams) =>
    apiClient.post('/physician-message/generate', params),
  reviewPolypharmacy: (patientId: string, medications?: PolyMedicationInput[], messageFormat: 'sbar' | 'concise' = 'sbar') =>
    apiClient.post('/polypharmacy/review', {
      patient_id: patientId,
      message_format: messageFormat,
      ...(medications ? { medications } : {}),
    }),
  interpretPGx: (patientId: string, drugs?: string[], genotypes?: PGxGenotypeInput[]) =>
    apiClient.post('/pgx/interpret', {
      patient_id: patientId,
      ...(drugs?.length ? { drugs } : {}),
      ...(genotypes?.length ? { genotypes } : {}),
    }),
  labSafetyAssess: (patientId: string) =>
    apiClient.post('/lab-safety/assess', { patient_id: patientId }),
  medReconcile: (payload: MedReconcilePayload) =>
    apiClient.medReconcile(payload),
  // Routes to Second Brain RAG: same clinical corpus, but degrades gracefully
  // (returns retrieved passages) when no LLM key — unlike /knowledge/query (503).
  queryKnowledge: (question: string, patientId?: string, aiAssist = true) =>
    apiClient.post('/second-brain/query', { question, patient_id: patientId, top_k: 8, ai_assist: aiAssist }),
  getInteractionReport: (patientId: string) =>
    apiClient.get(`/cds/interaction-report/${patientId}`),
  acknowledgeInteractions: (body: {
    patient_id: string; rx_id?: string; findings_hash: string;
    acknowledged: { rule_id: string; severity: string }[];
  }) => apiClient.post('/cds/interaction-ack', body),
  generatePhysicianLetter: (body: {
    patient_id: string; rx_id?: string; language: string;
    physician_name: string; council_id?: string;
    findings: { rule_id?: string; participants: { name: string }[]; mechanism: string; severity: string }[];
  }) => apiClient.post('/cds/physician-letter', body),
  getPhysicianLetter: (id: string) => apiClient.get(`/cds/physician-letter/${id}`),
  revisePhysicianLetter: (id: string, letter_text: string) =>
    apiClient.post(`/cds/physician-letter/${id}/revise`, { letter_text }),
  getInteractionAudit: (params: {
    patient_name?: string; council_id?: string; from?: string; to?: string;
    type?: 'ack' | 'letter'; limit?: number; offset?: number;
  }) => apiClient.get('/cds/interaction-audit', { params }),
  getBundleStatus: () => apiClient.get('/cds/interaction-bundle/status'),
  installBundle: (file: File, confirmReplace = false) => {
    const form = new FormData()
    form.append('file', file)
    form.append('confirm_replace', String(confirmReplace))
    return apiClient.post('/cds/interaction-bundle/install', form,
      { headers: { 'Content-Type': 'multipart/form-data' } })
  },
  querySecondBrain: (question: string, patientId?: string, topK = 8) =>
    apiClient.post('/second-brain/query', {
      question,
      ...(patientId ? { patient_id: patientId } : {}),
      top_k: topK,
    }),
  getDrugMonograph: (params: DrugMonographParams) =>
    apiClient.post('/drug-intelligence/monograph', params),
  queryDrugInteraction: (drugA: string, drugB: string) =>
    apiClient.post('/knowledge/query/drug-interaction', null, { params: { drug_a: drugA, drug_b: drugB } }),
}

// ── Audio / Dictation ─────────────────────────────────────────────────────
export const audioApi = {
  dictate: (blob: Blob, context = 'note', language = 'fa') => {
    const form = new FormData()
    form.append('audio_file', blob, 'dictation.webm')
    form.append('context', context)
    form.append('language', language)
    return apiClient.post('/audio/dictate', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 30_000,
    })
  },
}

// ── Biometric ─────────────────────────────────────────────────────────────
export const biometricApi = {
  identify: (formData: FormData) =>
    apiClient.post('/biometric/identify', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),
}

// ── Types ──────────────────────────────────────────────────────────────────
export interface PatientCreateData {
  first_name: string; last_name: string; date_of_birth: string; gender: string
  phone_primary?: string; email?: string; address_line1?: string; city?: string; state?: string; zip_code?: string
}
export interface AllergyData {
  allergen_name: string; allergen_type?: string; reaction?: string; severity?: string
}
export interface RxIntakeData {
  patient_id: string; prescriber_id: string; ndc: string; drug_name: string
  drug_strength?: string
  sig_text: string; quantity_prescribed: number; days_supply: number
  refills_authorized?: number; written_date: string; source?: string; dea_schedule?: string
}
export interface ClaimSubmitData {
  fill_id: string; insurance_id: string; ingredient_cost: number; dispensing_fee?: number
}
export interface PurchaseOrderData {
  wholesaler: string; lines: Array<{ ndc11: string; quantity_ordered: number }>
}
export interface CDSMedicationInput {
  drug_name: string
  strength?: string
  dose?: string
  route?: string
  frequency?: string
  source?: string
}
export interface ADRMedicationInput extends CDSMedicationInput {
  start_date?: string
  stop_date?: string
  recent_dose_increase?: boolean
}
export interface CounsellingGenerateParams {
  drug_name?: string
  rx_id?: string
  patient_id?: string
  level?: 'professional' | 'standard' | 'low_literacy' | 'elderly' | 'caregiver'
  language?: 'en' | 'fr' | 'fa' | 'ar' | 'es'
}
export interface PhysicianMessageGenerateParams {
  patient_id?: string
  prescriber_name?: string
  patient_context?: string
  medication_issue: string
  clinical_rationale?: string
  recommendation_or_question: string
  urgency?: 'routine' | 'urgent' | 'emergent'
  supporting_data?: string[]
  pharmacist_name?: string
  format?: 'sbar' | 'soap' | 'concise' | 'letter'
  language?: 'en' | 'fr' | 'fa' | 'ar' | 'es'
}
export interface PolyMedicationInput {
  drug_name: string
  indication?: string
  status?: string
  source?: string
}
export interface MedReconcileMedicationInput {
  drug_name: string
  normalized_name?: string
  strength?: string
  dose?: string
  route?: string
  frequency?: string
  indication?: string
  status?: string
  source?: string
  patient_id?: string
  id?: string
}
export interface MedReconcilePayload {
  patient_id: string
  source_a_label?: string
  source_b_label?: string
  source_a?: string
  source_b?: string
  source_a_meds?: MedReconcileMedicationInput[]
  source_b_meds?: MedReconcileMedicationInput[]
}
export interface MedReconcileDiscrepancy {
  discrepancy_id: string
  discrepancy_type: string
  severity: 'high' | 'moderate' | 'low'
  source_a_label: string
  source_b_label: string
  drug_name: string
  drugs_involved: string[]
  source_a_entry: MedReconcileMedicationInput | null
  source_b_entry: MedReconcileMedicationInput | null
  explanation: string
  suggested_pharmacist_action: string
  confidence: number
  pharmacist_verification_notice: string
}
export interface MedReconcileResponse {
  patient_id: string
  source_a_label: string
  source_b_label: string
  discrepancies: MedReconcileDiscrepancy[]
  drugs_in_source_a: number
  drugs_in_source_b: number
  reconciled_count: number
  assessment_date: string
  pharmacist_verification_notice: string
}
export interface SecondBrainSource {
  source_id: string
  source_title: string
  source_type: string
  snippet: string
  similarity_score: number
  evidence_grade?: string | null
  url?: string | null
  full_text?: string
}
export interface SecondBrainResponse {
  question: string
  answer: string
  sources: SecondBrainSource[]
  patient_context?: string | null
  confidence: 'high' | 'moderate' | 'low' | 'none'
  refused: boolean
  unsupported: boolean
  llm_used: boolean
  degraded: boolean
  pharmacist_verification_notice: string
}
export interface DrugMonographParams {
  drug_name?: string
  rx_id?: string
  sections?: string[]
  top_k?: number
}
export interface DrugMonographSection {
  key: string
  label: string
  answer: string
  sources: SecondBrainSource[]
  confidence: 'high' | 'moderate' | 'low' | 'none'
  refused: boolean
  unsupported: boolean
  llm_used: boolean
}
export interface DrugMonographResponse {
  drug_name: string
  normalized_name: string
  model_version: string
  sections: DrugMonographSection[]
  any_evidence: boolean
  llm_used: boolean
  degraded: boolean
  pharmacist_verification_notice: string
  trainable_note: string
}
export interface PGxGenotypeInput {
  gene: string
  diplotype?: string
  phenotype?: string
  source?: string
}
