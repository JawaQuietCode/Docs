// wa.js — transport WhatsApp (Node). Socket nix408 + pairing + reconnect.
//
// Tugas file ini HANYA transport: sambung WA, terima pesan, teruskan ke inti
// agent (Python), lalu kirim balasan. Semua kecerdasan ada di Python.
//
// Sejak WhatsApp memakai **LID**, identitas pengirim sering hanya berupa
// `<lid>@lid` (angka LID BUKAN nomor telepon). Di sini semua identitas
// di-resolve ke daftar lengkap (LID + nomor telepon) via lid.js supaya
// allowlist owner tetap bekerja.
//
// Pola pairing & reconnect diambil dari referensi nix.js (bot produksi):
//   - pairing hanya untuk sesi BELUM terdaftar (state.creds.registered)
//   - custom pairing code = argumen ke-2 requestPairingCode
//   - jeda 3000 ms setelah event 'connecting'
//   - guard pairingRequested supaya tidak minta berkali-kali
//   - reconnect exponential backoff; loggedOut → hapus sesi + pairing ulang

import { makeWASocket, DisconnectReason, useMultiFileAuthState, makeCacheableSignalKeyStore, downloadMediaMessage, fetchLatestWaWebVersion }
  from '@itsliaaa/baileys'
import { Boom } from '@hapi/boom'
import pino from 'pino'
import fs from 'node:fs/promises'
import path from 'node:path'
import { loadConfig } from './config.js'
import { handleIncoming, setSock, setUiConfig, isOwner } from './router.js'
import { agentHeaders } from './agent-auth.js'
import { resolveSenderIds, isLid } from './lid.js'
import { isOwn } from './state.js'
import { isTextLikeDocument, extractHtml, htmlFromQuotedInner, stripLeadingMentions, ownerBypassGroupMention } from './commands.js'
import { isAfkGroupCommand } from './afk-policy.js'
import { shouldInspectMedia } from './media-policy.js'
import { instrumentSock } from './sentlog.js'
import { guardSock, hardenSessionFiles } from './sessionguard.js'
import { startActionServer } from './api/actions.js'
import { startScheduler } from './scheduler.js'
import { listPlugins } from './plugin-host.js'
import { setCardLabel, setCardMedia, primeCardMedia } from './api/rich.js'

const config = loadConfig()
// Banner label kartu (Fitur #31): teks/warna dari config; default pink.
if (config.card && config.card.label !== undefined) setCardLabel(config.card.label)
// Media intro (video/carousel/product) di bawah label — Fitur #37.
if (config.card && config.card.media !== undefined) setCardMedia(config.card.media)
const silent = pino({ level: 'silent' })
const MAX_INBOUND_MEDIA_BYTES = 100 * 1024 * 1024

// Jaring keselamatan proses: satu promise yang reject TANPA catch (mis. dari
// pustaka WA) bisa mematikan transport Node → bot berhenti membalas tanpa
// jejak. Catat & JANGAN biarkan proses mati karena hal yang tak terduga.
process.on('unhandledRejection', (reason) => {
  console.error('[wa] unhandledRejection:', reason?.message || reason)
})
process.on('uncaughtException', (err) => {
  // Kesalahan tak tertangkap bisa meninggalkan proses dalam keadaan rusak
  // (socket mati, state separuh jalan). Lebih baik keluar agar start.sh /
  // panel me-restart proses yang SEHAT daripada diam-diam berhenti membalas.
  //
  // PENGECUALIAN: kesalahan I/O pada socket yang TIDAK merusak state —
  // `EPIPE`/`ECONNRESET`/`ERR_STREAM_DESTROYED` dari unggahan media besar —
  // tidak boleh menjatuhkan bot. Dulu satu unggahan video 1,3 MB memicu
  // EPIPE tepat setelah connect → proses keluar → panel menandai crash →
  // bot mati. Untuk kelas kesalahan ini cukup dicatat.
  const code = err?.code || err?.errno
  const transient = code === 'EPIPE' || code === 'ECONNRESET'
    || code === 'ERR_STREAM_DESTROYED' || code === 'ETIMEDOUT'
    || /EPIPE|ECONNRESET|premature close/i.test(String(err?.message || ''))
  if (transient) {
    console.error('[wa] kesalahan I/O sementara (diabaikan):', err?.message || err)
    return
  }
  console.error('[wa] uncaughtException:', err?.stack || err?.message || err)
  setTimeout(() => process.exit(1), 100)
})

