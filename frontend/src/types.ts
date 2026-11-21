export interface Metadata {
  resolution_source: 'LIVE_INFERENCE' | 'LLM_CACHE'
  model: string
  latency_ms: number
  input_tokens: number
  output_tokens: number
  key_id: string | null
  fallback_attempts: number
}

export interface Message {
  role: 'user' | 'assistant'
  content: string
  metadata?: Partial<Metadata>
}

export interface ChatResponse {
  session_id: string
  reply: string
  metadata: Metadata
}

export interface Health {
  status: 'ok'
  keys_configured: number
  cache_enabled: boolean
}
