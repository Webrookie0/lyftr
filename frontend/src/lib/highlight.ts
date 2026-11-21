import hljs from 'highlight.js/lib/core'
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import python from 'highlight.js/lib/languages/python'
import bash from 'highlight.js/lib/languages/bash'
import json from 'highlight.js/lib/languages/json'
import css from 'highlight.js/lib/languages/css'
import xml from 'highlight.js/lib/languages/xml'
import sql from 'highlight.js/lib/languages/sql'
import go from 'highlight.js/lib/languages/go'
import rust from 'highlight.js/lib/languages/rust'
import java from 'highlight.js/lib/languages/java'
import cpp from 'highlight.js/lib/languages/cpp'
import yaml from 'highlight.js/lib/languages/yaml'
import diff from 'highlight.js/lib/languages/diff'

for (const [name, grammar] of Object.entries({ javascript, typescript, python, bash, json, css, xml, sql, go, rust, java, cpp, yaml, diff })) {
  hljs.registerLanguage(name, grammar)
}

const aliases: Record<string, string> = {
  js: 'javascript', jsx: 'javascript', ts: 'typescript', tsx: 'typescript',
  py: 'python', sh: 'bash', shell: 'bash', console: 'bash', 'shell-session': 'bash',
  html: 'xml', svg: 'xml', yml: 'yaml', rs: 'rust', c: 'cpp', 'c++': 'cpp',
  plaintext: 'text', txt: 'text',
}

const escapeHtml = (code: string) => code.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;')

export function highlightCode(code: string, hint = ''): { html: string; language: string } {
  const requested = hint.toLowerCase()
  const language = aliases[requested] ?? requested
  try {
    if (language && hljs.getLanguage(language)) {
      return { html: hljs.highlight(code, { language, ignoreIllegals: true }).value, language }
    }
    if (!language && code.length <= 10000) {
      const result = hljs.highlightAuto(code)
      if (result.language && result.relevance >= 3) return { html: result.value, language: result.language }
    }
  } catch {
    // Unsupported or malformed syntax still renders as escaped, copyable code.
  }
  return { html: escapeHtml(code), language: language || 'text' }
}
