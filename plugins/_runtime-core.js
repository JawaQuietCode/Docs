// plugins/_runtime-core.js — INTI BERSAMA untuk `test` (pesan biasa) dan
// `!ping` (perintah cepat). Keduanya WAJIB mengeluarkan bentuk yang SAMA:
// lembar spesifikasi runtime + teks markdown + CTA.
//
// Kenapa modul terpisah: dua titik masuk (router.js untuk `test`, commands.js
// untuk `!ping`) harus memakai SATU sumber kebenaran. Kalau logikanya disalin,
// keduanya menyimpang pelan-pelan — dan pemilik melihat dua rasa berbeda.
//
// Berkas ini diawali `_` jadi TIDAK dimuat sebagai plugin (lihat listPluginFiles).
import fs from 'node:fs'
import path from 'node:path'
import os from 'node:os'

const PAGE = 'data/html-pages/runtime.html'

// Uptime HARUS dari proses Node induk, bukan dari modul ini: plugin di-`import`
// ulang setiap dipanggil (cache-bust di plugin-host), jadi `Date.now() - STARTED`
// selalu 0 detik. `process.uptime()` tidak ikut ter-reset.
export function uptimeMs() {
  try {
    return process.uptime() * 1000
  } catch {
    return 0
  }
}

// ---------- util ----------

export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ))
}

// Nilai + satuan dipisah supaya satuan bisa dikecilkan: angka tetap satu-satunya
// elemen besar di kolom kanan.
// Satuan KAPITAL (SI): `139 MB`, bukan `139 mb`. Satu gaya untuk semua baris —
// campuran `mb` kecil dan `%` berspasi adalah cara tercepat membuat kartu
// terbaca hasil generator, bukan hasil desain.
function fmtBytes(n) {
  const b = Number(n) || 0
  if (b >= 1024 ** 3) return [+(b / 1024 ** 3).toFixed(1), 'GB']
  if (b >= 1024 ** 2) return [Math.round(b / 1024 ** 2), 'MB']
  if (b >= 1024) return [Math.round(b / 1024), 'KB']
  return [b, 'B']
}

// Satuan ditulis utuh: `1j 2m` hanya terbaca kalau pembaca menebak jam/menit.
function fmtUptime(ms) {
  const s = Math.max(0, Math.floor(ms / 1000))
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  if (d) return `${d} hari ${h} jam`
  if (h) return `${h} jam ${m} mnt`
  if (m) return `${m} mnt ${sec} dtk`
  return `${sec} dtk`
}

// Baca /proc tanpa paket tambahan. Nilai yang tak terbaca → null, supaya kartu
// bisa menampilkan "—" alih-alih angka palsu.
export function readProc() {
  const out = { rss: null, heapUsed: null, load1: null, cpuCount: os.cpus().length, sysFree: null, sysTotal: null }
  try {
    const st = fs.readFileSync('/proc/self/status', 'utf8')
    const rss = /VmRSS:\s+(\d+) kB/.exec(st)
    if (rss) out.rss = Number(rss[1]) * 1024
  } catch { /* bukan Linux / tak terbaca */ }
  try {
    const m = process.memoryUsage()
    out.heapUsed = m.heapUsed
    if (!out.rss) out.rss = m.rss
  } catch { /* abaikan */ }
  try {
    out.load1 = os.loadavg()[0]
  } catch { /* abaikan */ }
  try {
    const mi = fs.readFileSync('/proc/meminfo', 'utf8')
    const tot = /MemTotal:\s+(\d+) kB/.exec(mi)
    const avail = /MemAvailable:\s+(\d+) kB/.exec(mi)
    if (tot) out.sysTotal = Number(tot[1]) * 1024
    if (avail) out.sysFree = Number(avail[1]) * 1024
  } catch { /* abaikan */ }
  return out
}

// ---------- halaman HTML ----------

// Bersihkan komentar CSS dari fragmen sebelum dikirim: catatan internal tak
// perlu ikut ke WhatsApp, dan beberapa di antaranya memuat literal `<html>`
// yang membuat penerima mengira ini dokumen penuh.
function stripCssComments(html) {
  return html.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\n{3,}/g, '\n\n').trim()
}

function readPage() {
  const cwd = process.cwd()
  const candidates = [path.resolve(cwd, PAGE), path.resolve(cwd, '..', PAGE)]
  for (const p of candidates) {
    try {
      if (!fs.existsSync(p)) continue
      // Komentar HTML dibuang: catatan internal tak perlu ikut ke WhatsApp.
      return stripCssComments(fs.readFileSync(p, 'utf8').replace(/<!--[\s\S]*?-->/g, ''))
    } catch { /* lanjut */ }
  }
  return ''
}

// Baris definisi: label kiri (kecil, kapital), nilai + satuan kanan.
// `--i` menunda goresan margin supaya menyala saat titik lewat.
function row({ name, value, unit = '', status = 'ok', i = 0 }) {
  const num = value == null ? '—' : String(value)
  return `<div class="rt-node" data-s="${esc(status)}" style="--i:${i}">`
    + `<span class="rt-port" aria-hidden="true"></span>`
    + `<span class="rt-name">${esc(name)}</span>`
    + `<span class="rt-val"><b>${esc(num)}</b>${unit ? `<span>${esc(unit)}</span>` : ''}</span>`
    + '</div>'
}

