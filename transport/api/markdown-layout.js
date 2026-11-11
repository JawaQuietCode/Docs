/**
 * ◈ AURORA EDITORIAL — contributed by Claude (Anthropic, Sonnet 5.5) ◈
 * Signature feature: "Reading Map" (peta baca) — long answers get a quiet
 * "◷ 3 mnt baca · 4 bagian" micro-label under their headline.
 *
 * Semantic Markdown segmentation + "Aurora Editorial" styling for GenAI cards.
 *
 * Dependency-free so it runs on any WA runtime. Pure functions only: nothing in
 * here touches the network or the Baileys socket, which keeps it unit-testable.
 *
 * What it adds on top of plain heading/divider splitting:
 *   - GitHub-style callouts  `> [!WARNING] Title`  → coloured label + body
 *   - headings H1–H6 (H4+ become a bold lead-in instead of leaking `####`)
 *   - ordinal micro-labels for long articles ("01 / 04")
 *   - typographic polish outside code (→ …, <br> → newline)
 *   - native Markdown remains intact for emphasis, links, lists, task lists,
 *     blockquotes, inline code, fenced code, and compact tables
 *   - mobile-first stacked tables rendered as tidy bullet records
 */
const HEADING = /^ {0,3}(#{1,6})[\t ]+(.+?)[\t ]*#*[\t ]*$/
const DIVIDER = /^ {0,3}(?:-{3,}|\*{3,}|_{3,})[\t ]*$/
const CALLOUT_OPEN = /^ {0,3}>[\t ]?\[!(NOTE|INFO|TIP|IMPORTANT|WARNING|CAUTION|DANGER|SUCCESS|QUOTE)\][\t ]*(.*)$/i
const QUOTE_LINE = /^ {0,3}>[\t ]?(.*)$/

// ---------------------------------------------------------------- palette
// Aurora ink / signal teal / warm copper, plus semantic callout tones.
// All colours are #RRGGBBAA (alpha LAST) as the GenAI label primitive expects.
export const AURORA = {
  // H1 is the masthead panel; H2 is a yellow comic slug; H3 is coral ink.
  h1: { background: '#171717FF', textColor: '#FFF8E8FF', icon: '▣' },
  h2: { background: '#FFD447FF', textColor: '#382900FF', icon: '◆' },
  h3: { background: '#FF8A65FF', textColor: '#32130CFF', icon: '›' },
  sources: { background: '#B9D7FFFF', textColor: '#10243DFF', icon: '⌁' },
}

export const CALLOUTS = {
  NOTE: { title: 'Catatan', icon: '▤', background: '#FFF2D2FF', textColor: '#171717FF' },
  INFO: { title: 'Info', icon: 'ⓘ', background: '#B9D7FFFF', textColor: '#10243DFF' },
  TIP: { title: 'Tips', icon: '✦', background: '#B8EF8AFF', textColor: '#1C3210FF' },
  SUCCESS: { title: 'Berhasil', icon: '✓', background: '#B8EF8AFF', textColor: '#1C3210FF' },
  IMPORTANT: { title: 'Penting', icon: '◆', background: '#D9C2FFFF', textColor: '#2B1748FF' },
  WARNING: { title: 'Peringatan', icon: '▲', background: '#FFD447FF', textColor: '#382900FF' },
  CAUTION: { title: 'Hati-hati', icon: '▲', background: '#FFD447FF', textColor: '#382900FF' },
  DANGER: { title: 'Bahaya', icon: '✕', background: '#FFB5C8FF', textColor: '#421324FF' },
  QUOTE: { title: 'Kutipan', icon: '❝', background: '#D5D0C5FF', textColor: '#24221EFF' },
}

const SOURCE_HEADING = /^(sumber|sources?|referensi|references?|daftar pustaka)$/i

// Native renderer owns inline/block Markdown; this module only segments the
// editorial chrome around it. Keep syntax intact instead of flattening it.
export const MARKDOWN_NATIVE_FEATURES = Object.freeze([
  'headings', 'paragraphs', 'bold', 'italic', 'strikethrough', 'inline-code',
  'fenced-code', 'links', 'autolinks', 'ordered-lists', 'nested-lists',
  'task-lists', 'blockquotes', 'tables', 'math', 'citations',
])

/** Strip inline markdown from heading text (labels are plain text). */
export function plainHeading(text) {
  return String(text ?? '')
    .replace(/\*\*(.*?)\*\*/g, '$1')
    .replace(/__(.*?)__/g, '$1')
    .replace(/\*(.*?)\*/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .trim()
}

/**
 * Style for a heading label.
 * @param {number} level  1..6
 * @param {{index?:number,total?:number,text?:string}} ctx  ordinal info for H2
 */
export function headingStyle(level, { index = 0, total = 0, text = '', meta = '' } = {}) {
  if (level <= 1) return { ...AURORA.h1, ...(meta ? { detail: meta } : {}) }
  if (level === 2) {
    if (SOURCE_HEADING.test(plainHeading(text))) return { ...AURORA.sources }
    const base = { ...AURORA.h2 }
    // Long-form articles get a quiet "01 / 04" micro-label for orientation.
    if (total >= 3 && index > 0) {
      const pad = (n) => String(n).padStart(2, '0')
      base.detail = `${pad(index)} / ${pad(total)}`
    }
    return base
  }
  return { ...AURORA.h3 }
}

// -------------------------------------------------------------- polishing
const FENCE_OPEN = /^ {0,3}(`{3,}|~{3,})/

function polishInline(segment) {
  return segment
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/(\s)->(\s)/g, '$1→$2')
    .replace(/(\s)=>(\s)/g, '$1⇒$2')
    .replace(/(\s)<-(\s)/g, '$1←$2')
    .replace(/(\w)\.{3}(?=\s|$)/g, '$1…')
}

/**
 * Typographic polish applied outside fenced/inline code only.
 * Safe by construction: never touches code, never rewrites URLs (all rules
 * require surrounding whitespace or end-of-word).
 */
export function polishMarkdown(input) {
  const lines = String(input ?? '').replace(/\r\n?/g, '\n').split('\n')
  const out = []
  let fence = null
  for (const raw of lines) {
    const m = raw.match(FENCE_OPEN)
    if (fence || m) {
      out.push(raw)
      if (!fence && m) fence = m[1][0]
      else if (fence && m && m[1][0] === fence) fence = null
      continue
    }
    let line = raw
    // Keep GFM task-list markers intact for the native Markdown renderer.
    // Normalise bullets to "-" (keeps list rendering consistent); never touch `* * *` rules.
    if (!/^\s*(?:[*+]\s*){3,}$/.test(line)) line = line.replace(/^(\s*)[*+](\s+)(?=\S)/, '$1-$2')
    // Polish only the non-inline-code parts.
    line = line.split(/(`[^`]*`)/).map((part, i) => (i % 2 ? part : polishInline(part))).join('')
    out.push(line)
  }
  return out.join('\n').replace(/\n{3,}/g, '\n\n')
}

