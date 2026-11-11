// router.js — jembatan antara transport WA dan inti agent (Python).
//
// Dua jalur:
//   1. Perintah cepat "!"  → deterministik, TANPA AI (instan, tanpa biaya)
//   2. Pesan biasa         → diteruskan ke inti agent (Python) via HTTP lokal
//
// Semua balasan dikirim balik ke WhatsApp dari sini.

import { loadCommands, bufferHtml, looksLikeHtml, extractHtml, stripLeadingMentions } from './commands.js'
import { buildIndex } from './lid.js'
import { runtimeBlocks } from '../plugins/_runtime-core.js'
import { markOwn } from './state.js'
import { forgetSent } from './sentlog.js'
import { sendRich, sendRichHtml, groupReplies, editRichCard } from './api/rich.js'
import { buildProgressIndicatorMetadata, progressStep, PlanningStepStatus } from './api/progress.js'
import { listPlugins, runPlugin } from './plugin-host.js'
import { recordGroupMessage, enableGroupSummary, disableGroupSummary, getGroupSummaryStatus } from './group-summary.js'
import { appendFile, mkdir, unlink, readFile, writeFile, rename } from 'node:fs/promises'
import { basename, resolve as resolvePath } from 'node:path'
import http from 'node:http'
import { agentHeaders } from './agent-auth.js'
import { traceBlocks, divider, spamBlocks, bersih } from './api/ui.js'
import { ambil as ambilPilihan, lupakan as lupakanPilihan } from './api/pilihan.js'
import { Kemajuan } from './api/kemajuan.js'
import { htmlKeBerkas } from './api/htmlfile.js'
import { engineMode, engineReply, inspectMediaOnly } from './engine.js'
import https from 'node:https'
import { shouldInspectMedia } from './media-policy.js'
import { isAfkGroupCommand } from './afk-policy.js'
import { getModelPreference, setModelPreference } from './model-prefs.js'

let SOCK = null
let COMMANDS = null
// Setelan UI dari config (dipasang transport/wa.js saat start).
let UI = {}
const ADD_OWNER_PENDING = new Map()

export function setSock(sock) { SOCK = sock }

/** Setelan tampilan: `forceCard` (semua balasan jadi kartu GenAI), dsb. */
export function setUiConfig(ui) { UI = ui && typeof ui === 'object' ? ui : {} }

// Seam untuk pengujian: tes bisa menyuntikkan transport HTTP tiruan (mengganti
// `httpPostJson`), karena `fetch` global sudah tidak dipakai lagi (lihat di
// bawah — batas 300s tersembunyi undici). Produksi memakai `null` = node:http.
let AGENT_TRANSPORT = null
export function setAgentTransport(fn) { AGENT_TRANSPORT = fn }
export function resetAgentTransport() { AGENT_TRANSPORT = null }

// Catat peristiwa penting ke data/audit.log (best-effort) supaya kegagalan
// transport (mis. agent tidak bisa dihubungi) BISA didiagnosa dari produksi,
// bukan hanya hilang di stderr.
async function audit(event, fields = {}) {
  try {
    const dir = resolvePath(process.cwd(), 'data')
    await mkdir(dir, { recursive: true })
    const line = JSON.stringify({ ts: Math.floor(Date.now() / 1000), event, ...fields })
    await appendFile(resolvePath(dir, 'audit.log'), line + '\n')
  } catch { /* abaikan */ }
}

// Bangun ctx untuk plugin yang dipicu lewat perintah "!".
function pluginCtx(sock, msg = {}) {
  return {
    wa: {
      send: (to, content) => sock.sendMessage(to || msg.chatId, typeof content === 'string' ? { text: content } : content),
      // `blocks` boleh array (kartu biasa) ATAU objek balasan penuh
      // ({type,blocks,progressIndicator,disclaimerText}) untuk fitur yang tidak
      // bisa diungkapkan lewat array blok saja.
      sendRich: (to, blocks) => sendRich(sock, to || msg.chatId, Array.isArray(blocks) ? { type: 'richResponse', blocks } : blocks),
      raw: () => sock,
    },
    airich: { sendRich, groupReplies, buildProgressIndicatorMetadata, progressStep, PlanningStepStatus },
    log: () => {},
  }
}

// Ambil konteks KUTIPAN (reply) dari pesan masuk: id pesan yang dibalas,
// pengirimnya, dan apakah itu pesan bot sendiri. Dipakai plugin `delete` agar
// pemilik cukup me-REPLY pesan lalu ketik `!delete`.
function quotedOf(message) {
  const m = (message && message.message) || {}
  const ci = m.extendedTextMessage?.contextInfo
    || m.imageMessage?.contextInfo
    || m.videoMessage?.contextInfo
    || m.audioMessage?.contextInfo
    || m.documentMessage?.contextInfo
    || m.stickerMessage?.contextInfo
    || null
  if (!ci || !ci.stanzaId) return null
  // Pesan bot sendiri tidak punya `participant` di kutipan (atau participant
  // == JID bot). Selain itu, kiriman orang lain selalu punya `participant`.
  const me = [SOCK?.user?.id, SOCK?.user?.lid].filter(Boolean).map((j) => String(j).split('@')[0].split(':')[0])
  const who = ci.participant ? String(ci.participant).split('@')[0].split(':')[0] : ''
  const fromMe = !ci.participant || (who && me.includes(who))
  return { id: ci.stanzaId, fromMe, participant: ci.participant || '' }
}

// JID pengirim → token identitas (buang :device dan @server).
export function jidToNumber(jid) {
  return String(jid || '').split('@')[0].split(':')[0]
}

function isLidJid(jid) {
  const s = String(jid || '')
  return s.endsWith('@lid') || s.endsWith('@hosted.lid')
}

/**
 * Token bertipe dari sederet JID: { kind, num }.
 *
 * Jenis (`lid`/`pn`) WAJIB dipertahankan: angka LID dan nomor telepon berbagi
 * ruang angka, jadi mencocokkan angka mentah bisa menukar identitas dua owner.
 */
function typedTokens(value) {
  const out = []
  const list = Array.isArray(value) ? value.flat(Infinity) : [value]
  for (const v of list) {
    if (v == null) continue
    const n = jidToNumber(v)
    if (!n) continue
    out.push({ kind: isLidJid(v) ? 'lid' : 'pn', num: n })
  }
  return out
}

/**
 * Cek owner berdasarkan SEMUA identitas yang diketahui — SADAR-TIPE.
 *
 * PN pengirim cocok dengan PN owner; LID pengirim cocok HANYA bila LID itu
 * dipetakan (lid2pn) ke nomor owner. TIDAK ada pencocokan angka mentah
 * lintas-jenis, sehingga tabrakan LID<->PN tidak menukar identitas owner.
 */
export function isOwner(config, ...ids) {
  const lidDir = config?.whatsapp?.authDir
  const idx = lidDir ? buildIndex(lidDir) : null
  const owners = typedTokens(config.owner)
  const sender = ids.flatMap((id) => typedTokens(id))
  for (const s of sender) {
    for (const o of owners) {
      if (s.kind === o.kind && s.num === o.num) return true
      if (!idx) continue
      if (s.kind === 'lid' && o.kind === 'pn' && idx.lid2pn.get(s.num) === o.num) return true
      if (s.kind === 'pn' && o.kind === 'lid' && idx.pn2lid.get(s.num) === o.num) return true
    }
  }
  return false
}

function flatIds(value) {
  if (value == null) return []
  if (Array.isArray(value)) return value.flatMap(flatIds)
  const n = jidToNumber(value)
  return n ? [n] : []
}

// Perluas token dengan padanan LID<->PN dari indeks store sesi (bila ada).
// CATATAN: sudah TIDAK dipakai lagi — penggantinya `typedTokens` + `isOwner`
// yang sadar-tipe (mencegah tabrakan LID<->PN). Dihapus agar tidak ada jebakan
// fallback silang yang tertinggal.

