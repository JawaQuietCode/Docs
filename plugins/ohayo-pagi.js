// plugins/ohayo-pagi.js — sapaan pagi (Ohayou) + hidetag ke seluruh anggota grup.
//
// Dijalankan oleh cron harian `* 22 * * *` (22:00 UTC = 05:00 WIB) yang
// memanggil plugin ini setiap menit selama jam tersebut. Plugin memilih MENIT
// ACAK 0-59 sekali per hari (disimpan di state), lalu mengirim begitu menit itu
// tiba. Efeknya: sapaan tiap pagi terkirim pada menit yang berbeda antara
// 05:00-05:59 WIB.
//
// Hidetag: ambil daftar peserta grup via ctx.wa.raw().groupMetadata(), lalu
// kirim pesan dengan field `mentions` agar SEMUA anggota ikut ter-tag.
//
// Uji aman: jalankan dengan args { dryRun: true } untuk pratinjau tanpa kirim.

// Kalimat pembuka sapaan — formal, profesional, TANPA emoji. Diletakkan
// setelah header "Selamat pagi." agar tidak terjadi pengulangan.
const GREETINGS = [
  'Semoga hari ini berjalan lancar dan penuh keberkahan.',
  'Mari awali hari dengan semangat dan niat yang baik.',
  'Semoga setiap urusan hari ini dimudahkan.',
  'Jagalah kesehatan dan tetap fokus pada tujuan hari ini.',
  'Semoga hari ini membawa hasil yang terbaik untuk kita semua.',
  'Awali hari dengan tenang, dan kerjakan segala sesuatu dengan sungguh-sungguh.',
  'Semoga aktivitas hari ini berjalan aman dan produktif.',
  'Tetap semangat, dan jangan lupa beristirahat secukupnya.',
  'Semoga hari ini dipenuhi hal baik dan kemudahan.',
  'Mari mulai hari ini dengan sikap yang positif.',
]

function pick(arr) {
  return arr[Math.floor(Math.random() * arr.length)]
}

// Ambil daftar JID peserta grup untuk hidetag. Prioritas `id`, lalu `jid`,
// lalu `lid`; hasil dideduplikasi agar tidak ada mention ganda.
function participantJids(meta) {
  const out = []
  const seen = new Set()
  for (const p of meta?.participants || []) {
    for (const cand of [p?.id, p?.jid, p?.lid]) {
      const jid = String(cand || '').trim()
      if (!jid || seen.has(jid)) continue
      seen.add(jid)
      out.push(jid)
      break
    }
  }
  return out
}

export default {
  name: 'ohayo-pagi',
  description: 'Sapaan selamat pagi (profesional, tanpa emoji) + hidetag ke grup, menit acak antara 05:00-05:59 WIB.',
  commands: ['ohayo'],

  async run(ctx) {
    // Waktu WIB (UTC+7) — server berjalan di UTC.
    const wib = new Date(Date.now() + 7 * 3600 * 1000)
    const day = `${wib.getUTCFullYear()}-${String(wib.getUTCMonth() + 1).padStart(2, '0')}-${String(wib.getUTCDate()).padStart(2, '0')}`
    const minute = wib.getUTCMinutes()

    const to = ctx.args?.to || ctx.msg?.chatId
    const dryRun = Boolean(ctx.args?.dryRun)

    // Pilih menit acak sekali per hari.
    if (ctx.state.get('day', null) !== day) {
      const target = Math.floor(Math.random() * 60) // 0..59
      ctx.state.set('day', day)
      ctx.state.set('targetMinute', target)
      ctx.log(`hari baru ${day} — menit acak = ${String(target).padStart(2, '0')}`)
    }
    const target = ctx.state.get('targetMinute', 0)
    const sentDay = ctx.state.get('sentDay', null)

    // Dipicu manual lewat perintah "!ohayo" (bukan dari cron) → kirim langsung,
    // tanpa menunggu menit acak.
    const manual = !ctx.msg?.scheduled

    const greeting = pick(GREETINGS)
    const shouldSend = dryRun || manual || (sentDay !== day && minute >= target)
    if (!shouldSend) {
      return { ok: true, skipped: true, day, minute, target }
    }

    // Kumpulkan peserta grup untuk hidetag.
    let mentions = []
    try {
      const sock = ctx.wa?.raw ? ctx.wa.raw() : null
      if (sock && to && String(to).endsWith('@g.us')) {
        const md = await sock.groupMetadata(to)
        mentions = participantJids(md)
      }
    } catch (e) {
      ctx.log(`gagal ambil peserta: ${e.message}`)
    }

    const text = `Selamat pagi.\n\n${greeting}`

    if (dryRun) {
      return { ok: true, preview: text, to, mentions: mentions.length, day, minute, target }
    }

    const payload = mentions.length ? { text, mentions } : { text }
    await ctx.wa.send(to, payload)
    // Hanya tandai "sudah terkirim hari ini" untuk pengiriman OTOMATIS (cron),
    // supaya pemanggilan manual "!ohayo" tidak membatalkan jadwal harian.
    if (!manual) ctx.state.set('sentDay', day)
    ctx.log(`terkirim ke ${to} (${mentions.length} mention)`)
    // Kembalikan null agar router tidak menambah balasan teks saat dipicu
    // lewat perintah "!ohayo" (pesan sudah terkirim di atas).
    return null
  },
}
