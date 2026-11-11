// rich.js — pembangun pesan kaya WhatsApp (kontrak nix408 / @itsliaaa/baileys 0.3.18).
//
// STRUKTUR YANG BENAR (dari nix408 `rich-message-utils.js`):
//
//   messageContextInfo.botMetadata          ← metadata bot + proofs (wajib)
//   └─ botForwardedMessage.message
//      └─ richResponseMessage
//         ├─ submessages[]                 ← ISI (teks/kode/tabel) — WAJIB ada
//         ├─ messageType: 1                ← AI_RICH_RESPONSE_TYPE_STANDARD
//         ├─ unifiedResponse.data          ← Buffer(JSON) cermin submessages
//         └─ contextInfo                   ← isForwarded + forwardedAiBotMessageInfo
//
// Dua kesalahan yang membuat kartu "kurang rapi" (sudah diperbaiki di sini):
//   1. `submessages[]` tidak diisi → WhatsApp tidak tahu isi kartu.
//   2. `unifiedResponse.data` dikirim sebagai string base64, bukan Buffer.
//   3. `verificationMetadata.proofs[]` kosong → kartu dianggap tidak sah.
//   4. Node `biz` (native_flow v:9 name:mixed) tidak ikut → kartu tidak dirender
//      penuh. Paket terinstal TIDAK mengenali richResponseMessage di
//      shouldIncludeBizBinaryNode, jadi kita kirim `additionalNodes` eksplisit.
//
// Catatan paket terinstal: bentuk `{ richResponse: [{code:'str'}] }` dan
// `{ richResponse: [{table}] }` CRASH (`codeBlocks.map is not a function`).
// Karena itu kita membangun kartu sendiri memakai tokenizeCode/toUnified/
// wrapToBotForwardedMessage yang memang diekspor paket, lalu relay `{...card, raw:true}`.

import { tokenizeCode, toUnified, wrapToBotForwardedMessage, generateWAMessageFromContent, generateMessageIDV2, proto } from '@itsliaaa/baileys'
import { buildCardParts, buildEmbeddedSources, tokenizeCodeSafe, labelSection, newLayout,
         resolveMedia, videoPreview, resizeImage, mp4Duration } from './airich.js'

// SubMessageType (lib/Types/RichType.js)
const T_TEXT = 2
const T_TABLE = 4
const T_CODE = 5
// Identitas bot yang diharapkan WhatsApp untuk kartu AI Rich.
const AI_BOT_JID = '867051314767696@bot'

// Node `biz` yang membuat WhatsApp merender kartu AI Rich secara penuh.
// Disalin dari nix408 `generic-utils.js` (MIXED_NATIVE_FLOW).
export const MIXED_NATIVE_FLOW_NODE = {
  tag: 'interactive',
  attrs: { type: 'native_flow', v: '1' },
  content: [{ tag: 'native_flow', attrs: { v: '9', name: 'mixed' } }],
}

export function bizNode() {
  return {
    tag: 'biz',
    attrs: {
      actual_actors: '2',
      host_storage: '2',
      privacy_mode_ts: `${(Date.now() / 1000) | 0}`,
    },
    content: [MIXED_NATIVE_FLOW_NODE],
  }
}

