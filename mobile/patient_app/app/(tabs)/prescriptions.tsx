/**
 * Prescriptions Tab — patient's active Rx list with refill requests.
 * Shows: status badge, drug name, copay, pickup date, refill button.
 * Real-time updates via polling (WebSocket in future Phase).
 */
import { useState, useCallback } from 'react'
import {
  View, Text, FlatList, TouchableOpacity,
  StyleSheet, RefreshControl, Alert, ActivityIndicator,
} from 'react-native'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { patientApi } from '../../src/api/client'

interface Prescription {
  id: string
  rx_number: string
  drug_name: string
  drug_strength?: string
  sig_text: string
  days_supply: number
  refills_remaining: number
  status: string
  fill_date?: string
  is_controlled: boolean
  dea_schedule?: string
}

const STATUS_CONFIG: Record<string, { label: string; color: string; bg: string }> = {
  will_call:           { label: 'Ready for Pickup', color: '#166534', bg: '#dcfce7' },
  filled:              { label: 'Ready for Pickup', color: '#166534', bg: '#dcfce7' },
  ready_to_fill:       { label: 'Being Prepared',   color: '#1d4ed8', bg: '#dbeafe' },
  filling:             { label: 'Being Prepared',   color: '#1d4ed8', bg: '#dbeafe' },
  pending_adjudication:{ label: 'Processing',       color: '#92400e', bg: '#fef3c7' },
  adjudication_rejected:{ label: 'Insurance Issue', color: '#991b1b', bg: '#fee2e2' },
  pending_pa:          { label: 'Prior Auth Needed', color: '#991b1b', bg: '#fee2e2' },
  on_hold:             { label: 'On Hold',          color: '#374151', bg: '#f3f4f6' },
  dispensed:           { label: 'Picked Up',        color: '#6b7280', bg: '#f9fafb' },
}

function RxCard({ rx, onRefillRequest }: { rx: Prescription; onRefillRequest: (id: string) => void }) {
  const status = STATUS_CONFIG[rx.status] || { label: rx.status, color: '#6b7280', bg: '#f9fafb' }
  const canRefill = rx.refills_remaining > 0 &&
    ['dispensed', 'will_call', 'filled'].includes(rx.status)
  const needsAttention = ['adjudication_rejected', 'pending_pa'].includes(rx.status)

  return (
    <View style={[styles.card, needsAttention && styles.cardAlert]}>
      {/* Header */}
      <View style={styles.cardHeader}>
        <View style={styles.drugNameRow}>
          <Text style={styles.drugName}>{rx.drug_name}</Text>
          {rx.drug_strength && (
            <Text style={styles.strength}> {rx.drug_strength}</Text>
          )}
          {rx.is_controlled && (
            <View style={styles.controlledBadge}>
              <Text style={styles.controlledText}>{rx.dea_schedule}</Text>
            </View>
          )}
        </View>
        <View style={[styles.statusBadge, { backgroundColor: status.bg }]}>
          <Text style={[styles.statusText, { color: status.color }]}>{status.label}</Text>
        </View>
      </View>

      {/* Details */}
      <Text style={styles.sig} numberOfLines={2}>{rx.sig_text}</Text>
      <View style={styles.detailRow}>
        <Text style={styles.detail}>#{rx.rx_number}</Text>
        <Text style={styles.detail}>{rx.days_supply}-day supply</Text>
        <Text style={styles.detail}>
          {rx.refills_remaining > 0 ? `${rx.refills_remaining} refill${rx.refills_remaining > 1 ? 's' : ''} left` : 'No refills'}
        </Text>
      </View>

      {/* Action */}
      {canRefill && (
        <TouchableOpacity
          style={styles.refillBtn}
          onPress={() => onRefillRequest(rx.id)}
        >
          <Text style={styles.refillBtnText}>Request Refill</Text>
        </TouchableOpacity>
      )}
      {needsAttention && (
        <View style={styles.alertBanner}>
          <Text style={styles.alertText}>⚠ Contact your pharmacy for assistance</Text>
        </View>
      )}
    </View>
  )
}