// Kirim balasan (teks). Jeda kecil supaya tidak dianggap spam.
async function sendText(chatId, text, quoted) {
  if (!text) return
  const opts = quoted ? { quoted } : {}
  const sent = await SOCK.sendMessage(chatId, { text: String(text) }, opts)
  // Catat id pesan bot sendiri supaya self-chat tidak memicu loop balasan.
  // (Pencatatan ke sent.log untuk fitur hapus kini OTOMATIS lewat instrumentasi
  // socket di transport/wa.js — jadi tidak perlu dicatat manual di sini.)
  markOwn(sent?.key?.id)
  const delay = 800
  if (delay > 0) await new Promise((r) => setTimeout(r, delay))
}

// Kirim daftar balasan dari agent (bisa multi-bubble, multi-jenis blok).
// Deret teks+kode+tabel digabung jadi SATU kartu AI Rich agar tidak terpecah.
/**
 * Gabungkan jejak langkah (thinking/tool/shell) ke balasan pertama.
 *
 * Hasil: balasan pertama berubah menjadi kartu berisi ringkasan langkah,
 * garis pemisah, lalu teks jawaban apa adanya. Balasan lain (kode, tabel,
 * gambar) dibiarkan — itu hasil kerja, bukan narasi.
 *
 * Bisa dimatikan lewat config `ui.showTrace: false`.
 */
export function withTrace(replies, trace, config) {
  const daftar = Array.isArray(replies) ? replies.slice() : []
  if (!daftar.length) return daftar
  if (config?.ui?.showTrace === false) return daftar
  const langkah = Array.isArray(trace) ? trace : []
  if (!langkah.length) return daftar

  const blok = traceBlocks(langkah)
  if (!blok.length) return daftar

  const pertama = daftar[0]
  if (!pertama || pertama.type !== 'text' || !String(pertama.text || '').trim()) {
    // Balasan pertama bukan teks (kartu/kode): sisipkan langkah ke DALAM kartu
    // itu. Mengirim kartu langkah terpisah berarti dua pesan untuk satu giliran.
    if (pertama && pertama.type === 'card' && Array.isArray(pertama.blocks)) {
      pertama.blocks = [...blok, divider(), ...pertama.blocks]
      return daftar
    }
    daftar.unshift({ type: 'card', blocks: blok })
    return daftar
  }

  daftar[0] = {
    type: 'card',
    blocks: [...blok, divider(), { type: 'text', text: String(pertama.text) }],
  }
  return daftar
}

/**
 * Buang aksara Tionghoa/Jepang/Korea dari balasan sebelum dikirim.
 *
 * Model kadang menyelipkan aksara CJK walau sudah dilarang di prompt; di
 * banyak ponsel aksara itu tampil sebagai kotak. Blok kode TIDAK disentuh —
 * isi kode harus utuh apa adanya.
 */
export function bersihkanBalasan(reply) {
  if (!reply || typeof reply !== 'object') return reply
  if (reply.type === 'text' && typeof reply.text === 'string') {
    reply.text = bersih(reply.text)
  }
  if (Array.isArray(reply.blocks)) {
    for (const b of reply.blocks) {
      if (!b || typeof b !== 'object') continue
      if (b.type === 'text' && typeof b.text === 'string') b.text = bersih(b.text)
      if (b.type === 'label') {
        if (typeof b.text === 'string') b.text = bersih(b.text)
        if (typeof b.detail === 'string') b.detail = bersih(b.detail)
      }
    }
  }
  return reply
}

/**
 * Gabungkan teks berurutan menjadi SATU pesan.
 *
 * Model kadang menjawab dalam beberapa bagian pendek; mengirimnya satu per satu
 * membuat chat penuh balon pesan. Digabung dengan jarak satu baris kosong,
 * dibaca sebagai satu jawaban. Kalau kepanjangan (> 3500 huruf) dipecah lagi
 * supaya tidak kena batas WhatsApp.
 */
function gabungTeks(daftar) {
  const hasil = []
  let buf = ''
  const buang = () => {
    if (!buf.trim()) { buf = ''; return }
    while (buf.length > 3500) {
      const potong = buf.lastIndexOf('\n', 3500)
      const titik = potong > 1000 ? potong : 3500
      hasil.push({ type: 'text', text: buf.slice(0, titik).trim() })
      buf = buf.slice(titik).trim()
    }
    if (buf.trim()) hasil.push({ type: 'text', text: buf.trim() })
    buf = ''
  }
  for (const r of daftar) {
    if (r && r.type === 'text') {
      buf += (buf ? '\n\n' : '') + String(r.text || '')
      continue
    }
    buang()
    hasil.push(r)
  }
  buang()
  return hasil
}

async function sendReplies(chatId, replies, quoted) {
  // Anti-dobel DALAM satu giliran saja: kalau model mengembalikan dua balasan
  // yang isinya sama persis, cukup satu yang dikirim. Tidak berlaku antar
  // giliran — pengguna boleh bertanya hal sama dua kali.
  const sudahKirim = new Set()

  for (const r of gabungTeks(groupReplies(replies))) {
    bersihkanBalasan(r)
    try {
      if (r.type === 'text') {
        const isi = String(r.text || '').trim()
        if (!isi) continue
        if (sudahKirim.has(isi)) {
          console.warn('[router] teks dobel dalam satu giliran dilewati')
          continue
        }
        sudahKirim.add(isi)
        // WAJIB GenAI: teks pun dikirim sebagai kartu (_typename), bukan tipe
        // biasa. Bila perakitan kartu gagal, baru jatuh ke teks polos supaya
        // pengguna tetap dapat jawaban.
        if (UI.forceCard !== false) {
          const kartu = await sendRich(SOCK, chatId, { type: 'text', text: isi }, quoted).catch(() => null)
          if (kartu) {
            markOwn(kartu?.key?.id)
            await new Promise((res) => setTimeout(res, 400))
            continue
          }
        }
        await sendText(chatId, isi, quoted)
        continue
      }
      if (r.type === 'html_file') {
        const isi = String(r.html || '')
        if (!isi.trim()) continue
        const namaAsli = basename(String(r.filename || 'kwee-output.html')).replace(/[^a-zA-Z0-9._-]/g, '-')
        const fileName = /\.html?$/i.test(namaAsli) ? namaAsli : `${namaAsli || 'kwee-output'}.html`
        const doc = await SOCK.sendMessage(chatId, {
          document: Buffer.from(isi, 'utf8'),
          mimetype: 'text/html',
          fileName,
          ...(r.caption ? { caption: String(r.caption) } : {}),
        }, quoted ? { quoted } : {})
        markOwn(doc?.key?.id)
        continue
      }
      if (r.type === 'image') {
        const img = await SOCK.sendMessage(chatId, { image: { url: r.path }, caption: r.caption || '' }, quoted ? { quoted } : {})
        markOwn(img?.key?.id)
        continue
      }
      if (r.type === 'buttons') {
        // Tombol sederhana (fallback teks bila tidak didukung)
        await sendText(chatId, `${r.text}\n\n${(r.buttons || []).map((b) => `• ${b.label}`).join('\n')}`, quoted)
        continue
      }
      // Blok kaya (code/table/event/poll/invite/widget/web2ui) → transport/api/rich.js; html_file dikirim sebagai dokumen.
      const sent = await sendRich(SOCK, chatId, r, quoted)
      if (sent) {
        markOwn(sent?.key?.id)
        await new Promise((res) => setTimeout(res, 800))
      } else {
        await sendText(chatId, r.text || JSON.stringify(r), quoted)
      }
    } catch (e) {
      console.error('[router] gagal kirim balasan', e.message)
    }
  }
}