export async function generateTag(sock) {
  if (typeof sock?.generateMessageTag === 'function') return await sock.generateMessageTag()
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

// ---------- pembangun kartu AI Rich ----------

// Petakan bahasa umum → bahasa yang dikenal lexer paket.
const LANG_ALIAS = {
  js: 'javascript', node: 'javascript', nodejs: 'javascript', mjs: 'javascript',
  ts: 'typescript', py: 'python', py3: 'python', sh: 'bash', shell: 'bash',
  zsh: 'bash', 'c#': 'csharp', cs: 'csharp', 'c++': 'cpp', cplusplus: 'cpp',
  rs: 'rust', go: 'golang', golang: 'golang', yml: 'yaml', md: 'markdown',
  htm: 'html', ps1: 'powershell', cmd: 'bat', bat: 'bat', console: 'bash',
  json5: 'json', text: 'text', plaintext: 'text', txt: 'text',
}

// Lexer paket hanya mengenal 25 bahasa. Yang tidak dikenal tetap aman
// (tokenizeCode jatuh ke DEFAULT), tapi kita normalisasi yang umum dulu.
export function normalizeLang(language) {
  const l = String(language || '').trim().toLowerCase()
  if (!l) return 'javascript'
  return LANG_ALIAS[l] || l
}

// Ubah penanda NATIVE WhatsApp -> Markdown, untuk teks yang masuk ke kartu AI
// Rich. Teks kartu dirender sebagai Markdown (GenAIMarkdownTextUXPrimitive,
// lihat nix408 README), sedangkan bubble biasa merender penanda native.
//
// Kalau tidak diterjemahkan: `*tebal*` native tampil sebagai *miring*, dan
// ```mono``` native dibaca Markdown sebagai FENCE KODE -> muncul kartu kode
// hampa di dalam kartu. Karena itu mono sebaris harus jadi `mono`.
export function nativeToMarkdown(text) {
  let s = String(text ?? '')
  // mono sebaris ```x``` -> `x` (fence di dalam teks kartu salah dianggap blok)
  s = s.replace(/```([^`\n]+)```/g, '`$1`')
  // coret native ~x~ -> Markdown ~~x~~
  s = s.replace(/~([^~\n]+)~/g, '~~$1~~')
  // CATATAN: `*x*` TIDAK diubah. Kartu AI Rich merender Markdown, dan di
  // Markdown `*x*` = *miring* yang SAH (dulu keliru diubah jadi `**x**`).
  // bullet native "• " -> daftar Markdown "- "
  s = s.replace(/^(\s*)•\s+/gm, '$1- ')
  return s
}

// Jaring pengaman terakhir untuk teks kartu. Blok kode yang SAH sudah diubah
// agent/format.py menjadi {type:'code'}, jadi sisa ``` di dalam teks PASTI
// bocoran (model menulis mono sebaris pakai tiga backtick) dan akan dibaca
// Markdown sebagai FENCE -> kartu "Lihat kode" hampa. Fungsi ini idempoten:
// aman dijalankan walau teks sudah bersih.
export function sanitizeCardText(text) {
  let s = String(text ?? '')
  // mono sebaris ```x``` -> `x` (baris dalamnya dirapatkan)
  s = s.replace(/```([^`]*?)```/gs, (m, body) => {
    const b = String(body).replace(/\s*\n\s*/g, ' ').trim()
    return b ? `\`${b}\`` : ''
  })
  // sisa run backtick >= 2 -> satu
  s = s.replace(/`{2,}/g, '`')
  return s
}

// Ubah blok dari agent/format.py menjadi submessages[] yang sah.
export function buildSubmessages(blocks) {
  const out = []
  for (const b of blocks || []) {
    const t = b?.type
    if (t === 'code') {
      const lang = normalizeLang(b.language)
      const code = String(b.code ?? '')
      if (!code.trim()) continue
      out.push({
        messageType: T_CODE,
        codeMetadata: { codeLanguage: lang, codeBlocks: tokenizeCodeSafe(code, lang) },
      })
    } else if (t === 'table') {
      const rows = (b.rows || [])
        .map((items, i) => ({
          isHeading: i === 0,
          items: (Array.isArray(items) ? items : [items]).map((x) => String(x ?? '')),
        }))
        .filter((r) => r.items.length)
      if (!rows.length) continue
      out.push({ messageType: T_TABLE, tableMetadata: { title: String(b.title || ''), rows } })
    } else if (t === 'text') {
      // Teks sudah dalam Markdown (agent/format.py mempertahankannya), dan
      // kartu AI Rich merender Markdown — jadi lewatkan apa adanya, hanya
      // menyapu sisa run backtick yang bisa jadi fence hampa.
      const text = sanitizeCardText(b.text ?? '').trim()
      if (text) out.push({ messageType: T_TEXT, messageText: text })
    }
  }
  return out
}

/**
 * Bangun SATU kartu AI Rich dari daftar blok.
 * Mengembalikan objek pesan siap-relay, atau null bila tidak ada isi.
 */
