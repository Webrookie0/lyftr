import { Children, isValidElement, useMemo, useState } from 'react'
import type { ComponentPropsWithoutRef, ReactNode } from 'react'
import { Check, Copy } from 'lucide-react'
import { highlightCode } from '../lib/highlight'

export function CodeBlock({ code, language }: { code: string; language?: string }) {
  const highlighted = useMemo(() => highlightCode(code, language), [code, language])
  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState(false)

  async function copyCode() {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      setCopyError(false)
      window.setTimeout(() => setCopied(false), 1800)
    } catch { setCopyError(true) }
  }

  return <div className="code-block" data-testid="code-block">
    <div className="code-block-header">
      <span className="code-language">{highlighted.language}</span>
      <button type="button" className="code-copy-button" data-testid="copy-code-button" onClick={copyCode} aria-label={`Copy ${highlighted.language} code`}>
        {copied ? <Check size={13} /> : <Copy size={13} />}
        {copied ? 'Copied' : copyError ? 'Copy unavailable' : 'Copy code'}
      </button>
    </div>
    <pre><code className="hljs" dangerouslySetInnerHTML={{ __html: highlighted.html }} /></pre>
  </div>
}

export function MarkdownPre({ children }: ComponentPropsWithoutRef<'pre'>) {
  const child = Children.toArray(children)[0]
  if (!isValidElement<{ className?: string; children?: ReactNode }>(child)) return <pre>{children}</pre>
  const code = typeof child.props.children === 'string' ? child.props.children : String(child.props.children ?? '')
  const language = /(?:^|\s)language-([^\s]+)/.exec(child.props.className ?? '')?.[1]
  return <CodeBlock code={code.replace(/\n$/, '')} language={language} />
}