// GET JSON lewat node:http (tanpa batas 300s tersembunyi milik fetch()).
function httpGetJson(url, timeoutMs) {
  // Seam pengujian: `AGENT_TRANSPORT` dipanggil dengan body=null untuk GET.
  if (typeof AGENT_TRANSPORT === 'function') return AGENT_TRANSPORT(url, null, timeoutMs)
  return new Promise((resolve, reject) => {
    let u
    try { u = new URL(url) } catch (e) { return reject(e) }
    const lib = u.protocol === 'https:' ? https : http
    let done = false
    const finish = (fn, arg) => { if (!done) { done = true; fn(arg) } }
    const req = lib.request({
      protocol: u.protocol,
      hostname: u.hostname,
      port: u.port || (u.protocol === 'https:' ? 443 : 80),
      path: u.pathname + u.search,
      method: 'GET',
      headers: agentHeaders({ 'Content-Type': 'application/json' }),
      agent: false,
    }, (res) => {
      const chunks = []
      res.on('data', (c) => chunks.push(c))
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8')
        finish(resolve, { status: res.statusCode || 0, text })
      })
      res.on('error', (e) => finish(reject, e))
    })
    req.setTimeout(timeoutMs || 15000, () => {
      const e = new Error('agent timeout')
      e.name = 'TimeoutError'
      req.destroy(e)
    })
    req.on('error', (e) => finish(reject, e))
    req.end()
  })
}

// Kirim permintaan berhenti ke inti agent (dipakai perintah !stop).
export async function requestCancel(config, chatId) {
  const url = `http://${config.agent.host || '127.0.0.1'}:${config.agent.port || 8787}/cancel`
  try {
    await httpPostJson(url, { chat_id: chatId }, 10000)
    return true
  } catch { return false }
}

// Pantau job background sampai selesai, lalu kembalikan balasannya.
//
// Ini kunci "tugas agentic selalu selesai": transport TIDAK menunggu satu
// koneksi HTTP panjang (yang punya plafon). Ia menyerahkan tugas ke inti agent
// (mode background) lalu memeriksa statusnya berkala. Berapa pun lamanya tugas,
// hasilnya tetap dikirim saat selesai.
export async function pollJob(config, jobId, chatId) {
  const host = config.agent?.host || '127.0.0.1'
  const port = config.agent?.port || 8787
  const url = `http://${host}:${port}/job/${encodeURIComponent(jobId)}`
  // Poll awal RAPAT (tugas cepat terasa cepat), lalu melambat (tugas panjang
  // tidak membebani server dengan polling terus-menerus).
  const firstMs = Math.max(500, Number(config.agent?.jobFirstPollMs) || 1200)
  const intervalMs = Math.max(2000, Number(config.agent?.jobPollMs) || 4000)
  // Plafon keselamatan tinggi (default 6 jam). Bukan pembatas tugas normal —
  // hanya jaring supaya job yang benar-benar macet tidak menggantung selamanya.
  const ceilingS = Number(config.agent?.maxTaskSeconds) || 21600
  const deadline = Date.now() + ceilingS * 1000
  let first = true
  // Toleransi balasan "unknown" BERTURUT-TURUT. Inti agent bisa sedang
  // di-restart (job in-memory belum pulih) sehingga satu-dua balasan unknown
  // bukan berarti tugas hilang. Setelah ambang ini, barulah menyerah dengan
  // pesan yang jelas & bisa dilanjutkan — bukan menggantung selamanya.
  let unknownStreak = 0
  const MAX_UNKNOWN = Math.max(3, Number(config.agent?.jobUnknownTolerance) || 5)
  for (;;) {
    await new Promise((r) => setTimeout(r, first ? firstMs : intervalMs))
    first = false
    let res
    try {
      res = await httpGetJson(url, 15000)
    } catch (e) {
      // Gangguan sesaat saat memantau — coba lagi, jangan buang tugas.
      await audit('agent.poll_error', { chat: chatId, job: jobId, error: String(e?.message || e) })
      if (Date.now() > deadline) throw jobFail('timeout', 'job tidak selesai dalam batas keselamatan')
      continue
    }
    let j
    try { j = JSON.parse(res.text) } catch { j = null }
    if (!j || j.status === 'unknown' || j.ok === false) {
      unknownStreak += 1
      if (unknownStreak < MAX_UNKNOWN && Date.now() <= deadline) {
        await audit('agent.poll_unknown', { chat: chatId, job: jobId, streak: unknownStreak })
        continue
      }
      throw jobFail('lost', j?.error || 'job hilang sebelum selesai')
    }
    unknownStreak = 0
    if (j.status === 'done') {
      await audit('agent.job_done', { chat: chatId, job: jobId })
      return { replies: j.replies || [], raw: j.raw || '', trace: j.trace || [], activity: j.activity || null, activity_html: j.activity_html || '' }
    }
    if (j.status === 'error') {
      await audit('agent.job_error', { chat: chatId, job: jobId, error: j.error || '' })
      throw jobFail('error', j.error || 'job gagal')
    }
    if (Date.now() > deadline) {
      await audit('agent.job_timeout', { chat: chatId, job: jobId })
      throw jobFail('timeout', 'job melebihi batas keselamatan')
    }
  }
}

// Tandai kegagalan JOB (bukan gangguan transport) supaya pemanggil bisa memilih
// pesan yang tepat: tugas terhenti tapi progresnya tersimpan → tawarkan !lanjut.
function jobFail(kind, message) {
  const e = new Error(message)
  e.jobFailure = kind   // 'lost' | 'error' | 'timeout'
  return e
}

// POST JSON lewat node:http — TANPA batas 300s tersembunyi.
//
// PENTING: `fetch()` global Node (undici) punya `headersTimeout` default
// **300 detik** yang TIDAK bisa diubah lewat AbortSignal. Akibatnya tugas
// panjang (>5 menit) SELALU diputus di 300s dengan error "fetch failed" —
// padahal inti agent masih bekerja (terbukti di audit: tool call terus jalan
// setelah kegagalan). Karena itu permintaan ke agent memakai node:http murni
// dengan timeout sendiri.
function httpPostJson(url, bodyObj, timeoutMs) {
  if (typeof AGENT_TRANSPORT === 'function') return AGENT_TRANSPORT(url, bodyObj, timeoutMs)
  return new Promise((resolve, reject) => {
    let u
    try { u = new URL(url) } catch (e) { return reject(e) }
    const lib = u.protocol === 'https:' ? https : http
    const data = Buffer.from(JSON.stringify(bodyObj))
    let done = false
    const finish = (fn, arg) => { if (!done) { done = true; fn(arg) } }
    const req = lib.request({
      protocol: u.protocol,
      hostname: u.hostname,
      port: u.port || (u.protocol === 'https:' ? 443 : 80),
      path: u.pathname + u.search,
      method: 'POST',
      headers: agentHeaders({ 'Content-Type': 'application/json',
                                'Content-Length': data.length }),
      // keepAlive mati: tiap turn koneksi baru, tidak ada socket basi.
      agent: false,
    }, (res) => {
      const chunks = []
      res.on('data', (c) => chunks.push(c))
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8')
        finish(resolve, { status: res.statusCode || 0, text })
      })
      res.on('error', (e) => finish(reject, e))
    })
    // Timeout TUNGGU-RESPON (bukan idle socket): agent boleh berpikir lama,
    // tapi tidak boleh menggantung tanpa batas.
    req.setTimeout(timeoutMs, () => {
      const e = new Error('agent timeout')
      e.name = 'TimeoutError'
      req.destroy(e)
    })
    req.on('error', (e) => finish(reject, e))
    req.write(data)
    req.end()
  })
}