export function buildRichCard(blocks, { disclaimerText = '', responseId, progressIndicator } = {}) {
  // Pakai perakit kaya (airich.js): selain submessages, ia menghasilkan
  // `sections[]` yang memuat inline entities (tautan bisa diklik), sources,
  // gambar, suggest, dll. Bila kosong, jatuh ke jalur lama.
  const parts = buildCardParts(blocks)
  let submessages = parts.submessages
  let unified
  if (!submessages.length && !parts.sections.length) {
    submessages = buildSubmessages(blocks)
    if (!submessages.length) return null
    unified = toUnified(submessages, responseId)
  } else {
    const uuid = responseId || parts.responseId
    unified = { response_id: uuid, sections: parts.sections }
    // Panel "Sources" (chip sitasi bisa di-tap) + layar tertanam lain (HTML).
    const embedded = [...(parts.embedded || []), ...buildEmbeddedSources(parts.sources || [])]
    if (embedded.length) unified.embedded_screens = embedded
  }

  const uuid = responseId || unified.response_id || `kwee-${Date.now()}-${Math.random().toString(36).slice(2)}`
  unified.response_id = uuid
  // Root `__typename` (sesuai contoh payload resmi). Tanpanya, klien tak
  // mengenali respons sebagai GenAIUnifiedResponse dan mengabaikan override
  // warna pada primitive (mis. GenAILabelPrimitive).
  unified.__typename = 'GenAIUnifiedResponse'

  // Banner label otomatis di SETIAP kartu (Fitur #31/#32): header identitas +
  // footer konteks (mis. "Sumber: …"). Disisipkan SETELAH isi dipastikan ada,
  // supaya kartu kosong tetap mengembalikan null.
  injectCardLabels(unified, parts.sources)

  const richResponseMessage = proto.AIRichResponseMessage.create({
    submessages,
    messageType: proto.AIRichResponseMessageType.AI_RICH_RESPONSE_TYPE_STANDARD,
    // WAJIB Buffer (bukan string base64) supaya ter-encode sebagai bytes.
    unifiedResponse: { data: Buffer.from(JSON.stringify(unified)) },
    contextInfo: {
      isForwarded: true,
      forwardingScore: 1,
      forwardedAiBotMessageInfo: { botJid: AI_BOT_JID },
      forwardOrigin: 4,
    },
  })

  const card = wrapToBotForwardedMessage(richResponseMessage)
  card.messageContextInfo.botMetadata.botResponseId = uuid
  if (disclaimerText) card.messageContextInfo.botMetadata.messageDisclaimerText = disclaimerText
  // Progress indicator (UI "planning steps" Meta AI): menempel di botMetadata,
  // BUKAN di unifiedResponse — itu tempatnya menurut protobuf
  // IBotProgressIndicatorMetadata (progressDescription + stepsMetadata[]).
  if (progressIndicator) {
    card.messageContextInfo.botMetadata.progressIndicatorMetadata = progressIndicator
  }
  // `deviceListMetadata` + versinya: wajib ada agar WhatsApp menerima kartu ini
  // sebagai pesan bot yang SAH (lihat MessageBuilder.js `build()`). Tanpa ini
  // WhatsApp menandai kartu sebagai media tak terverifikasi → tombol "Unduh".
  card.messageContextInfo.deviceListMetadata = {}
  card.messageContextInfo.deviceListMetadataVersion = 2
  return card
}

