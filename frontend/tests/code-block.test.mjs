import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { fileURLToPath } from 'node:url'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ReactMarkdown from 'react-markdown'
import { createServer } from 'vite'

let server
let MarkdownPre
let CodeBlock

before(async () => {
  server = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)),
    configFile: false,
    cacheDir: '.verification/render-tests',
    esbuild: { jsx: 'automatic' },
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    optimizeDeps: { noDiscovery: true },
  })
  ;({ MarkdownPre, CodeBlock } = await server.ssrLoadModule('/src/components/CodeBlock.tsx'))
})

after(async () => { await server?.close() })

function markdown(text) {
  return renderToStaticMarkup(React.createElement(
    ReactMarkdown, { components: { pre: MarkdownPre }, skipHtml: true }, text,
  ))
}

test('fenced Python code renders a labeled block with keyword highlighting', () => {
  const html = markdown('```python\ndef hello(name):\n    return "Hello " + name\n```')
  assert.match(html, /data-testid="code-block"/)
  assert.match(html, /class="code-language">python/)
  assert.match(html, /class="hljs-keyword">def/)
  assert.match(html, /class="hljs-string"/)
  assert.match(html, /Copy code/)
  assert.equal((html.match(/<pre>/g) || []).length, 1)
})

test('TypeScript aliases retain syntax highlighting and the source code', () => {
  const html = markdown('```ts\nconst answer: number = 42;\n```')
  assert.match(html, /class="code-language">typescript/)
  assert.match(html, /class="hljs-keyword">const/)
  assert.match(html, /class="hljs-number">42/)
})

test('inline code stays inline instead of becoming a block', () => {
  const html = markdown('Use `console.log()` here.')
  assert.match(html, /<code>console.log\(\)<\/code>/)
  assert.doesNotMatch(html, /data-testid="code-block"/)
})

test('HTML inside code is highlighted as text and never creates executable tags', () => {
  const html = markdown('```html\n<script>alert("test")</script>\n<img src=x onerror=alert(1)>\n```')
  assert.match(html, /hljs-name/)
  assert.match(html, /&lt;/)
  assert.doesNotMatch(html, /<script\b|<img\b/i)
})

test('raw Markdown HTML remains disabled outside code blocks', () => {
  const html = markdown('<script>alert("test")</script>\n\n<img src=x onerror=alert(1)>')
  assert.doesNotMatch(html, /<script\b|<img\b/i)
})

test('unknown languages fall back to escaped copyable code', () => {
  const code = '<secret attr="value">&</secret>'
  const html = renderToStaticMarkup(React.createElement(CodeBlock, { code, language: 'unknown-lang' }))
  assert.match(html, /unknown-lang/)
  assert.match(html, /&lt;secret attr=&quot;value&quot;&gt;&amp;&lt;\/secret&gt;/)
  assert.doesNotMatch(html, /<secret\b/)
})
