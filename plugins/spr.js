// plugins/spr.js — sPR / sBPR (sBRP): 1 pesan grup, isi BEDA per penerima.
//
// Ditulis ulang untuk Kwee (bukan salinan pihak lain):
//   - TARGET = SEMUA ANGGOTA grup (bukan hanya admin lagi); admin diprioritaskan
//     supaya tetap kebagian saat anggota melebihi batas. MAKS 100 penerima,
//     BOT SENDIRI TIDAK IKUT.
//   - Tag penerima pakai FOTO PROFIL (getpp) bila ada; gagal/keburu waktu →
//     otomatis jatuh ke teks (tidak pernah gagal total).
//   - PESAN SUKSES selalu dikirim LANGSUNG setelah stanza terbit — tidak lagi
//     bergantung pada jalur balasan router yang bisa kepotong timeout 30s.
//   - Tanpa watermark zero-width, tanpa integrity-gate, tanpa shell (RCE).
//
// Perintah (khusus grup):
//   !sbpr <teks {name}>   (alias !sbrp) → batch: tiap anggota lihat {name} = tag dirinya
//   !spr <privat>         (sambil REPLY pesan siapa pun) → hanya dia dapat <privat>
//   !spr <publik> | <privat>

import {
  encodeWAMessage, encodeSignedDeviceIdentity, jidDecode, prepareWAMessageMedia,
} from '@itsliaaa/baileys'
import crypto from 'node:crypto'

const MAKS_TARGET = 100          // batas penerima per kiriman
const BUDGET_PP_MS = 10000       // anggaran fase getpp (plugin timeout 30s)
const KONKURENSI = 10            // paralelisme unggah/unduh PP
const PUBLIK_DEFAULT = 'Pesan ini memiliki isi khusus untuk beberapa penerima.'
const TEKS_DEFAULT = 'Halo {name}, ada pesan untukmu.'

const num = (jid) => { try { return jidDecode(jid)?.user || String(jid).split('@')[0].split(':')[0] } catch { return String(jid).split('@')[0].split(':')[0] } }
const norm = (jid) => { const n = num(jid); return n ? `${n}${String(jid).includes('@lid') ? '@lid' : '@s.whatsapp.net'}` : '' }

/** Jalankan `fn` untuk tiap item dengan paralelisme terbatas (urutan hasil dijaga). */
async function pool(items, limit, fn) {
  const out = new Array(items.length)
  let i = 0
  const worker = async () => {
    while (i < items.length) {
      const idx = i++
      try { out[idx] = await fn(items[idx], idx) } catch { out[idx] = null }
    }
  }
  await Promise.all([...Array(Math.min(limit, items.length))].map(worker))
  return out
}

/** Promise.race dengan batas waktu yang timer-nya selalu dibersihkan. */
async function denganBatas(promise, ms, nilai) {
  let t
  const batas = new Promise((r) => { t = setTimeout(() => r(nilai), ms); if (t.unref) t.unref() })
  try { return await Promise.race([promise, batas]) } finally { clearTimeout(t) }
}

/**
 * Ambil daftar penerima: SEMUA anggota grup (admin dulu), bot tidak ikut,
 * dipotong MAKS_TARGET. Mengembalikan juga jumlah di luar batas agar bisa
 * dilaporkan di pesan sukses.
 */
async function anggota(sock, groupJid) {
  const meta = await sock.groupMetadata(groupJid)
  const me = new Set([
    sock.user?.id, sock.user?.lid,
    sock.authState?.creds?.me?.id, sock.authState?.creds?.me?.lid,
  ].filter(Boolean).map(num))
  const semua = (meta.participants || []).filter((p) => p?.id && !me.has(num(p.id)))
  const rank = (p) => (p.admin === 'superadmin' ? 0 : p.admin === 'admin' ? 1 : 2)
  const urut = [...semua].sort((a, b) => rank(a) - rank(b))
  const target = urut.slice(0, MAKS_TARGET)
  return { meta, target, jumlah: semua.length, diLuarBatas: Math.max(0, semua.length - target.length) }
}

async function devicesFor(sock, jids) {
  let devices = []
  try { if (sock.getUSyncDevices) devices = (await sock.getUSyncDevices(jids, true, false)) || [] } catch { /* fallback */ }
  if (!devices.length) devices = jids.map((jid) => ({ jid }))
  return devices.filter((d) => d?.jid && !d.jid.endsWith('@hosted.lid') && jidDecode(d.jid)?.device !== 99)
}

/** devicesFor dengan batas waktu: gagal/lambat → 1 device per JID (tetap terkirim). */
async function devicesAman(sock, jids) {
  const fallback = jids.map((jid) => ({ jid }))
  try {
    const hasil = await denganBatas(devicesFor(sock, jids), 9000, null)
    return hasil && hasil.length ? hasil : fallback
  } catch { return fallback }
}

/* ── getpp: foto profil penerima (di-cache, dibatasi anggaran waktu) ── */