export default function PrescriptionsScreen() {
  const queryClient = useQueryClient()
  const [refreshing, setRefreshing] = useState(false)

  const { data: rxList, isLoading, error } = useQuery({
    queryKey: ['prescriptions'],
    queryFn: () => patientApi.getPrescriptions().then(r => r.data as Prescription[]),
    refetchInterval: 30_000, // Poll every 30 seconds
  })

  const refillMutation = useMutation({
    mutationFn: (rxId: string) => patientApi.requestRefill(rxId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['prescriptions'] })
      Alert.alert('✓ Refill Requested', 'Your pharmacy has been notified. You\'ll receive a notification when it\'s ready.')
    },
    onError: () => Alert.alert('Error', 'Could not submit refill request. Please try again.'),
  })

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await queryClient.invalidateQueries({ queryKey: ['prescriptions'] })
    setRefreshing(false)
  }, [queryClient])

  const handleRefillRequest = (rxId: string) => {
    Alert.alert(
      'Request Refill?',
      'Your pharmacy will receive this refill request and prepare your medication.',
      [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Request', onPress: () => refillMutation.mutate(rxId) },
      ]
    )
  }

  if (isLoading) return (
    <View style={styles.center}>
      <ActivityIndicator size="large" color="#3b82f6" />
      <Text style={styles.loadingText}>Loading your prescriptions…</Text>
    </View>
  )

  if (error) return (
    <View style={styles.center}>
      <Text style={styles.errorText}>⚠ Could not load prescriptions</Text>
      <Text style={styles.errorSub}>Check your connection and try again</Text>
    </View>
  )

  const active = (rxList || []).filter(rx => rx.status !== 'dispensed')
  const history = (rxList || []).filter(rx => rx.status === 'dispensed')

  return (
    <FlatList
      style={styles.list}
      data={active}
      keyExtractor={item => item.id}
      renderItem={({ item }) => (
        <RxCard rx={item} onRefillRequest={handleRefillRequest} />
      )}
      ListHeaderComponent={() => (
        <View style={styles.header}>
          <Text style={styles.headerTitle}>My Prescriptions</Text>
          <Text style={styles.headerSub}>{active.length} active</Text>
        </View>
      )}
      ListEmptyComponent={() => (
        <View style={styles.empty}>
          <Text style={styles.emptyIcon}>💊</Text>
          <Text style={styles.emptyText}>No active prescriptions</Text>
          <Text style={styles.emptySub}>Your prescriptions will appear here</Text>
        </View>
      )}
      ListFooterComponent={() =>
        history.length > 0 ? (
          <View style={styles.historySection}>
            <Text style={styles.historyLabel}>Recently Dispensed</Text>
            {history.slice(0, 5).map(rx => (
              <RxCard key={rx.id} rx={rx} onRefillRequest={handleRefillRequest} />
            ))}
          </View>
        ) : null
      }
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#3b82f6" />
      }
      contentContainerStyle={styles.listContent}
    />
  )
}

const styles = StyleSheet.create({
  list:           { flex: 1, backgroundColor: '#0f172a' },
  listContent:    { padding: 16, paddingBottom: 32 },
  header:         { marginBottom: 16 },
  headerTitle:    { fontSize: 26, fontWeight: '700', color: '#f8fafc' },
  headerSub:      { fontSize: 14, color: '#64748b', marginTop: 2 },
  card: {
    backgroundColor: '#1e293b',
    borderRadius: 16,
    padding: 16,
    marginBottom: 12,
    gap: 8,
  },
  cardAlert:    { borderWidth: 1, borderColor: '#ef4444' },
  cardHeader:   { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 },
  drugNameRow:  { flex: 1, flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center' },
  drugName:     { fontSize: 17, fontWeight: '600', color: '#f8fafc' },
  strength:     { fontSize: 15, color: '#94a3b8' },
  controlledBadge: { backgroundColor: '#451a03', borderRadius: 4, paddingHorizontal: 6, paddingVertical: 2, marginLeft: 6 },
  controlledText:  { fontSize: 10, color: '#fdba74', fontWeight: '600' },
  statusBadge:  { borderRadius: 8, paddingHorizontal: 10, paddingVertical: 4, flexShrink: 0 },
  statusText:   { fontSize: 12, fontWeight: '600' },
  sig:          { fontSize: 13, color: '#94a3b8', fontStyle: 'italic' },
  detailRow:    { flexDirection: 'row', gap: 12 },
  detail:       { fontSize: 12, color: '#64748b' },
  refillBtn: {
    backgroundColor: '#1d4ed8',
    borderRadius: 10,
    paddingVertical: 10,
    alignItems: 'center',
    marginTop: 4,
  },
  refillBtnText: { color: '#fff', fontWeight: '600', fontSize: 14 },
  alertBanner:   { backgroundColor: '#450a0a', borderRadius: 8, padding: 10, marginTop: 4 },
  alertText:     { color: '#fca5a5', fontSize: 13, textAlign: 'center' },
  center:        { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: '#0f172a', gap: 8 },
  loadingText:   { color: '#94a3b8', fontSize: 14 },
  errorText:     { color: '#ef4444', fontSize: 16, fontWeight: '600' },
  errorSub:      { color: '#64748b', fontSize: 13 },
  empty:         { alignItems: 'center', paddingTop: 60, gap: 8 },
  emptyIcon:     { fontSize: 48 },
  emptyText:     { color: '#f8fafc', fontSize: 18, fontWeight: '600' },
  emptySub:      { color: '#64748b', fontSize: 14 },
  historySection:{ marginTop: 24 },
  historyLabel:  { fontSize: 16, fontWeight: '600', color: '#64748b', marginBottom: 12 },
})
