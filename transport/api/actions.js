// actions.js — jembatan aksi WhatsApp: inti agent (Python) -> socket (Node).
//
// Kenapa ada: kecerdasan ada di Python, tapi hanya Node yang memegang socket
// WhatsApp. Jadi Python mengirim permintaan aksi lewat HTTP loopback ke sini,
// dan di sini baru dipanggil method socket yang sebenarnya.
//
// Keamanan:
//   - HANYA bind ke 127.0.0.1 (tidak terekspos ke jaringan)
//   - WAJIB header `x-kwee-token` yang cocok dengan token dari .env
//   - aksi merusak (kick/promote/ubah grup/hapus kontak) WAJIB `confirm: true`
//     supaya AI tidak bisa melakukannya tanpa persetujuan pemilik lebih dulu

import http from 'node:http'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { buildRichCard, bizNode, sendRich, groupReplies, relayCard } from './rich.js'
import { buildProgressIndicatorMetadata, progressStep, PlanningStepStatus } from './progress.js'
import { listPlugins, runPlugin } from '../plugin-host.js'

const jalankan = promisify(execFile)

// Batas media WhatsApp: video/gambar/audio ~16 MB, dokumen ~100 MB. Di atas
// itu unggahan ditolak server WhatsApp, bukan oleh kita.
const BATAS_MEDIA = 15 * 1024 * 1024
const BATAS_DOKUMEN = 95 * 1024 * 1024

function diDalam(abs) {
  // Default: SEMUA path boleh (mode owner — pemilik bot bekerja di panel, /tmp,
  // /home/container, folder mount, mana saja). Set KWEE_ALLOW_ANY_PATH=0 untuk
  // mengurung pengiriman berkas ke root proyek saja.
  if (process.env.KWEE_ALLOW_ANY_PATH !== '0') return true
  const root = path.resolve(process.cwd())
  return abs === root || abs.startsWith(root + path.sep)
}

/**
 * Kecilkan video agar muat batas WhatsApp (H.264/AAC, tinggi maks 720p).
 * Mengembalikan path berkas hasil (atau null bila gagal / ffmpeg tak ada).
 */
async function kompresVideo(abs, batas = BATAS_MEDIA) {
  const out = path.join(os.tmpdir(), `kwee-kompres-${Date.now()}.mp4`)
  const args = [
    '-y', '-i', abs,
    '-vf', "scale='min(1280,iw)':-2",
    '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '28',
    '-c:a', 'aac', '-b:a', '96k',
    '-movflags', '+faststart',
    '-fs', String(batas),
    out,
  ]
  try {
    await jalankan('ffmpeg', args, { timeout: 10 * 60 * 1000, maxBuffer: 1024 * 1024 })
    if (fs.existsSync(out) && fs.statSync(out).size > 0) return out
  } catch { /* ffmpeg tidak ada atau gagal → pemanggil memakai berkas asli */ }
  try { fs.unlinkSync(out) } catch { /* abaikan */ }
  return null
}

// Bangun ctx untuk plugin: API WhatsApp + kartu kaya, dibatasi ke owner.
function makePluginCtx(sock) {
  return ({ plugin, logs }) => ({
    plugin: { name: plugin?.name || '', description: plugin?.description || '' },
    wa: {
      send: (to, content) => sock.sendMessage(toJid(to), typeof content === 'string' ? { text: content } : content),
      // `blocks` boleh array (kartu biasa) ATAU objek balasan penuh
      // ({type,blocks,progressIndicator,disclaimerText}).
      sendRich: (to, blocks, opts = {}) => sendRich(sock, toJid(to), Array.isArray(blocks) ? { type: 'richResponse', blocks } : blocks, opts.quoted),
      group: (action, args) => runAction(sock, action, args),
      raw: () => sock,
    },
    airich: { buildRichCard, bizNode, sendRich, groupReplies, buildProgressIndicatorMetadata, progressStep, PlanningStepStatus },
    log: (...x) => logs.push(x.map((v) => (typeof v === 'string' ? v : JSON.stringify(v))).join(' ')),
  })
}

