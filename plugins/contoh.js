// plugins/contoh.js — contoh plugin Kwee-agentic.
//
// Plugin adalah cara Kwee "membuat bot kecil di dalam bot". File ini contoh;
// Kwee bisa menulis plugin baru sendiri lewat tool `plugin_write`.
//
// Cara memakai:
//   - Perintah cepat: kirim "!contoh" (lihat `commands`)
//   - Atau minta AI: "jalankan plugin contoh"
//   - Atau jadwalkan: cron kind=plugin args={"name":"contoh"}
//
// ctx yang tersedia:
//   ctx.wa.send(to, content)        — kirim pesan (text/objek WA)
//   ctx.wa.sendRich(to, blocks)     — kirim kartu AI Rich (array blok)
//   ctx.airich.*                    — pembangun kartu (buildRichCard, dll.)
//   ctx.msg                         — info pesan pemicu (chatId, senderId, isGroup, text)
//   ctx.args                        — argumen (mis. "!contoh halo dunia" → {text:'halo dunia', argv:[...]})
//   ctx.log(...)                    — catat ke log
//   ctx.state.get/set/all/clear     — penyimpanan key-value persisten per-plugin
export default {
  name: 'contoh',
  description: 'Contoh plugin: hitung berapa kali dipanggil + kirim kartu',
  commands: ['contoh'],

  async run(ctx) {
    const n = (ctx.state.get('hit', 0) || 0) + 1
    ctx.state.set('hit', n)
    ctx.log(`dipanggil ke-${n}`)

    const to = ctx.msg.chatId
    // Kirim kartu AI Rich (teks + tabel) — sama seperti balasan AI biasa.
    await ctx.wa.sendRich(to, [
      { type: 'text', text: `**Plugin contoh** dipanggil ke-${n} kali.` },
      {
        type: 'table',
        title: 'Info',
        rows: [
          ['Kunci', 'Nilai'],
          ['hit', String(n)],
          ['args', ctx.args.text || '(kosong)'],
        ],
      },
      { type: 'suggest', prompts: ['Panggil lagi', 'Lihat status'] },
    ])
    return `ok (hit=${n})`
  },
}
