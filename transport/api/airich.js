// airich.js — toolkit kartu AI Rich yang LEBIH KAYA.
//
// Diadaptasi dari `MessageBuilder.js` (nix408) untuk Kwee-agentic. Yang diambil
// adalah bagian yang benar-benar menambah kemampuan:
//
//   1. `extractIE()`      — inline entities: hyperlink, citation, latex.
//                           Tautan jadi BISA DIKLIK di dalam kartu (bukan teks
//                           mentah `[teks](url)`), dan ada dukungan sitasi.
//   2. `Toolkit`          — "auto unduh" media: URL/buffer diunduh & di-upload
//                           ke WhatsApp (`prepareWAMessageMedia`) supaya gambar
//                           dan video benar-benar tampil di kartu.
//   3. Primitif kaya      — image (GRID_IMAGE), sources, suggest (follow-up
//                           pill), footer action, compact, widget 3P.
//   4. Tabel kaya         — `unified_rows` + `markdown_cells` + inline entities
//                           per sel (sel bisa berisi tautan yang bisa diklik).
//
// Struktur kartu TETAP sama seperti rich.js (botForwardedMessage →
// richResponseMessage{submessages, unifiedResponse.data}), hanya saja sekarang
// `sections[]` (sumber render) bisa memuat primitif tambahan.
import crypto from 'node:crypto'
import { spawn } from 'node:child_process'
import { PassThrough, Readable } from 'node:stream'
import { prepareWAMessageMedia, tokenizeCode } from '@itsliaaa/baileys'
import {
  segmentMarkdown, tableNeedsStacking, tableToStackedMarkdown,
  polishMarkdown, countSections, headingStyle, plainHeading, codeCaption, readingMap, CALLOUTS,
} from './markdown-layout.js'

export const AI_BOT_JID = '867051314767696@bot'

// Progress indicator (planning steps) — implementasi ada di progress.js,
// di sini hanya di-re-export supaya `import { ... } from './airich.js'` tetap
// berlaku. Satu sumber kebenaran: progress.js.
export { PlanningStepStatus, progressStep, buildProgressIndicatorMetadata } from './progress.js'

// RichSubMessageType (lib/Types/RichType.js)
export const T = {
  GRID_IMAGE: 1,
  TEXT: 2,
  INLINE_IMAGE: 3,
  TABLE: 4,
  CODE: 5,
  DYNAMIC: 6,
  MAP: 7,
  LATEX: 8,
  CONTENT_ITEMS: 9,
}