// Aksi yang mengubah keadaan dan berisiko → wajib konfirmasi eksplisit.
export const DESTRUCTIVE = new Set([
  'kick', 'promote', 'demote', 'set_subject', 'set_description', 'leave',
  'create_group', 'revoke_invite', 'remove_contact', 'profile_pic',
  'profile_name', 'profile_status', 'block', 'unblock',
  'ephemeral', 'announce', 'locked', 'approve', 'reject', 'delete_message',
  'join',
])

// Ambil KODE undangan dari link penuh (https://chat.whatsapp.com/<code>),
// bentuk `/invite/<code>`, atau kode mentah. Kembalikan '' bila tak sah.
export function inviteCodeOf(v) {
  const raw = String(v || '').trim()
  if (!raw) return ''
  const m = raw.match(/chat\.whatsapp\.com\/(?:invite\/)?([A-Za-z0-9_-]{10,})/)
  if (m) return m[1]
  if (/^[A-Za-z0-9_-]{10,}$/.test(raw)) return raw
  return ''
}

// Baca argumen dengan aman.
function req(args, key, fallback = undefined) {
  if (args && Object.prototype.hasOwnProperty.call(args, key) && args[key] !== undefined) return args[key]
  return fallback
}

// Normalisasi target ke JID lengkap. Nomor telanjang -> @s.whatsapp.net.
// Menolak nilai yang tidak punya bagian pengguna (mis. "@g.us", "g.us").
export function toJid(v, suffix = 's.whatsapp.net') {
  const s = String(v || '').trim()
  if (!s) return ''
  if (s.includes('@')) {
    const [local, server] = s.split('@')
    if (!local) return '' // "@g.us" -> tidak valid
    return server ? s : `${local}@${suffix}`
  }
  const digits = s.replace(/[^0-9]/g, '')
  if (!digits) return '' // "g.us", "abc" -> tidak valid
  return `${digits}@${suffix}`
}

// True bila nilai tampak seperti JID yang punya bagian pengguna.
export function looksLikeJid(v) {
  const s = String(v || '').trim()
  if (!s || !s.includes('@')) return false
  return Boolean(s.split('@')[0])
}

// Tebak MIME dari ekstensi file (WhatsApp butuh mimetype untuk dokumen).
function guessMime(name) {
  const ext = String(name || '').toLowerCase().split('.').pop()
  const map = {
    pdf: 'application/pdf', zip: 'application/zip', gz: 'application/gzip',
    tgz: 'application/gzip', tar: 'application/x-tar', txt: 'text/plain',
    md: 'text/markdown', json: 'application/json', csv: 'text/csv',
    js: 'text/javascript', mjs: 'text/javascript', ts: 'text/plain',
    py: 'text/x-python', html: 'text/html', css: 'text/css',
    png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif',
    webp: 'image/webp', mp4: 'video/mp4', mp3: 'audio/mpeg', wav: 'audio/wav',
    doc: 'application/msword', xls: 'application/vnd.ms-excel',
    docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    sh: 'text/x-shellscript', yml: 'text/yaml', yaml: 'text/yaml',
    db: 'application/x-sqlite3', sqlite: 'application/x-sqlite3',
  }
  return map[ext] || 'application/octet-stream'
}

// Bangun daftar JID dari nilai apa pun (string tunggal atau array).
function jidsOf(v, suffix) {
  if (Array.isArray(v)) return v.map((x) => toJid(x, suffix)).filter(Boolean)
  const j = toJid(v, suffix)
  return j ? [j] : []
}

/**
 * Jalankan satu aksi terhadap socket WhatsApp.
 * Mengembalikan objek hasil; tidak pernah melempar (error dibungkus).
 */
