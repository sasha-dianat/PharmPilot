/**
 * useAbbreviationScanner
 * =======================
 * Read-only global abbreviation helper.
 *
 * React owns the application DOM, so this hook never rewrites text nodes or
 * injects wrappers into rendered content. It delegates hover detection from the
 * document, reads the text node under the pointer with caretRangeFromPoint, and
 * displays definitions in one tooltip node appended outside React's root.
 */
import { useEffect } from 'react'
import { GLOSSARY, GLOSSARY_KEYS } from './glossary'

type CaretPointDocument = Document & {
  caretRangeFromPoint?: (x: number, y: number) => Range | null
  caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null
}

type TextPosition = {
  textNode: Text
  offset: number
}

type GlossaryMatch = {
  term: string
  definition: string
}

type PointerSnapshot = {
  x: number
  y: number
  target: Element | null
}

const HOVER_DELAY_MS = 120
const TOOLTIP_OFFSET_PX = 12
const VIEWPORT_MARGIN_PX = 8

const INTERACTIVE_SELECTOR = [
  'input',
  'textarea',
  'select',
  'button',
  'a',
  '[contenteditable]',
  '[data-no-abbr]',
].join(',')

const GLOSSARY_BY_LOWER = new Map<string, GlossaryMatch>(
  GLOSSARY_KEYS.map((term) => [
    term.toLowerCase(),
    { term, definition: GLOSSARY[term] },
  ]),
)

const PHRASE_KEYS = GLOSSARY_KEYS.filter((term) => /[^A-Za-z0-9-]/.test(term))

function isWordChar(char: string | undefined): boolean {
  return !!char && /[A-Za-z0-9-]/.test(char)
}

function hasWordBoundary(text: string, start: number, end: number): boolean {
  return !isWordChar(text[start - 1]) && !isWordChar(text[end])
}

function normalizeTextPosition(node: Node, offset: number): TextPosition | null {
  if (node.nodeType === Node.TEXT_NODE) {
    return { textNode: node as Text, offset }
  }

  const childAtOffset = node.childNodes.item(offset)
  if (childAtOffset?.nodeType === Node.TEXT_NODE) {
    return { textNode: childAtOffset as Text, offset: 0 }
  }

  const childBeforeOffset = offset > 0 ? node.childNodes.item(offset - 1) : null
  if (childBeforeOffset?.nodeType === Node.TEXT_NODE) {
    return {
      textNode: childBeforeOffset as Text,
      offset: childBeforeOffset.textContent?.length ?? 0,
    }
  }

  return null
}

function getTextPositionFromPoint(x: number, y: number): TextPosition | null {
  const doc = document as CaretPointDocument
  const range = doc.caretRangeFromPoint?.(x, y)

  if (range) {
    return normalizeTextPosition(range.startContainer, range.startOffset)
  }

  const position = doc.caretPositionFromPoint?.(x, y)
  if (position) {
    return normalizeTextPosition(position.offsetNode, position.offset)
  }

  return null
}

function extractWordAtOffset(text: string, rawOffset: number): string | null {
  if (!text) return null

  let offset = Math.min(Math.max(rawOffset, 0), text.length)
  if (offset === text.length) offset -= 1
  if (!isWordChar(text[offset]) && offset > 0 && isWordChar(text[offset - 1])) {
    offset -= 1
  }
  if (!isWordChar(text[offset])) return null

  let start = offset
  while (start > 0 && isWordChar(text[start - 1])) start -= 1

  let end = offset + 1
  while (end < text.length && isWordChar(text[end])) end += 1

  return text.slice(start, end)
}

function findPhraseMatch(text: string, offset: number): GlossaryMatch | null {
  if (PHRASE_KEYS.length === 0) return null

  const lowerText = text.toLowerCase()
  for (const term of PHRASE_KEYS) {
    const lowerTerm = term.toLowerCase()
    let index = lowerText.indexOf(lowerTerm)

    while (index !== -1) {
      const end = index + lowerTerm.length
      if (offset >= index && offset <= end && hasWordBoundary(text, index, end)) {
        return GLOSSARY_BY_LOWER.get(lowerTerm) ?? null
      }
      index = lowerText.indexOf(lowerTerm, index + 1)
    }
  }

  return null
}

