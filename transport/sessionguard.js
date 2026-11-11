// sessionguard.js — proteksi & DETEKSI sesi WhatsApp di lapisan TRANSPORT (Node).
//
// Kenapa ada: identitas sesi (creds.json → me.id / me.lid / account) adalah
// kunci akun. Dua vektor penyalahgunaan yang dijaga di sini:
//
//   1) Enumerasi perangkat DIRI SENDIRI lewat getUSyncDevices([me.id]) atau
//      getUSyncDevices([me.lid]). Panggilan itu memetakan "device bin"
//      (device-list) + perangkat PRIMARY milik sesi ini — bahan mentah untuk
//      membajak sesi. Panggilan INTERNAL Baileys saat mengirim pesan memakai
//      closure LOKAL (`messages-send.js`), BUKAN `sock.getUSyncDevices`, jadi
//      membungkus `sock.getUSyncDevices` hanya menangkap pemanggil EKSTERNAL
//      (plugin / kode kita) dan TIDAK mengganggu pengiriman pesan normal.
//
//   2) File sesi Baileys (creds.json, device-list-*.json, session-*/sender-key-*/
//      pre-key-*, app-state-sync-*, identity-key-*, tctoken-*) yang bocor =
//      akun diambil alih. Izinnya dikeraskan (dir 0700, berkas 0600).
//
// Semua operasi best-effort: TIDAK pernah melempar kecuali mode blokir diminta
// eksplisit (`blockSelfEnumeration`), karena guard yang meng-crash bot lebih
// buruk daripada ancaman yang dicegahnya.

import fs from 'node:fs'
import path from 'node:path'

const DIR = path.resolve(process.cwd(), 'data')
const LOG_FILE = path.join(DIR, 'session-guard.log')
const MAX_LINES = 2000
const PRUNE_AT_BYTES = 256 * 1024

// Nama berkas sesi yang bersifat RAHASIA (kunci akun / perangkat / pesan).
const SENSITIVE = [
  /^creds\.json$/,
  /^device-list-.*\.json$/,
  /^session-.*\.json$/,
  /^sender-key-.*\.json$/,
  /^pre-key-.*\.json$/,
  /^app-state-sync.*\.json$/,
  /^identity-key-.*\.json$/,
  /^lid-mapping-.*\.json$/,
  /^tctoken-.*\.json$/,
]

/** Bagian `user` sebuah JID, tanpa `:device` dan tanpa `@server`. */
export function jidUser(jid) {
  const s = String(jid || '')
  if (!s) return ''
  const [user] = s.split('@')
  return String(user).split(':')[0]
}

/** Himpunan user milik SESI INI (dari me.id + me.lid), dalam bentuk PN & LID. */
export function selfUserSet(creds) {
  const me = creds?.me || {}
  const out = new Set()
  for (const v of [me.id, me.lid]) {
    const u = jidUser(v)
    if (u) out.add(u)
  }
  return out
}

/**
 * Deteksi apakah daftar JID memuat identitas SESI INI (enumerasi diri sendiri).
 * @returns {string[]} JID yang cocok (kosong = tak ada enumerasi diri).
 */
export function findSelfEnumeration(jids, creds) {
  const self = selfUserSet(creds)
  if (!self.size) return []
  const list = Array.isArray(jids) ? jids : (jids == null ? [] : [jids])
  const hits = []
  for (const j of list) {
    const u = jidUser(j)
    if (u && self.has(u)) hits.push(String(j))
  }
  return hits
}

/** Apakah nama berkas termasuk berkas sesi rahasia. */
export function isSensitiveSessionFile(name) {
  const base = path.basename(String(name || ''))
  return SENSITIVE.some((re) => re.test(base))
}

/** Daftar berkas sesi rahasia yang benar-benar ada di `authDir`. */
export function sessionSecretFiles(authDir) {
  try {
    return fs.readdirSync(authDir).filter(isSensitiveSessionFile)
  } catch {
    return []
  }
}

function recordEvent(ev) {
  try {
    fs.mkdirSync(DIR, { recursive: true })
    fs.appendFileSync(LOG_FILE, JSON.stringify(ev) + '\n')
    try {
      if (fs.statSync(LOG_FILE).size > PRUNE_AT_BYTES) {
        const lines = fs.readFileSync(LOG_FILE, 'utf8').split('\n').filter(Boolean)
        if (lines.length > MAX_LINES) {
          fs.writeFileSync(LOG_FILE, lines.slice(-MAX_LINES).join('\n') + '\n')
        }
      }
    } catch { /* best-effort */ }
  } catch { /* best-effort */ }
}

/**
 * Pasang guard DETEKSI pada `sock.getUSyncDevices`.
 *
 * - Enumerasi identitas SESI INI (me.id / me.lid) dicatat sebagai peristiwa
 *   keamanan (`self-device-enumeration`) ke `data/session-guard.log`.
 * - Default: DETEKSI saja (panggilan tetap diteruskan). Setel
 *   `blockSelfEnumeration: true` untuk menolaknya (panggilan dilempar).
 * - Idempoten: memanggil dua kali pada socket yang sama tidak menggandakan.
 *
 * @param {object} sock  socket Baileys
 * @param {{blockSelfEnumeration?: boolean, onDetect?: Function, logger?: object}} [opts]
 */
export function guardSock(sock, opts = {}) {
  if (!sock || typeof sock.getUSyncDevices !== 'function') return sock
  if (sock.__kweeSessionGuarded) return sock
  try { sock.__kweeSessionGuarded = true } catch { /* objek beku */ }

  const orig = sock.getUSyncDevices.bind(sock)
  const block = opts.blockSelfEnumeration === true

  sock.getUSyncDevices = async (jids, useCache, ignoreZeroDevices) => {
    try {
      const creds = sock.authState?.creds || { me: sock.user }
      const hits = findSelfEnumeration(jids, creds)
      if (hits.length) {
        const ev = {
          ts: Date.now(),
          type: 'self-device-enumeration',
          jids: hits,
          blocked: block,
        }
        recordEvent(ev)
        try { opts.onDetect?.(ev) } catch { /* abaikan */ }
        try {
          opts.logger?.warn?.(
            '[sessionguard] enumerasi perangkat DIRI terdeteksi (device bin/primary):',
            hits.join(', '),
          )
        } catch { /* abaikan */ }
        if (block) {
          const err = new Error('enumerasi perangkat diri ditolak (sessionguard)')
          err.code = 'SELF_ENUMERATION_BLOCKED'
          throw err
        }
      }
    } catch (e) {
      if (e?.code === 'SELF_ENUMERATION_BLOCKED') throw e
      /* deteksi gagal → jangan ganggu pengiriman */
    }
    return orig(jids, useCache, ignoreZeroDevices)
  }

  return sock
}

/**
 * Keraskan izin folder sesi: dir 0700, tiap berkas 0600.
 * Idempoten & best-effort. Mengembalikan laporan ringkas.
 */
export function hardenSessionFiles(authDir) {
  const out = { ok: true, dir: String(authDir || ''), hardened: 0, total: 0 }
  if (!authDir) { out.ok = false; out.note = 'authDir kosong'; return out }
  let entries
  try {
    entries = fs.readdirSync(authDir)
  } catch {
    out.note = 'folder sesi tidak ada (belum login)'
    return out
  }
  try { fs.chmodSync(authDir, 0o700) } catch { /* best-effort */ }
  out.total = entries.length
  for (const name of entries) {
    const full = path.join(authDir, name)
    try {
      if (fs.statSync(full).isFile()) { fs.chmodSync(full, 0o600); out.hardened++ }
    } catch { /* best-effort */ }
  }
  return out
}