// Panggil inti agent (Python).
//
// Batas waktu DI SINI harus LEBIH PANJANG dari `agent.timeoutSeconds` (Python),
// bukan sebaliknya. Kalau router menyerah lebih dulu, tugas coding/scraper yang
// masih berjalan akan diputus dan pengguna melihat "agent tidak bisa dihubungi"
// padahal agent masih bekerja. Beri margin 60s di atas batas agent.
export async function callAgent(config, payload, opts = {}) {
  const url = `http://${config.agent.host || '127.0.0.1'}:${config.agent.port || 8787}/message`
  const agentTimeoutS = Number(config.agent?.timeoutSeconds || 600)
  const abortMs = (agentTimeoutS + 60) * 1000
  const wantBackground = opts.background === true
  const body = wantBackground ? { ...payload, mode: 'background' } : payload
  let lastErr = null
  // Satu percobaan ulang untuk kegagalan transien (koneksi putus / 5xx).
  // Kegagalan ABORT (waktu habis) TIDAK diulang — agent masih bekerja.
  for (let attempt = 1; attempt <= 2; attempt++) {
    try {
      // Mode background: serahkan tugas lalu pantau lewat /job (tanpa plafon).
      // Timeout POST cukup pendek karena Python hanya menerima & langsung balas.
      const postMs = wantBackground ? 30000 : abortMs
      const res = await httpPostJson(url, body, postMs)
      if (res.status < 200 || res.status >= 300) {
        lastErr = new Error(`agent HTTP ${res.status}${res.text ? `: ${res.text.slice(0, 200)}` : ''}`)
        await audit('agent.error', { chat: payload.chat_id, attempt, error: lastErr.message })
        if (res.status >= 500 && attempt < 2) { await new Promise((r) => setTimeout(r, 1500)); continue }
        throw lastErr
      }
      let parsed
      try {
        parsed = JSON.parse(res.text)
      } catch (e) {
        lastErr = new Error(`agent balas bukan JSON: ${res.text.slice(0, 200)}`)
        await audit('agent.error', { chat: payload.chat_id, attempt, error: lastErr.message })
        throw lastErr
      }
      // Mode background: tunggu hasilnya sampai selesai (tanpa plafon tunggu).
      if (wantBackground && parsed && parsed.background && parsed.job_id) {
        return await pollJob(config, parsed.job_id, payload.chat_id)
      }
      return parsed
    } catch (e) {
      lastErr = e
      const aborted = e?.name === 'TimeoutError' || e?.name === 'AbortError'
      await audit('agent.error', { chat: payload.chat_id, attempt,
                  aborted: !!aborted, error: String(e?.message || e) })
      if (aborted) throw e            // jangan ulang: agent masih berjalan
      if (attempt < 2) { await new Promise((r) => setTimeout(r, 1500)); continue }
      throw e
    }
  }
  throw lastErr || new Error('agent tidak bisa dihubungi')
}

// Tampilkan status kerja (typing + pesan progres yang diperbarui) selagi agent
// mengerjakan tugas. Ini yang membuat pemilik tahu bot TIDAK mati: indikator
// "mengetik…" aktif terus, dan satu pesan status diperbarui (diedit) dengan
// langkah terakhir dari /status.
//
// Semua efek samping di sini bersifat best-effort: kegagalan tidak boleh
// menggagalkan pengiriman balasan akhir.
async function withProgress(chatId, quoted, promise, afterMs, config) {
  const agentHost = config?.agent?.host || '127.0.0.1'
  const agentPort = config?.agent?.port || 8787

  const bacaStatus = async () => {
    try {
      const r = await fetch(`http://${agentHost}:${agentPort}/status?chat_id=${encodeURIComponent(chatId)}`, {
        signal: AbortSignal.timeout(4000),
        headers: agentHeaders(),
      })
      if (!r.ok) return null
      const j = await r.json()
      return (j.activities || [])[0] || null
    } catch { return null }
  }

  // Tandai pesan dibaca (centang biru) — sekali, bukan tiap langkah.
  try { await SOCK.readMessages?.([{ remoteJid: chatId, id: quoted?.key?.id }]) } catch { /* abaikan */ }

  const kemajuan = new Kemajuan({
    chatId,
    sock: SOCK,
    quoted,
    tungguMs: afterMs,
    bacaStatus,
    tandai: markOwn,
    lupakan: (id) => forgetSent(chatId, id),
    // Status (thinking/progres) = KARTU GenAI, diperbarui dengan EDIT kartu.
    genai: {
      kirim: (blok) => sendRich(SOCK, chatId, blok),
      edit: (id, blok, opts) => editRichCard(SOCK, chatId, id, blok, opts),
    },
    // Laporan progres .html mandiri (dilampirkan sesudah jawaban). Matikan
    // lewat agent.progressHtml: false. Interval edit kartu bisa dipercepat
    // lewat agent.progressTickMs (minimal 1500 ms) agar bilah terasa lebih hidup.
    laporanHtml: config?.agent?.progressHtml !== false,
    laporanMode: config?.agent?.progressHtmlMode === 'file' ? 'file' : 'card',
    jedaMinimalMs: Number(config?.agent?.progressTickMs) || undefined,
    jedaMs: Number(config?.agent?.progressTickMs) || undefined,
    // Modul progres HTML ringkas: jejak tool, tahap skills, lalu jawaban satu kartu.
    modul: config?.agent?.progressModul === false ? null : {
      kirim: (blok) => sendRich(SOCK, chatId, { type: 'card', blocks: blok }),
      edit: (id, blok, opts) => editRichCard(SOCK, chatId, id, blok, opts),
    },
  })
  kemajuan.mulai()

  try {
    return { hasil: await promise, kemajuan }
  } catch (e) {
    // Error ditangani pemanggil; di sini cukup pastikan tidak ada pesan sisa.
    await kemajuan.batal()
    throw e
  }
}

// Teks/perintah yang diabaikan TOTAL: tidak diproses, tidak ditafsirkan,
// tidak dibalas sama sekali (termasuk tidak sampai ke inti agent / LLM).
// Aturan ini di level kode supaya tidak bergantung pada ingatan model.
const IGNORED_TEXT_PREFIXES = ['.run']

function isIgnoredText(text) {
  const t = String(text || '').trim().toLowerCase()
  if (!t) return false
  return IGNORED_TEXT_PREFIXES.some((p) => t === p || t.startsWith(p + ' ') || t.startsWith(p + '\n') || t.startsWith(p + '\t'))
}

// Path lampiran (media) yang perlu dikirim ulang sebagai SWGC: pesan ini
// sendiri lebih dulu; kalau tidak ada, pesan yang di-reply.
function mediaPathOf(ctx) {
  const own = ctx.attachment
  if (own && ['image', 'video', 'document'].includes(own.kind)) return own.path
  const q = ctx.quotedAttachment
  if (q && ['image', 'video', 'document'].includes(q.kind)) return q.path
  return ''
}

