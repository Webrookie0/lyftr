import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import { ArrowUp, Check, ChevronDown, Copy, Menu, Plus, RefreshCw, Sparkles, X, Zap } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ApiError, getHealth, getHistory, sendMessage } from './lib/api'
import type { Health, Message } from './types'
import { MarkdownPre } from './components/CodeBlock'

const newSession = () => `s-${crypto.randomUUID()}`
const suggestions = [
  { label: 'Make something complex, simple', text: 'Explain how response caching works using a simple everyday example.', icon: '↗' },
  { label: 'Find a fresh perspective', text: 'Give me three unexpected ways to build a daily reading habit.', icon: '✳' },
  { label: 'Put memory to the test', text: 'Remember that my favorite city is Kyoto. Tell me one interesting fact about it.', icon: '◎' },
]

function AssistantMessage({ message }: { message: Message }) {
  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState(false)
  const metadata = message.metadata
  async function copy() {
    try {
      await navigator.clipboard.writeText(message.content)
      setCopied(true)
      setCopyError(false)
      window.setTimeout(() => setCopied(false), 1800)
    } catch { setCopyError(true) }
  }
  return <article className="assistant-message" data-testid="assistant-message">
    <div className="message-byline"><span className="mini-mark">l<span>↗</span></span><strong>Lyftr</strong><span className="byline-note">A little clarity</span></div>
    <div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{ pre: MarkdownPre }} skipHtml>{message.content}</ReactMarkdown></div>
    {metadata && <div className="reply-metadata">
      <div className="metadata-top">
        <span className={`source ${metadata.resolution_source === 'LLM_CACHE' ? 'cached' : ''}`}>
          <Zap size={11} aria-hidden="true" /><span data-testid="meta-source">{metadata.resolution_source}</span>
        </span>
        <span data-testid="meta-model" className="model-name">{metadata.model}</span>
      </div>
      <div className="metadata-bottom">
        <span data-testid="meta-latency">{metadata.latency_ms} ms</span><span className="meta-divider" />
        <span>Input <b data-testid="meta-input-tokens">{metadata.input_tokens}</b></span>
        <span>Output <b data-testid="meta-output-tokens">{metadata.output_tokens}</b></span>
        <span className="key-meta">{metadata.key_id || 'no key used'} · {metadata.fallback_attempts ?? 0} fallbacks</span>
      </div>
    </div>}
    <button className="copy-button" onClick={copy} aria-label="Copy reply">{copied ? <Check size={14} /> : <Copy size={14} />}{copied ? 'Copied' : copyError ? 'Copy unavailable' : 'Copy reply'}</button>
  </article>
}

