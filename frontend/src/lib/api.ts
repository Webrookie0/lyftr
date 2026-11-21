import type { ChatResponse, Health, Message } from '../types'

export const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '')

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init)
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = data?.detail
    const message = response.status === 503
      ? 'All providers are unavailable. Your message wasn’t saved. Try again in a moment.'
      : typeof detail === 'string' ? detail
      : Array.isArray(detail) ? detail.map((item: { msg: string }) => item.msg).join('. ')
      : `The server returned an error (${response.status}).`
    throw new ApiError(response.status, message)
  }
  if (!data) throw new Error('The server returned an unreadable response.')
  return data as T
}

export const getHealth = (signal?: AbortSignal) => request<Health>('/health', { signal })
export const getHistory = (session: string) => request<{ messages: Message[] }>(
  `/sessions/${encodeURIComponent(session)}/messages`,
)
export const sendMessage = (session: string, message: string, prompt: string | undefined) =>
  request<ChatResponse>('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: session, message, ...(prompt !== undefined ? { system_prompt: prompt } : {}) }),
  })
