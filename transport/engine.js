// engine.js — pemilih engine jawaban untuk transport.
//
// Dua mode:
//   openhands (BAWAAN) — jawaban dibuat OpenHands lewat stack agent-canvas
//                        (HTTP ke agent-server). Ini engine utama Kwee.
//   native             — inti agent Python (agent/main.py). Hanya dipakai bila
//                        diminta, atau sebagai cadangan saat stack canvas mati
//                        dan `engine.fallback` tidak dimatikan.
//
// Yang TIDAK berubah: perintah `!`, kartu GenAI, pengiriman media, dan aksi
// grup — semuanya tetap di transport. Engine hanya menggantikan "siapa yang
// menjawab".
import fs from 'node:fs'
import path from 'node:path'
import { buildPreamble, confirmPreambleIncluded } from './memori.js'

const SELESAI = new Set(['done', 'finished', 'completed', 'complete', 'stopped', 'idle'])
const GAGAL = new Set(['error', 'failed', 'cancelled', 'canceled'])

export function engineMode(config) {
  const mode = String(config?.engine?.mode || 'openhands').trim().toLowerCase()
  return mode === 'native' ? 'native' : 'openhands'
}

function canvasCfg(config) {
  const c = config?.mechanisms?.canvas || {}
  return {
    port: Number(c.port || 8000),
    timeoutMs: Number((c.timeoutSeconds || 1800)) * 1000,
    pollMs: Math.max(1000, Number(c.pollSeconds || 4) * 1000),
    startWaitMs: Number((c.startTimeoutSeconds || 600)) * 1000,
    apiKey: String(c.apiKey || process.env.LOCAL_BACKEND_API_KEY || process.env.KWEE_CANVAS_KEY || ''),
  }
}

/** Kunci API: config/env, atau berkas yang dibuat agent (data/canvas.key). */
function apiKey(config) {
  const { apiKey: k } = canvasCfg(config)
  if (k) return k
  try {
    return fs.readFileSync(path.resolve(process.cwd(), 'data', 'canvas.key'), 'utf8').trim()
  } catch {
    return ''
  }
}

function headers(config) {
  const k = apiKey(config)
  return {
    'Content-Type': 'application/json',
    Accept: 'application/json',
    ...(k ? { 'X-API-Key': k, Authorization: `Bearer ${k}` } : {}),
  }
}

async function call(config, method, p, body, timeoutMs = 30000) {
  const { port } = canvasCfg(config)
  const url = `http://127.0.0.1:${port}${p}`
  const res = await fetch(url, {
    method,
    headers: headers(config),
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(timeoutMs),
  })
  const text = await res.text()
  let json = null
  try { json = text ? JSON.parse(text) : {} } catch { json = { raw: text } }
  return { status: res.status, json }
}

export async function canvasUp(config) {
  try {
    const r = await call(config, 'GET', '/api/conversations', null, 4000)
    return r.status < 500
  } catch {
    return false
  }
}

function idOf(j) {
  if (!j || typeof j !== 'object') return ''
  for (const k of ['id', 'conversation_id', 'conversationId', 'session_id']) {
    if (j[k]) return String(j[k])
  }
  for (const k of ['conversation', 'session', 'data']) {
    if (j[k] && typeof j[k] === 'object') {
      for (const kk of ['id', 'conversation_id']) if (j[k][kk]) return String(j[k][kk])
    }
  }
  return ''
}

function statusOf(j) {
  if (!j || typeof j !== 'object') return ''
  for (const k of ['status', 'execution_status', 'state', 'conversation_status']) {
    if (typeof j[k] === 'string' && j[k]) return j[k].toLowerCase()
  }
  for (const k of ['conversation', 'data']) {
    if (j[k] && typeof j[k] === 'object') {
      const s = statusOf(j[k])
      if (s) return s
    }
  }
  return ''
}

function textOf(j) {
  if (typeof j === 'string') return j.trim()
  if (!j || typeof j !== 'object') return ''
  for (const k of ['response', 'final_response', 'content', 'message', 'text', 'answer', 'output']) {
    const v = j[k]
    if (typeof v === 'string' && v.trim()) return v.trim()
    if (v && typeof v === 'object') {
      const t = textOf(v)
      if (t) return t
    }
  }
  for (const k of ['data', 'conversation', 'result', 'events']) {
    if (j[k] && typeof j[k] === 'object') {
      const t = textOf(j[k])
      if (t) return t
    }
  }
  return ''
}