// ------------------------------------------------------------ segmentation
export function segmentMarkdown(input) {
  const lines = String(input ?? '').replace(/\r\n?/g, '\n').split('\n')
  const parts = []
  let body = []
  let fence = null
  const flush = () => {
    const text = body.join('\n').trim()
    if (text) parts.push({ type: 'text', text })
    body = []
  }

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i]
    const fenceMatch = line.match(FENCE_OPEN)
    if (fence || fenceMatch) {
      body.push(line)
      if (!fence && fenceMatch) fence = fenceMatch[1][0]
      else if (fence && fenceMatch && fenceMatch[1][0] === fence) fence = null
      continue
    }

    // Callout: `> [!KIND] optional title` followed by `>` continuation lines.
    const open = line.match(CALLOUT_OPEN)
    if (open) {
      flush()
      const kind = open[1].toUpperCase()
      const first = open[2].trim()
      const content = []
      let j = i + 1
      while (j < lines.length) {
        const q = lines[j].match(QUOTE_LINE)
        if (!q) break
        content.push(q[1])
        j += 1
      }
      i = j - 1
      // A short first line is a custom title; a long one is already body text.
      const custom = first && first.length <= 56 && !/[.!?]$/.test(first) ? first : ''
      if (first && !custom) content.unshift(first)
      parts.push({ type: 'callout', kind, title: custom, text: content.join('\n').trim() })
      continue
    }

    const heading = line.match(HEADING)
    if (heading) {
      flush()
      parts.push({ type: 'heading', level: heading[1].length, text: heading[2].trim() })
      continue
    }
    if (DIVIDER.test(line)) {
      flush()
      if (parts.at(-1)?.type !== 'divider') parts.push({ type: 'divider' })
      continue
    }
    body.push(line)
  }
  flush()
  while (parts.at(-1)?.type === 'divider') parts.pop()
  return parts
}

