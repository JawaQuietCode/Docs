// plugins/delete.js — hapus pesan bot (dan pesan yang di-reply) lewat WhatsApp.
//
// MASALAH YANG DIPECAHKAN
//   WhatsApp butuh `message_id` untuk menghapus pesan, dan id itu hilang begitu
//   `sendMessage` selesai. Dulu bot tidak menyimpan id pesannya, jadi pemilik
//   harus menghapus manual. Kini `transport/sentlog.js` mencatat setiap pesan
//   KELUAR (persisten di `data/sent.log`), dan plugin ini memakainya untuk
//   menghapus balasan bot yang sudah terkirim.
//
// PEMAKAIAN
//   !delete              -> hapus 1 balasan bot terakhir di chat ini
//   !delete 2            -> hapus 2 balasan bot terakhir
//   !delete 5            -> hapus 5 balasan bot terakhir (maks 50)
//   (reply) !delete      -> hapus PESAN YANG DI-REPLY itu (cara paling tepat,
//                           karena id-nya ikut terkirim dalam kutipan)
//   !delete list         -> tampilkan 10 id balasan terakhir (tanpa menghapus)
//
// CATATAN
//   - Hanya bisa menghapus pesan KELUAR (fromMe=true). WhatsApp menolak
//     menghapus pesan orang lain.
//   - "Hapus untuk semua orang" hanya berhasil dalam batas waktu WhatsApp
//     (± 2 hari 12 jam untuk chat biasa, ± 1 jam untuk grup). Di luar itu,
//     WhatsApp akan menolak dan plugin melaporkannya.
//   - Semua operasi best-effort: kegagalan satu id tidak menghentikan yang lain.

import { recentSent } from '../transport/sentlog.js'

const MAX_DELETE = 50

// Kirim satu pesan teks singkat.
function say(ctx, to, text) {
  return ctx.wa.send(to, { text })
}

// Hapus satu pesan berdasarkan key. Mengembalikan { ok, error }.
async function removeOne(sock, chatId, { id, fromMe = true }) {
  try {
    await sock.sendMessage(chatId, { delete: { remoteJid: chatId, id, fromMe } })
    return { ok: true, id }
  } catch (e) {
    return { ok: false, id, error: e?.message || String(e) }
  }
}

export default {
  name: 'delete',
  description: 'Hapus pesan bot yang sudah terkirim (via id tersimpan) atau pesan yang kamu reply. Perintah: !delete [jumlah|list].',
  commands: ['delete', 'del', 'hapus'],

  async run(ctx) {
    const to = ctx.msg?.chatId
    const sock = ctx.wa?.raw ? ctx.wa.raw() : null
    if (!sock || !to) return { ok: false, error: 'socket/tujuan tidak tersedia' }

    const argv = (ctx.args?.argv || []).map((s) => String(s).toLowerCase())
    const raw = ctx.args?.text || ''
    const quoted = ctx.msg?.quoted || null

    // --- Mode daftar: !delete list ---
    if (argv[0] === 'list' || argv[0] === 'daftar') {
      const items = recentSent(to, 10)
      if (!items.length) {
        await say(ctx, to, '📭 Belum ada catatan balasan bot untuk dihapus.')
        return { ok: true, listed: 0 }
      }
      const lines = items.map((it, i) => {
        const t = new Date(it.ts).toLocaleTimeString('id-ID')
        return `${i + 1}. ${t} — ${String(it.id).slice(0, 12)}… (${it.kind || 'text'})`
      })
      await say(ctx, to, `📝 Balasan bot terakhir (${items.length}):\n${lines.join('\n')}\n\nHapus dengan: !delete <jumlah>`)
      return { ok: true, listed: items.length }
    }

    // --- Mode reply: hapus pesan yang di-reply (paling akurat) ---
    if (quoted?.id) {
      if (!quoted.fromMe) {
        await say(ctx, to, '⚠️ Aku hanya bisa menghapus pesanku sendiri, bukan pesan orang lain.')
        return { ok: false, error: 'bukan pesan bot' }
      }
      const r = await removeOne(sock, to, quoted)
      await say(ctx, to, r.ok ? '🗑️ Pesan yang kamu reply sudah dihapus.' : `⚠️ Gagal menghapus: ${r.error}`)
      ctx.log(`hapus(reply) ${r.ok ? 'ok' : 'gagal'} id=${quoted.id}`)
      return { ok: r.ok, mode: 'reply', id: quoted.id, error: r.error }
    }

    // --- Mode jumlah: !delete N (default 1) ---
    const n = Math.max(1, Math.min(MAX_DELETE, parseInt(raw, 10) || 1))
    const targets = recentSent(to, n)
    if (!targets.length) {
      await say(ctx, to, '📭 Tidak ada balasan bot yang tercatat untuk dihapus.\n\nTip: balas (reply) pesan yang mau dihapus lalu ketik `!delete`, atau lihat daftar dengan `!delete list`.')
      return { ok: true, deleted: 0 }
    }

    const results = []
    for (const t of targets) {
      const r = await removeOne(sock, to, { id: t.id, fromMe: true })
      results.push(r)
      // Jeda kecil agar tidak dianggap spam oleh WhatsApp.
      await new Promise((res) => setTimeout(res, 250))
    }
    const okCount = results.filter((r) => r.ok).length
    const failCount = results.length - okCount

    const note = failCount
      ? `\n\n⚠️ ${failCount} gagal (kemungkinan sudah lewat batas waktu hapus WhatsApp ± 2 hari).`
      : ''
    await say(ctx, to, `🗑️ ${okCount} pesan bot dihapus.${note}`)
    ctx.log(`hapus ${okCount}/${results.length} pesan di ${to}`)

    return { ok: failCount === 0, deleted: okCount, failed: failCount, ids: results.map((r) => r.id) }
  },
}
