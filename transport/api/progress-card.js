// progress-card.js — modul progres HTML ringkas di dalam satu kartu AI Rich.
//
// Progres berjalan = SATU blok HTML di kartu yang sama, lalu diedit berkala.
// Tidak mengirim indikator progress/thinking native karena dukungan klien WA
// tidak seragam. Tidak ada blok code atau pesan status tambahan.
//
// Rasa "loading" dibangun dari tiga hal yang berubah sedikit demi sedikit di
// setiap edit, bukan dari teks yang berganti-ganti:
//   1. bilah dengan resolusi 1/8 sel (bergerak halus, bukan lompat per sel),
//   2. tepi bilah yang berdenyut + spinner braille yang berputar,
//   3. daftar fase yang tetap (✓ selesai · spinner = sedang jalan · ○ menunggu).

import { renderProgressLiveHtml } from './progress-html.js'

const PERSEN_MAKS_JALAN = 98
const LANGKAH_TAMPIL = 6
const LEBAR_BILAH = 24
const LEBAR_BILAH_KARTU = 16
const BATAS_DETAIL = 60
export const BATAS_JAWABAN = 8000

const TOOL_SHELL = new Set(['bash', 'shell', 'terminal', 'exec', 'run_shell'])
const TOOL_RISET = new Set(['web_search', 'agent_reach', 'agent-reach', 'web_fetch', 'web_see'])
const BLOK_GABUNG = new Set(['text', 'code', 'table', 'divider', 'spacer'])

export function hitungPersen(jumlahLangkah, selesai = false) {
  if (selesai) return 100
  // Fallback konservatif untuk pemanggil tanpa telemetry fase/waktu.
  return Math.min(92, Math.max(0, Number(jumlahLangkah) || 0) * 4)
}

const RENTANG_FASE = {
  skills: { awal: 3, ruang: 8, perDetik: 0.65, perLangkah: 1 },
  think:  { awal: 10, ruang: 18, perDetik: 0.45, perLangkah: 2 },
  tool:   { awal: 28, ruang: 48, perDetik: 0.50, perLangkah: 6 },
  respond:{ awal: 76, ruang: 17, perDetik: 0.70, perLangkah: 2 },
  asset:  { awal: 93, ruang: 5, perDetik: 0.40, perLangkah: 1 },
}

/**
 * Target estimasi berdasarkan fase, waktu fase, dan langkah nyata.
 * Kurvanya mendekati batas fase secara eksponensial: terus merayap, tidak
 * pernah "menempel" di satu angka seperti rumus linear + plafon yang lama.
 * Mengembalikan angka desimal; pembulatan dilakukan saat ditampilkan.
 */
export function targetPersen({ fase = 'think', detikFase = 0, langkahFase = 0 } = {}) {
  const r = RENTANG_FASE[fase] || RENTANG_FASE.tool
  const x = Math.max(0, Number(detikFase) || 0) * r.perDetik + Math.max(0, Number(langkahFase) || 0) * r.perLangkah
  const frac = 1 - Math.exp(-x / (r.ruang * 0.6))
  return Math.min(PERSEN_MAKS_JALAN, r.awal + r.ruang * frac)
}

/**
 * Batasi perubahan per refresh agar progress tidak melompat saat fase berganti.
 * `minNaik` = merayap minimal per refresh supaya bilah tidak diam saat target
 * sudah tercapai (default 0 = perilaku lama). Tidak pernah mundur, tidak pernah
 * melewati PERSEN_MAKS_JALAN.
 */
export function haluskanPersen(sebelumnya, target, maksNaik = 2, minNaik = 0) {
  const now = Math.max(0, Math.min(PERSEN_MAKS_JALAN, Number(sebelumnya) || 0))
  const maks = Math.max(1, Number(maksNaik) || 2)
  let next = Math.max(now, Math.min(PERSEN_MAKS_JALAN, Number(target) || 0))
  next = Math.min(next, now + maks)
  const creep = Math.max(0, Number(minNaik) || 0)
  if (creep && next < now + creep) next = now + creep
  return Math.min(PERSEN_MAKS_JALAN, next)
}

export function bilah(persen, lebar = LEBAR_BILAH) {
  const p = Math.max(0, Math.min(100, Number(persen) || 0))
  const isi = Math.round((lebar * p) / 100)
  return '█'.repeat(isi) + '░'.repeat(lebar - isi)
}

const PARSIAL = ['', '▏', '▎', '▍', '▌', '▋', '▊', '▉']
const DENYUT = ['▓', '▒', '░', '▒']
const PUTAR = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']

export const spinner = (tick = 0) => PUTAR[Math.abs(Math.trunc(Number(tick) || 0)) % PUTAR.length]

/**
 * Bilah halus: resolusi 1/8 sel + tepi yang berdenyut mengikuti `tick`.
 * Setiap edit menggeser isi sedikit dan mengganti denyut, jadi terlihat
 * "mengalir" walau WhatsApp hanya menerima satu edit tiap beberapa detik.
 */
export function bilahHalus(persen, lebar = LEBAR_BILAH_KARTU, tick = 0) {
  const p = Math.max(0, Math.min(100, Number(persen) || 0))
  const unit = Math.round((lebar * 8 * p) / 100)
  const penuh = Math.min(lebar, Math.floor(unit / 8))
  const sisa = unit % 8
  const sel = []
  for (let i = 0; i < lebar; i++) {
    if (i < penuh) sel.push('█')
    else if (i === penuh && sisa > 0 && penuh < lebar) sel.push(PARSIAL[sisa])
    else sel.push('░')
  }
  if (p < 100) {
    const tepi = penuh + (sisa > 0 ? 1 : 0)
    if (tepi < lebar) sel[tepi] = DENYUT[Math.abs(Math.trunc(Number(tick) || 0)) % DENYUT.length]
  }
  return sel.join('')
}

