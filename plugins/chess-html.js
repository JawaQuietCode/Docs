// plugins/chess-html.js — Catur sebagai kartu HTML AI Rich (GenAI*).
//
// BEDA dengan perintah `!run` (transport/commands.js → api/rich.js):
//   `!run` memakai richHtmlMessage(): sections[] hanya berisi placeholder
//   GenAIBotProgressStatusPrimitive ("Click to Open") dan HTML-nya ditaruh di
//   **embedded_screens** → dibuka jadi LAYAR PENUH.
//
//   Plugin ini memakai blok { type: 'html' } (airich.js htmlSection):
//   GenAIaeacdsnwHtmlPrimitive berada LANGSUNG di sections[] → WhatsApp
//   merendernya sebagai widget ringkas di dalam kartu, bukan layar penuh.
//   Halamannya juga sengaja TANPA `100vh` / `position:fixed`, jadi tingginya
//   mengikuti ISI.
//
// Halaman HTML: data/html-pages/chess.html
//   (di data/, bukan root src/, supaya tidak bentrok dengan chess.html milik bot lain)
//
// Pemakaian:
//   !chess        → kirim widget catur
//   !catur        → alias
//   plugin_run { name: 'chess-html', args: { path, title, tab_header, url } }
//
// Struktur AI Rich lengkap (proofs, bubble id, node <biz>) sudah diurus
// relayCard() di transport/api/rich.js — plugin ini tidak menyentuh protokol.

import fs from 'node:fs'
import path from 'node:path'

const DEFAULT_PAGE = 'data/html-pages/chess.html'

// Cari halaman HTML: argumen → default, dengan beberapa kandidat cwd supaya
// tetap ketemu entah proses dijalankan dari root proyek atau dari parentnya.
function readPage(ctx) {
  const wanted = String((ctx.args && ctx.args.path) || DEFAULT_PAGE).trim()
  const cwd = process.cwd()
  const candidates = [
    path.resolve(cwd, wanted),
    path.resolve(cwd, '..', wanted),
    path.resolve(cwd, DEFAULT_PAGE),
    path.resolve(cwd, '..', DEFAULT_PAGE),
  ]
  for (const p of candidates) {
    try {
      if (!fs.existsSync(p)) continue
      const html = fs.readFileSync(p, 'utf8').trim()
      if (!html) continue
      if (!/id=["']board["']/i.test(html)) continue
      return { html: html, file: p }
    } catch (e) { /* coba kandidat berikutnya */ }
  }
  throw new Error('Halaman catur tidak ditemukan (dicari: ' + wanted + ')')
}

export default {
  name: 'chess-html',
  description: 'Catur interaktif sebagai kartu HTML AI Rich (widget ringkas, bukan layar penuh seperti !run)',
  commands: ['chess', 'catur'],

  async run(ctx) {
    const to = (ctx.msg && ctx.msg.chatId) || (ctx.args && ctx.args.to)
    if (!to) return { ok: false, error: 'chat tujuan kosong' }

    const page = readPage(ctx)
    ctx.log('halaman: ' + page.file + ' (' + page.html.length + ' byte)')

    // Blok `html` → GenAIaeacdsnwHtmlPrimitive langsung di sections[].
    // Inilah kunci "widget": HTML hidup sebagai primitif di dalam kartu.
    const block = {
      type: 'html',
      data: {
        html: page.html,
        url: String((ctx.args && ctx.args.url) || 'https://example.com'),
        title: String((ctx.args && ctx.args.title) || 'Chess Mini'),
        tab_header: String((ctx.args && ctx.args.tab_header) || 'Catur'),
        trusted_sources: [],
      },
    }

    try {
      await ctx.wa.sendRich(to, [block])
      return { ok: true, to: to, file: page.file, bytes: page.html.length, via: 'html-card' }
    } catch (err) {
      // Jaring pengaman: kalau kartu gagal, pemilik tetap dapat kabar.
      const msg = (err && err.message) || String(err)
      ctx.log('kartu gagal: ' + msg)
      await ctx.wa.send(to, { text: 'Gagal mengirim widget catur: ' + msg })
      return { ok: false, error: msg }
    }
  },
}