export default function App() {
  const [session, setSession] = useState(newSession)
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [prompt, setPrompt] = useState('')
  const [customPrompt, setCustomPrompt] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [uncertain, setUncertain] = useState(false)
  const [health, setHealth] = useState<Health | null>(null)
  const [healthChecking, setHealthChecking] = useState(true)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const textarea = useRef<HTMLTextAreaElement>(null)
  const conversationViewport = useRef<HTMLDivElement>(null)
  const followLatest = useRef(true)
  const inFlight = useRef(false)
  const recovery = useRef<{ prior: Message[]; message: string } | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    getHealth(controller.signal).then(setHealth).catch(() => {}).finally(() => setHealthChecking(false))
    return () => controller.abort()
  }, [])
  useLayoutEffect(() => {
    const viewport = conversationViewport.current
    if (viewport && followLatest.current) viewport.scrollTo({ top: messages.length || pending || error ? viewport.scrollHeight : 0, behavior: 'auto' })
  }, [messages, pending, error])
  useLayoutEffect(() => {
    if (textarea.current) {
      textarea.current.style.height = 'auto'
      textarea.current.style.height = `${Math.min(textarea.current.scrollHeight, 180)}px`
      const viewport = conversationViewport.current
      if (viewport && followLatest.current) viewport.scrollTo({ top: messages.length || pending || error ? viewport.scrollHeight : 0, behavior: 'auto' })
    }
  }, [input])

  function reset() {
    if (inFlight.current) return
    followLatest.current = true
    setSession(newSession()); setMessages([]); setInput(''); setError(null); setUncertain(false)
    setPrompt(''); setCustomPrompt(false); setSidebarOpen(false); recovery.current = null
    textarea.current?.focus({ preventScroll: true })
  }

  async function reconcile(prior: Message[], submitted: string) {
    try {
      const history = await getHistory(session)
      setMessages(history.messages)
      const saved = history.messages.length === prior.length + 2
        && history.messages[prior.length]?.role === 'user'
        && history.messages[prior.length]?.content === submitted
        && history.messages.at(-1)?.role === 'assistant'
      if (saved) {
        setError(null); setInput(''); setUncertain(false); recovery.current = null
        return
      }
      // A failed fetch may have left an inference running on the server. Keep sends
      // blocked until the user checks again; the history route shares its session lock.
      if (history.messages.length === prior.length) {
        setUncertain(false); recovery.current = null
        setError('The connection was interrupted. Your message wasn’t saved. You can send it again.')
      } else {
        setUncertain(true)
        setError('History changed unexpectedly. Start a new chat or check history again.')
      }
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 404) {
        setMessages(prior); setUncertain(false); recovery.current = null
        setError('The connection was interrupted. Your message wasn’t saved. You can send it again.')
      } else {
        setUncertain(true)
        setError('Connection lost. Check history before sending again so your message isn’t duplicated.')
      }
    }
  }

  async function submit(event?: FormEvent) {
    event?.preventDefault()
    const submitted = input.trim()
    if (!submitted || submitted.length > 4000 || inFlight.current || uncertain) return
    inFlight.current = true; setPending(true); setError(null)
    followLatest.current = true
    const prior = messages
    const submittedPrompt = customPrompt ? prompt : undefined
    setMessages([...prior, { role: 'user', content: submitted }]); setInput('')
    try {
      const response = await sendMessage(session, submitted, submittedPrompt)
      setMessages([...prior, { role: 'user', content: submitted }, { role: 'assistant', content: response.reply, metadata: response.metadata }])
      setHealthChecking(true)
      getHealth().then(setHealth).catch(() => setHealth(null)).finally(() => setHealthChecking(false))
    } catch (failure) {
      setInput(submitted)
      if (failure instanceof ApiError && [422, 503].includes(failure.status)) {
        setMessages(prior); setError(failure.message)
      } else {
        setMessages(prior); recovery.current = { prior, message: submitted }
        await reconcile(prior, submitted)
      }
    } finally {
      inFlight.current = false; setPending(false)
      window.setTimeout(() => textarea.current?.focus({ preventScroll: true }), 0)
    }
  }

  async function checkHistory() {
    if (!recovery.current || inFlight.current) return
    inFlight.current = true; setPending(true)
    try { await reconcile(recovery.current.prior, recovery.current.message) }
    finally { inFlight.current = false; setPending(false) }
  }
  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault(); void submit()
    }
  }

  return <div className="app-shell">
    {sidebarOpen && <button className="sidebar-backdrop" aria-label="Close navigation" onClick={() => setSidebarOpen(false)} />}
    <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
      <a className="brand" href="/" aria-label="Lyftr home"><span className="brand-mark">l<span>↗</span></span>lyftr<span className="brand-dot">.</span></a>
      <button className="mobile-close icon-button" aria-label="Close navigation" onClick={() => setSidebarOpen(false)}><X size={20} /></button>
      <button className="new-chat" onClick={reset} disabled={pending}><Plus size={17} />New conversation<span>↗</span></button>
      <div className="sidebar-section-label">YOUR SPACE</div>
      <div className="active-conversation"><span className="conversation-dot" /><span>{messages.find(m => m.role === 'user')?.content || 'A fresh start'}</span></div>
      <p className="session-note">A new space with every visit.<br />Context stays within this conversation.</p>
      <div className="sidebar-controls">
        <details className="control-details">
          <summary><Sparkles size={15} />Set the tone<ChevronDown size={14} /></summary>
          <div className="control-content">
            <label className="checkbox-label"><input type="checkbox" checked={customPrompt} disabled={pending} onChange={e => setCustomPrompt(e.target.checked)} />Use a custom instruction</label>
            <label htmlFor="system-prompt">How should Lyftr respond?</label>
            <textarea id="system-prompt" data-testid="system-prompt-input" placeholder="e.g. Be concise. Explain with examples." value={prompt} disabled={pending || !customPrompt} onChange={e => setPrompt(e.target.value)} />
            <p>Applies when your next message succeeds. Start a new chat to reset the tone.</p>
          </div>
        </details>
      </div>
      <div className="sidebar-footer">
        <div className="health-row"><span className={`health-dot ${health ? 'online' : ''}`} />{healthChecking ? 'Checking connection' : health ? 'All systems ready' : 'Backend offline'}
          <button className="icon-button" aria-label="Check backend connection" onClick={() => { setHealthChecking(true); getHealth().then(setHealth).catch(() => setHealth(null)).finally(() => setHealthChecking(false)) }}><RefreshCw size={13} /></button>
        </div>
        <span className="health-detail">{health ? `${health.keys_configured} keys · ${health.cache_enabled ? 'cache enabled' : 'cache disabled'}` : 'Connect a backend to start chatting'}</span>
        <div className="footer-rule" /><div className="sidebar-tagline">Built for the moments<br />you need a little clarity.<span>LYFTR / 01</span></div>
      </div>
    </aside>
    <main className="main-panel">
      <header className="topbar"><div><button className="mobile-menu icon-button" aria-label="Open navigation" onClick={() => setSidebarOpen(true)}><Menu size={21} /></button><span className="topbar-title">Your thinking space</span><span className="topbar-slash">/</span><span className="topbar-subtitle">Conversation</span></div><span className="private-note"><span />Session context</span></header>
      <div className="conversation-scroll" ref={conversationViewport} onScroll={event => {
        const viewport = event.currentTarget
        followLatest.current = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 80
      }}>
        {messages.length === 0 && !pending ? <section className="welcome">
          <div className="welcome-eyebrow"><span />ROOM FOR YOUR NEXT IDEA</div>
          <h1>Less noise.<br /><span>More clarity.</span></h1>
          <p className="welcome-copy">A place for big questions and unfinished ideas.<br className="desktop-break" /> Let’s make something click.</p>
          <div className="suggestions">{suggestions.map(suggestion => <button key={suggestion.label} onClick={() => { setInput(suggestion.text); textarea.current?.focus({ preventScroll: true }); setSidebarOpen(false) }}><span className="suggestion-icon">{suggestion.icon}</span><span>{suggestion.label}</span><span className="suggestion-arrow">↗</span></button>)}</div>
          <div className="welcome-footnote"><Zap size={12} />Every reply comes with its source, speed, and token usage.</div>
        </section> : <div className="conversation" aria-label="Conversation">
          <div className="conversation-start">A FRESH CONVERSATION<span /></div>
          {messages.map((message, index) => message.role === 'user'
            ? <div className="user-message" key={`${session}-${index}`}><span className="user-byline">YOU</span><p>{message.content}</p></div>
            : <AssistantMessage message={message} key={`${session}-${index}`} />)}
          {pending && <div className="pending-reply" role="status"><span className="mini-mark">l<span>↗</span></span><span className="thinking-dots"><i /><i /><i /></span><span>Thinking it through</span></div>}
        </div>}
        {error && <div className="error-message" role="alert"><span>{error}</span>{uncertain && <button onClick={checkHistory} disabled={pending}><RefreshCw size={14} />Check history</button>}</div>}
        <div aria-live="polite" className="sr-only">{pending ? 'Lyftr is thinking.' : messages.at(-1)?.role === 'assistant' ? 'Lyftr replied.' : ''}</div>
      </div>
      <div className="composer-area"><div className="composer-width">
        <form className="composer" onSubmit={submit}>
          <label htmlFor="chat-input" className="sr-only">Message Lyftr</label>
          <textarea ref={textarea} id="chat-input" data-testid="chat-input" placeholder="What’s on your mind?" value={input} onChange={e => setInput(e.target.value)} onKeyDown={keyDown} disabled={pending || uncertain} rows={1} />
          <div className="composer-bottom"><span><Sparkles size={13} />A thought is a good place to start.</span><div><span className={`char-count ${input.trim().length > 4000 ? 'over-limit' : ''}`}>{input.length ? `${input.trim().length.toLocaleString()} / 4,000` : ''}</span><button data-testid="send-button" className="send-button" type="submit" aria-label="Send message" disabled={!input.trim() || input.trim().length > 4000 || pending || uncertain}><ArrowUp size={19} /></button></div></div>
        </form>
        <div className="composer-footnote"><span>Lyftr can make mistakes. A little double-check goes a long way.</span><span className="keyboard-hint">↵ send <span>·</span> ⇧ ↵ new line</span></div>
      </div></div>
    </main>
  </div>
}