// ---------- tokenizer kode yang LOSSLESS (perbaikan korupsi LaTeX/kode) -----
//
// `tokenizeCode` bawaan paket memakai regex ber-`\b` sehingga **membuang** token
// yang dimulai angka lalu huruf: `2cm`, `3pt`, `10px`, `2n`. Akibatnya LaTeX
// `\usepackage[margin=2cm]{geometry}` jadi `\usepackage[margin=]{geometry}` dan
// kode CSS/JS ikut rusak diam-diam. `#` juga dibaca sebagai komentar (merusak
// makro LaTeX seperti `#1`).
//
// Solusi: jalankan tokenizer paket; kalau hasilnya TIDAK bisa disusun ulang
// persis sama dengan sumbernya (lossy), pakai lexer lokal yang menjamin
// lossless — tiap celah yang tak cocok dijadikan blok DEFAULT, jadi tak ada
// teks yang hilang. Highlighting tetap ada untuk komentar/string/angka.
const _LEXER_RE = /(\/\/.*|\/\*[\s\S]*?\*\/|#.*)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`[\s\S]*?`)|(\b[a-zA-Z_]\w*\b)(?=\s*\()|(\b[a-zA-Z_]\w*\b)|(\b\d+(?:\.\d+)?\b)|(\s+|[^\w\s]+)/g

// Kode tipe menyamai CodeHighlightType paket:
// 0 DEFAULT, 1 KEYWORD, 2 METHOD, 3 STRING, 4 NUMBER, 5 COMMENT.
function lexLossless(code) {
  const src = String(code ?? '')
  const out = []
  let last = 0
  let m
  _LEXER_RE.lastIndex = 0
  while ((m = _LEXER_RE.exec(src)) !== null) {
    if (m[0] === '') { _LEXER_RE.lastIndex += 1; continue }
    if (m.index > last) out.push({ highlightType: 0, codeContent: src.slice(last, m.index) })
    let type = 0
    if (m[1]) type = 5        // komentar
    else if (m[2]) type = 3   // string
    else if (m[3]) type = 2   // pemanggilan (method)
    else if (m[5]) type = 4   // angka
    out.push({ highlightType: type, codeContent: m[0] })
    last = m.index + m[0].length
  }
  if (last < src.length) out.push({ highlightType: 0, codeContent: src.slice(last) })
  return out
}

/**
 * Tokenizer kode yang menjamin teks TIDAK berubah (lossless).
 * Pakai tokenizer paket bila hasilnya bisa disusun ulang persis; kalau tidak
 * (mis. LaTeX `2cm`, CSS `10px`, JS `2n`), jatuh ke lexer lokal.
 */
export function tokenizeCodeSafe(code, language = 'text') {
  const src = String(code ?? '')
  if (!src) return []
  try {
    const pb = tokenizeCode(src, language)
    if (pb.length && pb.map((b) => b.codeContent).join('') === src) return pb
  } catch { /* jatuh ke lexer lokal */ }
  return lexLossless(src)
}

// ---------- inline entities (hyperlink / citation / latex) ----------

/**
 * Ubah Markdown inline `[teks](url)`, `[](url)` (sitasi) dan `[tex]<url>` (latex)
 * menjadi teks + daftar inline entity yang dirender WhatsApp sebagai elemen
 * interaktif (tautan bisa diklik) alih-alih teks mentah.
 */
/**
 * Pola URL mentah (bare) — `https://…` tanpa pembungkus Markdown `[teks](url)`.
 * Dipakai pass 2 `extractIE` untuk mengubah URL yang ditulis model apa adanya
 * menjadi inline entity yang bisa diklik (atau chip sumber bila cocok dengan
 * blok `wa:sources`). Berhenti di spasi / kurung / tanda kutip.
 */
const BARE_URL_RE = /https?:\/\/[^\s<>()"'\]]+/gi

export function extractIE(text, { extract = true, hyperlink = true, citation = true, latex = true, bareUrl = true, sourceLookup = null } = {}) {
  if (!extract) return { text, ie: [], inline_entities: [] }

  const createIE = (type, ie) => {
    if (type === 'hyperlink') {
      return {
        key: ie.key,
        metadata: {
          display_name: ie.text,
          is_trusted: ie.is_trusted,
          url: ie.url,
          __typename: 'GenAIInlineLinkItem',
        },
      }
    }
    if (type === 'citation') {
      // Bila URL sitasi cocok dengan salah satu sumber (`wa:sources`), isi
      // judul & chip sumbernya dari data itu; kalau tidak, pakai URL apa adanya.
      const src = sourceLookup && sourceLookup[ie.url]
      const title = (src && (src.title || src.displayName)) || ie.url
      const site = (src && (src.site || src.domain || src.subtitle)) || ''
      return {
        key: ie.key,
        metadata: {
          reference_id: ie.reference_id,
          reference_url: ie.url,
          reference_title: title,
          reference_display_name: title,
          sources: [{
            source_type: 'THIRD_PARTY',
            source_display_name: title,
            source_subtitle: site,
            source_url: ie.url,
            favicon: { url: (src && (src.icon || src.favicon)) || '', mime_type: 'image/jpeg', width: 16, height: 16 },
          }],
          __typename: 'GenAISearchCitationItem',
        },
      }
    }
    if (type === 'latex') {
      return {
        key: ie.key,
        metadata: {
          latex_expression: ie.text,
          latex_image: {
            url: ie.url,
            width: Number(ie.width) || 100,
            height: Number(ie.height) || 100,
          },
          font_height: Number(ie.font_height) || 83.333333333333,
          padding: Number(ie.padding) || 15,
          __typename: 'GenAILatexItem',
        },
      }
    }
    return undefined
  }

  const ie = []
  const inline_entities = []
  let result = ''
  let last = 0
  let citation_index = 1
  let hyperlink_index = 0
  let latex_index = 0
  const stack = []

  for (let i = 0; i < text.length; i++) {
    if (text[i] === '[' && text[i - 1] !== '\\') {
      stack.push(i)
    } else if (text[i] === ']' && (text[i + 1] === '(' || text[i + 1] === '<')) {
      const start = stack.pop()
      if (start == null) continue

      const open = text[i + 1]
      const close = open === '(' ? ')' : '>'
      const type = open === '(' ? 'link' : 'latex'
      let end = i + 2
      let depth = 1
      while (end < text.length && depth) {
        if (text[end] === open && text[end - 1] !== '\\') depth++
        else if (text[end] === close && text[end - 1] !== '\\') depth--
        end++
      }
      if (depth) continue

      const raw = text.slice(start + 1, i).trim()
      let url = text.slice(i + 2, end - 1).trim()

      let key
      let tag
      let data

      if (type === 'latex') {
        if (!latex) continue
        const [txt = '', width = null, height = null, font_height = null, padding = null] = raw.split('|')
        key = `KWEE_LATEX_${latex_index++}`
        tag = `{{${key}}}${txt || 'image'}{{/${key}}}`
        data = { type: 'latex', ie: { key, text: txt, url, width, height, font_height, padding } }
      } else if (raw) {
        if (!hyperlink) continue
        const trusted = !url.startsWith('!')
        if (!trusted) url = url.slice(1)
        key = `KWEE_HYPERLINK_${hyperlink_index++}`
        tag = `{{${key}}}${url}{{/${key}}}`
        data = { type: 'hyperlink', ie: { key, text: raw, url, is_trusted: trusted } }
      } else {
        if (!citation) continue
        key = `KWEE_CITATION_${citation_index - 1}`
        tag = `{{${key}}}${url}{{/${key}}}`
        data = { type: 'citation', ie: { reference_id: citation_index++, key, text: '', url } }
      }

      result += text.slice(last, start) + tag
      last = end
      ie.push(data)
      const entity = createIE(data.type, data.ie)
      if (entity) inline_entities.push(entity)
      i = end - 1
    }
  }

  result += text.slice(last)

  // Pass 2: URL MENTAH (bare, tanpa `[teks](url)`) → inline entity yang bisa
  // diklik. URL yang cocok dengan blok `wa:sources` jadi CHIP SUMBER (favicon
  // + judul); selain itu jadi tautan biasa. Placeholder `{{KEY}}…{{/KEY}}`
  // hasil pass 1 DILEWATI supaya tidak dobel-taut.
  if (bareUrl && (hyperlink || citation)) {
    const PH = /\{\{KWEE_[A-Z0-9_]+?\}\}[\s\S]*?\{\{\/KWEE_[A-Z0-9_]+?\}\}/g
    const segments = []
    let pos = 0
    let mm
    while ((mm = PH.exec(result))) {
      segments.push({ t: result.slice(pos, mm.index), ph: false })
      segments.push({ t: mm[0], ph: true })
      pos = mm.index + mm[0].length
    }
    segments.push({ t: result.slice(pos), ph: false })

    let rebuilt = ''
    for (const seg of segments) {
      if (seg.ph) { rebuilt += seg.t; continue }
      rebuilt += seg.t.replace(BARE_URL_RE, (raw) => {
        // Buang tanda baca akhir yang bukan bagian URL (`.`/`,`/`;`/`)` …).
        let url = raw.replace(/[.,;:!?]+$/g, '')
        const trailing = raw.slice(url.length)
        if (!url) return raw
        const src = sourceLookup && sourceLookup[url]
        let key
        let entity
        if (src && citation) {
          key = `KWEE_CITATION_${citation_index - 1}`
          entity = createIE('citation', { reference_id: citation_index++, key, text: '', url })
        } else if (hyperlink) {
          key = `KWEE_HYPERLINK_${hyperlink_index++}`
          entity = createIE('hyperlink', { key, text: url, url, is_trusted: true })
        } else {
          return raw
        }
        if (!entity) return raw
        ie.push({ type: src && citation ? 'citation' : 'hyperlink', ie: { key, url } })
        inline_entities.push(entity)
        return `{{${key}}}${url}{{/${key}}}${trailing}`
      })
    }
    result = rebuilt
  }

  return { text: result, ie, inline_entities }
}

// ---------- Toolkit: media ("auto unduh") ----------

const isUrl = (s) => /^https?:\/\/.+/i.test(String(s || ''))

async function fetchBuffer(url, { silent = true } = {}) {
  try {
    const res = await fetch(url)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return Buffer.from(await res.arrayBuffer())
  } catch (e) {
    if (silent) return Buffer.alloc(0)
    throw e
  }
}

// Ambil metadata OpenGraph halaman (judul, gambar, deskripsi, penulis).
// Best-effort: kegagalan / halaman tanpa OG → null, pemanggil tetap jalan.
// Ini yang mengisi kartu reel/post (thumbnail, judul, penulis) tanpa AI.
export async function fetchLinkMeta(url) {
  if (!isUrl(url)) return null
  let html = ''
  try {
    const ctl = new AbortController()
    const timer = setTimeout(() => ctl.abort(), 8000)
    const res = await fetch(url, {
      signal: ctl.signal,
      headers: {
        'user-agent': 'Mozilla/5.0 (compatible; KweeBot/1.0; +https://marrlabs.my.id)',
        accept: 'text/html,application/xhtml+xml',
      },
    })
    clearTimeout(timer)
    if (!res.ok) return null
    html = (await res.text()).slice(0, 200000)
  } catch {
    return null
  }
  const meta = (name) => {
    const re = new RegExp(
      `<meta[^>]+(?:property|name)=["']${name}["'][^>]+content=["']([^"']+)["']`, 'i')
    const re2 = new RegExp(
      `<meta[^>]+content=["']([^"']+)["'][^>]+(?:property|name)=["']${name}["']`, 'i')
    const m = html.match(re) || html.match(re2)
    return m ? decodeEntities(m[1].trim()) : ''
  }
  const title = meta('og:title') || meta('twitter:title') || (html.match(/<title[^>]*>([^<]*)<\/title>/i)?.[1] || '').trim()
  const image = meta('og:image') || meta('twitter:image') || meta('og:image:url')
  const description = meta('og:description') || meta('twitter:description') || meta('description')
  const author = meta('og:site_name') || meta('author') || meta('twitter:creator')
  const out = {
    title: decodeEntities(title),
    image: image ? new URL(image, url).href : '',
    description: decodeEntities(description),
    author: decodeEntities(author),
    favicon: '',
  }
  try { out.favicon = new URL('/favicon.ico', url).href } catch { /* abaikan */ }
  if (!out.title && !out.image) return null
  return out
}

// Decode entitas HTML umum pada atribut meta (&#39; &amp; dst).
function decodeEntities(s) {
  return String(s || '')
    .replace(/&#x([0-9a-f]+);/gi, (_, h) => String.fromCodePoint(parseInt(h, 16)))
    .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(Number(d)))
    .replace(/&quot;/g, '"').replace(/&#0*39;/g, "'").replace(/&apos;/g, "'")
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&')
}

/** Upload buffer/URL ke server WhatsApp, kembalikan URL media yang bisa dipakai kartu. */
async function toWaUrl(sock, media, mediaType = 'image') {
  if (!media) throw new Error('media kosong')
  const input = Buffer.isBuffer(media) ? media : { url: media }
  const prepared = await prepareWAMessageMedia(
    { [mediaType]: input },
    { upload: sock.waUploadToServer, jid: '@newsletter' },
  )
  return Object.values(prepared)[0]?.url
}

/**
 * Selesaikan satu nilai media menjadi URL WhatsApp.
 * Menerima: URL http(s) (langsung pakai atau unduh dulu), buffer, atau base64.
 * Ini bagian "auto unduh": media yang dirujuk URL akan diunduh lalu di-upload
 * ke WhatsApp supaya bisa dirender di kartu.
 */
export async function resolveMedia(sock, media, mediaType = 'image') {
  if (media == null || media === '') return undefined
  if (Array.isArray(media)) {
    const out = []
    for (const m of media) out.push(await resolveMedia(sock, m, mediaType))
    return out.filter(Boolean)
  }
  if (typeof media === 'string' && isUrl(media)) {
    // URL http(s) biasa: coba pakai langsung (lebih cepat); kalau gagal upload.
    try {
      return await toWaUrl(sock, media, mediaType)
    } catch {
      const buf = await fetchBuffer(media)
      if (!buf.length) return undefined
      try { return await toWaUrl(sock, buf, mediaType) } catch { return undefined }
    }
  }
  let buf
  if (Buffer.isBuffer(media)) buf = media
  else if (typeof media === 'string') {
    try { buf = Buffer.from(media, 'base64') } catch { return undefined }
  } else return undefined
  if (!buf || !buf.length) return undefined
  try { return await toWaUrl(sock, buf, mediaType) } catch { return undefined }
}

/** Resize gambar via sharp (opsional, best-effort). */
export async function resizeImage(buffer, width = 300, height = 300) {
  try {
    const sharp = (await import('sharp')).default
    return await sharp(buffer)
      .resize(width, height, { fit: 'cover', position: 'center' })
      .png()
      .toBuffer()
  } catch {
    return buffer
  }
}

/** Ambil 1 frame video jadi PNG via ffmpeg (best-effort; kosong bila gagal). */
export function videoPreview(videoBuffer, { time = 1 } = {}) {
  return new Promise((resolve) => {
    try {
      if (!Buffer.isBuffer(videoBuffer) || !videoBuffer.length) return resolve(Buffer.alloc(0))
      const input = new Readable({ read() {} })
      input.push(videoBuffer)
      input.push(null)
      const out = new PassThrough()
      const chunks = []
      out.on('data', (c) => chunks.push(c))
      out.on('end', () => resolve(Buffer.concat(chunks)))
      out.on('error', () => resolve(Buffer.alloc(0)))
      const ff = spawn('ffmpeg', ['-ss', String(time), '-i', 'pipe:0', '-vframes', '1', '-vcodec', 'png', '-f', 'image2pipe', 'pipe:1'])
      ff.on('error', () => resolve(Buffer.alloc(0)))
      ff.stderr.on('data', () => {})
      input.pipe(ff.stdin)
      ff.stdout.pipe(out, { end: true })
    } catch {
      resolve(Buffer.alloc(0))
    }
  })
}

/** Durasi video (detik) dari atom mvhd MP4. 0 bila tidak diketahui. */
export function mp4Duration(buffer) {
  try {
    if (!Buffer.isBuffer(buffer) || buffer.length < 8) return 0
    let offset = 0
    while (offset < buffer.length - 8) {
      const size = buffer.readUInt32BE(offset)
      if (size < 8 || offset + size > buffer.length) return 0
      const type = buffer.toString('ascii', offset + 4, offset + 8)
      if (type === 'moov') {
        let mo = offset + 8
        const moEnd = offset + size
        while (mo < moEnd - 8) {
          const cs = buffer.readUInt32BE(mo)
          if (cs < 8 || mo + cs > moEnd) return 0
          const ct = buffer.toString('ascii', mo + 4, mo + 8)
          if (ct === 'mvhd') {
            const version = buffer.readUInt8(mo + 8)
            if (version === 0) {
              const ts = buffer.readUInt32BE(mo + 20)
              const dur = buffer.readUInt32BE(mo + 24)
              return ts ? dur / ts : 0
            }
            if (version === 1) {
              const ts = buffer.readUInt32BE(mo + 32)
              const dur = Number(buffer.readBigUInt64BE(mo + 36))
              return ts ? dur / ts : 0
            }
          }
          mo += cs
        }
      }
      offset += size
    }
  } catch {
    return 0
  }
  return 0
}

/** Ukuran PNG dari header IHDR (byte 16-23). {width, height} atau {} bila bukan PNG. */
export function pngSize(buffer) {
  try {
    if (!Buffer.isBuffer(buffer) || buffer.length < 24) return {}
    // Tanda tangan PNG: \x89PNG\r\n\x1a\n
    if (buffer.readUInt32BE(0) !== 0x89504e47) return {}
    return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) }
  } catch {
    return {}
  }
}

// ---------- section builders ----------

/** Bungkus primitive ke view_model layout (nama layout: Single/HScroll/ActionRow). */
export function newLayout(name, data, extra = {}) {
  // Terima alias tata letak (mis. 'Carousel' → 'HScroll') supaya nama kanonik
  // yang benar-benar dirender klien selalu yang dipancarkan.
  const canon = LAYOUT_ALIASES[String(name)] || String(name)
  return {
    ...extra,
    // `__typename` tingkat SECTION. Klien WhatsApp memakai ini untuk mengenali
    // section dengan benar; tanpanya, override warna pada primitive (mis.
    // GenAILabelPrimitive) DIABAIKAN dan jatuh ke tampilan default. Struktur ini
    // mengikuti contoh payload resmi (GenAIUnifiedResponseSection).
    __typename: 'GenAIUnifiedResponseSection',
    view_model: {
      [Array.isArray(data) ? 'primitives' : 'primitive']: data,
      __typename: `GenAI${canon}LayoutViewModel`,
    },
  }
}

// Alias nama tata letak → nama kanonik (yang terbukti dirender).
export const LAYOUT_ALIASES = {
  Carousel: 'HScroll',
  CarouselView: 'HScroll',
  HScrollView: 'HScroll',
  ActionRowLayout: 'ActionRow',
  Scroll: 'HScroll',
  Multi: 'MultipleResponse',
  GridLayout: 'Grid',
  VStack: 'VStack',
}

function textSection(text, { hyperlink = true, citation = true, latex = true } = {}) {
  const { text: extracted, inline_entities } = extractIE(text, { hyperlink, citation, latex })
  return newLayout('Single', {
    text: extracted,
    ...(inline_entities.length ? { inline_entities } : {}),
    __typename: 'GenAIMarkdownTextUXPrimitive',
  })
}

function codeSection(language, codeBlocks) {
  return newLayout('Single', {
    language,
    code_blocks: codeBlocks.map((b) => ({
      content: b.codeContent,
      type: typeof b.highlightType === 'number'
        ? ['DEFAULT', 'KEYWORD', 'METHOD', 'STRING', 'NUMBER', 'COMMENT'][b.highlightType] || 'DEFAULT'
        : b.highlightType,
    })),
    __typename: 'GenAICodeUXPrimitive',
  })
}

function tableSection(title, rows, { hyperlink = true, citation = true, latex = true } = {}) {
  const unified_rows = rows.map((r, i) => {
    const cells = (r.items || r.cells || []).map((c) => String(c ?? ''))
    const markdown_cells = cells.map((cell) => {
      const ex = extractIE(cell, { hyperlink, citation, latex })
      return { text: ex.text, ...(ex.inline_entities.length ? { inline_entities: ex.inline_entities } : {}) }
    })
    return {
      is_header: r.isHeading ?? r.is_header ?? i === 0,
      cells,
      ...(markdown_cells.some((c) => c.inline_entities?.length) ? { markdown_cells } : {}),
    }
  })
  return newLayout('Single', {
    title: title || '',
    rows: unified_rows,
    __typename: 'GenATableUXPrimitive',
  })
}

function imageSection({ url, width, height, status = 'READY', update_text, imagine_type = 'IMAGE' }) {
  return newLayout('Single', {
    media: { url, mime_type: 'image/png', width, height },
    imagine_type,
    status: { status, update_text },
    __typename: 'GenAIImaginePrimitive',
  })
}

/**
 * Video animasi via GenAIImaginePrimitive (`imagine_type: 'ANIMATE'`).
 * Untuk SATU video. Untuk GRID banyak video pakai `videoGridSection`
 * (GenAIVideoPrimitive di dalam GenAIGridLayoutViewModel).
 */
function videoSection({ url, thumbnail, width, height, status = 'READY', update_text }) {
  if (!url) return null
  return newLayout('Single', {
    media: {
      url,
      mime_type: 'video/mp4',
      ...(width ? { width: Number(width) } : {}),
      ...(height ? { height: Number(height) } : {}),
    },
    ...(thumbnail ? { thumbnail: { url: thumbnail } } : {}),
    imagine_type: 'ANIMATE',
    status: { status, update_text },
    __typename: 'GenAIImaginePrimitive',
  })
}

/**
 * Grid video — GenAIVideoPrimitive di dalam GenAIGridLayoutViewModel.
 *
 * Terverifikasi dari fork Baileys produksi `plogme` (`crysnovax/bailey`,
 * `rich-message-utils.js`:955-1000): WhatsApp memakai `GenAIVideoPrimitive`
 * per item untuk grid video, dengan `video_delivery_response` berisi
 * `progressive_urls` / `dash_manifests`. Bentuk ini ASLI (bukan karangan) —
 * dulu saya keliru menganggapnya tidak ada dan mengaliasnya ke Imagine.
 */
function videoGridSection(items) {
  const list = (Array.isArray(items) ? items : [items]).filter(Boolean)
  const prims = list.map((it) => {
    const url = it.reels_url || it.video_url || it.url || it.waUrl
    if (!url) return null
    const progressive = (Array.isArray(it.progressive_urls) ? it.progressive_urls : [])
      .map((v) => ({ progressive_url: typeof v === 'string' ? v : (v?.progressive_url ?? ''), __typename: 'GenAIProgressiveUrlResponse' }))
      .filter((v) => v.progressive_url)
    const dash = (Array.isArray(it.dash_manifests) ? it.dash_manifests : [])
      .map((v) => ({ manifest_xml: typeof v === 'string' ? v : (v?.manifest_xml ?? ''), __typename: 'GenAIDashManifestResponse' }))
      .filter((v) => v.manifest_xml)
    return {
      ...(it.post_id != null ? { post_id: String(it.post_id) } : {}),
      reels_url: url,
      ...(it.title || it.reels_title ? { reels_title: String(it.title ?? it.reels_title) } : {}),
      ...(it.thumbnail || it.thumbnail_url ? { thumbnail_url: it.thumbnail_url || it.thumbnail } : {}),
      ...(it.creator ? { creator: String(it.creator) } : {}),
      ...(it.avatar || it.avatar_url ? { avatar_url: it.avatar_url || it.avatar } : {}),
      ...(it.likes_count != null ? { likes_count: Number(it.likes_count) } : {}),
      ...(it.comments_count != null ? { comments_count: Number(it.comments_count) } : {}),
      ...(it.shares_count != null ? { shares_count: Number(it.shares_count) } : {}),
      ...(it.is_verified != null ? { is_verified: Boolean(it.is_verified) } : {}),
      ...(it.source_app || it.source ? { source_app: it.source_app || it.source } : {}),
      video_delivery_response: {
        ...(progressive.length ? { progressive_urls: progressive } : {}),
        ...(dash.length ? { dash_manifests: dash } : {}),
        __typename: 'GenAIVideoDeliveryResponse',
      },
      __typename: 'GenAIVideoPrimitive',
    }
  }).filter(Boolean)
  if (!prims.length) return null
  return newLayout(prims.length === 1 ? 'Single' : 'Grid', prims.length === 1 ? prims[0] : prims)
}

// Normalisasi satu sumber ke bentuk yang dipakai WhatsApp.
// Mengikuti MessageBuilder.addSources(): source_type THIRD_PARTY, favicon 16×16.
function normSource(s) {
  return {
    source_type: s.source_type || 'THIRD_PARTY',
    source_display_name: s.title || s.displayName || s.display_name || s.url || '',
    source_subtitle: s.subtitle || s.site || s.domain || '',
    source_url: s.url || '',
    favicon: {
      url: s.icon || s.favicon || '',
      mime_type: 'image/jpeg',
      width: 16,
      height: 16,
    },
  }
}

function sourcesSection(sources) {
  const norm = (sources || []).map(normSource)
  return newLayout('Single', {
    sources: norm,
    search_engine: 'MASE',
    __typename: 'GenAISearchResultPrimitive',
  })
}

/**
 * Panel "Sources" yang muncul saat chip sitasi di-tap: `embedded_screens`
 * berisi satu tab `FOAEmbeddedScreenContentTabbed` dengan daftar sumber
 * (`GenAISearchResultPrimitive`). Struktur ini mengikuti kartu sumber yang
 * TERBUKTI jalan (contoh produksi Meta AI).
 */
export function buildEmbeddedSources(sources) {
  const norm = (sources || []).map(normSource).filter((s) => s.source_url)
  if (!norm.length) return []
  return [
    {
      secondary_screen_id: 1,
      content: [
        {
          __typename: 'FOAEmbeddedScreenContentTabbed',
          tabs: [
            {
              sections: [
                {
                  __typename: 'GenAIUnifiedResponseSection',
                  view_model: {
                    __typename: 'GenAISingleLayoutViewModel',
                    primitive: {
                      __typename: 'GenAISearchResultPrimitive',
                      sources: norm,
                      search_engine: 'MASE',
                    },
                  },
                },
              ],
              tab_header: 'Sources',
              id: 'sources',
            },
          ],
        },
      ],
    },
  ]
}

function suggestSection(prompts, { scroll = true } = {}) {
  const list = (Array.isArray(prompts) ? prompts : [prompts]).filter(Boolean).map((t) => ({
    prompt_text: String(t),
    prompt_type: 'SUGGESTED_PROMPT',
    __typename: 'GenAIFollowUpSuggestionPillPrimitive',
  }))
  const name = list.length === 1 ? 'Single' : scroll ? 'HScroll' : 'ActionRow'
  return newLayout(name, name === 'Single' ? list[0] : list)
}

function footerActionSection(actions) {
  const list = (actions || []).map((a) => ({
    cta_text: a.text || a.cta_text || '',
    cta_type: a.type || a.cta_type || 'OPEN_URL',
    cta_url: a.url || a.cta_url || '',
    footer_action_type: String(a.footer_action_type || a.action_type || a.type || 'OPEN_URL').toUpperCase(),
    __typename: 'GenAIFooterActionPrimitive',
  }))
  // plogme:569-572 (fork produksi) menaruh footer action di dalam
  // `GenAIActionRowLayoutViewModel` — bukan HScroll. Satu aksi → Single.
  const name = list.length === 1 ? 'Single' : 'ActionRow'
  return newLayout(name, name === 'Single' ? list[0] : list)
}

/**
 * Tombol balasan cepat — GenAIQuickReplyPrimitive.
 *
 * Berbeda dari `wa:suggest` (pill saran lanjutan) dan `wa:footer` (CTA buka URL):
 * ini tombol yang MENGIRIM TEKS balasan saat ditekan (seperti tombol template).
 * `reply_text` = teks yang dikirim; `label` = yang tampil.
 */
function quickReplySection(items) {
  const list = (Array.isArray(items) ? items : [items]).filter(Boolean).map((q) => {
    const label = String(typeof q === 'string' ? q : (q.label ?? q.text ?? q.title ?? '')).trim()
    const reply = String(typeof q === 'string' ? q : (q.reply_text ?? q.reply ?? q.id ?? label)).trim()
    return {
      label,
      reply_text: reply || label,
      quick_reply_type: String((q && q.quick_reply_type) || (q && q.type) || 'TEXT').toUpperCase(),
      __typename: 'GenAIQuickReplyPrimitive',
    }
  }).filter((q) => q.label)
  if (!list.length) return null
  const name = list.length === 1 ? 'Single' : 'ActionRow'
  return newLayout(name, name === 'Single' ? list[0] : list)
}

// ---------- registri __typename: kanonik + alias --------------------------
//
// PENTING (dari uji klien nyata): `__typename` yang TIDAK dikenal klien akan
// GAGAL DIAM-DIAM — section-nya tidak dirender sama sekali. Karena itu kita
// TIDAK pernah mengarang nama. Yang kita lakukan: menerima nama "alias"/
// versi-lama yang beredar di spec, lalu MEMETAKANNYA ke nama kanonik yang
// TERBUKTI dirender.
//
// Referensi kanonik: `lib/Utils/rich-message-utils.js` (nix408) + kartu
// produksi Meta AI yang sudah terbukti.
export const TYPENAME_ALIASES = {
  // Varian akhiran yang salah → nama kanonik.
  GenAISearchResultV2Primitive: 'GenAISearchResultPrimitive',
  GenAICodeBlockPrimitive: 'GenAICodeUXPrimitive',
  GenAITablePrimitive: 'GenATableUXPrimitive',
  GenAICarouselViewModel: 'GenAIHScrollLayoutViewModel',
  GenAIHScrollLayoutViewMo: 'GenAIHScrollLayoutViewModel',
  // Nama yang TIDAK ADA di skema mana pun → diganti primitif asli yang dirender.
  // (GenAIVideoPrimitive TIDAK di sini: ia NYATA — lihat videoGridSection.)
  GenAIMapPrimitive: 'GenAISingleLayoutViewModel',      // peta = pesan `location` asli
  GenAIAudioPrimitive: 'GenAISingleLayoutViewModel',    // audio = pesan `audio` asli
  GenAISearchResultV2: 'GenAISearchResultPrimitive',
  GenAIMarkdownTextPrimitive: 'GenAIMarkdownTextUXPrimitive',
  GenAICodePrimitive: 'GenAICodeUXPrimitive',
  GenAIVideoUXPrimitive: 'GenAIVideoPrimitive',         // varian nama → primitif asli
}

/** Nama kanonik untuk sebuah `__typename` (alias → kanonik; lain → apa adanya). */
export function normalizeTypename(name) {
  const n = String(name ?? '')
  return TYPENAME_ALIASES[n] || n
}

/** True bila `name` (alias atau kanonik) punya primitif yang benar-benar dirender. */
export function isKnownTypename(name) {
  const n = normalizeTypename(name)
  return SUPPORTED_TYPENAMES.has(n)
}

// Semua `__typename` yang BENAR-BENAR kita pancarkan & terverifikasi dirender.
export const SUPPORTED_TYPENAMES = new Set([
  // tata letak (layout view model)
  'GenAISingleLayoutViewModel', 'GenAIHScrollLayoutViewModel',
  'GenAIActionRowLayoutViewModel', 'GenAIMultipleResponseLayoutViewModel',
  'GenAIIGSuggestedBloomCardLayoutViewModel',
  // tata letak dari skema otoritatif whatsmeow
  'GenAIGridLayoutViewModel', 'GenAIVStackLayoutViewModel',
  // teks & konten
  'GenAIMarkdownTextUXPrimitive', 'GenAICodeUXPrimitive', 'GenATableUXPrimitive',
  'GenAIMetadataTextPrimitive', 'GenAILatexUXPrimitive', 'GenAILatexItem',
  'GenAISearchCitationItem', 'GenAIInlineLinkItem', 'GenAISearchResultPrimitive',
  'GenAIDividerPrimitive', 'GenAISpacerPrimitive', 'GenAILabelPrimitive',
  // media
  'GenAIImagePrimitive', 'GenAIImaginePrimitive', 'GenAIProductItemCardPrimitive',
  'GenAIPostPrimitive', 'GenAIReelPrimitive',
  // video grid (whatsmeow/plogme: GenAIVideoPrimitive di GenAIGridLayoutViewModel)
  'GenAIVideoPrimitive', 'GenAIVideoDeliveryResponse',
  'GenAIProgressiveUrlResponse', 'GenAIDashManifestResponse',
  // aksi
  'GenAIFollowUpSuggestionPillPrimitive', 'GenAIFooterActionPrimitive',
  'GenAIQuickReplyPrimitive',
  // status
  'GenAIBotThinkingStatusPrimitive', 'GenAIBotProgressStatusPrimitive',
  'GenAITaskPrimitive',
  // lain-lain
  'GenAICompactEntityPrimitive',
  // widget pihak ketiga (3P) — sub-struktur ikut dipancarkan
  'GenAI3PExtWidgetPrimitive', 'GenAI3PExtWidgetStandardHeader',
  'GenAI3PExtCalendarEventList', 'GenAI3PExtWidgetCTA', 'GenAI3PExtWidgetToast',
  'GenAIMetaSubsQuotaUpsellPrimitive', 'GenAIMetaSubsQuotaUpsellButton',
  'GenAIaeacdsnwHtmlPrimitive',
  // FOA (jalur Meta sendiri)
  'FOATextPrimitive', 'FOABloksPrimitive', 'FOAEmbeddedScreenContentTabbed',
  'FOAIDNixelButtonSheets',
  // pembungkus
  'GenAIUnifiedResponseSection', 'GenAIUnifiedResponse',
])

function compactSection(data = {}) {
  // Struktur ini mengikuti kartu yang TERBUKTI jalan (entity card + gambar):
  //   GenAICompactEntityPrimitive dengan `image: {url, mime_type}` dan enum
  //   bertipe STRING (`entity_type: "PAGE"`, `action_type: "VIEW"`).
  // plogme:420-432 (fork produksi) memakai `image: {url, mime_type:'image/png'}`.
  // Bila `data.image` berupa string, dibungkus jadi objek {url} agar sah.
  const img = data.image
  const image = img && typeof img === 'object'
    ? { url: img.url ?? '', mime_type: img.mime_type ?? 'image/png', ...(img.url_fallback ? { url_fallback: img.url_fallback } : {}) }
    : { url: String(img ?? ''), mime_type: 'image/png' }
  return newLayout('Single', {
    __typename: 'GenAICompactEntityPrimitive',
    title: String(data.title ?? ''),
    subtitle: String(data.subtitle ?? ''),
    secondary_subtitle: String(data.secondary_subtitle ?? ''),
    image,
    entity_id: data.entity_id ?? '',
    entity_url: String(data.entity_url ?? ''),
    entity_type: data.entity_type ?? 'PAGE',
    action_type: data.action_type ?? 'VIEW',
    is_verified: Boolean(data.is_verified),
  })
}

function widget3pSection(items) {
  const list = (Array.isArray(items) ? items : [items]).map((item) => ({
    __typename: 'GenAI3PExtWidgetPrimitive',
    header: { __typename: 'GenAI3PExtWidgetStandardHeader', title: item.title ?? '', ...(item.header || {}) },
    body: {
      __typename: 'GenAI3PExtCalendarEventList',
      sections: item.sections ?? [],
      ctas: (item.actions || []).map((a) => ({
        __typename: 'GenAI3PExtWidgetCTA',
        label: a.label ?? '',
        state: a.state ?? 'PENDING',
        kind: a.kind ?? 'OTHER',
        tool_call_id: a.tool_call_id ?? a.id ?? '',
        ...(a.toast ? { toast: { __typename: 'GenAI3PExtWidgetToast', label: a.toast.label ?? a.label ?? '' } } : {}),
      })),
      ...(item.body || {}),
    },
  }))
  const name = list.length === 1 ? 'Single' : 'HScroll'
  return newLayout(name, name === 'Single' ? list[0] : list)
}

// Teks metadata kecil (label/catatan) — GenAIMetadataTextPrimitive.
function metadataSection(text, { tip = false } = {}) {
  return newLayout('Single', {
    text: (tip ? 'ⓘ ' : '') + String(text ?? ''),
    __typename: 'GenAIMetadataTextPrimitive',
  })
}

// Kartu produk (belanja) — GenAIProductItemCardPrimitive.
// Skema otoritatif (whatsmeow) hanya memodelkan `{ __typename, title }`; field
// lain (brand/price/image/…) tak dijamin dibaca klien. `image` hanya dikirim
// bila ADA URL — jangan kirim `{url:''}` (bisa memicu slot gambar kosong).
function productSection(items) {
  const list = (Array.isArray(items) ? items : [items]).filter(Boolean).map((it) => {
    const img = it.image_url ?? it.image ?? it.waUrl ?? ''
    return {
      __typename: 'GenAIProductItemCardPrimitive',
      title: it.title ?? '',
      brand: it.brand ?? '',
      price: it.price ?? '',
      sale_price: it.sale_price ?? '',
      product_url: it.product_url ?? it.url ?? '',
      ...(img ? { image: { url: img } } : {}),
      additional_images: (Array.isArray(it.additional_images) ? it.additional_images : [])
        .map((x) => ({ url: typeof x === 'string' ? x : (x?.url ?? '') }))
        .filter((x) => x.url),
    }
  })
  if (!list.length) return null
  const name = list.length === 1 ? 'Single' : 'HScroll'
  return newLayout(name, name === 'Single' ? list[0] : list)
}

// Kartu postingan sosial (IG/TikTok) — GenAIPostPrimitive.
function postSection(items) {
  const list = (Array.isArray(items) ? items : [items]).filter(Boolean).map((p) => ({
    __typename: 'GenAIPostPrimitive',
    title: p.title ?? '',
    subtitle: p.subtitle ?? '',
    username: p.username ?? '',
    profile_picture_url: p.profile_picture_url ?? p.profile_url ?? p.profile ?? '',
    is_verified: Boolean(p.is_verified ?? p.verified),
    thumbnail_url: p.thumbnail_url ?? p.thumbnail ?? '',
    post_caption: p.post_caption ?? p.caption ?? '',
    likes_count: p.likes_count ?? p.like ?? 0,
    comments_count: p.comments_count ?? p.comment ?? 0,
    shares_count: p.shares_count ?? p.share ?? 0,
    post_url: p.post_url ?? p.url ?? '',
    source_app: p.source_app || p.source || 'INSTAGRAM',
    footer_label: p.footer_label ?? p.footer ?? '',
    orientation: p.orientation ?? 'LANDSCAPE',
    post_type: p.post_type ?? 'VIDEO',
  }))
  if (!list.length) return null
  return newLayout(list.length === 1 ? 'Single' : 'HScroll', list.length === 1 ? list[0] : list)
}

// Reel / video pendek — GenAIReelPrimitive.
function reelSection(items) {
  const list = (Array.isArray(items) ? items : [items]).filter(Boolean).map((r) => ({
    __typename: 'GenAIReelPrimitive',
    thumbnail_url: r.thumbnail_url ?? r.thumbnail ?? '',
    video_url: r.video_url ?? r.video ?? r.url ?? '',
    title: r.title ?? '',
    username: r.username ?? '',
    likes_count: r.likes_count ?? 0,
    comments_count: r.comments_count ?? 0,
    post_url: r.post_url ?? r.url ?? '',
    source_app: r.source_app || r.source || 'INSTAGRAM',
  }))
  if (!list.length) return null
  return newLayout(list.length === 1 ? 'Single' : 'HScroll', list.length === 1 ? list[0] : list)
}

// Korsel gambar — banyak gambar dalam SATU kartu (HScroll, bukan kartu
// terpisah). Setiap slide = GenAIImagePrimitive.
function carouselSection(items) {
  const list = (Array.isArray(items) ? items : [items]).filter(Boolean)
  const prims = list.map((it) => ({
    full_image: {
      url: it.waUrl || it.url || it.media,
      ...(it.width ? { width: Number(it.width) } : {}),
      ...(it.height ? { height: Number(it.height) } : {}),
    },
    ...(it.waUrl && it.url && it.waUrl !== it.url ? { preview_image: { url: it.url } } : {}),
    ...(it.caption ? { alt_text: String(it.caption) } : {}),
    __typename: 'GenAIImagePrimitive',
  })).filter((p) => p.full_image.url)
  if (!prims.length) return null
  return newLayout(prims.length === 1 ? 'Single' : 'HScroll', prims.length === 1 ? prims[0] : prims)
}

// ---------- matematika / LaTeX ----------
//
// WhatsApp tidak merender LaTeX sendiri: rumus harus dijadikan GAMBAR lebih
// dulu, lalu gambar itu ditempel sebagai inline entity `GenAILatexItem`
// (teks `{{KEY}}…{{/KEY}}` + metadata `latex_expression` + `latex_image.url`).
// Kita render lewat CodeCogs (LaTeX → PNG publik), lalu unggah ke WhatsApp
// supaya pasti bisa diambil perender.

/** URL PNG hasil render LaTeX (CodeCogs). `display` = rumus blok (lebih besar). */
export function latexRenderUrl(expr, { display = false, dpi = 200 } = {}) {
  const body = String(expr ?? '').trim()
  if (!body) return ''
  // \bg_white = latar putih + teks hitam → terbaca di kartu terang maupun gelap.
  const tex = `\\bg_white \\dpi{${dpi}} ${display ? '\\displaystyle ' : ''}${body}`
  return `https://latex.codecogs.com/png.image?${encodeURIComponent(tex)}`
}

/** Section matematika: teks Markdown + inline entity GenAILatexItem. */
function mathSection(items) {
  const list = (Array.isArray(items) ? items : [items]).filter(Boolean)
  const entities = []
  const parts = []
  list.forEach((it, i) => {
    const expr = String(it.expr ?? it.latex ?? it.formula ?? '').trim()
    const url = it.waUrl || it.url
    if (!expr || !url) return
    const key = `KWEE_MATH_${i}`
    const display = it.display !== false
    // Rumus blok berdiri sendiri di barisnya; inline menyatu dengan teks.
    parts.push(display ? `\n{{${key}}}${expr}{{/${key}}}\n` : `{{${key}}}${expr}{{/${key}}}`)
    entities.push({
      key,
      metadata: {
        latex_expression: expr,
        latex_image: {
          url,
          width: Number(it.width) || 200,
          height: Number(it.height) || 80,
        },
        font_height: Number(it.font_height) || 83.333333333333,
        padding: Number(it.padding) || 15,
        __typename: 'GenAILatexItem',
      },
    })
  })
  if (!entities.length) return null
  return newLayout('Single', {
    text: parts.join(' '),
    inline_entities: entities,
    __typename: 'GenAIMarkdownTextUXPrimitive',
  })
}

// ---------- primitif tambahan (divider/spacer/status/image/latex-ux) ----------

/** Garis pemisah — GenAIDividerPrimitive (`style`: HORIZONTAL_LINE | DOT). */
function dividerSection({ style } = {}) {
  const s = String(style || 'HORIZONTAL_LINE').toUpperCase()
  const val = (s === 'DOT' || s === 'DOTS') ? 'DOT' : 'HORIZONTAL_LINE'
  return newLayout('Single', {
    // Field OTORITATIF dari skema WhatsApp (whatsmeow richresponse/primitive.go:
    // `GenAIDividerPrimitive { __typename, type }`). `divider_style` TIDAK ada
    // di skema mana pun — dipertahankan hanya sebagai alias lama agar tak
    // merusak klien yang mungkin masih membacanya (nilainya sama, jadi aman).
    type: val,
    divider_style: val,
    __typename: 'GenAIDividerPrimitive',
  })
}

/**
 * Spacer kosong — GenAISpacerPrimitive.
 * Skema otoritatif (whatsmeow): hanya `{ __typename, spacing }`. Field `height`
 * DULU ikut dikirim padahal tak ada di skema → dibuang.
 */
function spacerSection({ spacing, height } = {}) {
  const value = spacing ?? height
  return newLayout('Single', {
    ...(value ? { spacing: Number(value) } : {}),
    __typename: 'GenAISpacerPrimitive',
  })
}

/**
 * Banner label — GenAILabelPrimitive (Fitur #31 / #32).
 *
 * Kotak berwarna (header/footer) yang tampil di SETIAP kartu AI Rich sebagai
 * identitas bot.
 *
 * PENTING (dari uji nyata): warna override HANYA dipatuhi bila struktur section
 * memakai `__typename: 'GenAIUnifiedResponseSection'` (lihat `newLayout`) dan
 * root memakai `'GenAIUnifiedResponse'`. Sebelum itu, override diabaikan dan
 * label jatuh ke warna default `variant` (hijau gelap). Field yang benar persis
 * seperti contoh resmi: `background_color_override` + `text_color_override`,
 * format `#RRGGBBAA` (alpha di BELAKANG). JANGAN menambah alias `background_color`
 * — di klien nyata field itu justru mewarnai TEKS, bukan latar.
 *
 * `detail`/`icon` opsional untuk label lebih kaya; `text` tetap wajib.
 */
export function labelSection({ text, detail, icon, variant = 'POSITIVE', background, textColor, bg, fg } = {}) {
  const t = String(text ?? '').trim()
  if (!t) return null
  const bgc = background || bg
  const fgc = textColor || fg
  const prim = {
    text: t,
    variant: String(variant || 'POSITIVE').toUpperCase(),
    __typename: 'GenAILabelPrimitive',
  }
  if (detail) prim.subtitle = String(detail)
  if (icon) prim.icon = String(icon)
  if (bgc) prim.background_color_override = String(bgc)   // #RRGGBBAA
  if (fgc) prim.text_color_override = String(fgc)
  return newLayout('Single', prim)
}

/**
 * Formula LaTeX sebagai blok tersendiri — GenAILatexUXPrimitive.
 * Berbeda dari rumus inline (`GenAILatexItem` di dalam teks), ini primitive
 * yang berdiri sendiri sebagai satu section (rumus blok / display).
 */
function latexUxSection({ expr, waUrl, url, width, height, font_height, padding }) {
  const image = waUrl || url
  if (!image) return null
  return newLayout('Single', {
    latex_expression: String(expr ?? ''),
    latex_image: {
      url: image,
      width: Number(width) || 200,
      height: Number(height) || 80,
    },
    font_height: Number(font_height) || 83.333333333333,
    padding: Number(padding) || 15,
    __typename: 'GenAILatexUXPrimitive',
  })
}

/** Status "thinking" bot — GenAIBotThinkingStatusPrimitive. */
function thinkingSection({ title = 'Berpikir…', thought_duration_sec, meta_search_apps } = {}) {
  return newLayout('Single', {
    title: String(title ?? ''),
    ...(thought_duration_sec != null ? { thought_duration_sec: Number(thought_duration_sec) } : {}),
    ...(Array.isArray(meta_search_apps) ? { meta_search_apps } : {}),
    __typename: 'GenAIBotThinkingStatusPrimitive',
  })
}

/** Status progress bot — GenAIBotProgressStatusPrimitive. */
function progressSection({ title = 'Memproses…', is_in_progress = true } = {}) {
  return newLayout('Single', {
    title: String(title ?? ''),
    is_in_progress: Boolean(is_in_progress),
    __typename: 'GenAIBotProgressStatusPrimitive',
  })
}

/**
 * Gambar "penuh" (bukan imagine) — GenAIImagePrimitive.
 * `full_image` untuk tampilan besar, `preview_image` untuk thumbnail.
 */
function fullImageSection({ fullUrl, previewUrl, width, height, alt } = {}) {
  const full = fullUrl
  if (!full) return null
  return newLayout('Single', {
    full_image: { url: full, width: Number(width) || undefined, height: Number(height) || undefined },
    ...(previewUrl ? { preview_image: { url: previewUrl } } : {}),
    ...(alt ? { alt_text: String(alt) } : {}),
    __typename: 'GenAIImagePrimitive',
  })
}

/**
 * Kartu saran IG — GenAIIGSuggestedBloomCardLayoutViewModel.
 * Layout (bukan primitive): satu kartu saran bergaya Instagram.
 */
function igSuggestedSection(cards) {
  const list = (Array.isArray(cards) ? cards : [cards]).filter(Boolean).map((c) => ({
    title: c.title ?? '',
    subtitle: c.subtitle ?? '',
    ...(c.image_url || c.image ? { image_url: c.image_url || c.image } : {}),
    ...(c.url ? { url: c.url } : {}),
    ...(c.username ? { username: c.username } : {}),
  }))
  if (!list.length) return null
  return {
    view_model: {
      primitives: list,
      __typename: 'GenAIIGSuggestedBloomCardLayoutViewModel',
    },
  }
}

/**
 * Layout multi-respons — GenAIMultipleResponseLayoutViewModel.
 * Beberapa section (respons alternatif) dibungkus satu view_model.
 */
function multipleResponseSection(sections) {
  const list = (Array.isArray(sections) ? sections : [sections]).filter(Boolean)
  if (!list.length) return null
  return {
    view_model: {
      primitives: list.map((s) => (s.view_model ? s.view_model.primitive ?? s.view_model.primitives : s)),
      __typename: 'GenAIMultipleResponseLayoutViewModel',
    },
  }
}

/**
 * Kartu upsell langganan Meta — GenAIMetaSubsQuotaUpsellPrimitive
 * (+ tombol GenAIMetaSubsQuotaUpsellButton).
 */
function metaUpsellSection({ title, subtitle, button } = {}) {
  return newLayout('Single', {
    title: String(title ?? ''),
    subtitle: String(subtitle ?? ''),
    ...(button ? {
      button: {
        label: button.label ?? button.text ?? '',
        ...(button.url ? { url: button.url } : {}),
        __typename: 'GenAIMetaSubsQuotaUpsellButton',
      },
    } : {}),
    __typename: 'GenAIMetaSubsQuotaUpsellPrimitive',
  })
}

/**
 * Kartu tugas — GenAITaskPrimitive (task_id, title, subtitle, status).
 * Cocok untuk menampilkan progres pekerjaan berlangkah.
 */
function taskSection({ task_id, title, subtitle, status = 'IN_PROGRESS', steps } = {}) {
  return newLayout('Single', {
    task_id: String(task_id ?? `task-${Date.now()}`),
    title: String(title ?? ''),
    subtitle: String(subtitle ?? ''),
    status: String(status ?? 'IN_PROGRESS'),
    ...(Array.isArray(steps) ? { steps: steps.map((s) => ({
      title: String(s.title ?? s ?? ''),
      status: String(s.status ?? 'PENDING'),
    })) } : {}),
    __typename: 'GenAITaskPrimitive',
  })
}

/** Teks dari FOA — FOATextPrimitive (teks biasa di jalur FOA). */
function foaTextSection(text) {
  return newLayout('Single', {
    text: String(text ?? ''),
    __typename: 'FOATextPrimitive',
  })
}

/**
 * Layar Bloks (A2UI) dari server Meta — FOABloksPrimitive.
 * `payload` = JSON string permukaan A2UI (createSurface).
 * Skema otoritatif mewajibkan `type` (non-omitempty) — dikirim, default
 * `im_a2ui` (sama seperti `bloksWidgetMessage` di rich.js).
 */
function bloksSection({ payload, surface, uuid, type } = {}) {
  const data = typeof payload === 'string'
    ? payload
    : JSON.stringify(payload ?? surface ?? {})
  return newLayout('Single', {
    __typename: 'FOABloksPrimitive',
    type: String(type || 'im_a2ui'),
    ...(uuid ? { uuid: String(uuid) } : {}),
    data,
  })
}

/**
 * Dokumen HTML utuh untuk WebView Android — GenAIaeacdsnwHtmlPrimitive.
 * (FOAHtmlPrimitive = kelas Kotlin; di payload JSON pakai aeacdsnw.)
 */
function htmlSection({ html, url = 'https://example.com', trusted_sources = [], title, tab_header = 'Web' } = {}) {
  if (!String(html ?? '').trim()) return null
  const section = newLayout('Single', {
    __typename: 'GenAIaeacdsnwHtmlPrimitive',
    payload: String(html),
    url: String(url),
    trusted_sources: (trusted_sources || []).map((s) => String(s)),
  })
  // HTML butuh layar tertanam (embedded_screen) agar bisa dibuka penuh.
  section.__embedded = {
    title: String(title ?? 'HTML'),
    content: [{
      __typename: 'FOAEmbeddedScreenContentTabbed',
      tabs: [{
        id: 'tab_html', tab_header: String(tab_header),
        sections: [{ __typename: 'GenAIUnifiedResponseSection', view_model: section.view_model }],
        step_entries: [],
      }],
    }],
  }
  return section
}

// --- matematika inline di dalam teks ($x^2$, $$\int$$) ---------------------
//
// Model menulis rumus inline dengan `$…$` dan blok dengan `$$…$$` (atau baris
// `\[…\]`). WhatsApp tidak merender itu, jadi tiap rumus dirender jadi gambar
// lalu ditempel sebagai inline entity `GenAILatexItem` di dalam teks.

// Pola rumus yang dikenali. Model sering memakai pembatas LaTeX asli
// (`\[…\]` untuk blok, `\(…\)` untuk inline) SELAIN `$…$`/`$$…$$` — kalau
// `\(…\)` tidak ditangkap, rumus tampil sebagai teks mentah di kartu.
const _MATH_RE = /\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$([^$\n]+?)\$/g

/** True bila teks memuat penanda rumus yang bisa dirender. */
export function hasMath(text) {
  // Regex `g` bersifat stateful (lastIndex) → reset dulu, atau pakai regex baru.
  _MATH_RE.lastIndex = 0
  return _MATH_RE.test(String(text ?? ''))
}

/**
 * Ekstrak rumus dari teks: kembalikan {text, math:[{key,expr,display}]}.
 * `text` memakai placeholder `{{KEY}}expr{{/KEY}}` agar posisi inline terjaga.
 */
export function extractMath(text) {
  const s = String(text ?? '')
  const math = []
  let idx = 0
  // Grup: 1=$$…$$ (blok), 2=\[…\] (blok), 3=\(…\) (inline), 4=$…$ (inline)
  const out = s.replace(_MATH_RE, (_m, display_expr, bracket_expr, paren_expr, inline_expr) => {
    const expr = String(display_expr ?? bracket_expr ?? paren_expr ?? inline_expr ?? '').trim()
    if (!expr) return _m
    const display = display_expr != null || bracket_expr != null
    const key = `KWEE_MATH_${idx++}`
    math.push({ key, expr, display })
    return `{{${key}}}${expr}{{/${key}}}`
  })
  return { text: out, math }
}

// ---------- perakit kartu ----------

// Ubah daftar blok (dari agent/format.py) menjadi {submessages, sections}.
// text/code/table 1:1; primitif kaya hanya punya section (sumber render).
export function buildCardParts(blocks) {
  const submessages = []
  const sections = []
  const allSources = []
  const allEmbedded = []

  // Lookup URL → sumber, supaya chip sitasi di dalam teks bisa memakai judul
  // & favicon dari blok `wa:sources` (bukan URL mentah).
  const sourceLookup = {}
  for (const b of blocks || []) {
    if (b && b.type === 'sources') {
      for (const s of (b.sources || [])) {
        if (s && s.url) sourceLookup[s.url] = s
      }
    }
  }

  const push = (section, submessage) => {
    if (section) sections.push(section)
    if (submessage) submessages.push(submessage)
  }

  // "Aurora Editorial": headings → high-contrast labels (with "01 / 04" ordinals
  // in long articles), `> [!NOTE]` style callouts → tinted labels, rules →
  // dividers with breathing room. Body stays native GenAI Markdown so links,
  // citations, and inline formatting remain interactive.
  const pushBody = (text, { segmentMath = [], messageText = text } = {}) => {
    const { text: extracted, inline_entities } = extractIE(text, { sourceLookup })
    const allIE = [...inline_entities, ...segmentMath]
    push(
      newLayout('Single', {
        text: extracted,
        ...(allIE.length ? { inline_entities: allIE } : {}),
        __typename: 'GenAIMarkdownTextUXPrimitive',
      }),
      { messageType: T.TEXT, messageText,
        ...(allIE.length ? { inlineEntities: allIE } : {}) },
    )
  }

  const pushEditorialText = (raw, { sourceText = raw, mathEntities = [] } = {}) => {
    const segments = segmentMarkdown(polishMarkdown(raw))
    const total = countSections(segments)
    let h2 = 0
    let prevType = ''
    for (const segment of segments) {
      const segmentMath = mathEntities.filter((entity) => segment.text?.includes(`{{${entity.key}}}`))
      if (segment.type === 'divider' && !segmentMath.length) {
        push(dividerSection({ style: 'HORIZONTAL_LINE' }))
        prevType = 'divider'
        continue
      }
      if (segment.type === 'callout' && !segmentMath.length) {
        const spec = CALLOUTS[segment.kind] || CALLOUTS.NOTE
        const title = plainHeading(segment.title) || spec.title
        push(labelSection({
          text: title, variant: 'POSITIVE', icon: spec.icon,
          background: spec.background, textColor: spec.textColor,
        }))
        if (segment.text) pushBody(segment.text)
        prevType = 'callout'
        continue
      }
      if (segment.type === 'heading' && !segmentMath.length) {
        const label = plainHeading(segment.text)
        if (!label) continue
        if (segment.level >= 4) {
          // H4–H6: a bold lead-in line, not a full label (keeps long answers calm).
          pushBody(`**${label}**`)
          prevType = 'text'
          continue
        }
        if (segment.level === 2) h2 += 1
        // Breathing room before a new section (never at the very start).
        if (sections.length && segment.level <= 2 && prevType !== 'divider') {
          push(spacerSection({ spacing: 6 }))
        }
        // Aurora "Reading Map" (matikan dengan KWEE_READING_MAP=0).
        const meta = segment.level === 1 && process.env.KWEE_READING_MAP !== '0'
          ? readingMap(raw, total) : ''
        const style = headingStyle(segment.level, { index: segment.level === 2 ? h2 : 0, total, text: label, meta })
        push(labelSection({ text: label, variant: 'POSITIVE', ...style }))
        prevType = 'heading'
        continue
      }
      const messageText = mathEntities.length && segments.length === 1 ? sourceText : segment.text
      pushBody(segment.text, { segmentMath, messageText })
      prevType = 'text'
    }
  }

  for (const b of blocks || []) {
    const t = b?.type
    if (t === 'text') {
      const text = String(b.text ?? '').trim()
      if (!text) continue
      // Rumus inline `$x^2$` di dalam teks → inline entity GenAILatexItem.
      // Media (gambar rumus) sudah diselesaikan lebih dulu oleh
      // resolveBlockMedia, yang menyisipkan `math` pada blok.
      const { text: noMath, math } = extractMath(text)
      const latexIE = (b._mathEntities || [])
      pushEditorialText(noMath, { sourceText: text, mathEntities: latexIE })
      void math
    } else if (t === 'code') {
      const code = String(b.code ?? '')
      if (!code.trim()) continue
      const lang = b.language || 'text'
      const codeBlocks = tokenizeCodeSafe(code, lang)
      // Quiet caption ("⌘ python · 14 baris") — terminal-style context line.
      const caption = codeCaption(lang, code)
      if (caption) push(metadataSection(caption, { tip: false }))
      push(codeSection(lang, codeBlocks), {
        messageType: T.CODE,
        codeMetadata: { codeLanguage: lang, codeBlocks },
      })
    } else if (t === 'table') {
      const rows = (b.rows || []).map((items, i) => ({
        isHeading: i === 0,
        items: (Array.isArray(items) ? items : [items]).map((x) => String(x ?? '')),
      })).filter((r) => r.items.length)
      if (!rows.length) continue
      if (tableNeedsStacking(rows.map((row) => row.items))) {
        const stacked = tableToStackedMarkdown(rows.map((row) => row.items))
        if (b.title) push(metadataSection(String(b.title), { tip: false }))
        pushEditorialText(stacked)
      } else {
        push(tableSection(b.title || '', rows), {
          messageType: T.TABLE,
          tableMetadata: { title: String(b.title || ''), rows },
        })
      }
    } else if (t === 'image') {
      const url = b.waUrl || b.url
      if (!url) continue
      push(imageSection({ url, width: b.width, height: b.height, status: b.status, update_text: b.update_text, imagine_type: b.imagine_type || 'IMAGE' }), {
        messageType: T.GRID_IMAGE,
        gridImageMetadata: { gridImageUrl: { imagePreviewUrl: url }, imageUrls: [{ imagePreviewUrl: url, imageHighResUrl: url, sourceUrl: url }] },
      })
    } else if (t === 'sources') {
      if (!(b.sources || []).length) continue
      allSources.push(...b.sources)
      push(sourcesSection(b.sources))
    } else if (t === 'suggest') {
      const prompts = Array.isArray(b.prompts) ? b.prompts : [b.text].filter(Boolean)
      if (!prompts.length) continue
      push(suggestSection(prompts, { scroll: b.scroll !== false }))
    } else if (t === 'footer') {
      if (!(b.actions || []).length) continue
      push(footerActionSection(b.actions))
    } else if (t === 'quickreply') {
      const s = quickReplySection(b.items || b.data || b.replies || [])
      if (s) push(s)
    } else if (t === 'compact') {
      push(compactSection(b.data || {}))
    } else if (t === 'widget3p') {
      push(widget3pSection(b.data || b.items || {}))
    } else if (t === 'metadata') {
      const text = String(b.text ?? b.data?.text ?? '')
      if (text.trim()) push(metadataSection(text, { tip: b.tip !== false }))
    } else if (t === 'product') {
      const s = productSection(b.items || b.data || [])
      if (s) push(s)
    } else if (t === 'post') {
      const s = postSection(b.items || b.data || [])
      if (s) push(s)
    } else if (t === 'reel') {
      const s = reelSection(b.items || b.data || [])
      if (s) push(s)
    } else if (t === 'carousel') {
      const s = carouselSection(b.items || b.data || [])
      if (s) {
        push(s, {
          messageType: T.GRID_IMAGE,
          gridImageMetadata: {
            gridImageUrl: { imagePreviewUrl: (b.items?.[0]?.waUrl || b.items?.[0]?.url) || '' },
            imageUrls: (b.items || []).map((it) => ({
              imagePreviewUrl: it.waUrl || it.url,
              imageHighResUrl: it.waUrl || it.url,
              sourceUrl: it.url || it.waUrl,
            })).filter((x) => x.imagePreviewUrl),
          },
        })
      }
    } else if (t === 'math') {
      const s = mathSection(b.items || b.data || [])
      if (s) push(s)
    } else if (t === 'latex') {
      // Rumus LaTeX sebagai blok tersendiri (GenAILatexUXPrimitive).
      const s = latexUxSection({
        expr: b.expr ?? b.latex ?? b.formula,
        waUrl: b.waUrl, url: b.url, width: b.width, height: b.height,
        font_height: b.font_height, padding: b.padding,
      })
      if (s) push(s)
    } else if (t === 'video') {
      const s = videoSection({
        url: b.waUrl || b.url, thumbnail: b.thumbnail || b._video?.thumbnail,
        width: b.width, height: b.height,
        status: b.status, update_text: b.update_text,
      })
      if (s) push(s, {
        messageType: T.GRID_IMAGE,
        gridImageMetadata: {
          gridImageUrl: { imagePreviewUrl: b.thumbnail || b._video?.thumbnail || b.waUrl || b.url },
          imageUrls: [{ imagePreviewUrl: b.thumbnail || b._video?.thumbnail || b.waUrl || b.url, imageHighResUrl: b.waUrl || b.url, sourceUrl: b.waUrl || b.url }],
        },
      })
    } else if (t === 'videogrid') {
      const s = videoGridSection(b.items || b.data || [])
      if (s) push(s)
    } else if (t === 'fullimage') {
      const s = fullImageSection({
        fullUrl: b.waUrl || b.url, previewUrl: b.preview || b.thumbnail,
        width: b.width, height: b.height, alt: b.alt,
      })
      if (s) push(s, {
        messageType: T.GRID_IMAGE,
        gridImageMetadata: {
          gridImageUrl: { imagePreviewUrl: b.preview || b.waUrl || b.url },
          imageUrls: [{ imagePreviewUrl: b.preview || b.waUrl || b.url, imageHighResUrl: b.waUrl || b.url, sourceUrl: b.waUrl || b.url }],
        },
      })
    } else if (t === 'divider') {
      push(dividerSection({ style: b.style }))
    } else if (t === 'label') {
      // Banner label eksplisit (GenAILabelPrimitive) — teks/warna dari model.
      const s = labelSection({
        text: b.text ?? b.data?.text ?? '',
        detail: b.detail ?? b.subtitle ?? b.data?.detail,
        icon: b.icon ?? b.data?.icon,
        variant: b.variant ?? b.data?.variant,
        background: b.background ?? b.bg ?? b.data?.background,
        textColor: b.textColor ?? b.fg ?? b.data?.textColor,
      })
      if (s) push(s)
    } else if (t === 'spacer') {
      push(spacerSection({ spacing: b.spacing, height: b.height }))
    } else if (t === 'thinking') {
      push(thinkingSection(b.data || b))
    } else if (t === 'progress') {
      push(progressSection(b.data || b))
    } else if (t === 'igsuggest') {
      const s = igSuggestedSection(b.items || b.data || [])
      if (s) push(s)
    } else if (t === 'multirow') {
      const s = multipleResponseSection(b.sections || b.items || [])
      if (s) push(s)
    } else if (t === 'upsell') {
      push(metaUpsellSection(b.data || b))
    } else if (t === 'task') {
      push(taskSection(b.data || b))
    } else if (t === 'foatext') {
      const s = foaTextSection(b.text ?? b.data?.text ?? '')
      if (s) push(s)
    } else if (t === 'bloks') {
      const s = bloksSection(b.data || b)
      if (s) push(s)
    } else if (t === 'html') {
      const s = htmlSection(b.data || b)
      if (s) {
        push(s)
        if (s.__embedded) allEmbedded.push(s.__embedded)
      }
    } else if (t === 'rawsection') {
      // Section MENTAH — dipancarkan apa adanya, TANPA normalisasi alias.
      // Dipakai untuk menguji nama `__typename` asli persis seperti yang
      // dikirim klien produksi (mis. GenAISearchResultV2Primitive). Ini
      // SENGAJA menembus gerbang alias: tujuannya mengukur nama mana yang
      // benar-benar dirender klien, bukan memaksa semuanya jadi kanonik.
      if (b.section && typeof b.section === 'object') push(b.section)
    } else if (t === 'rawsections') {
      // Beberapa section mentah sekaligus (satu respons, banyak primitif).
      for (const s of (Array.isArray(b.sections) ? b.sections : [])) {
        if (s && typeof s === 'object') push(s)
      }
      // Submessage opsional — kartu produksi SELALU membawa minimal satu
      // submessage (biasanya teks). Sertakan bila disediakan.
      for (const sm of (Array.isArray(b.submessages) ? b.submessages : [])) {
        if (sm && typeof sm === 'object') submessages.push(sm)
      }
    }
  }

  return { submessages, sections, sources: allSources, embedded: allEmbedded }
}

/** Bangun objek richResponseMessage + sections (tanpa contextInfo/proofs). */
export function buildRichResponse(blocks) {
  const { submessages, sections, sources, embedded } = buildCardParts(blocks)
  if (!sections.length) return null
  const responseId = crypto.randomUUID()
  const out = {
    responseId,
    submessages,
    sections,
    unified: { __typename: 'GenAIUnifiedResponse', response_id: responseId, sections },
  }
  const embeddedScreens = [...(embedded || []), ...buildEmbeddedSources(sources)]
  if (embeddedScreens.length) out.embedded_screens = embeddedScreens
  return out
}

/**
 * Selesaikan media pada blok gambar/video/math ("auto unduh").
 * Mengembalikan blok baru dengan `waUrl` terisi. Blok tanpa media dibiarkan.
 */
export async function resolveBlockMedia(sock, block) {
  if (!block) return block
  if (block.type === 'image' && (block.url || block.media)) {
    const waUrl = await resolveMedia(sock, block.waUrl || block.url || block.media, 'image')
    if (!waUrl) return null
    return { ...block, waUrl }
  }
  if (block.type === 'carousel') {
    // Korsel: selesaikan tiap gambar jadi URL WhatsApp (auto unduh).
    const items = Array.isArray(block.items) ? block.items : []
    const out = []
    for (const it of items) {
      const src = it.waUrl || it.url || it.media
      if (!src) continue
      const waUrl = await resolveMedia(sock, src, 'image')
      if (!waUrl) continue
      out.push({ ...it, waUrl })
    }
    if (!out.length) return null
    return { ...block, items: out }
  }
  if (block.type === 'reel' || block.type === 'post') {
    // Kartu sosial: perkaya dengan metadata OpenGraph (judul, thumbnail,
    // penulis) bila belum ada — best-effort, tak pernah menggagalkan kirim.
    const items = Array.isArray(block.items) ? block.items : []
    const out = []
    for (const it of items) {
      const url = it.post_url || it.url
      let enriched = { ...it }
      if (url && (!it.title || !it.thumbnail_url)) {
        const meta = await fetchLinkMeta(url)
        if (meta) {
          enriched = {
            ...enriched,
            title: enriched.title || meta.title || '',
            thumbnail_url: enriched.thumbnail_url || enriched.thumbnail || meta.image || '',
            username: enriched.username || meta.author || '',
            profile_picture_url: enriched.profile_picture_url || meta.favicon || '',
            post_caption: enriched.post_caption || enriched.caption || meta.description || '',
          }
        }
      }
      // Thumbnail/avatar diunggah ke WhatsApp supaya benar-benar dirender.
      if (enriched.thumbnail_url) {
        const th = await resolveMedia(sock, enriched.thumbnail_url, 'image')
        if (th) enriched.thumbnail_url = th
      }
      if (enriched.profile_picture_url) {
        const pp = await resolveMedia(sock, enriched.profile_picture_url, 'image')
        if (pp) enriched.profile_picture_url = pp
      }
      out.push(enriched)
    }
    if (!out.length) return null
    return { ...block, items: out }
  }
  if (block.type === 'video' && (block.url || block.media)) {
    const buf = isUrl(block.url || block.media)
      ? await fetchBuffer(block.url || block.media)
      : (Buffer.isBuffer(block.media) ? block.media : Buffer.alloc(0))
    const waUrl = await resolveMedia(sock, block.url || block.media, 'video')
    if (!waUrl) return null
    let thumbnail
    if (buf.length) {
      const frame = await videoPreview(buf, { time: block.time ?? 1 })
      if (frame.length) thumbnail = await resizeImage(frame, 300, 300)
    }
    return {
      ...block,
      waUrl,
      _video: { file_length: buf.length || undefined, duration: mp4Duration(buf) || undefined, thumbnail },
    }
  }
  if (block.type === 'fullimage' && (block.url || block.media)) {
    // GenAIImagePrimitive: gambar penuh + opsional preview/thumbnail.
    const waUrl = await resolveMedia(sock, block.waUrl || block.url || block.media, 'image')
    if (!waUrl) return null
    let preview = block.preview || block.thumbnail
    if (preview) preview = await resolveMedia(sock, preview, 'image')
    return { ...block, waUrl, ...(preview ? { preview } : {}) }
  }
  if (block.type === 'product') {
    // Kartu produk: gambar produk WAJIB diunggah ke WhatsApp dulu, kalau tidak
    // perender tak bisa mengambilnya (URL asing sering diblokir → gambar kosong).
    const items = Array.isArray(block.items) ? block.items : []
    const out = []
    for (const it of items) {
      const src = it.waUrl || it.image_url || it.image
      const waUrl = src ? await resolveMedia(sock, src, 'image') : ''
      const extra = (Array.isArray(it.additional_images) ? it.additional_images : [])
      const addOut = []
      for (const x of extra) {
        const xs = typeof x === 'string' ? x : (x?.url || x?.waUrl)
        if (!xs) continue
        const xu = await resolveMedia(sock, xs, 'image')
        addOut.push({ url: xu || xs })
      }
      out.push({
        ...it,
        ...(waUrl ? { waUrl, image_url: waUrl } : {}),
        ...(addOut.length ? { additional_images: addOut } : {}),
      })
    }
    if (!out.length) return null
    return { ...block, items: out }
  }
  if (block.type === 'compact') {
    // Kartu entity: gambar profil/entitas juga harus lewat WhatsApp.
    const d = block.data || block
    const img = d.image
    const src = (img && typeof img === 'object') ? (img.url || img.url_fallback) : img
    if (!src) return block
    const waUrl = await resolveMedia(sock, src, 'image')
    if (!waUrl) return block
    const image = (img && typeof img === 'object')
      ? { ...img, url: waUrl }
      : { url: waUrl }
    return { ...block, data: { ...d, image } }
  }
  if (block.type === 'igsuggest') {
    // Kartu saran IG: gambar kartu diunggah bila ada.
    const items = Array.isArray(block.items) ? block.items : []
    const out = []
    for (const it of items) {
      const src = it.image_url || it.image
      const waUrl = src ? await resolveMedia(sock, src, 'image') : ''
      out.push(waUrl ? { ...it, image_url: waUrl } : { ...it })
    }
    if (!out.length) return null
    return { ...block, items: out }
  }
  if (block.type === 'latex') {
    // Rumus LaTeX blok tersendiri: render → unggah → simpan waUrl + ukuran.
    const expr = String(block.expr ?? block.latex ?? block.formula ?? '').trim()
    if (!expr) return null
    const src = block.waUrl || block.url || latexRenderUrl(expr, { display: true })
    if (!src) return null
    let waUrl = await resolveMedia(sock, src, 'image')
    if (!waUrl) waUrl = src
    let width = Number(block.width) || 0
    let height = Number(block.height) || 0
    if (!width || !height) {
      const buf = await fetchBuffer(src)
      const dim = pngSize(buf)
      width = width || dim.width || 200
      height = height || dim.height || 80
    }
    return { ...block, expr, waUrl, width, height }
  }
  if (block.type === 'math') {
    // Setiap rumus: render ke PNG (CodeCogs) → unggah ke WhatsApp → simpan
    // `waUrl` + ukuran asli gambar agar perender tahu rasio yang benar.
    const items = Array.isArray(block.items) ? block.items : []
    const out = []
    for (const it of items) {
      const expr = String(it?.expr ?? it?.latex ?? it?.formula ?? '').trim()
      if (!expr) continue
      const src = it.waUrl || it.url || latexRenderUrl(expr, { display: it.display !== false })
      if (!src) continue
      // Unggah ke WhatsApp bila bisa; kalau gagal, pakai URL render langsung
      // (CodeCogs publik) supaya rumus tidak hilang dari kartu.
      let waUrl = await resolveMedia(sock, src, 'image')
      if (!waUrl) waUrl = src
      // Ukuran PNG dari CodeCogs (best-effort) untuk rasio yang pas.
      let width = Number(it.width) || 0
      let height = Number(it.height) || 0
      if (!width || !height) {
        const buf = await fetchBuffer(src)
        const dim = pngSize(buf)
        width = width || dim.width || 200
        height = height || dim.height || 80
      }
      out.push({ ...it, expr, waUrl, width, height, display: it.display !== false })
    }
    if (!out.length) return null
    return { ...block, items: out }
  }
  if (block.type === 'text' && hasMath(block.text)) {
    // Rumus inline `$…$` / blok `$$…$$` di dalam teks: render tiap rumus lalu
    // lampirkan sebagai inline entity `GenAILatexItem` (posisi tetap di teks).
    const { math } = extractMath(block.text)
    const entities = []
    for (const m of math) {
      const src = latexRenderUrl(m.expr, { display: m.display })
      if (!src) continue
      let waUrl = await resolveMedia(sock, src, 'image')
      if (!waUrl) waUrl = src
      let width = 0, height = 0
      const buf = await fetchBuffer(src)
      const dim = pngSize(buf)
      width = dim.width || 200
      height = dim.height || 80
      entities.push({
        key: m.key,
        metadata: {
          latex_expression: m.expr,
          latex_image: { url: waUrl, width, height },
          font_height: 83.333333333333,
          padding: 15,
          __typename: 'GenAILatexItem',
        },
      })
    }
    if (!entities.length) return block
    return { ...block, _mathEntities: entities }
  }
  return block
}