// Server aksi loopback: inti agent (Python) memanggil aksi WhatsApp lewat sini.
// Hanya bind 127.0.0.1 + wajib token, supaya tidak ada yang bisa menyuruh bot
// menendang orang dari jaringan.
let actionServer = null
let currentSock = null
let scheduler = null

let reconnectAttempts = 0
let reconnectTimer = null
let pairingRequested = false
// Watchdog koneksi: beberapa gangguan (jaringan mati mendadak, socket "zombie")
// TIDAK memicu event 'close' → bot tampak hidup tapi diam selamanya. Kita lacak
// status terakhir & waktu event terakhir, lalu paksa reconnect bila macet.
let connState = 'connecting'
let lastConnEventAt = Date.now()
let connecting = false

// Ekstraksi teks pesan (termasuk yang di-reply).
function getText(message) {
  const m = unwrapMessageContent(message)
  return (
    m.conversation ||
    m.extendedTextMessage?.text ||
    m.imageMessage?.caption ||
    m.videoMessage?.caption ||
    m.documentMessage?.caption ||
    m.buttonsResponseMessage?.selectedButtonId ||
    m.listResponseMessage?.singleSelectReply?.selectedRowId ||
    m.templateButtonReplyMessage?.selectedId ||
    ''
  ).trim()
}

// Ambil ISI (content) pesan yang DI-REPLY, setelah membuka pembungkus
// (viewOnce/ephemeral/edited/documentWithCaption). Mengembalikan objek pesan
// dalam bentuk yang sama seperti pesan biasa (mis. { documentMessage }, atau
// { conversation }), atau null.
function quotedInner(message) {
  const m = unwrapMessageContent(message)
  const ci = m.extendedTextMessage?.contextInfo
    || m.imageMessage?.contextInfo
    || m.videoMessage?.contextInfo
    || m.audioMessage?.contextInfo
    || m.documentMessage?.contextInfo
    || m.stickerMessage?.contextInfo
    || null
  const q = ci && ci.quotedMessage
  if (!q) return null
  return unwrapMessageContent(q)
}

// Ekstraksi TEKS pesan yang DI-REPLY (kutipan) — supaya AI paham konteks apa
// yang sedang dibicarakan pemilik. WhatsApp menaruh pesan asli di
// `contextInfo.quotedMessage`, dengan bentuk yang sama seperti pesan biasa.
// Kembalikan { text, kind } atau null.
function getQuoted(message) {
  const inner = quotedInner(message)
  if (!inner) return null
  const text = (
    inner.conversation ||
    inner.extendedTextMessage?.text ||
    inner.imageMessage?.caption ||
    inner.videoMessage?.caption ||
    inner.documentMessage?.caption ||
    ''
  ).trim()
  let kind = 'text'
  if (!text) {
    if (inner.imageMessage) kind = 'gambar'
    else if (inner.videoMessage) kind = 'video'
    else if (inner.audioMessage) kind = 'audio'
    else if (inner.documentMessage) kind = `berkas:${inner.documentMessage.fileName || ''}`
    else if (inner.stickerMessage) kind = 'stiker'
    else if (inner.locationMessage) kind = 'lokasi'
    else if (inner.contactMessage || inner.contactsArrayMessage) kind = 'kontak'
    else kind = 'pesan'
  }
  return { text, kind }
}

