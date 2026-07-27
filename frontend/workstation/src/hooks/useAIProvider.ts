/**
 * useAIProvider — React hook for multi-AI provider queries
 * Wraps the AI Hub API with React Query, streaming support,
 * and automatic cost tracking display.
 */
import { useState, useCallback, useRef } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { apiClient } from '../lib/api'

export type AITask =
  | 'clinical_consultation' | 'drug_interaction_check' | 'dosing_guidance'
  | 'prescription_ocr' | 'refill_message_generation' | 'rag_synthesis'
  | 'summarization' | 'adherence_counseling' | 'inventory_narrative'
  | 'fraud_analysis' | 'phi_safe_local' | 'general'

interface AIQueryOptions {
  task?: AITask
  systemPrompt?: string
  maxTokens?: number
  temperature?: number
  forceProvider?: string
  onChunk?: (chunk: string) => void
}

interface AIResponse {
  content: string
  provider: string
  model: string
  latencyMs: number
  costUsd: number
  tokensIn: number
  tokensOut: number
  invocationId: string
  fromFallback: boolean
}

/**
 * Hook for single-shot AI queries with automatic provider routing.
 *
 * Usage:
 *   const { query, response, isLoading, provider, cost } = useAIProvider()
 *   await query('What is the renal dose for metformin at eGFR 28?', { task: 'dosing_guidance' })
 */
export function useAIProvider() {
  const [response, setResponse] = useState<string>('')
  const [meta, setMeta] = useState<Partial<AIResponse>>({})
  const [streamedContent, setStreamedContent] = useState('')
  const abortRef = useRef<AbortController | null>(null)

  const { mutateAsync: queryMutation, isPending: isLoading, error } = useMutation({
    mutationFn: async ({ prompt, options }: { prompt: string; options?: AIQueryOptions }) => {
      const res = await apiClient.post('/ai/query', {
        prompt,
        task: options?.task || 'general',
        system_prompt: options?.systemPrompt,
        max_tokens: options?.maxTokens || 1024,
        temperature: options?.temperature || 0.1,
        force_provider: options?.forceProvider,
      })
      return res.data as AIResponse & {
        content: string; latency_ms: number; cost_usd: number
        tokens_input: number; tokens_output: number; invocation_id: string; from_fallback: boolean
      }
    },
    onSuccess: (data) => {
      setResponse(data.content)
      setMeta({
        provider: data.provider,
        model: data.model,
        latencyMs: data.latency_ms,
        costUsd: data.cost_usd,
        tokensIn: data.tokens_input,
        tokensOut: data.tokens_output,
        invocationId: data.invocation_id,
        fromFallback: data.from_fallback,
      })
    },
  })

  const query = useCallback(async (prompt: string, options?: AIQueryOptions) => {
    return queryMutation({ prompt, options })
  }, [queryMutation])

  /**
   * Streaming query — calls onChunk with each token as it arrives.
   * Use for long-form clinical consultation responses.
   */
  const streamQuery = useCallback(async (prompt: string, options?: AIQueryOptions) => {
    abortRef.current?.abort()
    abortRef.current = new AbortController()
    setStreamedContent('')

    const API = import.meta.env.VITE_API_URL || 'http://localhost:8001/api/v1'
    const token = localStorage.getItem('access_token') || ''

    try {
      const res = await fetch(`${API}/ai/query/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body: JSON.stringify({
          prompt, task: options?.task || 'general',
          system_prompt: options?.systemPrompt,
          max_tokens: options?.maxTokens || 2048,
        }),
        signal: abortRef.current.signal,
      })

      const reader = res.body?.getReader()
      const decoder = new TextDecoder()
      let full = ''

      while (reader) {
        const { done, value } = await reader.read()
        if (done) break
        const text = decoder.decode(value)
        const lines = text.split('\n')
        for (const line of lines) {
          if (line.startsWith('data: ') && line !== 'data: [DONE]') {
            try {
              const data = JSON.parse(line.slice(6))
              const chunk = data.chunk || ''
              full += chunk
              setStreamedContent(full)
              options?.onChunk?.(chunk)
            } catch { /* ignore parse errors */ }
          }
        }
      }
      setResponse(full)
    } catch (e: unknown) {
      if ((e as Error).name !== 'AbortError') throw e
    }
  }, [])

  const cancel = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  return {
    query,
    streamQuery,
    cancel,
    response,
    streamedContent,
    isLoading,
    error,
    provider: meta.provider,
    model: meta.model,
    latencyMs: meta.latencyMs,
    costUsd: meta.costUsd,
    fromFallback: meta.fromFallback,
    invocationId: meta.invocationId,
    reset: () => { setResponse(''); setStreamedContent(''); setMeta({}) },
  }
}

/**
 * Hook for provider status monitoring (AI Hub dashboard).
 */
export function useAIProviders() {
  return useQuery({
    queryKey: ['ai-providers'],
    queryFn: () => apiClient.get('/ai/providers').then(r => r.data),
    refetchInterval: 30_000,
    staleTime: 15_000,
  })
}

/**
 * Hook for A/B testing two providers.
 */
export function useAIABTest() {
  return useMutation({
    mutationFn: (params: { prompt: string; providerA: string; providerB: string; task?: AITask }) =>
      apiClient.post('/ai/ab-test', {
        prompt: params.prompt,
        provider_a: params.providerA,
        provider_b: params.providerB,
        task: params.task || 'general',
      }).then(r => r.data),
  })
}