const cacheAvatar = new Map()   // url -> Buffer
const cacheUpload = new Map()   // md5(buffer) -> imageMessage

async function fetchBuffer(url) {
  if (!url) return null
  if (cacheAvatar.has(url)) return cacheAvatar.get(url)
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(8000), headers: { 'User-Agent': 'Mozilla/5.0' } })
    if (!res.ok) return null
    const buf = Buffer.from(await res.arrayBuffer())
    if (!buf.length) return null
    cacheAvatar.set(url, buf)
    if (cacheAvatar.size > 200) cacheAvatar.delete(cacheAvatar.keys().next().value)
    return buf
  } catch { return null }
}

async function uploadImage(sock, buffer) {
  if (!buffer?.length || typeof sock.waUploadToServer !== 'function') return null
  const hash = crypto.createHash('md5').update(buffer).digest('hex')
  if (cacheUpload.has(hash)) return cacheUpload.get(hash)
  try {
    const media = await prepareWAMessageMedia({ image: buffer }, { upload: sock.waUploadToServer })
    const img = media?.imageMessage
    if (img) {
      cacheUpload.set(hash, img)
      if (cacheUpload.size > 200) cacheUpload.delete(cacheUpload.keys().next().value)
      return img
    }
  } catch { /* fallback ke teks */ }
  return null
}

/** Map user(number) -> imageMessage. Gagal/keburu waktu = dilewati. */
async function getppMap(sock, participants) {
  const map = new Map()
  if (typeof sock.profilePictureUrl !== 'function') return map
  const t0 = Date.now()
  const hasil = await pool(participants, KONKURENSI, async (p) => {
    if (Date.now() - t0 > BUDGET_PP_MS) return null
    let url = null
    try { url = await sock.profilePictureUrl(p.id, 'image') } catch { /* tanpa PP */ }
    if (!url && String(p.id).endsWith('@lid')) {
      try {
        const pn = await sock.signalRepository?.lidMapping?.getPNForLID?.(p.id)
        if (pn) url = await sock.profilePictureUrl(String(pn).split('@')[0] + '@s.whatsapp.net', 'image')
      } catch { /* tetap tanpa PP */ }
    }
    if (!url) return null
    const buf = await fetchBuffer(url)
    const img = await uploadImage(sock, buf)
    if (!img) return null
    const keys = [num(p.id), p?.lid ? num(p.lid) : ''].filter(Boolean)
    return { keys, img }
  })
  for (const r of hasil) if (r?.img) for (const k of r.keys) map.set(k, r.img)
  return map
}

/** getppMap dengan pagar waktu keras: apa pun yang terjadi, fase PP tak boleh
 *  menghabiskan seluruh timeout plugin. */
async function getppAman(sock, people) {
  try {
    const hasil = await denganBatas(getppMap(sock, people), BUDGET_PP_MS + 4000, null)
    return hasil instanceof Map ? hasil : new Map()
  } catch { return new Map() }
}

/** Pesan pribadi satu penerima: gambar PP+caption bila ada, selain itu teks. */
function pesanPersonal(ppImg, caption, mentions) {
  if (ppImg) return { imageMessage: { ...ppImg, caption, contextInfo: { mentionedJid: mentions } } }
  return { extendedTextMessage: { text: caption, contextInfo: { mentionedJid: mentions } } }
}

async function kirimStanza(sock, groupJid, meta, { publicText, perDevice }) {
  const meId = sock.authState?.creds?.me?.lid || sock.user?.id
  const isLid = meta?.addressingMode === 'lid'
  let withDeviceIdentity = false
  const nodes = (await Promise.all(perDevice.map(async ({ jid, message }) => {
    try {
      const bytes = encodeWAMessage(message)
      const { type, ciphertext } = await sock.signalRepository.encryptMessage({ jid, data: bytes })
      if (type === 'pkmsg') withDeviceIdentity = true
      return { tag: 'to', attrs: { jid }, content: [{ tag: 'enc', attrs: { v: '2', type }, content: ciphertext }] }
    } catch { return null }
  }))).filter(Boolean)
  if (!nodes.length) throw new Error('Enkripsi per-penerima gagal semua — coba lagi sesaat lagi.')

  let groupCiphertext = null
  if (publicText != null) {
    const bytes = encodeWAMessage({ extendedTextMessage: { text: publicText, contextInfo: { mentionedJid: [] } } })
    const enc = await sock.signalRepository.encryptGroupMessage({ group: groupJid, meId, data: bytes })
    groupCiphertext = enc?.ciphertext || null
  }

  const content = []
  if (groupCiphertext) content.push({ tag: 'enc', attrs: { v: '2', type: 'skmsg' }, content: groupCiphertext })
  content.push({ tag: 'participants', attrs: {}, content: nodes })
  if (withDeviceIdentity && sock.authState?.creds?.account) {
    content.push({ tag: 'device-identity', attrs: {}, content: encodeSignedDeviceIdentity(sock.authState.creds.account, true) })
  }
  await sock.query({
    tag: 'message',
    attrs: { to: groupJid, type: 'text', ...(isLid ? { addressing_mode: 'lid' } : {}) },
    content,
  })
  return nodes.length
}