// Ambil HTML dari pesan yang DI-REPLY, bila ada — entah berupa TEKS berisi HTML
// (`conversation`/`extendedTextMessage`, termasuk yang dibungkus ```fence```),
// atau LAMPIRAN dokumen (.html, atau teks seperti .txt/.md yang isinya HTML)
// yang perlu diunduh dulu. Dipakai `!run` agar bisa menjalankan HTML dari pesan
// yang di-reply. Mengembalikan string HTML bersih atau null.
async function getQuotedHtml(message, sock) {
  const inner = quotedInner(message)
  if (!inner) return null
  return htmlFromQuotedInner(inner, {
    download: async (doc) => {
      const fake = { key: message.key, message: { documentMessage: doc } }
      return downloadMediaMessage(fake, 'buffer', {}, { logger: silent, reuploadRequest: sock.updateMediaMessage })
    },
  })
}

// Kenali jenis lampiran dari sebuah isi pesan (image/video/audio/document/sticker)
// beserta metadata pentingnya. Mengembalikan null bila bukan lampiran.
function attachmentOf(inner) {
  if (!inner) return null
  if (inner.imageMessage) {
    const m = inner.imageMessage
    return { kind: 'image', mime: m.mimetype || 'image/jpeg', filename: '', caption: m.caption || '' }
  }
  if (inner.videoMessage) {
    const m = inner.videoMessage
    return { kind: 'video', mime: m.mimetype || 'video/mp4', filename: '', caption: m.caption || '' }
  }
  if (inner.audioMessage) {
    const m = inner.audioMessage
    return { kind: 'audio', mime: m.mimetype || 'audio/ogg', filename: '', caption: '' }
  }
  if (inner.documentMessage) {
    const m = inner.documentMessage
    return { kind: 'document', mime: m.mimetype || '', filename: m.fileName || '', caption: m.caption || '' }
  }
  if (inner.stickerMessage) {
    const m = inner.stickerMessage
    return { kind: 'sticker', mime: m.mimetype || 'image/webp', filename: '', caption: '' }
  }
  return null
}

function quotedMessageKey(message) {
  const m = unwrapMessageContent(message)
  const ci = m.extendedTextMessage?.contextInfo || m.imageMessage?.contextInfo
    || m.videoMessage?.contextInfo || m.audioMessage?.contextInfo
    || m.documentMessage?.contextInfo || m.stickerMessage?.contextInfo || {}
  return { ...(message?.key || {}), id: ci.stanzaId || message?.key?.id,
    participant: ci.participant || message?.key?.participant }
}

// Unduh lampiran sebuah pesan → kembalikan Buffer, atau null bila gagal.
async function downloadAttachment(message, inner, sock) {
  const targetMessage = inner || unwrapMessageContent(message)
  const target = { key: inner ? quotedMessageKey(message) : message?.key, message: targetMessage }
  let firstError = null
  try {
    const buf = await downloadMediaMessage(target, 'buffer', {}, {
      logger: silent, reuploadRequest: sock.updateMediaMessage,
    })
    if (buf && Buffer.from(buf).length) {
      const data = Buffer.from(buf)
      if (data.length > MAX_INBOUND_MEDIA_BYTES) throw new Error('media melebihi batas 100 MB')
      return data
    }
  } catch (e) { firstError = e }

  // Fallback streaming path for media payloads that the message-level helper
  // cannot resolve (notably some quoted/view-once payloads in WA Web sessions).
  try {
    const content = targetMessage.imageMessage || targetMessage.videoMessage
      || targetMessage.audioMessage || targetMessage.documentMessage
      || targetMessage.stickerMessage
    const att = attachmentOf(targetMessage)
    if (!content || !att) throw new Error('payload media tidak dikenali')
    const baileys = await import('@itsliaaa/baileys')
    if (typeof baileys.downloadContentFromMessage !== 'function') {
      throw new Error('downloadContentFromMessage tidak tersedia pada paket Baileys terpasang')
    }
    const mediaType = att.kind === 'sticker' ? 'image' : att.kind
    const stream = await baileys.downloadContentFromMessage(content, mediaType, {
      logger: silent, reuploadRequest: sock.updateMediaMessage,
    })
    const chunks = []
    let total = 0
    for await (const chunk of stream) {
      const part = Buffer.from(chunk)
      total += part.length
      if (total > MAX_INBOUND_MEDIA_BYTES) throw new Error('media melebihi batas 100 MB')
      chunks.push(part)
    }
    const buffer = Buffer.concat(chunks, total)
    if (!buffer.length) throw new Error('stream media kosong')
    return buffer
  } catch (fallbackError) {
    console.error('[wa] gagal unduh lampiran:', firstError?.message || 'downloadMediaMessage gagal',
      '| fallback:', fallbackError?.message || fallbackError)
    return null
  }
}