export async function runAction(sock, name, args = {}) {
  if (!sock) return { error: 'socket WhatsApp belum siap' }
  const a = args || {}

  if (DESTRUCTIVE.has(name) && a.confirm !== true) {
    return { error: `aksi '${name}' mengubah data dan butuh konfirmasi (confirm: true)` }
  }
  // Gerbang kedua: bukti persetujuan pemilik harus diteruskan eksplisit dari
  // inti agent (`__owner_confirmed`), bukan sekadar confirm dari pemanggil.
  if (DESTRUCTIVE.has(name) && a.__owner_confirmed !== true) {
    return { error: `aksi '${name}' ditahan: tidak ada persetujuan pemilik (__owner_confirmed)` }
  }

  try {
    switch (name) {
      // ---- kirim & pesan ----
      case 'send': {
        const to = toJid(req(a, 'to') || req(a, 'chat_id'))
        if (!to) return { error: 'tujuan kosong' }
        const content = a.content && typeof a.content === 'object' ? a.content : { text: String(a.text ?? '') }
        const sent = await sock.sendMessage(to, content)
        return { ok: true, id: sent?.key?.id, to }
      }
      case 'forward': {
        const to = toJid(req(a, 'to'))
        if (!to) return { error: 'tujuan kosong' }
        const sent = await sock.sendMessage(to, { forward: a.message, force: Boolean(a.force) })
        return { ok: true, id: sent?.key?.id, to }
      }
      case 'send_file': {
        // Kirim file dari disk. GAMBAR/VIDEO/AUDIO dikirim sebagai media INLINE
        // (bisa dipratinjau langsung di WhatsApp); sisanya sebagai dokumen.
        // Mendukung file besar lewat STREAM (bukan baca seluruh isi ke memori).
        const to = toJid(req(a, 'to') || req(a, 'chat_id'))
        if (!to) return { error: 'tujuan kosong' }
        const rel = String(req(a, 'path') || req(a, 'file') || '').trim()
        if (!rel) return { error: 'path file kosong' }
        // Path: boleh relatif (terhadap root proyek) atau absolut. Mode owner
        // mengizinkan folder mana pun kecuali yang berisi rahasia sesi.
        const root = path.resolve(process.cwd())
        const abs = path.isAbsolute(rel) ? path.resolve(rel) : path.resolve(root, rel)
        if (!diDalam(abs)) {
          return { error: `path di luar folder yang diizinkan: ${abs}` }
        }
        const base = path.basename(abs)
        if (process.env.KWEE_ALLOW_SECRET_FILES !== '1') {
          if (/(^|\/)\.env/i.test(rel) || base === '.env' || /\.env\./i.test(base)) {
            return { error: 'menolak mengirim file .env (rahasia). Set KWEE_ALLOW_SECRET_FILES=1 bila memang perlu.' }
          }
          if (/(^|\/)(data\/sessions|sessions)(\/|$)/i.test(abs)) {
            return { error: 'menolak mengirim berkas sesi WhatsApp (rahasia akun)' }
          }
        }
        if (!fs.existsSync(abs) || !fs.statSync(abs).isFile()) {
          return { error: `file tidak ditemukan: ${rel}` }
        }
        const size = fs.statSync(abs).size
        const fileName = String(req(a, 'filename') || req(a, 'file_name') || base)
        const caption = String(req(a, 'caption') || '')
        const mimetype = String(req(a, 'mimetype') || req(a, 'mime') || guessMime(fileName))
        // Kirim sebagai MEDIA INLINE bila memungkinkan (gambar/video/audio).
        const asMedia = req(a, 'as_media') !== false && req(a, 'as_document') !== true
        if (asMedia && /^image\//.test(mimetype) && !/gif/.test(mimetype)) {
          const sent = await sock.sendMessage(to, {
            image: { stream: fs.createReadStream(abs) },
            ...(caption ? { caption } : {}),
          })
          return { ok: true, id: sent?.key?.id, to, kind: 'image', mimetype, bytes: size }
        }
        if (asMedia && /^image\/gif/.test(mimetype)) {
          const sent = await sock.sendMessage(to, {
            video: { stream: fs.createReadStream(abs) }, gifPlayback: true,
            ...(caption ? { caption } : {}),
          })
          return { ok: true, id: sent?.key?.id, to, kind: 'gif', mimetype, bytes: size }
        }
        if (asMedia && /^video\//.test(mimetype)) {
          let sumber = abs
          let dipadatkan = false
          if (size > BATAS_MEDIA) {
            const kecil = await kompresVideo(abs, BATAS_MEDIA)
            if (kecil) {
              sumber = kecil
              dipadatkan = true
            }
          }
          const ukuranKirim = fs.statSync(sumber).size
          if (ukuranKirim > BATAS_MEDIA) {
            // Masih kebesaran: kirim sebagai dokumen supaya tetap sampai
            // (video 100 MB pun lewat jalur dokumen), dengan catatan jelas.
            const sent = await sock.sendMessage(to, {
              document: { stream: fs.createReadStream(sumber) },
              fileName,
              mimetype,
              ...(caption ? { caption: `${caption}\n(video ${(ukuranKirim / 1048576).toFixed(1)} MB — dikirim sebagai dokumen karena melebihi batas media WhatsApp)` } : {}),
            })
            return { ok: true, id: sent?.key?.id, to, kind: 'document', mimetype,
              bytes: ukuranKirim, note: 'video melebihi batas media; dikirim sebagai dokumen' }
          }
          try {
            const sent = await sock.sendMessage(to, {
              video: { stream: fs.createReadStream(sumber) },
              ...(caption ? { caption } : {}),
            })
            return { ok: true, id: sent?.key?.id, to, kind: 'video', mimetype, bytes: ukuranKirim,
              ...(dipadatkan ? { compressed: true, from_bytes: size } : {}) }
          } catch (err) {
            // Server menolak media (mis. codec aneh) → tetap kirim sebagai dokumen.
            const sent = await sock.sendMessage(to, {
              document: { stream: fs.createReadStream(sumber) },
              fileName,
              mimetype,
              ...(caption ? { caption } : {}),
            })
            return { ok: true, id: sent?.key?.id, to, kind: 'document', mimetype,
              bytes: ukuranKirim, note: `video gagal sebagai media (${err?.message || err}); dikirim sebagai dokumen` }
          }
        }
        if (asMedia && /^audio\//.test(mimetype)) {
          const sent = await sock.sendMessage(to, {
            audio: { stream: fs.createReadStream(abs) },
            mimetype,
            ...(req(a, 'ptt') ? { ptt: true } : {}),
          })
          return { ok: true, id: sent?.key?.id, to, kind: 'audio', mimetype, bytes: size }
        }
        if (size > BATAS_DOKUMEN) {
          return { error: `berkas ${(size / 1048576).toFixed(1)} MB melebihi batas dokumen WhatsApp (~95 MB); pecah atau kecilkan dulu` }
        }
        const stream = fs.createReadStream(abs)
        const sent = await sock.sendMessage(to, {
          document: { stream },
          fileName,
          mimetype,
          ...(caption ? { caption } : {}),
        })
        return { ok: true, id: sent?.key?.id, to, kind: 'document', fileName, mimetype, bytes: size }
      }
      case 'rich': {
        // Kirim kartu AI Rich (blok teks/kode/tabel) sebagai SATU pesan rapi.
        const to = toJid(req(a, 'to') || req(a, 'chat_id'))
        if (!to) return { error: 'tujuan kosong' }
        const blocks = a.blocks || a.richResponse || []
        const card = buildRichCard(blocks, { disclaimerText: a.disclaimerText || '' })
        if (!card) return { error: 'blok kosong / tidak ada isi' }
        const sent = await relayCard(sock, to, card)
        return { ok: true, id: sent?.key?.id, to, bypass: sent?.bypassDownload || null, submessages: card.botForwardedMessage.message.richResponseMessage.submessages.length }
      }
      case 'react': {
        const to = toJid(req(a, 'chat_id') || req(a, 'to'))
        const sent = await sock.sendMessage(to, {
          react: { text: String(a.emoji ?? '👍'), key: a.key || { remoteJid: to, id: a.message_id, fromMe: Boolean(a.from_me) } },
        })
        return { ok: true, id: sent?.key?.id }
      }
      case 'edit': {
        const to = toJid(req(a, 'chat_id') || req(a, 'to'))
        const sent = await sock.sendMessage(to, { text: String(a.text ?? ''), edit: a.key || { remoteJid: to, id: a.message_id } })
        return { ok: true, id: sent?.key?.id }
      }
      case 'delete_message': {
        const to = toJid(req(a, 'chat_id') || req(a, 'to'))
        await sock.sendMessage(to, { delete: a.key || { remoteJid: to, id: a.message_id, fromMe: a.from_me !== false } })
        return { ok: true }
      }

      // ---- aksi grup ----
      case 'add':
      case 'kick':
      case 'promote':
      case 'demote': {
        const group = toJid(req(a, 'group') || req(a, 'chat_id'), 'g.us')
        const targets = jidsOf(req(a, 'targets') ?? req(a, 'target'))
        if (!group) return { error: 'grup tidak sah (JID kosong)' }
        if (!targets.length) return { error: 'target kosong' }
        const action = name === 'kick' ? 'remove' : name
        // JID bot (@bot) tidak diterima groupParticipantsUpdate — yang dipakai
        // adalah JID teleponnya. Dulu ini tampak seperti "ditolak server"
        // padahal permintaannya bahkan tidak dikirim.
        const final = action === 'add'
          ? targets.map((j) => (j.endsWith('@bot') ? j.replace(/@bot$/, '@s.whatsapp.net') : j))
          : targets
        const res = await sock.groupParticipantsUpdate(group, final, action)
        const gagal = Array.isArray(res)
          ? res.filter((r) => String(r?.status ?? '200') !== '200')
          : []
        return {
          ok: gagal.length === 0,
          action,
          group,
          targets: final,
          result: res,
          ...(gagal.length ? { ditolak: gagal.map((r) => ({ jid: r?.jid, status: r?.status })) } : {}),
        }
      }
      case 'set_subject': {
        const group = toJid(req(a, 'group') || req(a, 'chat_id'), 'g.us')
        const res = await sock.groupUpdateSubject(group, String(a.subject ?? ''))
        return { ok: true, group, result: res }
      }
      case 'set_description': {
        const group = toJid(req(a, 'group') || req(a, 'chat_id'), 'g.us')
        const res = await sock.groupUpdateDescription(group, String(a.description ?? ''))
        return { ok: true, group, result: res }
      }
      case 'group_meta': {
        const group = toJid(req(a, 'group') || req(a, 'chat_id'), 'g.us')
        const md = await sock.groupMetadata(group)
        return {
          ok: true,
          subject: md.subject,
          desc: md.desc,
          size: (md.participants || []).length,
          announce: md.announce,
          locked: md.restrict,
          participants: (md.participants || []).map((p) => ({
            id: p.id, admin: p.admin || null, lid: p.lid || null, phoneNumber: p.phoneNumber || null,
          })),
        }
      }
      case 'invite_code': {
        const group = toJid(req(a, 'group') || req(a, 'chat_id'), 'g.us')
        const code = await sock.groupInviteCode(group)
        return { ok: true, code, link: `https://chat.whatsapp.com/${code}` }
      }
      case 'revoke_invite': {
        const group = toJid(req(a, 'group') || req(a, 'chat_id'), 'g.us')
        const code = await sock.groupRevokeInvite(group)
        return { ok: true, code }
      }
      case 'leave': {
        const group = toJid(req(a, 'group') || req(a, 'chat_id'), 'g.us')
        await sock.groupLeave(group)
        return { ok: true, group }
      }
      case 'invite_info': {
        // Intip info grup dari link/kode undangan (aman, tidak masuk).
        const code = inviteCodeOf(req(a, 'link') || req(a, 'code') || req(a, 'url'))
        if (!code) return { error: 'link/kode undangan tidak sah' }
        const info = await sock.groupGetInviteInfo(code)
        return { ok: true, code, info }
      }
      case 'join': {
        // Masuk ke grup lewat link/kode undangan.
        const code = inviteCodeOf(req(a, 'link') || req(a, 'code') || req(a, 'url'))
        if (!code) return { error: 'link/kode undangan tidak sah' }
        const group = await sock.groupAcceptInvite(code)
        if (!group) {
          return { error: 'gagal masuk: kode mungkin kedaluwarsa, sudah dipakai, atau butuh persetujuan admin' }
        }
        return { ok: true, group, code }
      }
      case 'create_group': {
        const participants = jidsOf(req(a, 'participants') ?? [])
        const res = await sock.groupCreate(String(a.subject ?? 'Grup Baru'), participants)
        return { ok: true, group: res?.id, result: res }
      }
      case 'announce':
      case 'locked': {
        const group = toJid(req(a, 'group') || req(a, 'chat_id'), 'g.us')
        const on = a.value !== false
        const res = await sock.groupSettingUpdate(group, name === 'announce' ? (on ? 'announcement' : 'not_announcement') : (on ? 'locked' : 'unlocked'))
        return { ok: true, group, result: res }
      }
      case 'ephemeral': {
        const group = toJid(req(a, 'group') || req(a, 'chat_id'), 'g.us')
        const res = await sock.groupToggleEphemeral(group, Number(a.seconds ?? 86400))
        return { ok: true, group, result: res }
      }
      case 'join_requests': {
        const group = toJid(req(a, 'group') || req(a, 'chat_id'), 'g.us')
        const list = await sock.groupRequestParticipantsList(group)
        return { ok: true, requests: list }
      }
      case 'approve':
      case 'reject': {
        const group = toJid(req(a, 'group') || req(a, 'chat_id'), 'g.us')
        const targets = jidsOf(req(a, 'targets') ?? req(a, 'target'))
        const res = await sock.groupRequestParticipantsUpdate(group, targets, name === 'approve' ? 'approve' : 'reject')
        return { ok: true, group, targets, result: res }
      }

      // ---- profil & kontak ----
      case 'profile_pic': {
        const target = toJid(req(a, 'target') || req(a, 'jid'))
        const url = await sock.profilePictureUrl(target, 'image')
        return { ok: true, url }
      }
      case 'profile_name':
      case 'profile_status': {
        const v = String(a.value ?? a.text ?? '')
        const res = name === 'profile_name' ? await sock.updateProfileName(v) : await sock.updateProfileStatus(v)
        return { ok: true, result: res }
      }
      case 'remove_contact': {
        const target = toJid(req(a, 'target') || req(a, 'jid'))
        const res = await sock.removeContact(target)
        return { ok: true, result: res }
      }
      case 'add_contact': {
        const target = toJid(req(a, 'target') || req(a, 'jid'))
        const res = await sock.addOrEditContact(target, a.contact || {})
        return { ok: true, result: res }
      }
      case 'check_number': {
        const nums = jidsOf(req(a, 'numbers') ?? req(a, 'number') ?? [])
        const res = await sock.onWhatsApp(...nums)
        return { ok: true, result: res }
      }
      case 'block':
      case 'unblock': {
        const target = toJid(req(a, 'target') || req(a, 'jid'))
        const res = await sock.updateBlockStatus(target, name)
        return { ok: true, result: res }
      }

      // ---- presence & baca ----
      case 'typing':
      case 'online':
      case 'paused': {
        const to = toJid(req(a, 'to') || req(a, 'chat_id'))
        await sock.sendPresenceUpdate(name === 'online' ? 'available' : name, to)
        return { ok: true }
      }
      case 'read': {
        const to = toJid(req(a, 'chat_id') || req(a, 'to'))
        await sock.readMessages([a.key || { remoteJid: to, id: a.message_id }])
        return { ok: true }
      }

      // ---- plugin (Kwee bisa menulis & menjalankan bot kecil sendiri) ----
      case 'plugin_list': {
        const plugins = await listPlugins()
        return { ok: true, count: plugins.length, plugins }
      }
      case 'plugin_run': {
        const name = String(req(a, 'name') || '').trim()
        if (!name) return { error: 'nama plugin kosong' }
        const out = await runPlugin(name, {
          args: a.args || {},
          msg: { chatId: toJid(req(a, 'to') || req(a, 'chat_id')), args: a.args || {} },
          makeCtx: makePluginCtx(sock),
        })
        return { ok: true, plugin: name, result: out.result, logs: out.logs }
      }
      case 'plugin_reload': {
        const plugins = await listPlugins()
        return { ok: true, count: plugins.length, plugins }
      }

      default:
        return { error: `aksi tidak dikenal: ${name}` }
    }
  } catch (e) {
    return { error: `aksi '${name}' gagal: ${e.message}` }
  }
}

