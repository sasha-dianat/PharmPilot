/**
 * Profile Tab — patient info, allergies, notification settings, logout.
 */
import {
  View, Text, ScrollView, TouchableOpacity,
  StyleSheet, Alert, Switch,
} from 'react-native'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { router } from 'expo-router'
import { useAuthStore } from '../../src/store/auth'
import { patientApi } from '../../src/api/client'

export default function ProfileScreen() {
  const { patientId, patientName, logout } = useAuthStore()
  const [refillReminders, setRefillReminders] = useState(true)
  const [pickupAlerts, setPickupAlerts]       = useState(true)

  const { data: allergies } = useQuery({
    queryKey: ['allergies', patientId],
    queryFn:  () => patientApi.getAllergies(patientId!).then(r => r.data),
    enabled:  !!patientId,
  })

  const handleLogout = () => {
    Alert.alert('Sign Out', 'Are you sure?', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Sign Out', style: 'destructive', onPress: async () => {
        await logout()
        router.replace('/(auth)/login')
      }},
    ])
  }

  return (
    <ScrollView style={styles.scroll} contentContainerStyle={styles.content}>
      {/* Patient card */}
      <View style={styles.patientCard}>
        <View style={styles.avatar}>
          <Text style={styles.avatarText}>
            {(patientName || 'P').charAt(0).toUpperCase()}
          </Text>
        </View>
        <Text style={styles.patientName}>{patientName || 'Patient'}</Text>
        <Text style={styles.patientId}>ID: {patientId?.slice(0, 8)}…</Text>
      </View>

      {/* Allergies */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Allergies on File</Text>
        {(allergies as { allergen_name: string; severity: string }[] || []).length === 0 ? (
          <Text style={styles.noData}>No allergies documented</Text>
        ) : (
          (allergies as { allergen_name: string; severity: string }[]).map((a, i) => (
            <View key={i} style={styles.allergyRow}>
              <Text style={styles.allergyDot}>⚠</Text>
              <Text style={styles.allergyName}>{a.allergen_name}</Text>
              <Text style={styles.allergySeverity}>{a.severity}</Text>
            </View>
          ))
        )}
      </View>

      {/* Notification preferences */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Notifications</Text>
        <View style={styles.prefRow}>
          <View>
            <Text style={styles.prefLabel}>Refill Reminders</Text>
            <Text style={styles.prefSub}>7 days before you run out</Text>
          </View>
          <Switch
            value={refillReminders}
            onValueChange={setRefillReminders}
            trackColor={{ true: '#3b82f6' }}
          />
        </View>
        <View style={styles.prefRow}>
          <View>
            <Text style={styles.prefLabel}>Pickup Alerts</Text>
            <Text style={styles.prefSub}>When your Rx is ready</Text>
          </View>
          <Switch
            value={pickupAlerts}
            onValueChange={setPickupAlerts}
            trackColor={{ true: '#3b82f6' }}
          />
        </View>
      </View>

      {/* Actions */}
      <View style={styles.section}>
        <TouchableOpacity style={styles.dangerBtn} onPress={handleLogout}>
          <Text style={styles.dangerBtnText}>Sign Out</Text>
        </TouchableOpacity>
      </View>

      <Text style={styles.version}>PharmPilot v1.0 · All data encrypted</Text>
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  scroll:       { flex: 1, backgroundColor: '#0f172a' },
  content:      { padding: 20, gap: 20, paddingBottom: 40 },
  patientCard:  { alignItems: 'center', gap: 8, paddingVertical: 8 },
  avatar: {
    width: 72, height: 72, borderRadius: 36,
    backgroundColor: '#1d4ed8', justifyContent: 'center', alignItems: 'center',
  },
  avatarText:   { fontSize: 28, fontWeight: '700', color: '#fff' },
  patientName:  { fontSize: 22, fontWeight: '700', color: '#f8fafc' },
  patientId:    { fontSize: 13, color: '#64748b' },
  section: {
    backgroundColor: '#1e293b', borderRadius: 16, padding: 16, gap: 12,
  },
  sectionTitle: { fontSize: 16, fontWeight: '600', color: '#94a3b8', marginBottom: 4 },
  noData:       { color: '#475569', fontSize: 14 },
  allergyRow:   { flexDirection: 'row', alignItems: 'center', gap: 8 },
  allergyDot:   { fontSize: 14, color: '#f87171' },
  allergyName:  { flex: 1, fontSize: 15, color: '#f8fafc' },
  allergySeverity:{ fontSize: 12, color: '#94a3b8' },
  prefRow: {
    flexDirection: 'row', justifyContent: 'space-between',
    alignItems: 'center', paddingVertical: 4,
  },
  prefLabel:    { fontSize: 15, color: '#f8fafc', fontWeight: '500' },
  prefSub:      { fontSize: 12, color: '#64748b', marginTop: 1 },
  dangerBtn: {
    backgroundColor: '#450a0a', borderRadius: 12,
    paddingVertical: 14, alignItems: 'center',
  },
  dangerBtnText: { color: '#fca5a5', fontWeight: '600', fontSize: 16 },
  version: { textAlign: 'center', color: '#334155', fontSize: 11 },
})