export async function handleIncoming(ctx) {
  const { config, chatId, isGroup, senderId, senderAlt, senderIds, message } = ctx
  const ids = [senderIds, senderId, senderAlt].filter(Boolean)
  const senderIsOwner = isOwner(config, ...ids)
  const isPublicChat = !senderIsOwner && !isGroup
    && config?.publicChat?.enabled !== false
    && config?.publicChat?.allowGroups !== true
  // Buang sebutan (mention) di awal teks — mis. "@628123 !run" dari grup — supaya
  // perintah "!" tetap terdeteksi meski didahului mention bot. Tapi HANYA dipakai
  // bila hasilnya memang perintah "!": kalau tidak, teks asli dipertahankan agar
  // mention ke ORANG LAIN di awal tidak hilang dari konteks AI. Mention bot tetap
  // tersampaikan lewat `is_mention_bot` yang dikirim terpisah.
  const withoutMention = stripLeadingMentions(ctx.text)
  let text = withoutMention.startsWith('!') ? withoutMention : ctx.text
  const afkParticipantCommand = isAfkGroupCommand(text, isGroup)

  // Media hanya dianalisis atas permintaan eksplisit: reply dengan instruksi,
  // atau mention bot pada pesan media yang memiliki caption. Unduhan tetap
  // tersedia di transport; lampiran tidak diteruskan ke LLM jika tidak diminta.
  const inspectMedia = shouldInspectMedia({
    text, attachment: ctx.attachment || ctx.incomingAttachment,
    quotedAttachment: ctx.quotedAttachment,
    isMentionBot: Boolean(ctx.isMentionBot),
  })
  const mediaFiles = [ctx.attachment, ctx.quotedAttachment].filter(Boolean)
  const cleanupMedia = async (items = mediaFiles) => Promise.all((items || []).map(async (item) => {
    if (!item?.path) return
    try { await unlink(item.path) } catch { /* sudah dipindah/dibersihkan */ }
  }))
  const isVisual = (item) => ['image', 'video', 'sticker'].includes(String(item?.kind || '').toLowerCase())
  const visualFiles = mediaFiles.filter(isVisual)
  // Dokumen/audio tetap tersedia bagi perintah/tool non-Vision. Hanya media
  // visual yang ditahan dari AI ketika tidak ada permintaan eksplisit.
  const attachments = mediaFiles.filter((item) => !isVisual(item) || inspectMedia)
  if (!String(text || '').trim() && attachments.length && !attachments.some(isVisual)) {
    text = `(Aku mengirim ${attachments[0].kind || 'berkas'}. Tolong periksa sesuai permintaanku.)`
  }
  const hasVisualMeta = isVisual(ctx.attachment || ctx.incomingAttachment)
    || isVisual(ctx.quotedAttachment || ctx.quotedAttachmentMeta)
  if (inspectMedia && (!visualFiles.length || ctx.attachmentDownloadFailed)) {
    await sendText(chatId, 'Aku menerima permintaan analisis, tetapi media WhatsApp gagal diunduh/dekripsi. Coba kirim ulang media tersebut lalu reply dengan pertanyaan atau tag @Kwee + caption.', message)
    await Promise.all(mediaFiles.map(async (item) => { if (item?.path) { try { await unlink(item.path) } catch {} } }))
    return
  }
  // Non-owner requests are intentionally vision-only: no shell, browser, files,
  // plugins, or other agent tools are reachable from an explicit media request.
  if (inspectMedia && !senderIsOwner && !isPublicChat) {
    try {
      const result = await inspectMediaOnly(attachments, config, text)
      await sendText(chatId, result || 'Vision tidak menghasilkan deskripsi yang dapat digunakan.', message)
    } catch (e) {
      await sendText(chatId, `Vision gagal membaca media: ${e?.message || e}`, message)
    } finally {
      await cleanupMedia()
    }
    return
  }
  if (hasVisualMeta && !inspectMedia && !String(text || '').trim() && senderIsOwner) {
    await cleanupMedia()
    await sendText(chatId, 'Media diterima. Aku tidak membacanya otomatis. Reply media dengan pertanyaan, atau tag @Kwee pada media bersama caption untuk meminta analisis.', message)
    return
  }

  // HTML dari pesan yang DI-REPLY (kalau ada) — dipakai `!run` supaya bisa
  // "reply pesan HTML lalu ketik !run". `extractHtml` membuang fence ``` dan
  // mengembalikan null bila kutipan bukan HTML.
  if (!ctx.quotedHtml && ctx.quoted?.text) {
    ctx.quotedHtml = extractHtml(ctx.quoted.text)
  }

  if (isGroup && !ctx.fromMe && !message?.key?.fromMe) {
    try { recordGroupMessage(chatId, senderId, ctx.text || '') } catch (e) { console.warn('[summary] pencatatan dilewati:', e.message) }
  }

  // PENJAGA GRUP (opsional, `groupAdmin.autoSpam: true`).
  // Menilai pesan anggota grup dengan heuristik di inti agent, lalu mengirim
  // kartu peringatan ke grup. Tidak menghapus apa pun — keputusan tetap di
  // tangan owner (tombol aksi di kartu hanya mengirim perintah).
  if (isGroup && config?.groupAdmin?.autoSpam && !senderIsOwner && !afkParticipantCommand && !inspectMedia
      && String(text || '').trim().length > 20) {
    try {
      const host = config.agent?.host || '127.0.0.1'
      const port = config.agent?.port || 8787
      const r = await fetch(`http://${host}:${port}/group`, {
        method: 'POST',
        signal: AbortSignal.timeout(4000),
        headers: agentHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ action: 'spam_check', chat_id: chatId, sender_id: senderId, teks: text, pengirim: senderId }),
      })
      const cek = await r.json()
      if (cek?.spam) {
        await audit('group.spam', { chat: chatId, sender: senderId, alasan: cek.alasan })
        await sendReplies(chatId, [{
          type: 'card',
          blocks: spamBlocks({ chat: chatId, pengirim: senderId, alasan: cek.alasan, cuplikan: text }),
        }], message)
        return
      }
    } catch (e) {
      // Pemeriksaan spam tidak boleh menghalangi pesan biasa.
      console.warn('[grup] pemeriksaan spam dilewati:', e.message)
    }
  }

  // 1) Secara default hanya owner yang dilayani. Pengecualian grup yang sempit:
  //    !afk untuk status diri. Permintaan media non-owner sudah dijawab lewat
  //    jalur Vision-only sebelum gate ini; tidak mendapat akses agent tools.
  //    `senderIds` = semua identitas (PN + LID) hasil resolve transport.
  if (!senderIsOwner && !afkParticipantCommand) {
    await cleanupMedia()
    // Log sekali per pengirim (bukan tiap pesan) + petunjuk memperbaikinya.
    const kunciAbai = ids.flat().join(',')
    if (!globalThis.__kweeAbai) globalThis.__kweeAbai = new Set()
    if (!globalThis.__kweeAbai.has(kunciAbai)) {
      globalThis.__kweeAbai.add(kunciAbai)
      console.log('[router] diabaikan (bukan owner):', ids.flat().join(', '),
        '— bila ini kamu, isi WA_OWNER_NUMBERS di .env dengan nomor tanpa + (mis. 6285743003734), lalu start ulang')
    }
    return
  }

  // 1a) Teks yang masuk daftar ABAIKAN (mis. ".run") → diam total, tanpa balasan.
  if (isIgnoredText(text)) {
    console.log('[router] diabaikan (teks terlarang):', String(text).slice(0, 60))
    return
  }

  // 1b) Lampiran file HTML → tampilkan PREVIEW (web2ui / WebView), tanpa AI.
  //     Transport sudah mengunduh isinya (ctx.htmlAttachment) karena dokumen
  //     tanpa caption tidak punya `text`. Deterministik & instan.
  if (ctx.htmlAttachment) {
    try {
      await bufferHtml(chatId, ctx.htmlAttachment)
      const sent = await sendRichHtml(SOCK, chatId, ctx.htmlAttachment, {
        url: 'https://example.com', sources: ['example.com'], title: 'Preview',
      })
      markOwn(sent?.key?.id)
    } catch (e) {
      await sendText(chatId, `Could not render the HTML preview: ${e.message}`, message)
    }
    return
  }

  // 1c) Pesan TEKS yang isinya HTML → simpan sebagai HTML terakhir (buffer),
  //     supaya `!run` (tanpa reply) bisa menampilkannya jadi widget. Tidak
  //     dijalankan otomatis supaya pemilik tetap memegang kendali.
  const buffered = extractHtml(text)
  if (buffered) {
    await bufferHtml(chatId, buffered).catch(() => {})
    // lanjut ke jalur AI/! di bawah (tidak return) agar tetap bisa dibalas.
  }

  // 1d) Kata kunci bebas `test` — SETELAH mention bot atau reply ke pesan bot.
  //     Sengaja diperlakukan SAMA PERSIS dengan `!ping`: satu modul
  //     (plugins/_runtime-core.js), satu bentuk kartu. Tanpa AI, tanpa token.
  //
  //     Syarat "berpihak ke bot" (mention/reply) mencegah obrolan biasa yang
  //     kebetulan memuat kata "test" ikut dibajak. Di chat pribadi, kata itu
  //     diterima apa adanya karena seluruh chat pribadi = owner.
  const probe = stripLeadingMentions(String(text ?? '')).trim().replace(/[!.?,\s]+$/, '')
  const addressedToBot = Boolean(ctx.isMentionBot || ctx.isReplyToBot)
  if (/^(?:test|tes)$/i.test(probe) && (addressedToBot || !isGroup)) {
    try {
      const plugins = await listPlugins().catch(() => [])
      const modules = plugins.filter((p) => p.ok).length
      await sendReplies(chatId, await runtimeBlocks({
        kind: 'test',
        modules,
      }), message)
    } catch (e) {
      console.error('[router] probe gagal', e.message)
      await sendText(chatId, `Could not read runtime: ${e.message}`, message)
    }
    await cleanupMedia()
    return
  }

  // 1e) Kata kunci TANPA prefix `swgc` — owner cukup menulis "swgc disini"
  //     atau "swgc <nama grup>" sambil mengirim/mereply media. Tanpa AI.
  const bare = stripLeadingMentions(String(text ?? '')).trim()
  const bareCmd = bare.match(/^swgc\b[\s:,.-]*(.*)$/i)
  if (bareCmd && (ctx.attachment || ctx.quotedAttachment)) {
    try {
      const argv = String(bareCmd[1] || '').trim().split(/\s+/).filter(Boolean)
      const out = await runPlugin('swgc', {
        args: { text: argv.join(' '), argv, media: mediaPathOf(ctx) },
        msg: { chatId, senderId, isGroup, text, messageId: ctx.messageId, quoted: quotedOf(message) },
        makeCtx: () => pluginCtx(SOCK, { chatId, senderId, isGroup, text }),
      })
      const res = typeof out.result === 'string' ? out.result : JSON.stringify(out.result)
      await sendText(chatId, res || 'SWGC terkirim.', message)
    } catch (e) {
      await sendText(chatId, `SWGC failed: ${e.message}`, message)
    }
    await cleanupMedia()
    return
  }

  if (/^\.(menu|help)\s*$/i.test(String(text || '').trim())) text = '!menu'

  // 2) Prefix '/' untuk perintah yang DIKENAL (mis. /prompt, /skills).
  //    Hanya diubah bila namanya benar-benar ada di daftar perintah, supaya
  //    pesan biasa yang diawali '/' (mis. "/etc/hosts isi apa?") tidak ditelan.
  if (!String(text).startsWith('!') && /^\/[a-z0-9_-]+(\s|$)/i.test(String(text).trim())) {
    COMMANDS = COMMANDS || await loadCommands()
    const probe = String(text).trim().slice(1).split(/\s+/)[0].toLowerCase()
    if (COMMANDS.has(probe) || ['models', 'model', 'addown', 'credit', 'usage'].includes(probe)) {
      text = '!' + String(text).trim().slice(1)
    }
  }

  // 1c) CTA NYATA: kartu menampilkan "♡ 1 · …". Balasan "1" diubah menjadi
  //     perintah aslinya, lalu diproses seperti perintah biasa. Ini bekerja di
  //     semua klien WhatsApp, tidak bergantung tombol yang sering mati.
  if (!String(text).trim().startsWith('!')) {
    try {
      const perintah = ambilPilihan(chatId, text)
      if (perintah) {
        await audit('cta.dipilih', { chat: chatId, pilihan: String(text).trim().slice(0, 8), perintah })
        lupakanPilihan(chatId)
        text = perintah
      }
    } catch (e) {
      console.warn('[cta] pilihan dilewati:', e.message)
    }
  }

  // 2) Lapisan perintah cepat "!" — deterministik, tanpa AI
  if (String(text).startsWith('!')) {
    COMMANDS = COMMANDS || await loadCommands()
    const [name, ...rest] = String(text).slice(1).trim().split(/\s+/)
    const rawCommand = String(name || '').toLowerCase()
    const lname = rawCommand === 'menu' ? 'help' : rawCommand
    if (lname === 'addown') {
      if (isGroup) {
        await sendText(chatId, 'Gunakan /addown hanya di chat pribadi dengan bot; perintah ini memberi hak owner penuh.', message)
      } else if (!senderIsOwner) {
        await sendText(chatId, 'Hanya owner yang sudah terdaftar boleh menambah owner.', message)
      } else {
        const actor = (senderIds || []).find((jid) => String(jid).endsWith('@s.whatsapp.net')) || senderId || senderAlt || ''
        const arg = String(rest[0] || '').trim()
        const target = String(rest[1] || arg).replace(/[^0-9]/g, '')
        const pendingKey = `${String(actor).replace(/[^0-9]/g, '')}:${target}`
        if (/^(confirm|konfirmasi)$/i.test(arg)) {
          const pending = ADD_OWNER_PENDING.get(pendingKey)
          if (!pending || pending.expires < Date.now()) {
            ADD_OWNER_PENDING.delete(pendingKey)
            await sendText(chatId, 'Tidak ada permintaan aktif untuk nomor itu. Mulai lagi: /addown 62xxxxxxxxxx', message)
          } else {
            const file = resolvePath(process.cwd(), 'data', 'owners.json')
            let existing = []
            try { existing = JSON.parse(await readFile(file, 'utf8')) } catch { existing = [] }
            const owners = [...new Set([...(Array.isArray(existing) ? existing : []), ...config.owner, pending.number].map((x) => String(x).replace(/\D/g, '')).filter(Boolean))]
            await mkdir(resolvePath(process.cwd(), 'data'), { recursive: true })
            const tmp = `${file}.tmp`
            await writeFile(tmp, JSON.stringify(owners, null, 2), { mode: 0o600 })
            await rename(tmp, file)
            config.owner = owners
            ADD_OWNER_PENDING.delete(pendingKey)
            await audit('owner.added', { by: String(actor).replace(/\D/g, ''), number: pending.number })
            await sendText(chatId, `Owner ${pending.number} ditambahkan. Hak owner penuh aktif; Python agent ikut membaca daftar persisten saat pesan berikutnya.`, message)
          }
        } else {
          const normalized = arg.replace(/\D/g, '')
          if (!/^62\d{8,13}$/.test(normalized)) {
            await sendText(chatId, 'Format: /addown 62xxxxxxxxxx · nomor harus format Indonesia 62… tanpa +.', message)
          } else if ((config.owner || []).some((n) => String(n).replace(/\D/g, '') === normalized)) {
            await sendText(chatId, `${normalized} sudah terdaftar sebagai owner.`, message)
          } else {
            const key = `${String(actor).replace(/[^0-9]/g, '')}:${normalized}`
            ADD_OWNER_PENDING.set(key, { number: normalized, expires: Date.now() + 5 * 60 * 1000 })
            await sendText(chatId, `Konfirmasi pemberian hak owner penuh kepada ${normalized}. Balas /addown confirm ${normalized} dalam 5 menit.`, message)
          }
        }
      }
      await cleanupMedia()
      return
    }
    if (!senderIsOwner && ['help', 'menu'].includes(lname)) {
      const models = [...new Set([config?.llm?.model, ...(config?.llm?.fallbackModels || [])].map((x) => String(x || '').trim()).filter(Boolean))]
      const suffix = models.length ? `\nModel bawaan: ${config.llm.model}\nDaftar model: /models\nGanti model: /model <nama> · Kembali ke bawaan: /model default` : '\nDaftar model belum diatur oleh admin server.'
      await sendText(chatId,
        `Kwee Agent · bantuan cepat\n\nChat pribadi ini terhubung ke AI Kwee. Kirim pertanyaan atau instruksi biasa untuk mulai. Untuk gambar, reply foto dengan pertanyaan atau kirim foto bersama caption !upscale untuk meningkatkan resolusi 2×.\n\nKuota: maksimal 1.000.000 token per akun dalam jendela bergulir 36 jam. Cek: /credit. Context prompt dibatasi konservatif untuk target 128K; model/provider tetap menentukan batas aktual.\n${suffix}\n\nPerintah owner, plugin lain, dan fitur grup tidak tersedia untuk akun pengguna umum.`, message)
      await cleanupMedia()
      return
    }
    if (lname === 'models' || lname === 'model') {
      const choices = [...new Set([config?.llm?.model, ...(config?.llm?.fallbackModels || [])].map((x) => String(x || '').trim()).filter(Boolean))]
      if (!choices.length) {
        await sendText(chatId, 'Daftar model belum dikonfigurasi di server. Owner perlu mengisi LLM_MODEL dan opsional LLM_FALLBACK_MODELS di .env.', message)
      } else if (lname === 'models') {
        const identity = (senderIds || []).find((jid) => String(jid).endsWith('@s.whatsapp.net')) || senderId || senderAlt
        const selected = await getModelPreference(identity)
        await sendText(chatId, `Model tersedia:\n${choices.map((m) => `${m === selected ? '• ' : ''}${m}`).join('\n')}\n\nPilih: /model <nama-model> · Default: /model default`, message)
      } else {
        const requested = String(rest[0] || '').trim()
        const identity = (senderIds || []).find((jid) => String(jid).endsWith('@s.whatsapp.net')) || senderId || senderAlt
        if (!requested) {
          const selected = await getModelPreference(identity)
          await sendText(chatId, `Model aktif: ${selected || config?.llm?.model || '(belum dikonfigurasi)'}`, message)
        } else if (/^(default|auto|reset)$/i.test(requested)) {
          await setModelPreference(identity, '')
          await sendText(chatId, `Model kembali ke bawaan: ${config?.llm?.model || '(belum dikonfigurasi)'}`, message)
        } else if (!choices.includes(requested)) {
          await sendText(chatId, `Model tidak ada di daftar konfigurasi. Gunakan /models untuk melihat pilihan.`, message)
        } else {
          await setModelPreference(identity, requested)
          await sendText(chatId, `Model chat ini diubah ke ${requested}.`, message)
        }
      }
      await cleanupMedia()
      return
    }
    if (lname === 'credit' || lname === 'usage') {
      if (senderIsOwner) {
        await sendText(chatId, 'Status kredit: unlimited (owner terdaftar di .env/daftar owner runtime).', message)
      } else if (!isPublicChat) {
        await sendText(chatId, 'Status kredit hanya tersedia lewat chat pribadi.', message)
      } else {
        const identity = (senderIds || []).find((jid) => String(jid).endsWith('@s.whatsapp.net')) || senderId || senderAlt || ''
        try {
          const host = config?.agent?.host || '127.0.0.1'
          const port = config?.agent?.port || 8787
          const response = await fetch(`http://${host}:${port}/quota`, {
            method: 'POST', signal: AbortSignal.timeout(10000),
            headers: agentHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ identity }),
          })
          const status = await response.json().catch(() => ({}))
          if (!response.ok) throw new Error(status.detail || `HTTP ${response.status}`)
          await sendText(chatId, `Kredit AI pribadi: ${Number(status.used || 0).toLocaleString('id-ID')} / ${Number(status.limit || 1000000).toLocaleString('id-ID')} token terpakai. Sisa ${Number(status.remaining || 0).toLocaleString('id-ID')} token. Reset bergulir 36 jam per pemakaian.`, message)
        } catch (e) {
          await sendText(chatId, `Status kredit belum dapat dibaca: ${e.message}`, message)
        }
      }
      await cleanupMedia()
      return
    }
    if (lname === 'ringkasan') {
      if (!isGroup) {
        await sendText(chatId, 'This command only works in a WhatsApp group.', message)
        await cleanupMedia()
        return
      }
      const action = String(rest[0] || '').toLowerCase()
      // Padanan bahasa Indonesia (menu memakai kata ini).
      const aksi = { nyala: 'on', hidup: 'on', mati: 'off', henti: 'off', keadaan: 'status' }[action] || action
      try {
        if (aksi === 'on') {
          const enabled = enableGroupSummary(chatId, rest[1])
          await sendText(chatId, `Daily summary on at ${enabled.when} server time. Group text is used for the summary and deleted after 7 days. Send !summary off to stop.`, message)
        } else if (aksi === 'off') {
          const result = disableGroupSummary(chatId)
          await sendText(chatId, result.deletedLogs == null ? 'Group summary was already off.' : `Group summary stopped; recording halted and ${result.deletedLogs} old log files deleted.`, message)
        } else if (aksi === 'status') {
          const status = getGroupSummaryStatus(chatId)
          const [minute, hour] = String(status.when || '').split(/\s+/)
          const timeLabel = hour != null ? `${hour.padStart(2, '0')}:${String(minute).padStart(2, '0')}` : ''
          await sendText(chatId, status.enabled ? `Summary runs daily at ${timeLabel} (server time).` : 'Group summary is off.', message)
        } else {
          await sendText(chatId, 'Use !summary on HH:MM, !summary status, or !summary off. Times follow the server clock ♡', message)
        }
      } catch (e) {
        await sendText(chatId, `Summary unchanged: ${e.message}`, message)
      }
      await cleanupMedia()
      return
    }
    // 2a) !stop — hentikan tugas yang sedang berjalan di chat ini.
    if (lname === 'stop' || lname === 'berhenti' || lname === 'hentikan') {
      await requestCancel(config, chatId)
      await sendText(chatId, '🛑 Stopped. If progress was saved, type !resume to continue.', message)
      await cleanupMedia()
      return
    }
    // 2b) !lanjut/!resume — teruskan tugas dari checkpoint terakhir.
    //     Diteruskan ke inti agent (Python) yang memuat checkpoint-nya.
    const isResume = ['lanjut', 'resume', 'continue', 'teruskan', 'continuekan'].includes(lname)
    if (!isResume) {
    // Non-owner hanya mendapat menu dan chat privat yang dikuotai. Perintah
    // plugin, grup, sistem, dan administrasi tetap khusus owner.
    if (!senderIsOwner && !['help', 'menu', 'models', 'model', 'upscale'].includes(lname)) {
      await sendText(chatId, 'Perintah ini khusus owner. Kirim !help untuk menu atau lanjutkan dengan chat pribadi.', message)
      await cleanupMedia()
      return
    }
    const cmd = COMMANDS.get(lname)
    if (cmd) {
      try {
        const out = await cmd.run({ ctx, args: rest, sock: SOCK, config })
        await sendReplies(chatId, out, message)
        await cleanupMedia()
      } catch (e) {
        await sendText(chatId, `Command failed: ${e.message}`, message)
        await cleanupMedia()
      }
      return
    }
    // 2c) Perintah milik PLUGIN (mis. plugin 'salam' punya commands:['halo']).
    //     Kwee menulis plugin ini sendiri; hanya owner yang sampai ke sini.
    const plugins = await listPlugins().catch(() => [])
    const owner = plugins.find((p) => p.ok && p.enabled !== false && (p.commands || []).map((c) => String(c).toLowerCase()).includes(lname))
    if (owner) {
      try {
        const out = await runPlugin(owner.name, {
          args: {
            text: rest.join(' '), argv: rest, media: mediaPathOf(ctx),
            mediaKind: ctx.attachment?.kind || ctx.quotedAttachment?.kind || '',
            // Berkas lampiran APA SAJA (termasuk audio/stiker) dari pesan yang
            // di-reply — dipakai plugin seperti `tourl` yang meng-upload berkas.
            quotedMedia: ctx.quotedAttachment?.path || '',
          },
          msg: {
            chatId, senderId, senderIds: (senderIds || []).concat([senderId, senderAlt]).filter(Boolean),
            isGroup, isOwner: senderIsOwner, text, messageId: ctx.messageId,
            // Teks/caption kutipan ikut diteruskan supaya plugin bisa membaca
            // URL atau keterangan dari pesan yang di-reply.
            quoted: { ...(quotedOf(message) || {}), text: ctx.quoted?.text || '' },
          },
          makeCtx: () => pluginCtx(SOCK, { chatId, senderId, isGroup, text }),
        })
        if (out.result == null) {
          // Plugin mengelola pengirimannya sendiri (mis. hidetag/mentionjid):
          // tidak perlu teks tambahan. Log tetap dicatat ke stdout.
          if (out.logs?.length) console.log(`[plugin] ${owner.name}: ${out.logs.join(' | ')}`)
        } else {
          const logs = out.logs?.length ? `\n\n_log: ${out.logs.join(' | ')}_` : ''
          const res = typeof out.result === 'string' ? out.result : JSON.stringify(out.result)
          await sendText(chatId, `${res || '(plugin selesai)'}${logs}`, message)
        }
        await cleanupMedia()
      } catch (e) {
        await sendText(chatId, `Plugin failed: ${e.message}`, message)
        await cleanupMedia()
      }
      return
    }
    await sendText(chatId, `Unknown command "${name}". Try !help`, message)
    await cleanupMedia()
    return
    }
    // (isResume === true) → lanjut ke jalur AI di bawah dengan teks apa adanya,
    // supaya inti agent memuat checkpoint & meneruskan pekerjaan.
  }

  // Caption tanpa mention boleh diproses sebagai teks saja; buang staging visual
  // sebelum masuk ke AI agar tidak ada jalur tak sengaja untuk melihatnya.
  if (!inspectMedia && visualFiles.length) {
    await Promise.all(visualFiles.map(async (item) => {
      if (!item?.path) return
      try { await unlink(item.path) } catch { /* mungkin sudah dibersihkan */ }
    }))
  }

  if (!senderIsOwner && !isPublicChat) {
    await cleanupMedia()
    await sendText(chatId, 'AI publik hanya tersedia lewat chat pribadi dan kuota; fitur grup tetap dibatasi.', message)
    return
  }

  // 3) Jalur AI — ENGINE yang menjawab.
  //
  // BAWAAN: OpenHands (stack agent-canvas). Inti agent Python hanya dipakai
  // bila `engine.mode: native`, atau sebagai cadangan ketika stack canvas mati
  // dan `engine.fallback` tidak dimatikan. Perintah `!`, kartu, dan media tetap
  // di transport — engine hanya menggantikan siapa yang menjawab.
  try {
    const payload = {
        chat_id: chatId,
        sender_id: senderId,
        sender_ids: (senderIds || []).concat([senderId, senderAlt]).filter(Boolean),
        sender_name: ctx.senderName,
        text,
        quoted_text: ctx.quoted?.text || "",
        quoted_kind: ctx.quoted?.kind || "",
        attachments,
        is_group: isGroup,
        message_id: ctx.messageId,
        is_mention_bot: ctx.isMentionBot,
        is_reply_to_bot: ctx.isReplyToBot,
        inspect_media: inspectMedia,
        public_user: isPublicChat,
        public_identity: isPublicChat
          ? ((senderIds || []).find((jid) => String(jid).endsWith('@s.whatsapp.net')) || senderId || senderAlt || '')
          : '',
        model: await getModelPreference(
          (senderIds || []).find((jid) => String(jid).endsWith('@s.whatsapp.net')) || senderId || senderAlt || '',
        ),
    }
    const pakaiCanvas = !isPublicChat && !payload.model && engineMode(config) === 'openhands'
    const kerja = (async () => {
      if (!pakaiCanvas) return callAgent(config, payload, { background: true })
      const r = await engineReply(text, config, attachments, inspectMedia, chatId)
      if (r.replies?.length) return r
      // Canvas gagal: turun ke inti Python bila fallback diizinkan.
      if (config.engine?.fallback === false) {
        return { replies: [{ type: 'text', text: `OpenHands engine: ${r.error || 'gagal menjawab'}` }], trace: [] }
      }
      console.warn('[router] canvas gagal, memakai inti Python:', r.error)
      return callAgent(config, payload, { background: true })
    })()
    const { hasil: out, kemajuan } = await withProgress(
      chatId, message,
      kerja,
      config.agent?.progressAfterMs,
      config,
    )
    // Ambil snapshot final agar tool cepat tetap tercatat meski selesai di
    // antara interval polling progress.
    kemajuan.adopsiStatus(out?.activity)
    let replies = Array.isArray(out?.replies) ? out.replies : []
    // HTML di dalam jawaban (fence ```html / dokumen utuh) → lampiran .html.
    // Inti Python sudah melakukannya, tetapi engine OpenHands mengembalikan teks
    // mentah — tanpa ini HTML tampil sebagai blok "Lihat kode".
    replies = htmlKeBerkas(replies)
    // Laporan progres .html dibuat oleh Kemajuan (transport) saat kartu ditutup,
    // jadi tetap muncul apa pun enginenya dan tidak bergantung pada Python.
    // JEJAK LANGKAH → kartu: satu pesan berisi langkah kerja + jawaban.
    // Dua pesan terpisah terasa berisik; satu kartu terasa seperti laporan.
    // Jejak langkah TIDAK dimasukkan ke kartu bila pesan status sudah
    // menampilkannya — itu yang dulu terbaca sebagai "thinking dua kali".
    replies = withTrace(replies, kemajuan.terlihat ? [] : out?.trace, config)
    // Jaring pengaman: kalau agent mengembalikan kosong (mis. gateway LLM
    // hiccup), JANGAN diam — beri tahu pengguna supaya tidak merasa bot mati.
    // Gabungkan lebih dulu supaya "satu jawaban" benar-benar satu pesan.
    replies = gabungTeks(groupReplies(replies))
    if (!replies.length) {
      replies = [{ type: 'text', text: 'Sorry, I could not put an answer together. Try again, or split it into smaller steps ♡' }]
    }
    // SATU pesan per giliran: kalau jawabannya satu teks, pesan status tadi
    // diedit menjadi jawaban itu — bukan dihapus lalu dikirim ulang.
    await kemajuan.tutup(replies, (r) => sendReplies(chatId, r, message), bersih)
  } catch (e) {
    console.error('[router] agent error', e.message)
    await audit('agent.failed', { chat: chatId, error: String(e?.message || e) })
    const aborted = e?.name === 'TimeoutError' || e?.name === 'AbortError'
    let text
    if (e?.jobFailure) {
      // Tugas benar-benar terhenti di inti agent. Progresnya tersimpan sebagai
      // checkpoint → beri tahu pemilik APA yang terjadi & cara melanjutkan,
      // jangan biarkan ia menunggu "Berpikir…" yang tak pernah berakhir.
      text = `This task stopped before finishing (${e.message}). Progress is saved — ` +
             'type !resume to continue, or send the command again.'
    } else if (aborted) {
      text = 'This task needed longer than the safety limit. Progress is saved — type !resume to continue.'
    } else {
      text = 'Sorry, the agent core is unreachable right now. Try again in a moment ♡'
    }
    await sendText(chatId, text, message)
  } finally {
    await cleanupMedia()
  }
}
