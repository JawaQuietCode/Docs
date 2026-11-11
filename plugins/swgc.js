// swgc — SWGC (status di dalam grup): list grup, kirim ke grup target, silent.
//
// Perintah:
//   !swgc list                          → daftar grup bot + ID
//   !swgc <grup|ID> <path> [caption]    → kirim file ke grup
// Tanpa prefix (di grup/chat, sambil kirim atau reply media):
//   swgc disini [caption]               → post ke grup ini
//   swgc <nama grup|ID> [caption]       → post ke grup lain
//
// Caption HANYA dipakai bila diisi; kalau tidak, KOSONG (tanpa karangan).
// Silent: tidak mention siapa pun, tidak reply pesan.
import fs from 'fs'
import path from 'path'

const VIDEO_EXT = new Set(['.mp4', '.mov', '.mkv', '.webm'])
const IMAGE_EXT = new Set(['.jpg', '.jpeg', '.png', '.webp'])

function mimeOf(ext) {
  const e = String(ext || '').toLowerCase()
  if (e === '.mp4') return 'video/mp4'
  if (e === '.mov') return 'video/quicktime'
  if (e === '.mkv') return 'video/x-matroska'
  if (e === '.webm') return 'video/webm'
  if (e === '.png') return 'image/png'
  if (e === '.webp') return 'image/webp'
  if (e === '.jpg' || e === '.jpeg') return 'image/jpeg'
  return 'application/octet-stream'
}

function human(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

// Cari grup dari ID penuh atau potongan nama (case-insensitive).
// `disini`/`here`/`sini` → grup percakapan sekarang.
async function resolveGroup(sock, input, chatId) {
  const raw = String(input || '').trim()
  const groups = (await sock.groupFetchAllParticipating?.()) || {}
  if (!raw || /^(disini|sini|here)$/i.test(raw)) {
    if (!chatId.endsWith('@g.us')) throw new Error('Bukan grup. Sebutkan nama/ID grup tujuan.')
    return { jid: chatId, subject: groups[chatId]?.subject || chatId }
  }
  if (groups[raw]) return { jid: raw, subject: groups[raw].subject || raw }
  const want = raw.toLowerCase()
  const exact = Object.entries(groups).find(([, m]) => String(m.subject || '').toLowerCase() === want)
  const partial = exact || Object.entries(groups).find(([, m]) => String(m.subject || '').toLowerCase().includes(want))
  if (!partial) {
    const names = Object.values(groups).map((m) => m.subject).filter(Boolean).slice(0, 20).join(', ')
    throw new Error(`Grup "${raw}" tidak ditemukan. Coba: ${names || '(belum ikut grup)'}`)
  }
  return { jid: partial[0], subject: partial[1].subject || partial[0] }
}

function absOf(rel) {
  const root = path.resolve(process.cwd())
  const abs = path.resolve(root, rel)
  if (abs !== root && !abs.startsWith(root + path.sep)) throw new Error('path di luar folder proyek')
  if (!fs.existsSync(abs) || !fs.statSync(abs).isFile()) throw new Error(`file tidak ditemukan: ${rel}`)
  return abs
}

async function post(ctx, to, abs, caption) {
  const ext = path.extname(abs)
  const bytes = fs.statSync(abs).size
  const base = { groupStatus: true, mimetype: mimeOf(ext) }
  const content = VIDEO_EXT.has(ext.toLowerCase())
    ? { video: { stream: fs.createReadStream(abs) }, ...base }
    : IMAGE_EXT.has(ext.toLowerCase())
      ? { image: { stream: fs.createReadStream(abs) }, ...base }
      : { document: { stream: fs.createReadStream(abs) }, fileName: path.basename(abs), ...base }
  if (caption) content.caption = caption
  const sent = await ctx.wa.send(to, content)
  return { id: sent?.key?.id || null, file: path.basename(abs), bytes }
}

export default {
  name: 'swgc',
  description: 'SWGC: daftar grup + kirim status grup ke grup ini/nama/ID, silent tanpa mention',
  commands: ['swgc'],
  async run(ctx) {
    const sock = ctx.wa?.raw?.()
    const argv = (ctx.args?.argv || []).filter((x) => x != null && x !== '')
    const chatId = String(ctx.msg?.chatId || ctx.args?.chat_id || '')
    const media = String(ctx.args?.media || ctx.args?.path || '').trim()

    if (argv[0] === 'list') {
      const groups = (await sock.groupFetchAllParticipating?.()) || {}
      const entries = Object.entries(groups)
      if (!entries.length) return 'Bot belum ikut grup mana pun.'
      const lines = [`📋 **Grup bot (${entries.length})**`, '']
      entries.forEach(([jid, m], i) => {
        lines.push(`${i + 1}. **${m.subject || '(tanpa nama)'}**`)
        lines.push(`   \`${jid}\``)
        lines.push(`   ${m.participants?.length || 0} anggota`)
      })
      return lines.join('\n')
    }

    // Bentuk perintah: !swgc <grup> <path> [caption]  |  swgc <grup> [caption] (dengan media)
    let target = 'disini'
    let rel = ''
    let caption = ''

    if (media) {
      target = argv[0] || 'disini'
      caption = argv.slice(1).join(' ')
    } else {
      target = argv[0] || ''
      rel = argv[1] || ''
      caption = argv.slice(2).join(' ')
      if (!target || !rel) {
        return [
          '**SWGC — status di dalam grup**',
          '',
          '`!swgc list` — daftar grup + ID',
          '`!swgc <grup|ID> <path> [caption]` — kirim file',
          '`swgc disini [caption]` — reply/kirim media lalu ketik ini',
          '`swgc <grup|ID> [caption]` — kirim ke grup lain',
          '',
          '_Video wajib H.264/AAC. Tanpa caption bila tidak diisi._',
        ].join('\n')
      }
    }

    const { jid, subject } = await resolveGroup(sock, target, chatId)
    const abs = media ? absOf(media) : absOf(rel)
    const out = await post(ctx, jid, abs, caption)
    ctx.log(`swgc -> ${jid} (${out.file}, ${human(out.bytes)})`)
    return `✅ SWGC → **${subject}**\n${out.file} (${human(out.bytes)})${out.id ? `\nid: ${out.id}` : ''}`
  },
}
