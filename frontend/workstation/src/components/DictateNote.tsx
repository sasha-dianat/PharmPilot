/**
 * DictateNote — Pharmacist voice-note dictation control.
 * ========================================================
 * Records audio via the browser MediaRecorder API, sends to
 * POST /audio/dictate (Whisper multilingual), shows the transcript
 * for ONE-TAP confirm before any data is committed.
 *
 * PHI discipline: audio is never stored automatically — only the
 * pharmacist-confirmed text is passed to the onConfirm callback.
 * The raw recording is discarded after transcription.
 *
 * Usage in VerificationCenter / PatientPanel:
 *   <DictateNote context="note" onConfirm={(text) => saveNote(text)} />
 *
 * context values:
 *   "note"         — generic Rx or patient note
 *   "dur_override" — DUR override reason (maps to the override field)
 *   "counseling"   — counseling documentation
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { audioApi } from '../lib/api'

type DictationContext = 'note' | 'dur_override' | 'counseling'
type Stage = 'idle' | 'recording' | 'transcribing' | 'review' | 'error'

interface Props {
  context?: DictationContext
  language?: string         // 'fa' (Persian, default) | 'en'
  placeholder?: string
  onConfirm: (text: string, context: DictationContext) => void
  onCancel?: () => void
  compact?: boolean         // smaller inline variant
}

const CONTEXT_LABEL: Record<DictationContext, string> = {
  note:         'Dictate Note',
  dur_override: 'Dictate Override Reason',
  counseling:   'Dictate Counseling Points',
}
const CONTEXT_COLOR: Record<DictationContext, string> = {
  note:         'bg-blue-600 hover:bg-blue-700',
  dur_override: 'bg-orange-600 hover:bg-orange-700',
  counseling:   'bg-teal-600 hover:bg-teal-700',
}

export default function DictateNote({
  context = 'note',
  language = 'fa',
  placeholder,
  onConfirm,
  onCancel,
  compact = false,
}: Props) {
  const [stage, setStage]         = useState<Stage>('idle')
  const [transcript, setTranscript] = useState('')
  const [editedText, setEditedText] = useState('')
  const [error, setError]         = useState('')
  const [elapsed, setElapsed]     = useState(0)

  const recorderRef  = useRef<MediaRecorder | null>(null)
  const chunksRef    = useRef<Blob[]>([])
  const timerRef     = useRef<ReturnType<typeof setInterval> | null>(null)
  const streamRef    = useRef<MediaStream | null>(null)

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stopTimer()
      if (streamRef.current) streamRef.current.getTracks().forEach(t => t.stop())
    }
  }, [])

  const stopTimer = () => {
    if (timerRef.current) clearInterval(timerRef.current)
    timerRef.current = null
  }

  const startRecording = useCallback(async () => {
    setError('')
    setTranscript('')
    setEditedText('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream
      chunksRef.current = []

      // Prefer WebM/Opus if available (smaller, faster to send)
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/webm')
        ? 'audio/webm'
        : 'audio/ogg'

      const recorder = new MediaRecorder(stream, { mimeType })
      recorderRef.current = recorder

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data)
      }

      recorder.onstop = async () => {
        stream.getTracks().forEach(t => t.stop())
        const blob = new Blob(chunksRef.current, { type: mimeType })
        await sendForTranscription(blob)
      }

      recorder.start(250)  // collect 250ms chunks for smoother streaming later
      setStage('recording')
      setElapsed(0)
      timerRef.current = setInterval(() => setElapsed(s => s + 1), 1000)
    } catch (err: any) {
      setError(err?.message ?? 'Microphone access denied')
      setStage('error')
    }
  }, [language, context])

  const stopRecording = useCallback(() => {
    stopTimer()
    if (recorderRef.current?.state === 'recording') {
      setStage('transcribing')
      recorderRef.current.stop()
    }
  }, [])

  const sendForTranscription = async (blob: Blob) => {
    try {
      const { data } = await audioApi.dictate(blob, context, language)
      if (data.status === 'empty' || !data.text) {
        setError('No speech detected — try again.')
        setStage('error')
        return
      }
      setTranscript(data.text)
      setEditedText(data.text)
      setStage('review')
    } catch (err: any) {
      const msg = err?.response?.data?.detail ?? 'Transcription failed'
      setError(msg)
      setStage('error')
    }
  }

  const handleConfirm = () => {
    const text = editedText.trim()
    if (text) {
      onConfirm(text, context)
    }
    reset()
  }

  const reset = () => {
    setStage('idle')
    setTranscript('')
    setEditedText('')
    setError('')
    setElapsed(0)
  }

  // ── Idle ───────────────────────────────────────────────────────────────────
  if (stage === 'idle') {
    return (
      <button
        onClick={startRecording}
        className={`flex items-center gap-2 px-3 py-1.5 text-white text-xs rounded-lg font-medium transition-colors ${CONTEXT_COLOR[context]}`}
        title={`${CONTEXT_LABEL[context]} (Persian/English)`}
      >
        <span>🎙</span>
        <span>{compact ? '' : CONTEXT_LABEL[context]}</span>
      </button>
    )
  }

  // ── Recording ──────────────────────────────────────────────────────────────
  if (stage === 'recording') {
    return (
      <div className="flex items-center gap-2 px-3 py-2 bg-red-50 border border-red-400 rounded-lg">
        <span className="w-3 h-3 rounded-full bg-red-500 animate-pulse flex-shrink-0" />
        <span className="text-xs text-red-700 font-medium flex-1">
          Recording… {elapsed}s — speak clearly
        </span>
        <button
          onClick={stopRecording}
          className="px-3 py-1 bg-red-600 text-white text-xs rounded hover:bg-red-700 font-medium flex-shrink-0"
        >
          ■ Stop
        </button>
      </div>
    )
  }

  // ── Transcribing ───────────────────────────────────────────────────────────
  if (stage === 'transcribing') {
    return (
      <div className="flex items-center gap-2 px-3 py-2 bg-blue-50 border border-blue-300 rounded-lg text-xs text-blue-700">
        <div className="w-3 h-3 border-2 border-blue-500 border-t-transparent rounded-full animate-spin flex-shrink-0" />
        Transcribing audio…
      </div>
    )
  }

  // ── Review — confirm before saving ────────────────────────────────────────
  if (stage === 'review') {
    return (
      <div className="space-y-2 p-3 bg-gray-50 border rounded-lg">
        <div className="flex items-center gap-2 text-xs text-gray-600">
          <span>📝</span>
          <span className="font-medium">{CONTEXT_LABEL[context]} — Review before saving</span>
          <span className="ml-auto text-gray-400 italic text-[10px]">AI transcription — verify accuracy</span>
        </div>
        <textarea
          value={editedText}
          onChange={e => setEditedText(e.target.value)}
          rows={3}
          dir={language === 'fa' ? 'rtl' : 'ltr'}
          className="w-full text-sm border rounded px-3 py-2 resize-none focus:outline-none focus:ring-2 focus:ring-blue-400"
          placeholder={placeholder ?? 'Edit if needed, then confirm…'}
        />
        <div className="flex gap-2">
          <button
            onClick={handleConfirm}
            disabled={!editedText.trim()}
            className="px-4 py-1.5 bg-green-600 text-white text-xs rounded hover:bg-green-700 font-medium disabled:opacity-40"
          >
            ✓ Confirm &amp; Save
          </button>
          <button
            onClick={startRecording}
            className="px-3 py-1.5 bg-gray-100 text-gray-700 text-xs rounded hover:bg-gray-200 border"
          >
            🎙 Re-record
          </button>
          <button
            onClick={() => { reset(); onCancel?.() }}
            className="px-3 py-1.5 text-gray-400 text-xs hover:text-gray-600"
          >
            Cancel
          </button>
        </div>
      </div>
    )
  }

  // ── Error ──────────────────────────────────────────────────────────────────
  return (
    <div className="flex items-center gap-2 px-3 py-2 bg-red-50 border border-red-300 rounded-lg text-xs text-red-700">
      <span>⚠</span>
      <span className="flex-1">{error}</span>
      <button onClick={reset} className="text-red-500 underline hover:no-underline">
        Retry
      </button>
    </div>
  )
}