// ---------- web2ui: HTML di kartu AI (aiRichHtml) ----------
export function buildRichData(html, { url = 'https://example.com', sources = ['example.com'], title = 'Preview', tabHeader = 'Widget' } = {}) {
  return {
    __typename: 'GenAIUnifiedResponse',
    response_id: `kwee-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    sections: [
      {
        __typename: 'GenAIUnifiedResponseSection',
        view_model: {
          __typename: 'GenAISingleLayoutViewModel',
          primitive: { __typename: 'GenAIBotProgressStatusPrimitive', title: 'Click to Open', is_in_progress: true },
        },
      },
    ],
    embedded_screens: [
      {
        title,
        content: [
          {
            __typename: 'FOAIDNixelButtonSheets',
            tabs: [
              {
                id: 'tab_0',
                tab_header: tabHeader,
                sections: [
                  {
                    __typename: 'GenAIUnifiedResponseSection',
                    view_model: {
                      __typename: 'GenAISingleLayoutViewModel',
                      primitive: {
                        __typename: 'GenAIaeacdsnwHtmlPrimitive',
                        payload: html,
                        url,
                        trusted_sources: sources,
                      },
                    },
                  },
                ],
                step_entries: [],
              },
            ],
          },
        ],
      },
    ],
  }
}

// Pesan HTML (web2ui) dengan struktur lengkap (submessages + proofs).
export function richHtmlMessage(html, opts = {}) {
  const { title = 'Preview', sources = ['example.com'], url = 'https://example.com', responseId, includeCardLabels = true } = opts
  const uuid = responseId || `kwee-${Date.now()}-${Math.random().toString(36).slice(2)}`
  const data = { ...buildRichData(html, { url, sources, title }), response_id: uuid }
  // Banner header + footer juga menempel di kartu web2ui (Fitur #31/#32).
  if (includeCardLabels) injectCardLabels(data, (sources || []).map((s) => (typeof s === 'string' ? { url: s } : s)))
  const richResponseMessage = proto.AIRichResponseMessage.create({
    messageType: proto.AIRichResponseMessageType.AI_RICH_RESPONSE_TYPE_STANDARD,
    unifiedResponse: { data: Buffer.from(JSON.stringify(data)) },
    contextInfo: {
      isForwarded: true, forwardingScore: 1,
      forwardedAiBotMessageInfo: { botJid: AI_BOT_JID }, forwardOrigin: 4,
    },
  })
  const card = wrapToBotForwardedMessage(richResponseMessage)
  card.messageContextInfo.botMetadata.botResponseId = uuid
  card.messageContextInfo.deviceListMetadata = {}
  card.messageContextInfo.deviceListMetadataVersion = 2
  return card
}

// A2UI / bloksWidget — permukaan interaktif.
export function bloksWidgetMessage({ uuid, data, type = 'im_a2ui' } = {}) {
  const payload = typeof data === 'string' ? data : JSON.stringify(data || {})
  return {
    interactiveMessage: {
      body: { text: '' },
      nativeFlowMessage: { buttons: [], messageParamsJson: '{}' },
      bloksWidget: { uuid: uuid || `kwee-${Date.now()}`, data: payload, type },
    },
  }
}

// ---------- pengelompokan blok ----------

// Jenis blok yang bisa masuk ke dalam SATU kartu AI Rich.
// `text/code/table` punya submessages 1:1; `image/sources/suggest/footer/compact/
// widget3p` hanya menambah `sections[]` (primitif render) — semuanya tetap satu kartu.
const CARD_KINDS = new Set([
  'text', 'code', 'table',
  'image', 'sources', 'suggest', 'footer', 'quickreply', 'compact', 'widget3p',
  'metadata', 'product', 'post', 'reel', 'math', 'latex', 'video', 'videogrid', 'fullimage',
  'divider', 'spacer', 'thinking', 'progress', 'igsuggest', 'multirow', 'upsell',
  'task', 'foatext', 'bloks', 'html', 'carousel', 'label',
])

// ---------- banner label (Fitur #31 / #32) ----------
// Banner `GenAILabelPrimitive` yang otomatis menempel di SETIAP kartu AI Rich.
// Dua lapis:
//   - HEADER : identitas KWEE / FIELD JOURNAL (ink navy).
//   - FOOTER : konteks editorial / domain sumber (aksen copper). Bisa dikosongkan.
// Bisa dimatikan dengan `setCardLabel(null)` / `{ enabled: false }`.
export let CARD_LABEL = {
  enabled: true,
  text: 'KWEE ◈ FIELD JOURNAL',
  detail: 'FIELD INTELLIGENCE · SIGNAL / EVIDENCE / CONTEXT',            // baris kedua opsional pada banner header
  icon: '',              // emoji/ikon opsional
  variant: 'POSITIVE',
  background: '#172A35FF',
  textColor: '#F4F1E8FF',
  header: true,            // tampilkan banner identitas (atas)
  footer: true,            // tampilkan banner konteks (bawah)
  footerText: '',          // kosong = tampilkan domain sumber otomatis
  footerBackground: '#E9B78CFF',
  footerTextColor: '#452A1DFF',
  footerShowSources: true, // footer otomatis menampilkan domain sumber
}

/** Ubah setelan banner label (dipanggil dari config saat boot). */
export function setCardLabel(cfg) {
  if (cfg === null || cfg === false) { CARD_LABEL = { enabled: false }; return CARD_LABEL }
  if (typeof cfg === 'object') CARD_LABEL = { ...CARD_LABEL, ...cfg }
  return CARD_LABEL
}

// ---------- media intro (di bawah label, setiap kartu) ----------
// Media pendek yang tampil di SETIAP kartu AI Rich, tepat di bawah banner
// label header. Tiga mode (config `card.media.mode`):
//   - `video`    : GenAIVideoPrimitive — video diputar LANGSUNG (default)
//   - `carousel` : frame video jadi gambar, bisa di-swipe (GenAIImagePrimitive
//                  di GenAIHScrollLayoutViewModel)
//   - `product`  : GenAIProductItemCardPrimitive dengan thumbnail video
//
// Media di-upload ke WhatsApp SEKALI lalu URL-nya di-cache: meng-upload 1,3 MB
// di setiap balasan akan boros & lambat. `primeCardMedia(sock)` dipanggil
// sekali setelah socket terbuka.
export let CARD_MEDIA = {
  enabled: false,
  mode: 'video',   // 'video' | 'carousel' | 'product'
  path: '',        // berkas lokal (assets/…) — diunggah sekali
  url: '',         // atau URL http(s) langsung
  title: '',
  creator: '',
  thumbnail: '',   // opsional (path/url gambar)
  frames: 5,       // mode carousel: jumlah frame yang diambil
}

/** Ubah setelan media intro (dipanggil dari config saat boot). */
export function setCardMedia(cfg) {
  if (cfg === null || cfg === false) { CARD_MEDIA = { enabled: false }; return CARD_MEDIA }
  if (typeof cfg === 'object') CARD_MEDIA = { ...CARD_MEDIA, ...cfg }
  return CARD_MEDIA
}

// URL WhatsApp hasil upload (cache sekali-upload).
let CARD_MEDIA_WA_URL = ''
let CARD_MEDIA_THUMB_URL = ''
let CARD_MEDIA_FRAME_URLS = []

/** Section media intro sesuai mode. Sinkron — memakai cache hasil upload. */
export function introMediaSection() {
  if (!CARD_MEDIA?.enabled) return null
  const mode = String(CARD_MEDIA.mode || 'video').toLowerCase()

  if (mode === 'carousel') {
    const urls = CARD_MEDIA_FRAME_URLS.filter(Boolean)
    if (!urls.length) return null
    const prims = urls.map((u) => ({
      full_image: { url: u },
      __typename: 'GenAIImagePrimitive',
    }))
    return newLayout(prims.length === 1 ? 'Single' : 'HScroll',
      prims.length === 1 ? prims[0] : prims)
  }

  if (mode === 'product') {
    const img = CARD_MEDIA_THUMB_URL || CARD_MEDIA_WA_URL
    if (!img) return null
    return newLayout('Single', {
      __typename: 'GenAIProductItemCardPrimitive',
      title: CARD_MEDIA.title || '',
      ...(CARD_MEDIA.creator ? { brand: String(CARD_MEDIA.creator) } : {}),
      image: { url: img },
    })
  }

  // mode video (default): GenAIVideoPrimitive
  // Bentuk field disamakan PERSIS dengan plogme `prepareVideoGridMessage`
  // (rich-message-utils.js:955-988): `video_delivery_response` SELALU ada
  // dengan `progressive_urls`/`dash_manifests` (boleh array kosong).
  const url = CARD_MEDIA_WA_URL || CARD_MEDIA.url
  if (!url) return null
  const prim = {
    reels_url: url,
    ...(CARD_MEDIA.title ? { reels_title: String(CARD_MEDIA.title) } : {}),
    ...(CARD_MEDIA.creator ? { creator: String(CARD_MEDIA.creator) } : {}),
    ...(CARD_MEDIA_THUMB_URL ? { thumbnail_url: CARD_MEDIA_THUMB_URL } : {}),
    video_delivery_response: {
      progressive_urls: [],
      dash_manifests: [],
      __typename: 'GenAIVideoDeliveryResponse',
    },
    __typename: 'GenAIVideoPrimitive',
  }
  return newLayout('Single', prim)
}

/**
 * Unggah media intro ke WhatsApp SEKALI, simpan URL-nya. Panggil setelah
 * socket terbuka. Idempoten (langsung kembali bila cache sudah ada).
 */
export async function primeCardMedia(sock) {
  if (!CARD_MEDIA?.enabled) return ''
  const src = CARD_MEDIA.path || CARD_MEDIA.url
  if (!src) return ''
  const mode = String(CARD_MEDIA.mode || 'video').toLowerCase()
  try {
    // Baca buffer sekali (untuk video & carousel butuh byte-nya).
    let buf = Buffer.alloc(0)
    if (CARD_MEDIA.path) {
      const fs = await import('node:fs/promises')
      buf = await fs.readFile(CARD_MEDIA.path)
    }

    // Thumbnail (dipakai mode product & sebagai poster video) — sekali saja.
    if (!CARD_MEDIA_THUMB_URL) {
      let th = CARD_MEDIA.thumbnail
      if (!th && buf.length) {
        const frame = await videoPreview(buf, { time: 1 })
        if (frame.length) th = await resizeImage(frame, 640, 360)
      }
      if (th) {
        const u = await resolveMedia(sock, th, 'image')
        if (u) CARD_MEDIA_THUMB_URL = u
      }
    }

    if (mode === 'carousel') {
      if (CARD_MEDIA_FRAME_URLS.length) return CARD_MEDIA_FRAME_URLS[0]
      if (!buf.length) return ''
      const n = Math.max(2, Math.min(10, Number(CARD_MEDIA.frames) || 5))
      // Sebar frame merata; video 29s → ~1s, 7s, 13s, 19s, 25s.
      const dur = mp4Duration(buf) || 29
      const times = Array.from({ length: n }, (_, i) => Math.max(1, Math.round((dur * (i + 0.5)) / n)))
      for (const t of times) {
        const frame = await videoPreview(buf, { time: t })
        if (!frame.length) continue
        const small = await resizeImage(frame, 640, 360)
        const u = await resolveMedia(sock, small, 'image')
        if (u) CARD_MEDIA_FRAME_URLS.push(u)
      }
      return CARD_MEDIA_FRAME_URLS[0] || ''
    }

    if (mode === 'product') {
      // Mode product hanya perlu thumbnail — sudah disiapkan di atas.
      return CARD_MEDIA_THUMB_URL
    }

    // mode video (default)
    if (CARD_MEDIA_WA_URL) return CARD_MEDIA_WA_URL
    const media = buf.length ? buf : src
    const waUrl = await resolveMedia(sock, media, 'video')
    if (waUrl) CARD_MEDIA_WA_URL = waUrl
    return CARD_MEDIA_WA_URL
  } catch (err) {
    if (typeof console !== 'undefined') console.warn('[rich] primeCardMedia gagal:', err?.message || err)
    return ''
  }
}

// Ambil hostname ringkas dari sebuah URL untuk footer konteks.
function hostOf(url) {
  try {
    return new URL(String(url)).hostname.replace(/^www\./, '')
  } catch { return '' }
}

// Susun banner header + footer untuk sebuah kartu. Mengembalikan
// { header, footer } (masing-masing section atau null).
export function buildCardLabels({ sources } = {}) {
  if (CARD_LABEL?.enabled === false) return { header: null, footer: null }
  let header = null
  let footer = null

  if (CARD_LABEL.header !== false) {
    header = labelSection({
      text: CARD_LABEL.text,
      detail: CARD_LABEL.detail,
      icon: CARD_LABEL.icon,
      variant: CARD_LABEL.variant,
      background: CARD_LABEL.background,
      textColor: CARD_LABEL.textColor,
    })
  }

  if (CARD_LABEL.footer !== false) {
    let ftext = String(CARD_LABEL.footerText || '').trim()
    if (!ftext && CARD_LABEL.footerShowSources !== false && Array.isArray(sources) && sources.length) {
      const hosts = [...new Set(sources.map((s) => hostOf(s?.url || s)).filter(Boolean))]
      if (hosts.length) ftext = `Sumber: ${hosts.slice(0, 3).join(', ')}`
    }
    // Footer is a source strip, not a second copy of the brand tagline.
    // Keep source provenance only when sources exist; avoid repeating filler.
    if (ftext) {
      footer = labelSection({
        text: ftext,
        variant: CARD_LABEL.variant,
        background: CARD_LABEL.footerBackground,
        textColor: CARD_LABEL.footerTextColor,
      })
    }
  }
  return { header, footer }
}

// Sisipkan header di awal & footer di akhir `sections[]` (in-place, aman).
// Media intro (video/carousel/product) disisipkan TEPAT DI BAWAH header.
function injectCardLabels(unified, sources) {
  const { header, footer } = buildCardLabels({ sources })
  const media = introMediaSection()
  if (!header && !footer && !media) return
  if (!Array.isArray(unified.sections)) unified.sections = []
  if (header) unified.sections.unshift(header)
  // Media tepat setelah header (atau di paling atas bila header dimatikan).
  if (media) unified.sections.splice(header ? 1 : 0, 0, media)
  if (footer) unified.sections.push(footer)
}

/**
 * Gabungkan deretan blok teks/kode/tabel menjadi SATU kartu AI Rich.
 *
 * Sejak keputusan "semua balasan lewat kartu AI Rich", SELURUH balasan —
 * termasuk teks polos — dikirim sebagai kartu. Alasannya: teks di dalam kartu
 * dirender WhatsApp sebagai **Markdown** (GenAIMarkdownTextUXPrimitive),
 * sedangkan bubble teks biasa TIDAK merender Markdown. Jadi kartu adalah
 * satu-satunya cara agar penanda seperti **tebal**, # judul, - daftar, `kode`,
 * dan tabel pipa tampil rapi.
 *
 * Aturan:
 *   - Deret blok teks/kode/tabel digabung menjadi satu `richResponse`.
 *   - Blok kaya lain (event/poll/invite/widget/web2ui) tetap berdiri sendiri
 *     dan memutus kartu.
 */
export function groupReplies(replies) {
  const out = []
  let buf = []
  const flush = () => {
    if (!buf.length) return
    out.push({ type: 'richResponse', blocks: buf, disclaimerText: '' })
    buf = []
  }
  for (const r of replies || []) {
    if (r && CARD_KINDS.has(r.type)) {
      // Buang blok teks kosong agar tidak jadi submessage hampa.
      if (r.type === 'text' && !String(r.text || '').trim()) continue
      // Buang blok kode hampa: isi hanya penanda/sisa bullet (mis. "- ").
      // Blok seperti ini muncul dari fence sebaris yang salah diurai dan
      // tampil di WhatsApp sebagai kartu "Lihat kode" kosong.
      if (r.type === 'code') {
        const body = String(r.code || '')
        if (!body.trim() || /^[\s\-*+•_~`]+$/.test(body)) continue
      }
      if (r.type === 'table' && !(Array.isArray(r.rows) && r.rows.length)) continue
      buf.push(r)
    } else {
      flush()
      if (r) out.push(r)
    }
  }
  flush()
  return out
}

// ---------- pengirim ----------

// Ubah string tanggal (ISO / epoch ms) pada event menjadi Date.
function coerceEventDates(event) {
  const out = { ...(event || {}) }
  for (const k of ['startDate', 'endDate']) {
    const v = out[k]
    if (typeof v === 'string' || typeof v === 'number') {
      const d = new Date(v)
      if (!Number.isNaN(d.getTime())) out[k] = d
    }
  }
  return out
}

// Kirim kartu AI Rich (botForwardedMessage) dengan node biz eksplisit.
//
// `raw: true` membuat paket meneruskan objek kartu apa adanya (tanpa mencoba
// menafsirkan ulang), sedangkan `additionalNodes` menyisipkan node `biz`
// (native_flow v:9 name:mixed) yang TIDAK ditambahkan otomatis oleh paket
// terinstal untuk richResponseMessage. Tanpa node ini kartu tampil pincang.
//
// BYPASS "Unduh" (diadaptasi dari MessageBuilder.js `send()` → `sendEdit()`):
// setelah kartu terkirim, kirim satu EDIT (`protocolMessage` type 14 /
// MESSAGE_EDIT) yang membawa kartu yang SAMA sebagai `editedMessage`. WhatsApp
// menganggap pesan hasil edit sebagai pesan yang sudah "selesai diproses" dan
// langsung merender kartunya inline — alih-alih menampilkan peringatan
// "tidak bisa memverifikasi keamanan media ini … Unduh". Trik ini WAJIB, bukan
// pilihan: tanpa edit, penerima selalu melihat prompt Unduh.
export function buildCardEdit(chatId, card, messageId) {
  return generateWAMessageFromContent(
    chatId,
    {
      botForwardedMessage: {
        message: {
          protocolMessage: {
            key: { remoteJid: chatId, fromMe: true, id: messageId },
            type: 14, // ProtocolMessage.Type.MESSAGE_EDIT
            editedMessage: card,
          },
        },
      },
    },
    { messageId: generateMessageIDV2() },
  )
}

/**
 * EDIT kartu GenAI yang sudah terkirim (pesan yang sama, bukan pesan baru).
 *
 * WhatsApp tidak mengizinkan `edit` biasa untuk pesan kaya, tapi
 * `protocolMessage` bertipe MESSAGE_EDIT dengan `editedMessage` berupa kartu
 * bot-forwarded diterima — mekanisme yang sama dipakai trik bypass "Unduh".
 * Dipakai pesan status (thinking/progres) supaya pembaruan tidak menumpuk.
 */
export async function editRichCard(sock, chatId, messageId, reply, opts = {}) {
  const blocks = Array.isArray(reply) ? reply : [reply]
  // PENTING: pakai ulang response_id kartu asli. Tanpa ini buildRichCard membuat
  // UUID baru → botResponseId edit ≠ kartu terkirim → edit diabaikan klien.
  const card = buildRichCard(blocks, {
    disclaimerText: opts.disclaimerText || '',
    progressIndicator: opts.progressIndicator,
    responseId: opts.responseId || undefined,
  })
  if (!card || !messageId) return null
  const edit = buildCardEdit(chatId, card, messageId)
  await sock.relayMessage(chatId, edit.message, { messageId: edit.key.id })
  return { ok: true, id: messageId, responseId: opts.responseId || null }
}

export async function relayCard(sock, chatId, card, quoted, { bypassDownload = true } = {}) {
  const opts = { additionalNodes: [bizNode()] }
  if (quoted) opts.quoted = quoted
  const sent = await sock.sendMessage(chatId, { ...card, raw: true }, opts)
  // Simpan response_id kartu ini: EDIT berikutnya HARUS memakai id yang sama,
  // kalau tidak WhatsApp menganggapnya kartu lain dan mengabaikan edit-nya.
  if (sent) sent.botResponseId = card?.messageContextInfo?.botMetadata?.botResponseId || null
  // Trik bypass "Unduh": kirim ulang kartu sebagai EDIT pesan yang baru saja
  // terkirim. Kegagalan edit TIDAK boleh menggagalkan pengiriman kartu.
  if (bypassDownload && sent?.key?.id) {
    try {
      const edit = buildCardEdit(chatId, card, sent.key.id)
      await sock.relayMessage(chatId, edit.message, { messageId: edit.key.id })
      sent.bypassDownload = { ok: true, editId: edit.key.id }
    } catch (err) {
      // Diamkan: kartu tetap terkirim, hanya tanpa render inline.
      sent.bypassDownload = { ok: false, error: String(err?.message || err) }
      if (typeof console !== 'undefined') console.warn('[rich] edit bypass gagal:', err?.message || err)
    }
  }
  return sent
}

/**
 * Kirim SATU blok balasan terstruktur (dari agent/format.py) ke sebuah chat.
 * Mengembalikan objek pesan terkirim (untuk markOwn), atau null bila dilewati.
 */
export async function sendRich(sock, chatId, reply, quoted) {
  const opts = quoted ? { quoted } : {}
  const type = reply?.type

  // Blok tunggal teks/kode/tabel/primitif kaya tetap jadi satu KARTU rapi.
  // Teks juga jadi kartu supaya Markdown (**tebal**, # judul, - daftar) dirender.
  if (CARD_KINDS.has(type)) {
    let blocks = [reply]
    // "auto unduh": selesaikan media (gambar, video, rumus LaTeX) jadi URL
    // WhatsApp dulu sebelum kartu dibangun.
    const { resolveBlockMedia, hasMath } = await import('./airich.js')
    const needsMedia = (b) => b && (
      b.type === 'image' || b.type === 'math' || b.type === 'video'
      || b.type === 'fullimage' || b.type === 'latex' || b.type === 'carousel'
      || b.type === 'reel' || b.type === 'post'
      || b.type === 'product' || b.type === 'compact' || b.type === 'igsuggest'
      || (b.type === 'text' && hasMath(b.text)))
    if (needsMedia(reply)) {
      const resolved = await resolveBlockMedia(sock, reply)
      if (!resolved) return null
      blocks = [resolved]
    }
    const card = buildRichCard(blocks, { disclaimerText: reply.disclaimerText || '', progressIndicator: reply.progressIndicator })
    if (!card) return null
    return await relayCard(sock, chatId, card, quoted)
  }
  // Kartu gabungan beberapa blok.
  if (type === 'richResponse' || type === 'card') {
    let blocks = reply.blocks || reply.richResponse || []
    // Selesaikan media (image/video/math) di dalam blok gabungan juga.
    const { resolveBlockMedia, hasMath } = await import('./airich.js')
    const needs = (b) => b && (
      b.type === 'image' || b.type === 'math' || b.type === 'video'
      || b.type === 'fullimage' || b.type === 'latex' || b.type === 'carousel'
      || b.type === 'reel' || b.type === 'post'
      || b.type === 'product' || b.type === 'compact' || b.type === 'igsuggest'
      || (b.type === 'text' && hasMath(b.text)))
    if (blocks.some(needs)) {
      const out = []
      for (const b of blocks) {
        if (needs(b)) {
          const resolved = await resolveBlockMedia(sock, b)
          if (resolved) out.push(resolved)
        } else if (b) {
          out.push(b)
        }
      }
      blocks = out
    }
    const card = buildRichCard(blocks, { disclaimerText: reply.disclaimerText || '', progressIndicator: reply.progressIndicator })
    if (!card) return null
    return await relayCard(sock, chatId, card, quoted)
  }
  if (type === 'event') {
    return await sock.sendMessage(chatId, { event: coerceEventDates(reply.event) }, opts)
  }
  if (type === 'poll') {
    const poll = { ...(reply.poll || {}) }
    if (typeof poll.endDate === 'string' || typeof poll.endDate === 'number') {
      const d = new Date(poll.endDate)
      if (!Number.isNaN(d.getTime())) poll.endDate = d
    }
    return await sock.sendMessage(chatId, { poll }, opts)
  }
  if (type === 'groupInvite') {
    return await sock.sendMessage(chatId, { groupInvite: reply.groupInvite || {} }, opts)
  }
  if (type === 'location') {
    const loc = reply.location || {}
    if (loc.degreesLatitude == null || loc.degreesLongitude == null) return null
    return await sock.sendMessage(chatId, {
      location: {
        degreesLatitude: Number(loc.degreesLatitude),
        degreesLongitude: Number(loc.degreesLongitude),
        ...(loc.name ? { name: String(loc.name) } : {}),
        ...(loc.address ? { address: String(loc.address) } : {}),
      },
    }, opts)
  }
  if (type === 'contacts') {
    const c = reply.contacts || {}
    if (!(c.contacts || []).length) return null
    return await sock.sendMessage(chatId, { contacts: c }, opts)
  }
  if (type === 'audio') {
    // Audio/voice note: unggah media dulu (URL eksternal tidak langsung dipakai
    // WhatsApp), lalu kirim sebagai audioMessage.
    const { resolveMedia } = await import('./airich.js')
    const src = reply.url || reply.media
    if (!src) return null
    const waUrl = await resolveMedia(sock, src, 'audio')
    if (!waUrl) return null
    return await sock.sendMessage(chatId, {
      audio: { url: waUrl },
      ptt: Boolean(reply.ptt),
      mimetype: reply.mimetype || 'audio/mpeg',
    }, opts)
  }
  if (type === 'widget') {
    const msg = bloksWidgetMessage(reply.widget || {})
    return await sock.relayMessage(chatId, msg, { messageId: await generateTag(sock) })
  }
  if (type === 'web2ui' || type === 'richHtml') {
    const msg = richHtmlMessage(reply.html || '', {
      title: reply.title || 'Preview', sources: reply.sources || [], url: reply.url,
    })
    return await relayCard(sock, chatId, msg, quoted)
  }
  return null
}

// Kirim widget HTML ke sebuah chat (kompat lama).
export async function sendRichHtml(sock, chatId, html, opts = {}) {
  const msg = richHtmlMessage(html, opts)
  return await relayCard(sock, chatId, msg)
}
