/**
 * PharmPilot Patient App — API client
 * All PHI stored only in SecureStore (encrypted keychain).
 * No PHI ever reaches AsyncStorage, console.log, or crash reporters.
 */
import axios from 'axios'
import type { AxiosInstance } from 'axios'
import * as SecureStore from 'expo-secure-store'

const BASE_URL = process.env.EXPO_PUBLIC_API_URL || 'http://localhost:8001/api/v1'

export const api: AxiosInstance = axios.create({
  baseURL: BASE_URL,
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' },
})

// Inject token from SecureStore on every request
api.interceptors.request.use(async (config) => {
  const token = await SecureStore.getItemAsync('access_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// Auto-refresh on 401
api.interceptors.response.use(
  (res) => res,
  async (error) => {
    if (error.response?.status === 401) {
      const refresh = await SecureStore.getItemAsync('refresh_token')
      if (refresh) {
        try {
          const { data } = await axios.post(`${BASE_URL.replace('/api/v1', '')}/api/v1/auth/refresh`, {
            refresh_token: refresh,
          })
          await SecureStore.setItemAsync('access_token', data.access_token)
          await SecureStore.setItemAsync('refresh_token', data.refresh_token)
          error.config.headers.Authorization = `Bearer ${data.access_token}`
          return api.request(error.config)
        } catch {
          await SecureStore.deleteItemAsync('access_token')
          await SecureStore.deleteItemAsync('refresh_token')
        }
      }
    }
    return Promise.reject(error)
  }
)

// ── Patient endpoints ──────────────────────────────────────────────────────
export const patientApi = {
  me: () => api.get('/auth/me'),

  // Prescriptions
  getPrescriptions: () => api.get('/prescriptions?status=will_call,filled,ready_to_fill'),
  requestRefill: (rxId: string) => api.post(`/prescriptions/${rxId}/refill-request`),
  getRxHistory: (patientId: string) => api.get(`/prescriptions?patient_id=${patientId}`),

  // Notifications preferences
  updateNotificationToken: (token: string, platform: 'ios' | 'android') =>
    api.post('/patients/notification-token', { token, platform }),

  // Secure messages
  getMessages: (patientId: string) =>
    api.get(`/patients/${patientId}/messages`),
  sendMessage: (patientId: string, body: string) =>
    api.post(`/patients/${patientId}/messages`, { body }),

  // Clinical
  getAllergies: (patientId: string) => api.get(`/patients/${patientId}/allergies`),
  getLabs: (patientId: string) => api.get(`/patients/${patientId}/labs`),
  getMedications: (patientId: string) => api.get(`/patients/${patientId}/medications`),
}