function unwrapMessageContent(message) {
  let inner = message?.message || message || {}
  for (let i = 0; i < 6; i++) {
    const next = inner.ephemeralMessage?.message || inner.viewOnceMessage?.message
      || inner.viewOnceMessageV2?.message || inner.viewOnceMessageV2Extension?.message
      || inner.documentWithCaptionMessage?.message
      || inner.editedMessage?.message
    if (!next) break
    inner = next
  }
  return inner
}

// Tentukan jenis lampiran pesan yang masuk (bukan kutipan), termasuk wrapper.
function incomingAttachment(message) {
  return attachmentOf(unwrapMessageContent(message))
}

// Tulis lampiran ke folder STAGING (`data/inbox`) dan kembalikan metadata
// (path, kind, mime, filename, bytes). Inti agent (yang tahu tenant) nanti
// memindahkannya ke penyimpanan media milik owner lewat `media.store`.
async function stageAttachment(buf, att, message) {
  try {
    if (!buf || Buffer.byteLength(buf) > MAX_INBOUND_MEDIA_BYTES) throw new Error('media kosong atau melebihi batas 100 MB')
    const dir = path.join(process.cwd(), 'data', 'inbox')
    await fs.mkdir(dir, { recursive: true })
    const ext = (() => {
      const fromName = att.filename && att.filename.includes('.') ? att.filename.split('.').pop() : ''
      if (fromName) return fromName.toLowerCase().slice(0, 6)
      const m = (att.mime || '').split(';')[0]
      const map = { 'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/gif': 'gif',
        'video/mp4': 'mp4', 'video/webm': 'webm', 'video/3gpp': '3gp', 'image/avif': 'avif', 'audio/ogg': 'ogg', 'audio/mpeg': 'mp3', 'application/pdf': 'pdf' }
      return map[m] || 'bin'
    })()
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    const file = path.join(dir, `${id}.${ext}`)
    await fs.writeFile(file, Buffer.from(buf))
    return {
      path: file, kind: att.kind, mime: att.mime || '',
      filename: att.filename || `${id}.${ext}`,
      bytes: Buffer.byteLength(buf),
      caption: att.caption || '',
      message_id: message?.key?.id || '',
    }
  } catch (e) {
    console.error('[wa] gagal staging lampiran', e?.message || e)
    return null
  }
}

function senderOf(message) {
  return message.key.participant || message.key.remoteJid || ''
}

function senderAltOf(message) {
  return message.key.participantAlt || message.key.remoteJidAlt || ''
}

function botJidsOf(sock) {
  return [sock?.user?.id, sock?.user?.lid].filter(Boolean)
}

// Apakah pesan ini memanggil bot? (di-@ atau balas pesan bot)
// Perbandingan dilakukan pada SEMUA bentuk identitas (PN maupun LID) supaya
// mention/reply yang memakai LID tetap terdeteksi.
function addressedToBot(message, sock, senderIds = []) {
  const m = unwrapMessageContent(message)
  const ci = m.extendedTextMessage?.contextInfo
    || m.imageMessage?.contextInfo
    || m.videoMessage?.contextInfo
    || m.documentMessage?.contextInfo
    || m.stickerMessage?.contextInfo
    || {}
  const botIds = new Set(botJidsOf(sock).flatMap((j) => idsOf(j)))
  // Identitas bot dalam bentuk apa pun (LID/PN) juga dianggap "bot".
  const isBot = (j) => idsOf(j).some((x) => botIds.has(x))
  const mentioned = Array.isArray(ci.mentionedJid) ? ci.mentionedJid : []
  const mentionedBot = mentioned.some((j) => isBot(j))
  const repliedTo = ci.participant || ''
  const repliedBot = Boolean(repliedTo) && isBot(repliedTo)
  return { mentionedBot, repliedBot, direct: mentionedBot || repliedBot }
}