const potong = (s, n) => {
  const t = String(s ?? '').replace(/\s+/g, ' ').trim()
  return t.length > n ? `${t.slice(0, n - 1)}…` : t
}

function barisLangkah(l, selesai, aktif) {
  const ikon = l.ok === false ? '✗' : (selesai || !aktif ? '✓' : '▸')
  const namaAsli = String(l.nama || '')
  const nama = potong(l.nama, 24)
  const detail = potong(l.detail, BATAS_DETAIL)
  if (TOOL_SHELL.has(namaAsli.toLowerCase()) && detail) return `  ${ikon} $ ${detail}`
  if (TOOL_RISET.has(namaAsli.toLowerCase())) return `  ${ikon} 🔎 ${nama}${detail ? `  ${detail}` : ''}`
  return `  ${ikon} ${nama}${detail ? `  ${detail}` : ''}`
}

/** Label fase untuk baris yang sedang berjalan (kalimat penuh). */
const LABEL_FASE = {
  skills: 'Memilih konteks',
  think: 'Menyusun arah',
  tool: 'Menjalankan langkah',
  respond: 'Merangkai jawaban',
  asset: 'Menyiapkan hasil',
  done: 'Selesai',
}

const LABEL_RINGKAS = {
  skills: 'Skills',
  think: 'Analisis',
  tool: 'Eksekusi',
  respond: 'Jawaban',
  asset: 'Hasil',
}

const URUTAN_FASE = ['skills', 'think', 'tool', 'respond', 'asset']

/**
 * Live progress selalu berupa satu dokumen HTML transparan di dalam kartu.
 * Isi event ditumpuk sebagai timeline sehingga tool/shell terlihat berurutan,
 * sedangkan status berubah tanpa mengirim pesan baru.
 */
export function blokBerjalan({ langkah = [], detik = 0, fase = 'think', skillInfo = '', persen, tick = 0, fasePernah } = {}) {
  const mentah = Number.isFinite(persen)
    ? Math.max(0, Math.min(PERSEN_MAKS_JALAN, persen))
    : hitungPersen(langkah.length, false)
  const html = renderProgressLiveHtml({
    judul: 'Kwee · activity',
    langkah: langkah.slice(-8),
    detik,
    fase,
    skillInfo,
    persen: mentah,
    tick,
    fasePernah: URUTAN_FASE.filter((f) => (fasePernah || []).includes(f)),
  })
  return [{
    type: 'html',
    data: {
      html,
      url: 'https://example.com',
      title: 'Kwee · progres',
      tab_header: 'Progress',
      trusted_sources: [],
    },
  }]
}

/**
 * Ratakan balasan menjadi blok kartu. Mengembalikan null bila ada blok yang
 * tidak aman digabung (gambar, video, widget, dsb) — pemanggil lalu menutup
 * kartu progres dan mengirim balasan itu seperti biasa.
 */
const BLOK_ASSET = new Set(['image', 'video', 'audio', 'document', 'file', 'sticker', 'html_file', 'web2ui'])

export function isAssetReply(reply) {
  if (BLOK_ASSET.has(reply?.type)) return true
  const blocks = (reply?.type === 'richResponse' || reply?.type === 'card')
    ? (reply.blocks || reply.richResponse || []) : []
  return Array.isArray(blocks) && blocks.length > 0 && blocks.every(b => BLOK_ASSET.has(b?.type))
}

export function urutkanBalasan(replies) {
  const items = []
  for (const r of Array.isArray(replies) ? replies : []) {
    if (r?.type === 'richResponse' || r?.type === 'card') {
      const blocks = Array.isArray(r.blocks || r.richResponse) ? (r.blocks || r.richResponse) : []
      const teks = blocks.filter(b => !BLOK_ASSET.has(b?.type))
      const aset = blocks.filter(b => BLOK_ASSET.has(b?.type))
      if (teks.length) items.push({ ...r, blocks: teks })
      if (aset.length) items.push({ ...r, blocks: aset })
    } else items.push(r)
  }
  const teks = items.filter(r => !BLOK_ASSET.has(r?.type))
  const aset = items.filter(r => BLOK_ASSET.has(r?.type))
  return [...teks, ...aset]
}

export function ratakanBalasan(replies) {
  const hasil = []
  for (const r of Array.isArray(replies) ? replies : []) {
    const daftar = (r?.type === 'richResponse' || r?.type === 'card') ? (r.blocks || r.richResponse || []) : [r]
    for (const b of daftar) {
      if (!b || !BLOK_GABUNG.has(b.type)) return null
      if (b.type === 'text' && !String(b.text || '').trim()) continue
      hasil.push(b)
    }
  }
  return hasil.length ? hasil : null
}

function panjangTeks(blok) {
  return blok.reduce((n, b) => n + String(b.text || b.code || '').length, 0)
}

/**
 * Blok kartu final: terminal 100% + jawaban, dalam kartu yang sama.
 * `ok:false` berarti jawaban tidak bisa digabung dan harus dikirim terpisah.
 */
export function blokSelesai({ langkah = [], detik = 0, replies } = {}) {
  const ringkasan = { type: 'text', text: `✓ Selesai · ${langkah.length} langkah · ${Number(detik).toFixed(1)}s` }
  const jawaban = ratakanBalasan(replies)
  if (!jawaban || panjangTeks(jawaban) > BATAS_JAWABAN) {
    return { ok: false, blok: [ringkasan, { type: 'text', text: 'Jawaban menyusul di pesan berikut ♡' }] }
  }
  return { ok: true, blok: [ringkasan, ...jawaban] }
}
