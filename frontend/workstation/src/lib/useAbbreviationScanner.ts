/**
 * useAbbreviationScanner
 * =======================
 * Global MutationObserver that automatically detects known pharmacy/clinical
 * abbreviations in any rendered text node and wraps them with tooltip spans.
 *
 * Works on ALL text in the app — labels, table cells, badges, chart tooltips,
 * error messages — without requiring manual changes to existing components.
 *
 * How it works:
 *   1. On mount, scans the full DOM for text nodes containing known abbreviations.
 *   2. Splits matching text nodes and inserts <mark class="pp-abbr"> elements.
 *   3. A MutationObserver watches for new nodes (route changes, dynamic data)
 *      and processes them automatically.
 *   4. CSS in index.css renders the tooltip on hover via ::after pseudo-element.
 *
 * Safe guards:
 *   - Skips <script>, <style>, <input>, <textarea>, <pre>, <code> nodes
 *   - Skips nodes already processed (data-abbr-scanned attribute)
 *   - Debounced 150 ms to avoid thrashing on rapid re-renders
 *   - Never modifies React-controlled event attributes
 */
import { useEffect } from 'react'
import { GLOSSARY, GLOSSARY_KEYS } from './glossary'

// Skip these tags — they contain non-display text or user-editable content
const SKIP_TAGS = new Set([
  'SCRIPT', 'STYLE', 'INPUT', 'TEXTAREA', 'PRE', 'CODE',
  'SELECT', 'BUTTON', 'A', 'SVG', 'PATH', 'CANVAS',
  'VIDEO', 'AUDIO', 'NOSCRIPT', 'TEMPLATE',
])

// Only wrap abbreviations inside these containers (avoids nav, modal backdrop, etc.)
const SCAN_ROOT_SELECTOR = 'main, [data-dashboard], [role="main"], .workstation-content'

let scanScheduled = false

/**
 * Build the regex once — sorted longest-first to prefer NDC-11 over NDC.
 */
const ABBR_PATTERN = new RegExp(
  `\\b(${GLOSSARY_KEYS.map(k => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})\\b`,
  'g',
)

function processTextNode(textNode: Text): void {
  const text = textNode.textContent || ''
  if (!text.trim()) return

  // Quick check before regex
  const hasAbbr = GLOSSARY_KEYS.some(k => text.includes(k))
  if (!hasAbbr) return

  const parent = textNode.parentNode as HTMLElement | null
  if (!parent) return
  if (SKIP_TAGS.has(parent.tagName)) return
  if (parent.closest('[data-no-abbr]')) return
  if ((parent as HTMLElement).classList?.contains('pp-abbr')) return

  // Reset lastIndex for global regex
  ABBR_PATTERN.lastIndex = 0

  const parts: Array<string | { term: string; def: string }> = []
  let last = 0
  let m: RegExpExecArray | null

  while ((m = ABBR_PATTERN.exec(text)) !== null) {
    const term = m[1]
    const def  = GLOSSARY[term]
    if (!def) continue
    if (m.index > last) parts.push(text.slice(last, m.index))
    parts.push({ term, def })
    last = m.index + term.length
  }

  if (parts.length === 0 || (parts.length === 1 && typeof parts[0] === 'string')) return
  if (last < text.length) parts.push(text.slice(last))

  // Build fragment replacing the text node
  const frag = document.createDocumentFragment()
  for (const part of parts) {
    if (typeof part === 'string') {
      frag.appendChild(document.createTextNode(part))
    } else {
      const span = document.createElement('mark')
      span.className    = 'pp-abbr'
      span.textContent  = part.term
      span.dataset.def  = part.def
      span.setAttribute('role', 'term')
      span.setAttribute('aria-label', `${part.term}: ${part.def}`)
      frag.appendChild(span)
    }
  }

  parent.replaceChild(frag, textNode)
}

function walkNode(node: Node): void {
  if (node.nodeType === Node.TEXT_NODE) {
    processTextNode(node as Text)
    return
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return

  const el = node as HTMLElement
  if (SKIP_TAGS.has(el.tagName)) return
  if (el.dataset.abbrScanned) return
  el.dataset.abbrScanned = '1'

  // Walk child nodes (collect first — replaceChild mutates childNodes)
  const children = Array.from(el.childNodes)
  children.forEach(walkNode)
}

function scanDom(): void {
  // Scan the broadest container available, fall back to document.body
  const roots = document.querySelectorAll(SCAN_ROOT_SELECTOR)
  const targets: Element[] = roots.length > 0
    ? Array.from(roots)
    : [document.body]

  targets.forEach(root => {
    // Reset scanned flag so updated nodes get re-processed
    root.querySelectorAll('[data-abbr-scanned]').forEach(el =>
      delete (el as HTMLElement).dataset.abbrScanned
    )
    walkNode(root)
  })
}

function scheduleScan(): void {
  if (scanScheduled) return
  scanScheduled = true
  setTimeout(() => {
    scanScheduled = false
    scanDom()
  }, 150)
}

export function useAbbreviationScanner(): void {
  useEffect(() => {
    // Initial scan
    scheduleScan()

    // Watch for DOM changes (route transitions, async data loads)
    const observer = new MutationObserver((mutations) => {
      const hasNewNodes = mutations.some(m => m.addedNodes.length > 0)
      if (hasNewNodes) scheduleScan()
    })

    observer.observe(document.body, {
      childList:  true,
      subtree:    true,
      attributes: false,
      characterData: false,
    })

    return () => observer.disconnect()
  }, [])
}