function idsOf(jid) {
  const s = String(jid || '')
  if (!s) return []
  const user = s.split('@')[0].split(':')[0]
  return user ? [user] : []
}

// Hapus sesi mati supaya bisa pairing ulang dari nol.
async function resetSession(authDir) {
  try {
    await fs.rm(path.resolve(authDir), { recursive: true, force: true })
    console.log(`[wa] session lama dihapus: ${authDir}`)
  } catch (e) {
    console.error('[wa] gagal hapus session', e.message)
  }
}

async function connect() {
  if (connecting) return
  connecting = true
  try {
    await _connect()
  } finally {
    connecting = false
  }
}

async function _connect() {
  const authDir = config.whatsapp.authDir
  const { state, saveCreds } = await useMultiFileAuthState(authDir)

  const sock = makeWASocket({
    version: [2, 3000, 1050908835],
    logger: silent,
    auth: { ...state, keys: makeCacheableSignalKeyStore(state.keys, silent) },
    markOnlineOnConnect: false,
    syncFullHistory: false,
  })
  // Rekam SETIAP pesan keluar (dari jalur mana pun: AI, plugin, cron, kartu
  // rich) agar bisa dihapus lewat plugin `delete`. Tanpa ini, hanya balasan
  // router yang tercatat → "!delete" sering bilang tidak ada pesan.
  instrumentSock(sock)
  // Proteksi sesi (transport): deteksi enumerasi perangkat DIRI
  // (getUSyncDevices([me.id]) / ([me.lid]) = device bin + primary device).
  guardSock(sock, {
    blockSelfEnumeration: config.whatsapp.blockSelfDeviceEnumeration === true,
    logger: silent,
  })
  // Keraskan izin berkas sesi (creds.json & kunci-kunci) ke 0700/0600.
  try { hardenSessionFiles(authDir) } catch { /* best-effort */ }
  setSock(sock)
  setUiConfig(config.ui)
  currentSock = sock

  sock.ev.on('creds.update', saveCreds)

  // SAMBUTAN ANGGOTA BARU (opsional, `groupAdmin.welcome: true`).
  // Teks sambutan diambil dari inti agent (data/knowledge/<ruangan>/sapaan.md),
  // jadi owner bisa mengubahnya tanpa menyentuh kode.
  sock.ev.on('group-participants.update', async ({ id, participants, action }) => {
    if (action !== 'add' || !config?.groupAdmin?.welcome) return
    for (const p of participants || []) {
      try {
        const host = config.agent?.host || '127.0.0.1'
        const port = config.agent?.port || 8787
        const r = await fetch(`http://${host}:${port}/group`, {
          method: 'POST',
          signal: AbortSignal.timeout(6000),
          headers: agentHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({
            action: 'welcome',
            chat_id: id,
            nama: String(p).split('@')[0],
            grup: '',
          }),
        })
        const j = await r.json()
        if (j?.teks) await sock.sendMessage(id, { text: j.teks })
      } catch (e) {
        console.warn('[grup] sambutan dilewati:', e.message)
      }
    }
  })

  sock.ev.on('messages.upsert', async ({ messages }) => {
    for (const message of messages) {
      if (!message.message) continue

      // Pesan bot sendiri harus diabaikan. TAPI di self-chat (chat ke diri
      // sendiri) pesan user juga ber-flag fromMe, jadi kita bedakan lewat
      // daftar id pesan yang benar-benar dikirim bot (state.js).
      const own = isOwn(message.key?.id)
      if (message.key.fromMe && own) continue
      const isSelfChat = Boolean(message.key.fromMe) && !own

      try {
        const chatId = message.key.remoteJid
        const isGroup = String(chatId).endsWith('@g.us')
        const text = getText(message)

        // Di self-chat tidak ada participant: pengirim = pemilik perangkat bot.
        const senderId = isSelfChat
          ? (sock.user?.id || chatId)
          : senderOf(message)
        const senderAlt = isSelfChat
          ? (sock.user?.lid || '')
          : senderAltOf(message)
        // Resolve LID -> nomor telepon (dan sebaliknya) untuk allowlist.
        const senderIds = await resolveSenderIds(sock, authDir, [senderId, senderAlt])

        const addressed = addressedToBot(message, sock, senderIds)

        // Kebijakan grup: hanya proses bila bot dipanggil — TAPI untuk OWNER
        // perintah "!" dan konten HTML lolos TANPA mention. Reply ke media visual
        // dengan prompt juga lolos sebagai permintaan Vision eksplisit. Peserta
        // grup punya satu pengecualian non-owner yang sempit: !afk untuk dirinya.
        const docMsg = message.message?.documentMessage
        const ownerDirect = ownerBypassGroupMention({
          text,
          isOwner: isOwner(config, ...[senderIds, senderId, senderAlt].filter(Boolean)),
          hasTextDoc: Boolean(docMsg && isTextLikeDocument({ fileName: docMsg.fileName, mimetype: docMsg.mimetype })),
        })
        const afkParticipantCommand = isAfkGroupCommand(stripLeadingMentions(text), isGroup)
        const quotedVisual = attachmentOf(quotedInner(message))
        const explicitVisualReply = shouldInspectMedia({ text, quotedAttachment: quotedVisual })
        if (isGroup && config.whatsapp.requireMentionInGroup && !addressed.direct && !ownerDirect
            && !afkParticipantCommand && !explicitVisualReply) {
          continue
        }

        // Lampiran dokumen bertekstual (mis. .html tanpa caption, atau .txt/.md
        // yang isinya HTML) → unduh isinya supaya bisa ditampilkan sebagai
        // PREVIEW WebView (web2ui), bukan dibuang karena `text` kosong.
        // Hanya untuk owner (dicek di router).
        let htmlAttachment = null
        const doc = message.message?.documentMessage
        if (doc && isTextLikeDocument({ fileName: doc.fileName, mimetype: doc.mimetype })) {
          try {
            const buf = await downloadMediaMessage(message, 'buffer', {}, { logger: silent, reuploadRequest: sock.updateMediaMessage })
            const raw = Buffer.from(buf).toString('utf8')
            const html = extractHtml(raw)
            if (html) htmlAttachment = html
          } catch (e) {
            console.error('[wa] gagal unduh lampiran HTML', e.message)
          }
        }

        // Lampiran MEDIA (gambar/video/audio/dokumen/stiker) → unduh & simpan ke
        // staging supaya inti agent bisa menyimpannya sebagai ASET (dan "melihat"
        // gambar via tool vision). Kutipan (pesan yang di-reply) juga diambil
        // bila berupa lampiran — inilah perbaikan "asset yang di-reply tidak
        // bisa dibaca agent".
        let attachment = null
        let quotedAttachment = null
        let attachmentDownloadFailed = false
        const incomingAtt = incomingAttachment(message)
        const qInner = quotedInner(message)
        const quotedAtt = attachmentOf(qInner)
        try {
          if (incomingAtt) {
            const buf = await downloadAttachment(message, null, sock)
            if (buf) attachment = await stageAttachment(buf, incomingAtt, message)
            if (!attachment) attachmentDownloadFailed = true
          }
          if (quotedAtt) {
            const qbuf = await downloadAttachment(message, qInner, sock)
            if (qbuf) quotedAttachment = await stageAttachment(qbuf, quotedAtt, message)
            if (!quotedAttachment) attachmentDownloadFailed = true
          }
        } catch (e) {
          attachmentDownloadFailed = true
          console.error('[wa] gagal menyiapkan lampiran', e?.message || e)
        }

        if (!text && !htmlAttachment && !attachment && !quotedAttachment) continue

        // `!run` bisa dijalankan atas pesan yang DI-REPLY (teks HTML, HTML di
        // dalam ```fence```, atau lampiran .html/.txt). Ambil isinya hanya bila
        // perintahnya memang `!run`, supaya tidak ada unduhan sia-sia.
        let quotedHtml = null
        if (/^!run\b/i.test(stripLeadingMentions(text))) {
          quotedHtml = await getQuotedHtml(message, sock)
        }

        await handleIncoming({
          sock,
          config,
          message,
          chatId,
          isGroup,
          text,
          htmlAttachment,
          attachment,
          quotedAttachment,
          incomingAttachment: incomingAtt,
          quotedAttachmentMeta: quotedAtt,
          attachmentDownloadFailed,
          quotedHtml,
          quoted: getQuoted(message),
          senderId,
          senderAlt,
          senderIds,
          isSelfChat,
          senderName: message.pushName || '',
          messageId: message.key?.id || '',
          isMentionBot: addressed.mentionedBot,
          isReplyToBot: addressed.repliedBot,
        })
      } catch (e) {
        console.error('[wa] gagal memproses pesan', e.message)
      }
    }
  })

  sock.ev.on('connection.update', async ({ connection, lastDisconnect }) => {
    if (connection) { connState = connection; lastConnEventAt = Date.now() }
    // Pairing: hanya untuk sesi yang BELUM terdaftar.
    if (connection === 'connecting'
      && !state.creds.registered
      && config.whatsapp.pairingNumber
      && !pairingRequested) {
      pairingRequested = true
      const customCode = String(config.whatsapp.pairingCode || '').trim().toUpperCase()
      setTimeout(async () => {
        try {
          const code = await sock.requestPairingCode(
            config.whatsapp.pairingNumber,
            customCode || undefined,
          )
          console.log(`[wa] 🔑 Pairing code: ${code}`)
          console.log('[wa] Masukkan di WhatsApp → Perangkat Tertaut → Tautkan dengan nomor telepon.')
        } catch (e) {
          pairingRequested = false // biar bisa dicoba lagi
          console.error('[wa] pairing code gagal dibuat', e.message)
        }
      }, 3000)
    }

    if (connection === 'open') {
      reconnectAttempts = 0
      pairingRequested = false
      console.log(`[wa] ✅ ${config.identity.name} terhubung sebagai ${sock.user?.id || 'akun WhatsApp'}`)
      if (sock.user?.lid) console.log(`[wa] LID bot: ${sock.user.lid}`)
      // Unggah media intro SEKALI (video/carousel/product) agar tiap kartu
      // berikutnya tinggal memakai URL hasil upload — hemat & cepat.
      //
      // JEDA dulu: mengunggah media besar tepat saat 'open' bisa memicu EPIPE
      // (socket belum stabil) → dulu mematikan bot. Tunggu 8 detik, lalu
      // coba beberapa kali dengan jeda bertambah. Semua kegagalan diabaikan
      // (bot tetap membalas, hanya tanpa media) — degradasi anggun.
      const primeWithRetry = async (attempt = 1) => {
        try {
          const u = await primeCardMedia(sock)
          if (u) { console.log('[wa] media intro siap (di-cache)'); return }
        } catch (err) {
          console.warn(`[wa] media intro gagal (percobaan ${attempt}):`, err?.message || err)
        }
        if (attempt < 3) setTimeout(() => { primeWithRetry(attempt + 1).catch(() => {}) }, 15000 * attempt)
      }
      setTimeout(() => { primeWithRetry().catch(() => {}) }, 8000)
    }

    if (connection === 'close') {
      const status = new Boom(lastDisconnect?.error)?.output?.statusCode
      if (status === DisconnectReason.loggedOut) {
        console.error('[wa] session logout; hapus session & pairing ulang...')
        await resetSession(authDir)
        pairingRequested = false
        reconnectAttempts = 0
        clearTimeout(reconnectTimer)
        reconnectTimer = setTimeout(connect, 2000)
        return
      }
      const rc = config.whatsapp.reconnect || {}
      const max = rc.maxAttempts ?? 10
      const base = rc.baseDelayMs ?? 3000
      const cap = rc.maxDelayMs ?? 60000
      // JANGAN pernah menyerah permanen. Dulu setelah `max` percobaan bot
      // hanya menyetel process.exitCode=1 TANPA keluar — proses tetap hidup
      // (scheduler/action server) dengan socket WA MATI → bot "kadang tidak
      // merespons" selamanya sampai di-restart manual. Sekarang: tetap coba
      // ulang selamanya dengan jeda yang dibatasi `cap`, dan setelah melewati
      // `max` jeda di-reset agar tidak menumpuk tanpa batas.
      reconnectAttempts += 1
      const step = ((reconnectAttempts - 1) % Math.max(1, max))
      const delay = Math.min(base * 2 ** step, cap)
      if (reconnectAttempts === max || reconnectAttempts % max === 0) {
        console.error(`[wa] sudah ${reconnectAttempts}x gagal reconnect — terus mencoba (jeda maks ${cap}ms)`)
      }
      console.warn(`[wa] terputus; reconnect #${reconnectAttempts} dalam ${delay}ms`)
      clearTimeout(reconnectTimer)
      reconnectTimer = setTimeout(connect, delay)
    }
  })
}

