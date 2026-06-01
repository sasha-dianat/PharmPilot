/**
 * Secure Messaging Tab — HIPAA-compliant patient↔pharmacy communication.
 * Drug names, diagnoses, and PHI never sent via SMS — only in-app encrypted channel.
 */
import { useState, useRef } from 'react'
import {
  View, Text, TextInput, FlatList, TouchableOpacity,
  StyleSheet, KeyboardAvoidingView, Platform,
} from 'react-native'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { patientApi } from '../../src/api/client'
import { useAuthStore } from '../../src/store/auth'

interface Message {
  id: string
  body: string
  sent_by: 'patient' | 'pharmacy'
  created_at: string
  read: boolean
}

export default function MessagesScreen() {
  const { patientId } = useAuthStore()
  const [text, setText] = useState('')
  const queryClient = useQueryClient()
  const listRef = useRef<FlatList>(null)

  const { data: messages } = useQuery({
    queryKey: ['messages', patientId],
    queryFn: () => patientApi.getMessages(patientId!).then(r => r.data as Message[]),
    enabled: !!patientId,
    refetchInterval: 15_000,
  })

  const sendMutation = useMutation({
    mutationFn: (body: string) => patientApi.sendMessage(patientId!, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['messages', patientId] })
      setText('')
      setTimeout(() => listRef.current?.scrollToEnd(), 100)
    },
  })

  const handleSend = () => {
    const trimmed = text.trim()
    if (!trimmed) return
    sendMutation.mutate(trimmed)
  }

  const sorted = [...(messages || [])].sort(
    (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
  )

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      keyboardVerticalOffset={90}
    >
      <View style={styles.header}>
        <Text style={styles.title}>Messages</Text>
        <Text style={styles.subtitle}>Your Pharmacy Team</Text>
        <View style={styles.encryptedBadge}>
          <Text style={styles.encryptedText}>🔒 Encrypted — HIPAA compliant</Text>
        </View>
      </View>

      <FlatList
        ref={listRef}
        data={sorted}
        keyExtractor={item => item.id}
        style={styles.list}
        contentContainerStyle={styles.listContent}
        onContentSizeChange={() => listRef.current?.scrollToEnd()}
        ListEmptyComponent={() => (
          <View style={styles.empty}>
            <Text style={styles.emptyText}>No messages yet</Text>
            <Text style={styles.emptySub}>
              Send a message to your pharmacy team.{'\n'}
              We respond within business hours.
            </Text>
          </View>
        )}
        renderItem={({ item }) => {
          const isPatient = item.sent_by === 'patient'
          const time = new Date(item.created_at).toLocaleTimeString([], {
            hour: '2-digit', minute: '2-digit',
          })
          return (
            <View style={[styles.bubbleRow, isPatient ? styles.bubbleRight : styles.bubbleLeft]}>
              {!isPatient && <Text style={styles.senderLabel}>Pharmacy</Text>}
              <View style={[styles.bubble, isPatient ? styles.bubblePatient : styles.bubblePharmacy]}>
                <Text style={[styles.bubbleText, isPatient ? styles.textPatient : styles.textPharmacy]}>
                  {item.body}
                </Text>
                <Text style={styles.time}>{time}</Text>
              </View>
            </View>
          )
        }}
      />

      {/* Input */}
      <View style={styles.inputRow}>
        <TextInput
          style={styles.input}
          value={text}
          onChangeText={setText}
          placeholder="Type a message…"
          placeholderTextColor="#475569"
          multiline
          maxLength={500}
        />
        <TouchableOpacity
          style={[styles.sendBtn, !text.trim() && styles.sendBtnDisabled]}
          onPress={handleSend}
          disabled={!text.trim() || sendMutation.isPending}
        >
          <Text style={styles.sendBtnText}>Send</Text>
        </TouchableOpacity>
      </View>
    </KeyboardAvoidingView>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0f172a' },
  header:    { padding: 16, paddingBottom: 8 },
  title:     { fontSize: 26, fontWeight: '700', color: '#f8fafc' },
  subtitle:  { fontSize: 14, color: '#64748b' },
  encryptedBadge: {
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: '#064e3b', borderRadius: 6,
    paddingHorizontal: 8, paddingVertical: 3,
    alignSelf: 'flex-start', marginTop: 6,
  },
  encryptedText: { fontSize: 11, color: '#6ee7b7' },
  list:        { flex: 1 },
  listContent: { padding: 16, gap: 8, paddingBottom: 8 },
  bubbleRow:   { gap: 2 },
  bubbleRight: { alignItems: 'flex-end' },
  bubbleLeft:  { alignItems: 'flex-start' },
  senderLabel: { fontSize: 11, color: '#64748b', marginLeft: 4, marginBottom: 2 },
  bubble:      { maxWidth: '80%', borderRadius: 16, padding: 12 },
  bubblePatient:  { backgroundColor: '#1d4ed8', borderBottomRightRadius: 4 },
  bubblePharmacy: { backgroundColor: '#1e293b', borderBottomLeftRadius: 4 },
  bubbleText:  { fontSize: 15, lineHeight: 20 },
  textPatient: { color: '#fff' },
  textPharmacy:{ color: '#e2e8f0' },
  time:        { fontSize: 10, color: 'rgba(255,255,255,0.5)', marginTop: 4, textAlign: 'right' },
  inputRow:    { flexDirection: 'row', padding: 12, gap: 8, borderTopWidth: 1, borderTopColor: '#1e293b' },
  input: {
    flex: 1, backgroundColor: '#1e293b', borderRadius: 20,
    paddingHorizontal: 16, paddingVertical: 10,
    color: '#f8fafc', fontSize: 15, maxHeight: 100,
  },
  sendBtn:         { backgroundColor: '#3b82f6', borderRadius: 20, paddingHorizontal: 18, justifyContent: 'center' },
  sendBtnDisabled: { opacity: 0.4 },
  sendBtnText:     { color: '#fff', fontWeight: '600', fontSize: 14 },
  empty:    { alignItems: 'center', paddingTop: 60, gap: 8 },
  emptyText:{ color: '#94a3b8', fontSize: 17, fontWeight: '600' },
  emptySub: { color: '#475569', fontSize: 14, textAlign: 'center', lineHeight: 20 },
})