/** Daftar nama aksi yang tersedia (dipakai tool Python untuk validasi/prompt). */
export const ACTION_NAMES = [
  'send', 'send_file', 'rich', 'forward', 'react', 'edit', 'delete_message',
  'kick', 'promote', 'demote', 'set_subject', 'set_description', 'group_meta',
  'invite_code', 'invite_info', 'revoke_invite', 'leave', 'join', 'create_group', 'announce', 'locked',
  'ephemeral', 'join_requests', 'approve', 'reject',
  'profile_pic', 'profile_name', 'profile_status', 'remove_contact', 'add_contact',
  'check_number', 'block', 'unblock',
  'typing', 'online', 'paused', 'read',
  'plugin_list', 'plugin_run', 'plugin_reload',
]

/**
 * Server HTTP loopback untuk menerima aksi dari inti agent (Python).
 * Mengembalikan objek server (untuk ditutup saat shutdown).
 */
export function startActionServer({ port, token, getSock, logger = console }) {
  const server = http.createServer(async (req, res) => {
    const send = (code, obj) => {
      res.writeHead(code, { 'content-type': 'application/json' })
      res.end(JSON.stringify(obj))
    }
    if (req.method === 'GET' && req.url === '/health') {
      return send(200, { ok: true, actions: ACTION_NAMES.length })
    }
    if (req.method !== 'POST' || req.url !== '/action') {
      return send(404, { error: 'not found' })
    }
    if (!token || req.headers['x-kwee-token'] !== token) {
      return send(401, { error: 'token tidak sah' })
    }
    let body = ''
    req.on('data', (c) => { body += c; if (body.length > 1e6) req.destroy() })
    req.on('end', async () => {
      let payload
      try { payload = JSON.parse(body || '{}') } catch { return send(400, { error: 'JSON tidak sah' }) }
      const { action, args } = payload || {}
      if (!action) return send(400, { error: 'action kosong' })
      const out = await runAction(getSock(), action, args || {})
      if (out && out.error) logger.warn?.(`[action] ${action} -> ${out.error}`)
      return send(200, out)
    })
  })
  server.listen(port, '127.0.0.1', () => {
    logger.log?.(`[action] server aksi di 127.0.0.1:${port} (${ACTION_NAMES.length} aksi)`)
  })
  return server
}
