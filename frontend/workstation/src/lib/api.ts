/**
 * PharmPilot API client — typed wrappers for all backend endpoints.
 */
import axios from 'axios'
import type { AxiosInstance } from 'axios'

const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8001/api/v1'

export const apiClient: AxiosInstance = axios.create({
  baseURL: BASE_URL,
  headers: { 'Content-Type': 'application/json' },
})

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

// ── Inventory ─────────────────────────────────────────────────────────────
export const inventoryApi = {
  searchDrugs: (q: string) => apiClient.get('/inventory/drugs/search', { params: { q } }),
  getDrug: (ndc: string) => apiClient.get(`/inventory/drugs/${ndc}`),
  getStock: () => apiClient.get('/inventory/stock'),
  getStockForNdc: (ndc: string) => apiClient.get(`/inventory/stock/${ndc}`),
  getExpiring: (days?: number) => apiClient.get('/inventory/expiring', { params: { days } }),
  createOrder: (data: PurchaseOrderData) => apiClient.post('/inventory/orders', data),
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
  queryKnowledge: (question: string, patientId?: string) =>
    apiClient.post('/knowledge/query', { question, patient_id: patientId }),
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
export interface SecondBrainSource {
  source_id: string
  source_title: string
  source_type: string
  snippet: string
  similarity_score: number
  evidence_grade?: string | null
  url?: string | null
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