/**
 * Kirim satu tugas ke OpenHands. Mengembalikan { ok, text|error, conversationId }.
 * Body pembuatan dicoba berurutan karena versi agent-server berbeda-beda.
 */
// --------------------------------------------------------------------------
// Lampiran gambar → teks
//
// Engine OpenHands tidak menerima gambar, jadi gambar yang dikirim pemilik
// diubah dulu menjadi deskripsi lewat inti Python (`POST /vision`). Tanpa ini
// bot "tidak bisa memahami gambar" walau tool vision-nya sehat.
// --------------------------------------------------------------------------
function agentCfg(config) {
  const a = (config && config.agent) || {}
  return {
    host: String(a.host || process.env.AGENT_HOST || '127.0.0.1'),
    port: Number(a.port || process.env.AGENT_PORT || 8787),
    token: String(a.authToken || process.env.ACTION_TOKEN || ''),
  }
}

function safeVisionError(value) {
  return String(value || 'layanan Vision gagal').replace(/(?:[A-Za-z]:)?[\\/](?:[^\s"'<>]+[\\/])*[^\s"'<>]*/g, '[file]').slice(0, 180)
}

async function lihatGambar(att, config, prompt) {
  const { host, port, token } = agentCfg(config)
  const body = {
    asset: String(att.id || ''),
    path: String(att.path || ''),
    url: String(att.url || ''),
    prompt: prompt || 'Jelaskan gambar ini: objek utama, teks yang terbaca, dan hal penting lain.',
  }
  if (!body.asset && !body.path && !body.url) return ''
  const res = await fetch(`http://${host}:${port}/vision`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Agent-Token': token },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(120000),
  })
  const json = await res.json().catch(() => ({}))
  if (!res.ok || json.error) return `[Vision gagal membaca gambar: ${safeVisionError(json.error || `HTTP ${res.status}`)}]`
  return String(json.text || json.description || json.content || '').trim()
}

async function lihatVideo(att, config, prompt) {
  const { host, port, token } = agentCfg(config)
  const body = { asset: String(att.id || ''), path: String(att.path || ''),
    prompt: prompt || 'Ringkas isi video ini, urutan kejadian, objek, dan teks yang terlihat.', max_frames: 4 }
  if (!body.asset && !body.path) return ''
  const res = await fetch(`http://${host}:${port}/video-vision`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Agent-Token': token },
    body: JSON.stringify(body), signal: AbortSignal.timeout(600000),
  })
  const json = await res.json().catch(() => ({}))
  if (!res.ok || json.error) return `[Vision gagal membaca video: ${safeVisionError(json.error || `HTTP ${res.status}`)}]`
  return String(json.text || '').trim()
}

/** Analyze only explicitly requested visual media; this function never runs agent tools. */
export async function inspectMediaOnly(attachments, config, prompt = '') {
  const daftar = (Array.isArray(attachments) ? attachments : []).filter((a) => {
    if (!a) return false
    const kind = String(a.kind || a.type || '').toLowerCase()
    const mime = String(a.mime || a.mimetype || '').toLowerCase()
    return kind === 'image' || kind === 'sticker' || kind === 'video' || mime.startsWith('image/') || mime.startsWith('video/')
  })
  if (!daftar.length) return ''
  const hasil = []
  for (const [index, a] of daftar.slice(0, 4).entries()) {
    const kind = String(a.kind || a.type || '').toLowerCase()
    const label = `${kind === 'video' ? 'Video' : kind === 'sticker' ? 'Stiker' : 'Gambar'} ${index + 1}`
    try {
      const teks = kind === 'video'
        ? await lihatVideo(a, config, String(prompt || '').slice(0, 2000))
        : await lihatGambar(a, config, String(prompt || '').slice(0, 2000))
      if (teks) hasil.push(`- ${label}: ${teks}`)
    } catch (e) {
      hasil.push(`- ${label}: Vision gagal: ${String(e?.message || e).slice(0, 180)}`)
    }
  }
  return hasil.join('\n').slice(0, 5000)
}

/** Ubah lampiran visual menjadi blok teks yang ditempelkan ke tugas agent owner. */
export async function describeAttachments(attachments, config, prompt = '') {
  const result = await inspectMediaOnly(attachments, config, prompt)
  return result ? `[Media yang diminta pengguna untuk dianalisis — hasil Vision]\n${result}\n\n` : ''
}

export async function askOpenHands(task, config, attachments = [], inspectMedia = false, requestId = '') {
  const cfg = canvasCfg(config)
  // Memori + skills DITEMPELKAN ke pesan: tanpa ini engine OpenHands hanya
  // menerima teks mentah, jadi suntikan memori dan skill tidak pernah sampai.
  let isi = task
  let preamble = ''
  let gambar = ''
  try {
    if (inspectMedia) gambar = await describeAttachments(attachments, config, task)
  } catch (e) {
    console.warn('[engine] gagal melihat lampiran:', e?.message || e)
  }
  try {
    preamble = buildPreamble(config, task, requestId)
    if (preamble) isi = `${preamble}${gambar}Tugas dari pemilik:\n${task}`
    else if (gambar) isi = `${gambar}Tugas dari pemilik:\n${task}`
  } catch (e) {
    console.warn('[engine] gagal menyusun konteks memori/skill:', e?.message || e)
  }
  const bodies = [
    { initial_user_msg: isi },
    { initial_user_msg: isi, workspace_mode: 'local_repo' },
    { initialUserMsg: isi },
    { task: isi },
  ]
  let cid = ''
  let lastErr = ''
  for (const b of bodies) {
    try {
      const r = await call(config, 'POST', '/api/conversations', b, 120000)
      if (r.status >= 200 && r.status < 300) {
        cid = idOf(r.json)
        if (cid) break
        lastErr = 'respons tanpa id'
      } else {
        lastErr = `HTTP ${r.status} ${JSON.stringify(r.json).slice(0, 200)}`
        if (r.status === 401 || r.status === 403) break
      }
    } catch (e) {
      lastErr = e.message
    }
  }
  if (!cid) return { ok: false, error: `canvas menolak permintaan: ${lastErr || 'tidak diketahui'}` }
  if (preamble) confirmPreambleIncluded(isi, requestId)

  const batas = Date.now() + cfg.timeoutMs
  let terakhir = null
  while (Date.now() < batas) {
    try {
      const r = await call(config, 'GET', `/api/conversations/${cid}`, null, 30000)
      if (r.status < 400 && r.json) {
        terakhir = r.json
        const st = statusOf(r.json)
        if (SELESAI.has(st) || GAGAL.has(st)) break
      }
    } catch { /* jaringan sesaat: lanjut memantau */ }
    await new Promise((r) => setTimeout(r, cfg.pollMs))
  }

  let teks = ''
  try {
    const r = await call(config, 'GET', `/api/conversations/${cid}/agent_final_response`, null, 30000)
    teks = r.status < 400 ? textOf(r.json) : ''
  } catch { /* jatuh ke teks dari polling */ }
  if (!teks) teks = textOf(terakhir)
  if (!teks) {
    const st = statusOf(terakhir)
    return { ok: false, conversationId: cid, error: st === '' ? 'tugas belum selesai (masih berjalan di canvas)' : `percakapan berakhir: ${st}` }
  }
  return { ok: true, text: teks, conversationId: cid }
}

/** Bentuk balasan yang sama dengan inti agent Python: { replies, trace }. */
export async function engineReply(text, config, attachments = [], inspectMedia = false, requestId = '') {
  const hasil = await askOpenHands(text, config, attachments, inspectMedia, requestId)
  if (hasil.ok) {
    return { replies: [{ type: 'text', text: hasil.text }], trace: [], engine: 'openhands', conversationId: hasil.conversationId }
  }
  return { replies: [], trace: [], engine: 'openhands', error: hasil.error, conversationId: hasil.conversationId }
}

export default { engineMode, askOpenHands, engineReply, canvasUp, describeAttachments }