/** Susun fragmen HTML. Semua nilai dinamis di-escape. */
export function buildRuntimeHtml({ kind = 'ping', modules = 0, uptimeMs = 0, proc } = {}) {
  const tpl = readPage()
  const p = proc || readProc()

  const [ramNum, ramUnit] = p.rss ? fmtBytes(p.rss) : [null, 'MB']
  const freePct = (p.sysTotal && p.sysFree) ? Math.round((p.sysFree / p.sysTotal) * 100) : null

  // Label sengaja pendek & seragam — satu kata atau dua, tanpa menyelipkan
  // dimensi lain (`beban · 6 core` memuat dua informasi di satu label, dan
  // memecah pola baris). Angka core pindah ke kaki kartu.
  // Status port: `ok` terisi, `none` kosong (tak terbaca), `warn` warna peringatan.
  const rail = [
    { i: 0, name: 'rss', value: ramNum, unit: ramUnit, status: p.rss ? 'ok' : 'none' },
    { i: 1, name: 'memori bebas', value: freePct, unit: '%', status: freePct == null ? 'none' : 'ok' },
    {
      i: 2, name: 'beban 1 menit',
      value: p.load1 != null ? p.load1.toFixed(2) : null,
      status: p.load1 == null ? 'none' : (p.load1 > p.cpuCount ? 'warn' : 'ok'),
    },
    { i: 3, name: 'plugin', value: modules, status: modules > 0 ? 'ok' : 'warn' },
  ].map(row).join('')

  if (!tpl) {
    // Halaman hilang → tetap kirim sesuatu yang jujur, bukan kartu kosong.
    return '<div style="font:13px/1.5 ui-sans-serif,system-ui,sans-serif;padding:10px 12px">'
      + `<p><strong>runtime</strong> — halaman ${esc(PAGE)} tidak ditemukan.</p>`
      + `<p>uptime ${esc(fmtUptime(uptimeMs))} · memori ${esc(ramNum ?? '—')} ${esc(ramUnit)} · plugin ${esc(modules)}</p>`
      + '</div>'
  }

  // Kicker TIDAK mengulang judul: dulu `status proses` + `Runtime proses`
  // mengatakan hal yang sama dua kali. Sekarang kicker menyebut apa yang
  // angkanya wakili — umur proses.
  const kicker = 'uptime'
  const title = kind === 'test' ? 'Kata kunci <em>test</em>' : 'Proses bot'

  // Versi Node BUKAN metrik — ia tak punya satuan dan tak bisa dibandingkan
  // dengan baris lain, jadi ia tidak boleh duduk di kolom angka. Ia turun ke
  // kaki kartu sebagai keterangan.
  return tpl
    .replace(/\{\{KICKER\}\}/g, esc(kicker))
    .replace(/\{\{UPTIME\}\}/g, esc(fmtUptime(uptimeMs)))
    .replace(/\{\{TITLE\}\}/g, title)
    .replace(/\{\{RAIL\}\}/g, rail)
    .replace(/\{\{MODULES\}\}/g, esc(modules))
    .replace(/\{\{CORES\}\}/g, esc(p.cpuCount))
    .replace(/\{\{FOOT_LEFT\}\}/g, esc(`node ${process.version.replace(/^v/, '')}`))
    .replace(/\{\{FOOT_RIGHT\}\}/g, esc(`${p.cpuCount} cores`))
}

// ---------- kartu lengkap: HTML + markdown + CTA ----------

/**
 * Array blok siap kirim. Urutan: teks → html → CTA.
 *
 * Teks markdown adalah PENJELASAN, kartu adalah ANGKA. Dua hal berbeda:
 * tak satu pun angka dari kartu ditulis ulang di teks, dan tak satu pun
 * kalimat kartu muncul di teks. Aturan itu yang mencegah balasan terasa
 * dobel saat dibaca di HP.
 */
export async function runtimeBlocks({ kind = 'ping', modules = 0, uptime = uptimeMs() } = {}) {
  const proc = readProc()

  // Teks ini BUKAN ringkasan kartu. Kartu memuat angka; teks memuat hal yang
  // tak bisa ditampilkan kartu: batas, jebakan tafsir, dan apa yang sengaja
  // tidak diukur. Nol pengulangan label & nol pengulangan angka.
  const text = kind === 'test'
    ? [
      '♡ I answered without touching the AI.',
      'This message is checked first, so replies still arrive even if every provider is down.',
      '· in groups, mention me first',
      '· in private chats, just type',
    ].join('\n')
    : [
      `♡ alive · uptime ${fmtUptime(uptime)}`,
      `· ${proc.cpuCount || '?'} cores · load ${proc.load || '-'}`,
      'The card above holds the numbers; a blank means /proc is unreadable here.',
    ].join('\n')

  const html = buildRuntimeHtml({ kind, modules, uptimeMs: uptime, proc })

  return [
    { type: 'text', text },
    {
      type: 'html',
      data: {
        html,
        url: 'https://example.com',
        title: 'Machine state',
        tab_header: 'Machine state',
        trusted_sources: [],
      },
    },
    {
      type: 'quickreply',
      items: [
        { label: 'Ask again', reply_text: '!ping' },
        { label: 'Open menu', reply_text: '!help' },
      ],
    },
  ]
}

export { fmtBytes, fmtUptime }
