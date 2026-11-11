// sentlog.js — catatan pesan KELUAR (id) per-chat, PERSISTEN.
//
// Kenapa ada: WhatsApp butuh `message_id` untuk menghapus pesan, tapi setelah
// `sendMessage` selesai, id itu hilang dari memori. Modul ini menuliskannya ke
// `data/sent.log` (JSONL) supaya plugin (mis. `delete`) bisa menghapus pesan
// bot yang SUDAH terkirim ("hapus 2 balasan terakhirmu").
//
// PENTING (perbaikan): dulu id HANYA dicatat manual di `router.js`, padahal bot
// mengirim lewat BANYAK jalur (tool `wa`, plugin, cron, kartu rich, …). Akibatnya
// pesan yang dikirim AI/plugin/cron tidak pernah bisa dihapus ("tidak ada
// balasan bot"). Sekarang socket di-INSTRUMENTASI sekali (`instrumentSock`),
// jadi SETIAP `sendMessage`/`relayMessage` otomatis tercatat — berapa pun
// jalur pengirimannya.
//
// Aman: semua operasi best-effort (tidak pernah melempar), file di-prune agar
// tidak tumbuh tanpa batas.

import fs from 'node:fs'
import path from 'node:path'
import { markOwn } from './state.js'

const DIR = path.resolve(process.cwd(), 'data')
const FILE = path.join(DIR, 'sent.log')
const MAX_LINES = 5000
const PRUNE_AT_BYTES = 512 * 1024 // prune saat file > 512 KB

/** Bentuk kanonik sebuah chat: buang `:device`, pertahankan `user@server`. */
function bareChat(chatId) {
  const s = String(chatId || '')
  if (!s) return ''
  const [user, server] = s.split('@')
  const u = String(user || '').split(':')[0]
  return server ? `${u}@${server}` : u
}

/** Catat satu pesan keluar. `meta` opsional (mis. { kind: 'text' }). */
export function recordSent(chatId, id, meta = {}) {
  if (!chatId || !id) return
  try {
    fs.mkdirSync(DIR, { recursive: true })
    const line = JSON.stringify({ ts: Date.now(), chat: String(chatId), id: String(id), ...meta })
    fs.appendFileSync(FILE, line + '\n')
    maybePrune()
  } catch { /* best-effort */ }
}

/** Lupakan (hapus dari catatan) id tertentu — dipakai setelah pesan dihapus. */
export function forgetSent(chatId, id) {
  if (!id) return
  try {
    const target = String(chatId)
    const targetBare = bareChat(chatId)
    const lines = fs.readFileSync(FILE, 'utf8').split('\n').filter(Boolean)
    const kept = lines.filter((l) => {
      try {
        const d = JSON.parse(l)
        if (!d || !d.id) return true
        if (String(d.id) !== String(id)) return true
        return String(d.chat) !== target && bareChat(d.chat) !== targetBare
      } catch { return true }
    })
    const tmp = `${FILE}.tmp`
    fs.writeFileSync(tmp, kept.length ? kept.join('\n') + '\n' : '')
    fs.renameSync(tmp, FILE)
  } catch { /* best-effort */ }
}

function maybePrune() {
  try {
    if (fs.statSync(FILE).size < PRUNE_AT_BYTES) return
    const lines = fs.readFileSync(FILE, 'utf8').split('\n').filter(Boolean)
    if (lines.length > MAX_LINES) {
      fs.writeFileSync(FILE, lines.slice(-MAX_LINES).join('\n') + '\n')
    }
  } catch { /* best-effort */ }
}

/**
 * N pesan keluar TERAKHIR untuk sebuah chat.
 * Pencocokan toleran bentuk: `123:4@s.whatsapp.net` dianggap sama dengan
 * `123@s.whatsapp.net` (device suffix diabaikan).
 * @returns {Array<{ts:number, chat:string, id:string}>} terbaru lebih dulu.
 */
