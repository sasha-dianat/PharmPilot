/**
 * Patient Login Screen
 * Password login + Face ID / Touch ID after first login.
 * HIPAA: no PHI on screen before auth, no screenshots permitted in production.
 */
import { useState, useEffect } from 'react'
import {
  View, Text, TextInput, TouchableOpacity,
  StyleSheet, Alert, KeyboardAvoidingView, Platform,
  ActivityIndicator, ScrollView,
} from 'react-native'
import { router } from 'expo-router'
import * as LocalAuthentication from 'expo-local-authentication'
import { useAuthStore } from '../../src/store/auth'

export default function LoginScreen() {
  const { login, loginWithBiometric, checkBiometricAvailability, biometricAvailable } = useAuthStore()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading]   = useState(false)
  const [bioType, setBioType]   = useState<'face' | 'fingerprint' | null>(null)

  useEffect(() => {
    checkBiometricAvailability()
    LocalAuthentication.supportedAuthenticationTypesAsync().then(types => {
      if (types.includes(LocalAuthentication.AuthenticationType.FACIAL_RECOGNITION)) {
        setBioType('face')
      } else if (types.includes(LocalAuthentication.AuthenticationType.FINGERPRINT)) {
        setBioType('fingerprint')
      }
    })
  }, [])

  const handleLogin = async () => {
    if (!username.trim() || !password.trim()) return
    setLoading(true)
    try {
      await login(username.trim(), password)
      router.replace('/(tabs)/prescriptions')
    } catch (err: unknown) {
      const e = err as { response?: { status: number } }
      if (e.response?.status === 401) {
        Alert.alert('Sign In Failed', 'Incorrect username or password.')
      } else if (e.response?.status === 423) {
        Alert.alert('Account Locked', 'Contact your pharmacy.')
      } else {
        Alert.alert('Connection Error', 'Cannot reach PharmPilot server.')
      }
    } finally {
      setLoading(false)
    }
  }

  const handleBiometric = async () => {
    try {
      await loginWithBiometric()
      router.replace('/(tabs)/prescriptions')
    } catch (err: unknown) {
      const e = err as { message?: string }
      Alert.alert('Authentication Failed', e.message || 'Please use your password.')
    }
  }

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
    >
      <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
        {/* Logo */}
        <View style={styles.logoSection}>
          <Text style={styles.emoji}>💊</Text>
          <Text style={styles.appName}>PharmPilot</Text>
          <Text style={styles.tagline}>Your Pharmacy, Smarter</Text>
        </View>

        {/* Form */}
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Sign In</Text>

          <TextInput
            style={styles.input}
            placeholder="Username or Patient ID"
            placeholderTextColor="#9ca3af"
            value={username}
            onChangeText={setUsername}
            autoCapitalize="none"
            autoCorrect={false}
            returnKeyType="next"
            textContentType="username"
          />
          <TextInput
            style={styles.input}
            placeholder="Password"
            placeholderTextColor="#9ca3af"
            value={password}
            onChangeText={setPassword}
            secureTextEntry
            returnKeyType="done"
            onSubmitEditing={handleLogin}
            textContentType="password"
          />

          <TouchableOpacity
            style={[styles.btn, styles.btnPrimary, (!username || !password || loading) && styles.btnDisabled]}
            onPress={handleLogin}
            disabled={!username || !password || loading}
          >
            {loading
              ? <ActivityIndicator color="#fff" />
              : <Text style={styles.btnText}>Sign In</Text>
            }
          </TouchableOpacity>

          {/* Biometric button */}
          {biometricAvailable && bioType && (
            <TouchableOpacity style={[styles.btn, styles.btnBio]} onPress={handleBiometric}>
              <Text style={styles.btnBioText}>
                {bioType === 'face' ? '🔐 Sign in with Face ID' : '👆 Sign in with Touch ID'}
              </Text>
            </TouchableOpacity>
          )}
        </View>

        <Text style={styles.footer}>
          Need help? Contact your pharmacy directly.
        </Text>
      </ScrollView>
    </KeyboardAvoidingView>
  )
}

const styles = StyleSheet.create({
  container:   { flex: 1, backgroundColor: '#0f172a' },
  scroll:      { flexGrow: 1, justifyContent: 'center', padding: 24 },
  logoSection: { alignItems: 'center', marginBottom: 40 },
  emoji:       { fontSize: 56, marginBottom: 8 },
  appName:     { fontSize: 32, fontWeight: '700', color: '#f8fafc', letterSpacing: -0.5 },
  tagline:     { fontSize: 14, color: '#94a3b8', marginTop: 4 },
  card:        { backgroundColor: '#1e293b', borderRadius: 20, padding: 24, gap: 12 },
  cardTitle:   { fontSize: 20, fontWeight: '600', color: '#f8fafc', marginBottom: 4 },
  input: {
    backgroundColor: '#0f172a',
    borderWidth: 1,
    borderColor: '#334155',
    borderRadius: 12,
    paddingHorizontal: 16,
    paddingVertical: 14,
    fontSize: 16,
    color: '#f8fafc',
  },
  btn: {
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: 'center',
    justifyContent: 'center',
  },
  btnPrimary:  { backgroundColor: '#3b82f6', marginTop: 4 },
  btnDisabled: { opacity: 0.5 },
  btnBio:      { backgroundColor: '#1e3a5f', borderWidth: 1, borderColor: '#3b82f6' },
  btnText:     { color: '#fff', fontSize: 16, fontWeight: '600' },
  btnBioText:  { color: '#93c5fd', fontSize: 15, fontWeight: '500' },
  footer:      { textAlign: 'center', color: '#475569', fontSize: 12, marginTop: 32 },
})