console.log(`[wa] memulai ${config.identity.name} ${config.identity.version}`)
console.log(`[wa] inti agent: http://${config.agent.host}:${config.agent.port}/message`)
console.log(`[wa] owner: ${config.owner.length ? config.owner.join(', ') : '(KOSONG!)'}`)
if (!config.owner.length) console.warn('[wa] ⚠️  WA_OWNER_NUMBERS kosong — bot tidak akan melayani siapa pun')
if (!config.whatsapp.pairingNumber) console.warn('[wa] ⚠️  WA_PAIRING_NUMBER kosong — pairing dilewati')

// Server aksi loopback (dipakai inti agent Python untuk kick/promote/edit grup/…).
// Token dari .env (ACTION_TOKEN); tanpa token, server tidak dijalankan.
const actionPort = Number(process.env.ACTION_PORT || 8788)
const actionToken = process.env.ACTION_TOKEN || ''
if (actionToken) {
  actionServer = startActionServer({
    port: actionPort,
    token: actionToken,
    getSock: () => currentSock,
  })
} else {
  console.warn('[wa] ⚠️  ACTION_TOKEN kosong — aksi WhatsApp (grup/profil) dinonaktifkan')
}

// Penjadwal tugas (cron) — membaca data/cron/jobs.json & menjalankannya.
// Job kind=send/agent/plugin; eksekusi di sini karena hanya Node yang punya socket.
scheduler = startScheduler({ getSock: () => currentSock })

