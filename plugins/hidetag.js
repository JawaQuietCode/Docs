// plugins/hidetag.js — group mentions and typed PN/LID diagnostics.
// !hidetag / !tagall require the caller to be a group admin. !mentionjid is
// owner-only through the router and prints full typed participant identities.

function fullJid(value) {
  const jid = String(value || '').trim()
  return jid.includes('@') ? jid : ''
}

function normalizeDeviceJid(jid) {
  const [user, server] = String(jid || '').split('@')
  return user && server ? `${user.split(':')[0]}@${server}` : ''
}

function participantRecord(participant) {
  const candidates = [participant?.id, participant?.jid, participant?.lid, participant?.phoneNumber]
    .map(fullJid).filter(Boolean)
  const unique = [...new Set(candidates)]
  return {
    mention: unique[0] || '',
    identities: unique,
    pn: unique.find((jid) => jid.endsWith('@s.whatsapp.net') || jid.endsWith('@hosted')) || '',
    lid: unique.find((jid) => jid.endsWith('@lid') || jid.endsWith('@hosted.lid')) || '',
    admin: Boolean(participant?.admin || participant?.isAdmin || participant?.isSuperAdmin),
  }
}

function participantRecords(meta) {
  const seen = new Set()
  const out = []
  for (const participant of meta?.participants || []) {
    const record = participantRecord(participant)
    if (!record.mention || seen.has(record.mention)) continue
    seen.add(record.mention)
    out.push(record)
  }
  return out
}

function senderIsAdmin(meta, senderIds) {
  const senderSet = new Set((senderIds || []).map(normalizeDeviceJid).filter(Boolean))
  if (!senderSet.size) return false
  return (meta?.participants || []).some((participant) => {
    const record = participantRecord(participant)
    return record.admin && record.identities.some((jid) => senderSet.has(normalizeDeviceJid(jid)))
  })
}

export default {
  name: 'hidetag',
  description: 'Tag semua anggota grup dengan pemeriksaan admin; !mentionjid menampilkan PN/LID bertipe lengkap.',
  commands: ['hidetag', 'tagall', 'mentionjid'],

  async run(ctx) {
    const to = ctx.msg?.chatId || ctx.args?.to || ''
    const isGroup = ctx.msg?.isGroup ?? String(to).endsWith('@g.us')
    if (!isGroup) return { ok: false, error: 'Perintah ini hanya bisa dipakai di dalam grup.' }

    const sock = ctx.wa?.raw ? ctx.wa.raw() : null
    if (!sock) return { ok: false, error: 'Socket WhatsApp tidak tersedia.' }

    let records = []
    let metadata
    try {
      metadata = await sock.groupMetadata(to)
      records = participantRecords(metadata)
    } catch (e) {
      return { ok: false, error: `Gagal mengambil data grup: ${e.message}` }
    }
    if (!records.length) return { ok: false, error: 'Tidak ada peserta dengan JID valid untuk diproses.' }

    const raw = String(ctx.msg?.text || '').trim()
    const sub = (raw.replace(/^[!.]/, '').split(/\s+/)[0] || 'hidetag').toLowerCase()

    if (sub === 'mentionjid') {
      const list = records.map((r, i) => {
        const aliases = [r.pn && `PN ${r.pn}`, r.lid && `LID ${r.lid}`].filter(Boolean).join(' · ')
        return `${i + 1}. ${r.mention}${aliases ? ` · ${aliases}` : ''}`
      }).join('\n')
      return `Identitas peserta grup (${records.length}; JID bertipe lengkap):\n${list}`
    }

    if (!senderIsAdmin(metadata, ctx.msg?.senderIds)) {
      return { ok: false, error: 'Fitur mention massal hanya boleh dijalankan admin grup.' }
    }

    const argv = Array.isArray(ctx.args?.argv) ? ctx.args.argv : []
    let text = argv.join(' ').trim() || String(ctx.args?.text || '').trim()
    text = text.replace(/^(hidetag|tagall)\s*/i, '').trim()
    if (!text && ctx.msg?.quoted?.text) text = String(ctx.msg.quoted.text)
    if (!text) text = 'Perhatian untuk semua anggota.'

    // Gunakan JID peserta persis seperti yang diberikan metadata grup; jangan
    // ubah PN↔LID berdasarkan angka lokal yang bisa bertabrakan.
    await ctx.wa.send(to, { text, mentions: records.map((r) => r.mention) })
    return null
  },
}