export function recentSent(chatId, n = 1) {
  const want = Math.max(1, Math.min(50, Number(n) || 1))
  const target = String(chatId)
  const targetBare = bareChat(chatId)
  try {
    const lines = fs.readFileSync(FILE, 'utf8').split('\n').filter(Boolean)
    const out = []
    for (let i = lines.length - 1; i >= 0 && out.length < want; i--) {
      try {
        const d = JSON.parse(lines[i])
        if (!d || !d.id) continue
        const chat = String(d.chat || '')
        if (chat === target || bareChat(chat) === targetBare) out.push(d)
      } catch { /* lewati baris rusak */ }
    }
    return out
  } catch {
    return []
  }
}

// ---------------------------------------------------------------------------
// Instrumentasi socket: rekam SEMUA pesan keluar, dari jalur mana pun.
// ---------------------------------------------------------------------------

// Konten yang merupakan MUTASI pesan lama (bukan pesan baru) → jangan dicatat.
const MUTATION_KEYS = ['delete', 'edit', 'react', 'pin', 'keep', 'pollUpdate']

function isMutationContent(content) {
  if (!content || typeof content !== 'object') return false
  for (const k of MUTATION_KEYS) if (content[k]) return true
  return false
}

// Apakah pohon pesan memuat `protocolMessage` (revoke/edit)? Bukan pesan baru.
function hasProtocolMessage(node, depth = 0) {
  if (!node || typeof node !== 'object' || depth > 8) return false
  if (node.protocolMessage) return true
  for (const v of Object.values(node)) {
    if (v && typeof v === 'object' && !ArrayBuffer.isView(v) && hasProtocolMessage(v, depth + 1)) return true
  }
  return false
}

function kindOf(content) {
  if (!content || typeof content !== 'object') return 'text'
  for (const k of ['image', 'video', 'audio', 'document', 'sticker']) if (content[k]) return k
  if (content.botForwardedMessage) return 'rich'
  if (content.text != null || content.conversation != null) return 'text'
  return 'other'
}

function chatOf(jid) {
  if (Array.isArray(jid)) return String(jid[0] || '')
  return String(jid || '')
}

/**
 * Bungkus `sendMessage` & `relayMessage` sebuah socket supaya setiap pesan
 * keluar otomatis: (1) ditandai "milik bot" (anti-loop self-chat), dan
 * (2) id-nya dicatat ke sent.log (agar bisa dihapus).
 *
 * Idempoten: memanggil dua kali pada socket yang sama tidak menggandakan.
 * Mengembalikan socket yang sama (sudah terbungkus).
 */
export function instrumentSock(sock) {
  if (!sock || sock.__kweeInstrumented) return sock
  try { sock.__kweeInstrumented = true } catch { /* objek beku: lewati */ }

  const origSend = typeof sock.sendMessage === 'function' ? sock.sendMessage.bind(sock) : null
  const origRelay = typeof sock.relayMessage === 'function' ? sock.relayMessage.bind(sock) : null

  if (origSend) {
    sock.sendMessage = async (jid, content, options) => {
      const sent = await origSend(jid, content, options)
      try {
        if (!isMutationContent(content)) {
          const id = sent?.key?.id
          if (id) {
            markOwn(id)
            recordSent(chatOf(jid), id, { kind: kindOf(content) })
          }
        }
      } catch { /* best-effort */ }
      return sent
    }
  }

  if (origRelay) {
    sock.relayMessage = async (jid, message, options = {}) => {
      const msgId = await origRelay(jid, message, options)
      try {
        if (!hasProtocolMessage(message)) {
          const id = typeof msgId === 'string' ? msgId : (msgId?.key?.id || options?.messageId)
          if (id) {
            markOwn(id)
            recordSent(chatOf(jid), id, { kind: kindOf(message) })
          }
        }
      } catch { /* best-effort */ }
      return msgId
    }
  }

  return sock
}