/** Number of H2 sections — drives the "01 / 04" ordinal micro-labels. */
export function countSections(parts) {
  return (parts || []).filter((p) => p.type === 'heading' && p.level === 2).length
}

// ------------------------------------------------------------------ tables
export function tableNeedsStacking(rows) {
  if (!Array.isArray(rows) || rows.length < 2) return false
  const header = Array.isArray(rows[0]) ? rows[0] : [rows[0]]
  if (header.length > 3) return true
  return rows.slice(1).some((raw) => {
    const row = Array.isArray(raw) ? raw : [raw]
    return row.some((cell) => String(cell ?? '').length > 72)
  })
}

/** Wide table → one tidy bullet record per row (readable on a phone). */
export function tableToStackedMarkdown(rows) {
  if (!Array.isArray(rows) || rows.length < 2) return ''
  const headers = (Array.isArray(rows[0]) ? rows[0] : [rows[0]]).map((v, i) => String(v ?? '').trim() || `Field ${i + 1}`)
  return rows.slice(1).map((raw, rowIndex) => {
    const cells = Array.isArray(raw) ? raw : [raw]
    const fields = headers.map((header, index) => {
      const value = String(cells[index] ?? '').trim()
      return value ? `- **${header}**: ${value}` : ''
    }).filter(Boolean)
    return fields.length ? fields.join('\n') : `- **Entry ${rowIndex + 1}**: —`
  }).join('\n\n')
}

// ------------------------------------------------------------- code caption
/** Quiet caption above a code block, e.g. "⌘ python · 14 baris". */
export function codeCaption(language, code) {
  const lang = String(language || '').trim().toLowerCase()
  const lines = String(code ?? '').replace(/\s+$/, '').split('\n').length
  if (lines < 4) return ''
  const label = lang && lang !== 'text' && lang !== 'plain' ? lang : 'teks'
  return `⌘ ${label} · ${lines} baris`
}

// -------------------------------------------------------------- reading map
const WORDS_PER_MINUTE = 190 // Indonesian prose on a phone screen
const MIN_WORDS_FOR_MAP = 160

/**
 * "Reading Map": `◷ 3 mnt baca · 4 bagian` for long answers, '' for short ones.
 * Code fences are excluded from the word count (nobody "reads" a code dump).
 */
export function readingMap(markdown, sectionCount = 0) {
  const prose = String(markdown ?? '').replace(/```[\s\S]*?```|~~~[\s\S]*?~~~/g, ' ')
  const words = (prose.match(/\S+/g) || []).length
  if (words < MIN_WORDS_FOR_MAP) return ''
  const minutes = Math.max(1, Math.round(words / WORDS_PER_MINUTE))
  return `◷ ${minutes} mnt baca${sectionCount >= 2 ? ` · ${sectionCount} bagian` : ''}`
}