// Info plugin yang tersedia saat boot (agar pemilik tahu apa yang bisa dipakai).
try {
  const plugins = await listPlugins()
  const names = plugins.filter((p) => p.ok).map((p) => p.name)
  console.log(`[plugin] ${names.length} plugin: ${names.join(', ') || '(kosong)'}`)
} catch (e) {
  console.warn(`[plugin] gagal memuat daftar plugin: ${e.message}`)
}

// Watchdog koneksi WA: bila status bukan 'open' terlalu lama (atau 'open' tapi
// tak ada event apa pun sangat lama), paksa sambung ulang. Ini menangkap kasus
// socket "zombie" yang tak pernah memicu event 'close' → bot diam tanpa jejak.
const WATCHDOG_STALE_MS = Math.max(60000, Number(process.env.WA_WATCHDOG_MS || 180000))
setInterval(() => {
  try {
    const idle = Date.now() - lastConnEventAt
    const stuck = connState !== 'open' && idle > WATCHDOG_STALE_MS
    if (stuck && !connecting) {
      console.error(`[wa] watchdog: status='${connState}' macet ${Math.round(idle / 1000)}s — menyambung ulang`)
      lastConnEventAt = Date.now()
      try { currentSock?.ws?.close?.() } catch { /* abaikan */ }
      connect()
    }
  } catch { /* watchdog tak boleh mematikan bot */ }
}, 30000)

await connect()