/** Kirim pesan status ke grup — langsung, tidak lewat jalur balasan router. */
async function balas(ctx, chatId, text, mentions) {
  try {
    await ctx.wa.send(chatId, mentions?.length ? { text, mentions } : text)
  } catch { /* status gagal kirim = tidak fatal, stanza utama tetap terkirim */ }
}

export default {
  name: 'spr',
  description: `sPR/sBPR: satu pesan grup, isi berbeda per anggota (semua anggota, maks ${MAKS_TARGET}, bot tak ikut, tag pakai foto profil). Ada pesan sukses. Tanpa watermark.`,
  commands: ['spr', 'sbpr', 'sbrp'],

  async run(ctx) {
    const sock = ctx.wa?.raw ? ctx.wa.raw() : null
    if (!sock) return { ok: false, error: 'Socket WhatsApp tidak tersedia.' }
    const chatId = String(ctx.msg?.chatId || '')
    if (!chatId.endsWith('@g.us')) return 'Perintah ini hanya untuk grup.'

    const cmd = String(ctx.msg?.text || '').trim().replace(/^[!.]/, '').split(/\s+/)[0].toLowerCase()
    const text = String(ctx.args?.text || '').trim()

    // Metadata grup selalu dibutuhkan (addressingMode + daftar anggota).
    let info
    try { info = await anggota(sock, chatId) } catch (e) {
      return `Gagal membaca data grup: ${e.message}`
    }
    const { meta, target, jumlah, diLuarBatas } = info

    // ── !spr — publik | privat, target = pengirim pesan yang DI-REPLY ──
    if (cmd === 'spr') {
      const targetJid = ctx.msg?.quoted?.participant || ''
      if (!targetJid) return 'Reply pesan anggota yang mau dikirim pesan privat, lalu ketik ulang.'
      let publik = PUBLIK_DEFAULT
      let privat = text
      const sep = text.indexOf('|')
      if (sep >= 0) { publik = text.slice(0, sep).trim() || PUBLIK_DEFAULT; privat = text.slice(sep + 1).trim() }
      if (!privat) return 'Isi pesan privat kosong. Contoh: !spr yang lain baca ini | rahasia buat kamu'

      const ppMap = await getppAman(sock, [{ id: targetJid }])
      const ppImg = ppMap.get(num(targetJid)) || null
      const devices = await devicesAman(sock, [targetJid])
      const n = await kirimStanza(sock, chatId, meta, {
        publicText: publik,
        perDevice: devices.map((d) => ({ jid: d.jid, message: pesanPersonal(ppImg, privat, []) })),
      })
      await balas(ctx, chatId, `✅ sPR terkirim ke ${num(targetJid)} (${n} device${ppImg ? ' + foto profil' : ''}).`, [targetJid])
      return null
    }

    // ── !sbpr / !sbrp — batch ke SEMUA anggota (bot tidak ikut, maks 100) ──
    if (!target.length) return 'Tidak ada anggota yang bisa dijadikan target (bot tidak dihitung).'
    const template = text || TEKS_DEFAULT

    // Foto profil penerima (getpp) — dibatasi anggaran waktu; gagal = teks saja.
    const ppMap = await getppAman(sock, target.map((p) => ({ id: p.id, lid: p.lid })))
    const ppUntuk = (jid) => ppMap.get(num(jid)) || null
    const perDevice = await devicesAman(sock, target.map((p) => p.id))
    const allMentions = [...new Set(target.map((p) => (String(p.id).endsWith('@lid') ? p.id : norm(p.id))).filter(Boolean))]
    const n = await kirimStanza(sock, chatId, meta, {
      publicText: template.replace(/\{name\}/g, 'semua'),
      perDevice: perDevice.map((d) => {
        const anchor = `@${num(d.jid)}`
        const caption = template.includes('{name}') ? template.replace(/\{name\}/g, anchor) : `${anchor} ${template}`
        return { jid: d.jid, message: pesanPersonal(ppUntuk(d.jid), caption, allMentions) }
      }),
    })
    const pakaiPP = perDevice.filter((d) => ppUntuk(d.jid)).length
    const label = cmd === 'sbrp' ? 'sBRP' : 'sBPR'
    const ekor = diLuarBatas ? ` (+${diLuarBatas} anggota lain di luar batas ${MAKS_TARGET}${jumlah > MAKS_TARGET ? '' : ''})` : ''
    await balas(ctx, chatId, `✅ ${label} terkirim — ${target.length} penerima dari ${jumlah} anggota${ekor}, ${n} device (${pakaiPP} pakai foto profil).`)
    return null
  },
}