function findGlossaryMatch(textPosition: TextPosition): GlossaryMatch | null {
  const text = textPosition.textNode.textContent ?? ''
  const offset = Math.min(Math.max(textPosition.offset, 0), text.length)

  const phraseMatch = findPhraseMatch(text, offset)
  if (phraseMatch) return phraseMatch

  const word = extractWordAtOffset(text, offset)
  if (!word) return null

  return GLOSSARY_BY_LOWER.get(word.toLowerCase()) ?? null
}

function createTooltip(): HTMLDivElement {
  const tooltip = document.createElement('div')
  tooltip.className = 'pp-abbr-tooltip'
  tooltip.setAttribute('role', 'tooltip')
  Object.assign(tooltip.style, {
    position: 'fixed',
    display: 'none',
    zIndex: '9999',
    maxWidth: '340px',
    padding: '8px 12px',
    border: '1px solid rgba(139, 92, 246, 0.4)',
    borderRadius: '8px',
    background: '#1e1b4b',
    color: '#e0e7ff',
    boxShadow: '0 4px 16px rgba(0,0,0,0.5), 0 0 0 1px rgba(139,92,246,0.15)',
    fontFamily: 'system-ui, sans-serif',
    fontSize: '11.5px',
    fontWeight: '400',
    lineHeight: '1.5',
    pointerEvents: 'none',
    whiteSpace: 'normal',
  })
  document.body.appendChild(tooltip)
  return tooltip
}

function shouldSkipTarget(target: Element | null): boolean {
  return !target || !!target.closest(INTERACTIVE_SELECTOR)
}

export function useAbbreviationScanner(): void {
  useEffect(() => {
    const tooltip = createTooltip()
    let hoverTimer: ReturnType<typeof window.setTimeout> | null = null

    const clearHoverTimer = (): void => {
      if (hoverTimer === null) return
      window.clearTimeout(hoverTimer)
      hoverTimer = null
    }

    const hideTooltip = (): void => {
      clearHoverTimer()
      tooltip.style.display = 'none'
      tooltip.textContent = ''
      document.body.style.cursor = ''
    }

    const showTooltip = (match: GlossaryMatch, pointer: PointerSnapshot): void => {
      document.body.style.cursor = 'help'
      tooltip.textContent = `${match.term}: ${match.definition}`
      tooltip.style.display = 'block'
      tooltip.style.left = '0px'
      tooltip.style.top = '0px'

      const rect = tooltip.getBoundingClientRect()
      const preferredLeft = pointer.x + TOOLTIP_OFFSET_PX
      const preferredTop = pointer.y + TOOLTIP_OFFSET_PX
      const maxLeft = window.innerWidth - rect.width - VIEWPORT_MARGIN_PX
      const maxTop = window.innerHeight - rect.height - VIEWPORT_MARGIN_PX
      const fallbackTop = pointer.y - rect.height - TOOLTIP_OFFSET_PX

      const left = Math.max(VIEWPORT_MARGIN_PX, Math.min(preferredLeft, maxLeft))
      const top = preferredTop > maxTop
        ? Math.max(VIEWPORT_MARGIN_PX, fallbackTop)
        : Math.max(VIEWPORT_MARGIN_PX, preferredTop)

      tooltip.style.left = `${left}px`
      tooltip.style.top = `${top}px`
    }

    const updateTooltip = (pointer: PointerSnapshot): void => {
      if (shouldSkipTarget(pointer.target)) {
        hideTooltip()
        return
      }

      const textPosition = getTextPositionFromPoint(pointer.x, pointer.y)
      const match = textPosition ? findGlossaryMatch(textPosition) : null

      if (!match) {
        hideTooltip()
        return
      }

      showTooltip(match, pointer)
    }

    const onMouseMove = (event: MouseEvent): void => {
      clearHoverTimer()
      const pointer: PointerSnapshot = {
        x: event.clientX,
        y: event.clientY,
        target: event.target instanceof Element ? event.target : null,
      }

      hoverTimer = window.setTimeout(() => {
        hoverTimer = null
        updateTooltip(pointer)
      }, HOVER_DELAY_MS)
    }

    document.addEventListener('mousemove', onMouseMove, { passive: true })
    document.addEventListener('scroll', hideTooltip, { capture: true, passive: true })
    document.addEventListener('mouseleave', hideTooltip)

    return () => {
      clearHoverTimer()
      document.body.style.cursor = ''
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('scroll', hideTooltip, true)
      document.removeEventListener('mouseleave', hideTooltip)
      tooltip.remove()
    }
  }, [])
}
