/**
 * Medications Tab — full current medication list with adherence tracking.
 * Shows adherence score, last refill date, days until next refill due.
 */
import {
  View, Text, FlatList, StyleSheet, TouchableOpacity, Alert,
} from 'react-native'
import { useQuery } from '@tanstack/react-query'
import { patientApi } from '../../src/api/client'
import { useAuthStore } from '../../src/store/auth'

interface Medication {
  id: string
  drug_name: string
  drug_strength?: string
  sig_text: string
  days_supply: number
  refills_remaining: number
  last_fill_date?: string
  ndc: string
  is_controlled: boolean
}

function adherenceDaysLeft(lastFillDate: string | undefined, daysSupply: number): number | null {
  if (!lastFillDate) return null
  const lastFill = new Date(lastFillDate)
  const nextRefillDate = new Date(lastFill.getTime() + daysSupply * 24 * 3600 * 1000)
  const today = new Date()
  return Math.ceil((nextRefillDate.getTime() - today.getTime()) / (24 * 3600 * 1000))
}

function AdherenceBadge({ daysLeft }: { daysLeft: number | null }) {
  if (daysLeft === null) return null
  const urgent = daysLeft <= 7
  const due    = daysLeft <= 0
  return (
    <View style={[styles.adherenceBadge,
      due ? styles.badgeDue : urgent ? styles.badgeUrgent : styles.badgeOk]}>
      <Text style={styles.adherenceText}>
        {due ? 'Refill Due' : urgent ? `${daysLeft}d left` : `${daysLeft}d`}
      </Text>
    </View>
  )
}

export default function MedicationsScreen() {
  const { patientId } = useAuthStore()

  const { data: meds, isLoading } = useQuery({
    queryKey: ['medications', patientId],
    queryFn: () => patientApi.getMedications(patientId!).then(r => r.data as Medication[]),
    enabled: !!patientId,
  })

  return (
    <FlatList
      style={styles.list}
      contentContainerStyle={styles.content}
      data={meds || []}
      keyExtractor={item => item.id}
      ListHeaderComponent={() => (
        <View style={styles.header}>
          <Text style={styles.title}>Medication List</Text>
          <Text style={styles.sub}>{(meds || []).length} active medications</Text>
        </View>
      )}
      renderItem={({ item }) => {
        const daysLeft = adherenceDaysLeft(item.last_fill_date, item.days_supply)
        return (
          <View style={styles.card}>
            <View style={styles.row}>
              <View style={styles.nameCol}>
                <Text style={styles.drugName}>{item.drug_name}</Text>
                {item.drug_strength && <Text style={styles.strength}>{item.drug_strength}</Text>}
              </View>
              <AdherenceBadge daysLeft={daysLeft} />
            </View>
            <Text style={styles.sig}>{item.sig_text}</Text>
            <View style={styles.metaRow}>
              <Text style={styles.meta}>{item.days_supply}-day supply</Text>
              {item.last_fill_date && (
                <Text style={styles.meta}>
                  Last filled: {new Date(item.last_fill_date).toLocaleDateString()}
                </Text>
              )}
              <Text style={styles.meta}>
                {item.refills_remaining > 0 ? `${item.refills_remaining} refills left` : 'No refills'}
              </Text>
            </View>
          </View>
        )
      }}
      ListEmptyComponent={() => !isLoading ? (
        <View style={styles.empty}>
          <Text style={styles.emptyText}>No medications on file</Text>
        </View>
      ) : null}
    />
  )
}

const styles = StyleSheet.create({
  list:    { flex: 1, backgroundColor: '#0f172a' },
  content: { padding: 16, paddingBottom: 32 },
  header:  { marginBottom: 16 },
  title:   { fontSize: 26, fontWeight: '700', color: '#f8fafc' },
  sub:     { fontSize: 14, color: '#64748b', marginTop: 2 },
  card: {
    backgroundColor: '#1e293b', borderRadius: 16,
    padding: 16, marginBottom: 10, gap: 6,
  },
  row:      { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' },
  nameCol:  { flex: 1, gap: 2 },
  drugName: { fontSize: 16, fontWeight: '600', color: '#f8fafc' },
  strength: { fontSize: 13, color: '#94a3b8' },
  sig:      { fontSize: 13, color: '#64748b', fontStyle: 'italic' },
  metaRow:  { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  meta:     { fontSize: 12, color: '#475569' },
  adherenceBadge: { borderRadius: 8, paddingHorizontal: 10, paddingVertical: 4, marginLeft: 8 },
  badgeOk:     { backgroundColor: '#14532d' },
  badgeUrgent: { backgroundColor: '#451a03' },
  badgeDue:    { backgroundColor: '#450a0a' },
  adherenceText: { fontSize: 12, fontWeight: '600', color: '#fff' },
  empty:    { alignItems: 'center', paddingTop: 60 },
  emptyText:{ color: '#64748b', fontSize: 15 },
})
