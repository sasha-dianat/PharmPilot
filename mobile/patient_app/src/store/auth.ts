/**
 * Auth store — manages patient login state.
 * Tokens stored only in SecureStore (iOS Keychain / Android Keystore).
 * Never in AsyncStorage — PHI security requirement.
 */
import { create } from 'zustand'
import * as SecureStore from 'expo-secure-store'
import * as LocalAuthentication from 'expo-local-authentication'
import { api } from '../api/client'

interface AuthState {
  isAuthenticated: boolean
  patientId: string | null
  patientName: string | null
  pharmacyId: string | null
  biometricAvailable: boolean
  loading: boolean

  checkAuth: () => Promise<void>
  login: (username: string, password: string) => Promise<void>
  loginWithBiometric: () => Promise<void>
  logout: () => Promise<void>
  checkBiometricAvailability: () => Promise<void>
}

export const useAuthStore = create<AuthState>((set, get) => ({
  isAuthenticated: false,
  patientId: null,
  patientName: null,
  pharmacyId: null,
  biometricAvailable: false,
  loading: true,

  checkAuth: async () => {
    try {
      const token = await SecureStore.getItemAsync('access_token')
      if (!token) { set({ loading: false }); return }
      const { data } = await api.get('/auth/me')
      set({
        isAuthenticated: true,
        patientId: data.patient_id || data.staff_id,
        patientName: `${data.first_name} ${data.last_name}`,
        pharmacyId: data.pharmacy_id,
        loading: false,
      })
    } catch {
      await SecureStore.deleteItemAsync('access_token')
      await SecureStore.deleteItemAsync('refresh_token')
      set({ isAuthenticated: false, loading: false })
    }
  },

  login: async (username: string, password: string) => {
    const { data } = await api.post('/auth/login', { username, password })
    // Store tokens in Keychain — NEVER AsyncStorage
    await SecureStore.setItemAsync('access_token', data.access_token)
    await SecureStore.setItemAsync('refresh_token', data.refresh_token)
    // Store credentials for biometric re-auth (encrypted)
    await SecureStore.setItemAsync('saved_username', username)
    await SecureStore.setItemAsync('saved_password', password)
    set({
      isAuthenticated: true,
      patientId: data.staff_id,
      pharmacyId: data.pharmacy_id,
    })
  },

  loginWithBiometric: async () => {
    const result = await LocalAuthentication.authenticateAsync({
      promptMessage: 'Authenticate to access PharmPilot',
      cancelLabel: 'Use Password',
      fallbackLabel: 'Enter Password',
      disableDeviceFallback: false,
    })
    if (!result.success) throw new Error('Biometric authentication failed')

    // Use stored credentials after successful biometric
    const username = await SecureStore.getItemAsync('saved_username')
    const password = await SecureStore.getItemAsync('saved_password')
    if (!username || !password) throw new Error('No saved credentials — please log in with password first')
    await get().login(username, password)
  },

  logout: async () => {
    try { await api.post('/auth/logout') } catch { /* ignore */ }
    await SecureStore.deleteItemAsync('access_token')
    await SecureStore.deleteItemAsync('refresh_token')
    // Keep saved credentials for biometric re-login
    set({ isAuthenticated: false, patientId: null, patientName: null })
  },

  checkBiometricAvailability: async () => {
    const compatible = await LocalAuthentication.hasHardwareAsync()
    const enrolled  = await LocalAuthentication.isEnrolledAsync()
    const hasSaved  = !!(await SecureStore.getItemAsync('saved_username'))
    set({ biometricAvailable: compatible && enrolled && hasSaved })
  },
}))
