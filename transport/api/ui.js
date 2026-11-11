// ui.js — perakit kartu "immersive" berbasis primitive GenAI (__typename).
//
// Aturan modul ini:
//   1. RINGKAS     — tiap blok teks maksimal 4 baris; kelebihan "…+n lines".
//   2. BERSIH      — karakter kontrol & zero-width dibuang, spasi dirapatkan.
//                    Aksara Tionghoa DIPERTAHANKAN: dipakai sebagai aksen UI.
//   3. TIDAK RAPUH — tiap perakit dibungkus try/catch, selalu blok sah.
//   4. EDITORIAL  — hierarki jelas, aksen seperlunya, sumber mudah ditelusuri.
//   5. CTA NYATA   — tombol `quickreply` (diketuk → mengirim teks) dan
//                    `footer` OPEN_URL (diketuk → membuka tautan). Bukan pill
//                    hiasan yang mati di chat biasa.
import { BRAND, fem, sectionTitle, menuLine, palette, ORN } from './style.js'
import { simpan as simpanPilihan } from './pilihan.js'

// ------------------------------------------------------------------ pembersih
const KONTROL = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\u200b-\u200f\u2060\ufeff]/g

export const BATAS_BARIS = 4
export const BATAS_HURUF = 600

/** Bersihkan teks: buang karakter kontrol/zero-width, rapikan spasi.
 *  Aksara Tionghoa sengaja DIBIARKAN — dipakai sebagai aksen di UI. */
export function bersih(nilai) {
  if (nilai === null || nilai === undefined) return ''
  let t = String(nilai)
  t = t.replace(KONTROL, ' ')
  t = t.replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n')
  return t.trim()
}

/**
 * Rapikan teks agar muat di layar ponsel.
 * @param {*} nilai
 * @param {{maxLines?: number, maxChars?: number}} opsi
 */
export function rapikan(nilai, { maxLines = BATAS_BARIS, maxChars = BATAS_HURUF } = {}) {
  const t = bersih(nilai)
  if (!t) return ''

  const potongBaris = (baris) => {
    if (baris.length <= maxChars) return baris
    const potong = baris.slice(0, maxChars)
    const spasi = potong.lastIndexOf(' ')
    return (spasi > maxChars * 0.6 ? potong.slice(0, spasi) : potong).trimEnd() + '…'
  }

  const baris = t.split('\n').map((b) => potongBaris(b.trimEnd()))
  while (baris.length && !baris[0].trim()) baris.shift()
  while (baris.length && !baris[baris.length - 1].trim()) baris.pop()

  if (baris.length > maxLines) {
    const tampil = baris.slice(0, maxLines - 1)
    const sisa = baris.length - tampil.length
    return [...tampil, `…+${sisa} lines`].join('\n')
  }
  return baris.join('\n')
}

/** Blok teks yang sudah dirapikan; null bila kosong. */
export function teks(nilai, opsi) {
  const t = rapikan(nilai, opsi)
  return t ? { type: 'text', text: t } : null
}

const blokSah = (arr) => (Array.isArray(arr) ? arr.filter(Boolean) : [])

// ---------------------------------------------------------------------- label
/** Blok label (GenAILabelPrimitive) dengan palet editorial. */
export function label(text, { detail, color = 'ink', icon, variant = 'POSITIVE' } = {}) {
  const p = palette(color)
  const judul = rapikan(text, { maxLines: 1, maxChars: 90 })
  if (!judul) return null
  const sub = detail ? rapikan(detail, { maxLines: 1, maxChars: 90 }) : ''
  return {
    type: 'label',
    text: judul,
    ...(sub ? { detail: sub } : {}),
    ...(icon ? { icon } : {}),
    variant,
    background: p.background,
    textColor: p.textColor,
  }
}

export const divider = () => ({ type: 'divider' })
export const spacer = (height = 8) => ({ type: 'spacer', height })

/**
 * CTA yang benar-benar bisa diketuk.
 *
 * Dua bentuk, keduanya native (bukan pill saran yang mati di chat biasa):
 *   · `quickreply` — tombol yang MENGIRIM teks saat diketuk (mis. "!help").
 *   · `footer`     — tombol OPEN_URL yang membuka tautan.
 *
 * items = [{label:'Change my tone', cmd:'!prompt'}, ...] atau string perintah.
 * link  = {label:'Open dashboard', url:'https://…'} (opsional).
 */
export function cta(items, { chatId, link } = {}) {
  const list = (Array.isArray(items) ? items : [])
    .map((x) => (typeof x === 'string' ? { label: x, cmd: x } : x))
    .filter((x) => x && x.cmd)
    .slice(0, 3)
    .map((x) => ({
      label: rapikan(x.label || x.cmd, { maxLines: 1, maxChars: 24 }),
      reply_text: rapikan(x.cmd, { maxLines: 1, maxChars: 60 }),
    }))
  if (!list.length && !link) return []

  if (chatId) {
    // Cadangan: kalau tombol tidak dirender klien, balasan angka tetap jalan.
    simpanPilihan(chatId, list.map((x) => ({ label: x.label, cmd: x.reply_text })))
  }

  const blocks = []
  if (list.length) blocks.push({ type: 'quickreply', items: list })
  const url = typeof link === 'string' ? link : link?.url
  if (url) {
    blocks.push({
      type: 'footer',
      actions: [{ text: rapikan(link?.label || 'Open', { maxLines: 1, maxChars: 24 }), type: 'OPEN_URL', url }],
    })
  }
  return blocks
}

/** Header brand: nama bot + satu baris konteks. */
export function header(context = 'FIELD INTELLIGENCE · RESEARCH / MEMORY / TOOLS', { color = 'ink' } = {}) {
  return label(fem(BRAND), { detail: context, color })
}

// ------------------------------------------------------------ langkah kerja
const IKON = { selesai: ORN.check, gagal: ORN.cross }

/**
 * Ringkas jejak eksekusi. Langkah identik berurutan digabung ("think ×4"),
 * maksimal 3 baris + 1 baris penutup.
 */
export function traceBlocks(trace, { title = 'FIELD LOG · EXECUTION TRACE', color = 'teal' } = {}) {
  try {
    const langkah = (Array.isArray(trace) ? trace : []).filter((s) => s && typeof s === 'object')
    if (!langkah.length) return []

    const gabung = []
    for (const s of langkah) {
      const nama = s.kind === 'think' ? 'thinking' : String(s.name || s.kind || 'step')
      const ok = s.ok !== false
      const akhir = gabung[gabung.length - 1]
      if (akhir && akhir.nama === nama && akhir.ok === ok) {
        akhir.n += 1
        akhir.ms += Number(s.ms) || 0
        if (s.detail) akhir.detail = String(s.detail)
      } else {
        gabung.push({ nama, ok, n: 1, ms: Number(s.ms) || 0, detail: s.detail ? String(s.detail) : '' })
      }
    }

    const totalMs = gabung.reduce((a, s) => a + s.ms, 0)
    const gagal = gabung.filter((s) => !s.ok).length
    const ringkas = [
      `${gabung.length} steps`,
      gagal ? `${gagal} failed` : '',
      totalMs ? `${(totalMs / 1000).toFixed(1)}s` : '',
    ].filter(Boolean).join(' · ')

    const tampil = gabung.slice(0, 3).map((s) => {
      const ikon = s.ok ? IKON.selesai : IKON.gagal
      const kali = s.n > 1 ? ` ×${s.n}` : ''
      const ket = s.detail ? ` · ${s.detail}` : ''
      return `${ikon} ${s.nama}${kali}${ket}`
    })
    if (gabung.length > 3) tampil.push(`…+${gabung.length - 3} more steps`)

    return blokSah([
      label(sectionTitle(title), { detail: ringkas, color }),
      teks(tampil.join('\n'), { maxLines: 4, maxChars: 200 }),
    ])
  } catch {
    return blokSah([label(sectionTitle(title), { color })])
  }
}

// ---------------------------------------------------------------- menu bantuan
/**
 * Kartu bantuan: kategori → daftar perintah.
 * `groups` = [{judul, warna, items:[{cmd, desc}]}]
 */
export function helpBlocks(groups, { footer, chatId, link } = {}) {
  try {
    const blocks = [header('FIELD INDEX · AVAILABLE CAPABILITIES')]
    let kelebihan = 0

    for (const g of Array.isArray(groups) ? groups : []) {
      const items = (Array.isArray(g?.items) ? g.items : []).filter((it) => it && it.cmd)
      if (!items.length) continue
      const tampil = items.slice(0, 4)
      kelebihan += Math.max(0, items.length - 4)
      blocks.push(label(`${ORN.heart} ${g.judul || 'Other'}`, { color: g.warna || 'paper' }))
      blocks.push(teks(tampil
        .map((it) => menuLine(it.cmd, rapikan(it.desc, { maxLines: 1, maxChars: 70 })))
        .join('\n')))
    }

    if (kelebihan > 0) blocks.push(teks(`…and ${kelebihan} more commands in their sections.`))
    if (footer) {
      blocks.push(divider())
      blocks.push(teks(footer))
    }
    blocks.push(...cta([
      { label: 'Change my tone', cmd: '!prompt' },
      { label: 'My skills', cmd: '!skills' },
      { label: 'Search', cmd: '!search' },
    ], { chatId, link }))
    return blokSah(blocks)
  } catch {
    return blokSah([header('FIELD INDEX · MENU'), teks('Indeks belum dapat dirender. Jalankan !help kembali.')])
  }
}

// ------------------------------------------------------------------ pencarian
/**
 * Hasil pencarian: maksimal 4 temuan, satu baris judul + satu baris cuplikan.
 * res = {query, total, hasil:[{sumber, judul, cuplikan}]}
 */
export function searchBlocks(res, { limit = 4, chatId, link } = {}) {
  try {
    const hasil = (Array.isArray(res?.hasil) ? res.hasil : []).slice(0, Math.max(1, Math.min(limit, 4)))
    const total = Number(res?.total) || hasil.length
    const sumber = new Set(hasil.map((h) => String(h?.sumber || 'other'))).size

    const blocks = [
      header(`RESEARCH DESK · ${rapikan(res?.query, { maxLines: 1, maxChars: 40 }) || '-'}`, { color: 'ink' }),
      label(sectionTitle('Findings · Temuan'), { detail: `${total} hits · ${sumber} sources`, color: 'teal' }),
    ]

    if (!hasil.length) {
      blocks.push(teks('Belum ada temuan. Coba perluas kata kunci atau gunakan istilah yang lebih spesifik.'))
      return blokSah(blocks)
    }

    for (const h of hasil) {
      const nama = { skill: 'SKILLS', memori: 'MEMORY', berkas: 'FILES', riwayat: 'HISTORY' }[String(h?.sumber || '')] || String(h?.sumber || 'OTHER').toUpperCase()
      const judul = rapikan(h?.judul || '(untitled)', { maxLines: 1, maxChars: 60 })
      const cuplik = rapikan(h?.cuplikan || '', { maxLines: 1, maxChars: 110 })
      blocks.push(teks(`${ORN.sparkAlt} *${nama}* · ${judul}${cuplik ? `\n   ${cuplik}` : ''}`))
    }
    blocks.push(...cta([
      { label: 'Search again', cmd: '!search' },
      { label: 'My skills', cmd: '!skills' },
    ], { chatId, link }))
    return blokSah(blocks)
  } catch {
    return blokSah([header('search · 搜索'), teks('Searched, but could not render the results ♡')])
  }
}

// -------------------------------------------------------------- tim paralel
/**
 * Hasil beberapa sub-agent: maksimal 4 tugas, tiap tugas 2 baris.
 */
export function fanoutBlocks(res, { chatId, link } = {}) {
  try {
    const semua = Array.isArray(res?.tugas) ? res.tugas : []
    const tugas = semua.slice(0, 4)
    const ok = semua.filter((t) => t?.ok !== false).length

    const blocks = [
      header(`my little team · ${semua.length} agents · 团队`, { color: 'teal' }),
      label(sectionTitle('Results · 结果'), { detail: `${ok} done · ${semua.length - ok} failed`, color: 'teal' }),
    ]

    for (const t of tugas) {
      const tanda = t?.ok === false ? ORN.cross : ORN.check
      const judul = rapikan(t?.judul || 'task', { maxLines: 1, maxChars: 60 })
      const isi = rapikan(t?.hasil || '', { maxLines: 2, maxChars: 180 }).replace(/\n+/g, ' ')
      const peer = t?.agent ? ` · ${rapikan(t.agent, { maxLines: 1, maxChars: 24 })}` : ''
      const memory = t?.remember === false ? '' : t?.memory?.ok ? ' · shared note saved (unverified)' : ' · memory not saved'
      blocks.push(teks(`${tanda} *${judul}*${peer}${isi ? `\n   ${isi}` : ''}${memory ? `\n   ${memory}` : ''}`))
    }
    if (semua.length > 4) blocks.push(teks(`…+${semua.length - 4} more tasks`))
    blocks.push(...cta([
      { label: 'Run again', cmd: '!team' },
      { label: 'Search results', cmd: '!search' },
    ], { chatId, link }))
    return blokSah(blocks)
  } catch {
    return blokSah([header('my little team · 团队'), teks('The team finished, but I could not render it ♡')])
  }
}

// ---------------------------------------------------------------- admin grup
/** Jawaban FAQ dari dokumen grup: jawaban ≤ 4 baris + maksimal 2 rujukan. */
export function faqBlocks(res, { chatId, link } = {}) {
  try {
    const jawaban = rapikan(res?.jawaban || 'Belum ada jawaban yang cocok.', { maxLines: 4, maxChars: 500 })
    const blocks = [
      header('from the group book · 群组资料', { color: 'amber' }),
      teks(jawaban),
    ]

    const sumber = (Array.isArray(res?.sumber) ? res.sumber : []).slice(0, 2)
    if (sumber.length) {
      const baris = sumber.map((s) => {
        const judul = rapikan(s?.judul || 'document', { maxLines: 1, maxChars: 50 })
        const hal = s?.halaman ? ` · ${rapikan(s.halaman, { maxLines: 1, maxChars: 20 })}` : ''
        return `${ORN.dot} ${judul}${hal}`
      })
      blocks.push(label(sectionTitle('Sources · 来源'), { detail: `${sumber.length} refs`, color: 'amber' }))
      blocks.push(teks(baris.join('\n')))
    }
    blocks.push(...cta([
      { label: 'Group rules', cmd: '!group rules' },
      { label: 'Group books', cmd: '!group list' },
    ], { chatId, link }))
    return blokSah(blocks)
  } catch {
    return blokSah([header('group book · 群组资料'), teks('Could not render the answer ♡')])
  }
}

/** Kartu peringatan spam untuk owner (3 baris). */
export function spamBlocks({ chat, pengirim, alasan, cuplikan } = {}) {
  try {
    return blokSah([
      header('someone is being naughty · 可疑', { color: 'rose' }),
      label(sectionTitle('Suspected spam · 垃圾信息'), { detail: alasan || 'suspicious', color: 'rose' }),
      teks(
        `*Group*: ${rapikan(chat || '-', { maxLines: 1, maxChars: 40 })}\n`
        + `*Sender*: ${rapikan(pengirim || '-', { maxLines: 1, maxChars: 30 })}\n`
        + `*Text*: ${rapikan(cuplikan || '', { maxLines: 1, maxChars: 120 })}`,
        { maxLines: 3 },
      ),
    ])
  } catch {
    return blokSah([header('spam alert · 可疑'), teks('A message needs your review ♡')])
  }
}

/** Status kesehatan sistem: maksimal 4 butir. */
export function healthBlocks(hasil, opsi = {}) {
  try {
    const butir = (Array.isArray(hasil?.butir) ? hasil.butir : []).slice(0, 8)
    const lulus = Number(hasil?.lulus) || 0
    const total = Number(hasil?.total) || butir.length
    const blocks = [
      header(hasil?.gagal ? 'something needs fixing · 需修复' : 'I am healthy ♡ · 健康', { color: hasil?.gagal ? 'amber' : 'mint' }),
      label(sectionTitle('Checks · 检查'), { detail: `${lulus}/${total} passed`, color: hasil?.gagal ? 'amber' : 'mint' }),
    ]
    if (butir.length) {
      blocks.push(...(table('Self-check · 自检', ['', 'Check', 'Info'], butir.map((b) => [b?.ok ? ORN.check : ORN.cross, b?.nama || '?', b?.info || '']), { maxRows: 8 }) || []))
    }
    blocks.push(...cta(hasil?.gagal
      ? [{ label: 'Re-check', cmd: '!health' }, { label: 'Diagnostics', cmd: '!diag' }, { label: 'Recent log', cmd: '!log' }]
      : [{ label: 'Status', cmd: '!status' }, { label: 'Menu', cmd: '!help' }], { chatId: opsi?.chatId }))
    return blokSah(blocks)
  } catch {
    return blokSah([header('system status · 状态'), teks('Could not render the checks ♡')])
  }
}


// =========================================================================
// KARTU INTERAKTIF — tabel, menu yang bisa diketuk, dan status yang jelas.
//
// Prinsip: jangan kirim "tembok markdown". Daftar jadi TABEL native GenAI,
// kategori jadi LABEL berwarna, dan setiap kartu berakhir dengan tombol
// quickreply (diketuk → perintah dikirim) + cadangan balasan angka (1-6).
// Teks kartu tetap Inggris dengan aksen Tionghoa (aturan UI proyek).
// =========================================================================

const sel = (v, n = 60) => rapikan(v ?? '', { maxLines: 1, maxChars: n }).replace(/\n+/g, ' ')

/** Tabel native GenAI. Baris pertama = judul kolom. Batas baris + penanda sisa. */
export function table(title, header, rows, { maxRows = 8 } = {}) {
  try {
    const cols = Array.isArray(header) ? header.length : 0
    const data = (Array.isArray(rows) ? rows : [])
      .filter((r) => Array.isArray(r) && r.length)
      .slice(0, maxRows)
      .map((r) => Array.from({ length: cols }, (_, i) => sel(r[i], i === cols - 1 ? 70 : 40)))
    if (!cols || !data.length) return null
    const extra = (Array.isArray(rows) ? rows.length : 0) - data.length
    const out = [{ type: 'table', title: sel(title, 50), rows: [header.map((h) => sel(h, 24)), ...data] }]
    if (extra > 0) out.push(teks(`…+${extra} more`))
    return out
  } catch {
    return null
  }
}

/** Pemberitahuan (kosong / galat / berhasil) yang rapi, bukan baris teks mentah. */
export function noticeBlocks({ title = 'Heads up · 提示', text = '', color = 'amber', actions = [], chatId, link } = {}) {
  try {
    return blokSah([
      label(sectionTitle(title), { color }),
      teks(text, { maxLines: 4, maxChars: 320 }),
      ...cta(actions, { chatId, link }),
    ])
  } catch {
    return blokSah([teks(String(text || 'Something went wrong ♡'))])
  }
}

/** Nama pendek grup untuk tombol: bagian Inggris sebelum "·". */
export const namaGrup = (judul) => sel(String(judul || '').split('·')[0].trim() || 'Other', 24)
/** Kunci grup untuk `!help <kunci>`: kata pertama, huruf kecil. */
export const kunciGrup = (judul) => namaGrup(judul).toLowerCase().split(/[^a-z0-9-]+/)[0] || 'other'

/**
 * Menu utama yang bisa diketuk: tabel ringkas kelompok → ketuk satu untuk
 * membuka isinya (`!help <kelompok>`), atau balas angkanya.
 */
export function menuBlocks(groups, { footer, chatId, link } = {}) {
  try {
    const ada = (Array.isArray(groups) ? groups : []).filter((g) => Array.isArray(g?.items) && g.items.some((i) => i?.cmd))
    const total = ada.reduce((a, g) => a + g.items.length, 0)
    const baris = ada.map((g, i) => [i < 6 ? String(i + 1) : '·', namaGrup(g.judul), String(g.items.length), `!${String(g.items[0].cmd).split(' ')[0]}`])
    const blocks = [
      header('everything I can do · 全部指令'),
      teks(`${total} commands in ${ada.length} groups ${ORN.heart} Tap a group below, or reply with its number.`, { maxLines: 2, maxChars: 160 }),
      ...(table('Pick a group · 选择', ['#', 'Group', 'Cmds', 'Try'], baris, { maxRows: 8 }) || []),
    ]
    if (footer) blocks.push(teks(footer))
    const pilihan = ada.slice(0, 6).map((g) => ({ label: namaGrup(g.judul), cmd: `!help ${kunciGrup(g.judul)}` }))
    blocks.push(...cta(pilihan.slice(0, 3), { chatId, link }))
    // Cadangan angka sampai 6 kelompok (tombol hanya 3); menimpa simpanan cta().
    if (chatId && pilihan.length) simpanPilihan(chatId, pilihan)
    return blokSah(blocks)
  } catch {
    return blokSah([header('FIELD INDEX · MENU'), teks('Indeks belum dapat dirender. Jalankan !help kembali.')])
  }
}

/** Isi satu kelompok: SEMUA perintahnya dalam tabel + tombol coba/kembali. */
export function groupBlocks(g, { chatId, link } = {}) {
  try {
    const items = (Array.isArray(g?.items) ? g.items : []).filter((i) => i && i.cmd)
    const rows = items.map((i) => [`!${i.cmd}`, sel(i.desc, 80)])
    // Perintah tanpa argumen aman dijalankan langsung dari tombol.
    const aman = items
      .map((i) => String(i.cmd))
      .filter((c) => !/[<|]/.test(c) && c !== 'help' && !/^(stop|resume|run)$/.test(c))
      .slice(0, 2)
      .map((c) => ({ label: `Run !${c}`, cmd: `!${c}` }))
    return blokSah([
      header(`${namaGrup(g?.judul)} · 指令`, { color: g?.warna || 'lilac' }),
      ...(table(namaGrup(g?.judul), ['Command', 'What it does'], rows, { maxRows: 10 }) || []),
      teks('Commands with <…> need words after them, e.g. !search budget ♡'),
      ...cta([...aman, { label: 'Back to menu', cmd: '!help' }], { chatId, link }),
    ])
  } catch {
    return blokSah([header('FIELD INDEX · MENU'), teks('Could not open that group. Try !help ♡')])
  }
}

/** !status — apa yang sedang dikerjakan, sebagai tabel + tombol Stop/Refresh. */
export function statusBlocks(items, { chatId } = {}) {
  try {
    const list = (Array.isArray(items) ? items : []).filter(Boolean)
    if (!list.length) {
      return blokSah([
        header('all quiet · 休息中', { color: 'teal' }),
        teks(`Nothing running right now ${ORN.heart} Send me a task and I will start.`),
        ...cta([{ label: 'Health check', cmd: '!health' }, { label: 'Menu', cmd: '!help' }], { chatId }),
      ])
    }
    const baris = list.map((a) => [a.tool || a.step || 'working', a.detail || '', `${a.elapsed ?? 0}s`])
    return blokSah([
      header(`working · ${list.length} ${list.length === 1 ? 'task' : 'tasks'} · 进行中`, { color: 'paper' }),
      ...(table('Right now · 现在', ['Step', 'Detail', 'Time'], baris, { maxRows: 6 }) || []),
      ...cta([{ label: 'Refresh', cmd: '!status' }, { label: 'Stop task', cmd: '!stop' }, { label: 'Recent log', cmd: '!log' }], { chatId }),
    ])
  } catch {
    return blokSah([header('status · 状态'), teks('Could not render the status ♡')])
  }
}

/** !plugins — tabel add-on (nama, perintah, fungsi). */
export function pluginsBlocks(plugins, { chatId } = {}) {
  try {
    const list = (Array.isArray(plugins) ? plugins : []).filter(Boolean)
    if (!list.length) {
      return noticeBlocks({
        title: 'No add-ons yet · 暂无', color: 'paper', chatId,
        text: 'Ask me to build one, e.g. “make a plugin that greets new members”.',
        actions: [{ label: 'Menu', cmd: '!help' }],
      })
    }
    const baris = list.map((p) => [
      p.ok === false ? `⚠ ${p.name}` : p.name,
      (p.commands || []).slice(0, 2).map((c) => `!${c}`).join(' ') || '—',
      p.ok === false ? p.error : (p.description || '—'),
    ])
    return blokSah([
      header(`add-ons · ${list.length} · 附加`, { color: 'rose' }),
      ...(table('Installed · 已安装', ['Add-on', 'Commands', 'About'], baris, { maxRows: 8 }) || []),
      ...cta([{ label: 'Menu', cmd: '!help' }], { chatId }),
    ])
  } catch {
    return blokSah([header('add-ons · 附加'), teks('Could not render the add-on list ♡')])
  }
}

const gabungNama = (arr, maks) => {
  const a = (Array.isArray(arr) ? arr : []).map(String)
  const tampil = a.slice(0, maks).join(' · ')
  return a.length > maks ? `${tampil} · …+${a.length - maks}` : tampil
}

/** !skills — ringkasan: inti (selalu aktif) vs tambahan (sesuai kebutuhan). */
export function skillsBlocks(j, { chatId } = {}) {
  try {
    const kb = (n) => `${Math.round(Number(n || 0) / 1000)}k chars`
    return blokSah([
      header('my skills · 技能', { color: 'paper' }),
      label(sectionTitle('Core · always on · 常驻'), { detail: `${j?.internal ?? 0} skills · ${kb(j?.total_chars?.internal)}`, color: 'paper' }),
      teks(gabungNama(j?.nama_internal, 8) || '—', { maxLines: 3, maxChars: 260 }),
      label(sectionTitle('Extra · on demand · 按需'), { detail: `${j?.external ?? 0} skills · ${kb(j?.total_chars?.external)}`, color: 'teal' }),
      teks(gabungNama(j?.nama_external, 8) || '—', { maxLines: 3, maxChars: 260 }),
      label(sectionTitle('Live prompt load · 实时加载'), { detail: ['injected', 'loaded'].includes(j?.load_visual?.status) ? '● INJECTED' : j?.load_visual?.status === 'prepared' ? '◐ PREPARED' : '○ NOT INJECTED', color: ['injected', 'loaded'].includes(j?.load_visual?.status) ? 'mint' : 'amber' }),
      teks(`Request #${j?.load_visual?.sequence ?? 0} · ${j?.load_visual?.loaded_count ?? 0} injected · ${j?.load_visual?.prepared_count ?? 0} prepared · ${j?.load_visual?.deferred_count ?? 0} deferred`, { maxLines: 2, maxChars: 180 }),
      teks(['injected', 'loaded'].includes(j?.load_visual?.status)
        ? `In prompt: ${[...(j?.last_prompt_load?.internal || []), ...(j?.last_prompt_load?.external || [])].slice(0, 12).join(' · ') || 'No skills selected'}`
        : `Not yet sent to the model · ${j?.load_visual?.status || 'no load recorded'}`, { maxLines: 3, maxChars: 280 }),
      ...cta([{ label: 'Search skills', cmd: '!skills search' }, { label: 'Reload', cmd: '!skills reload' }, { label: 'Menu', cmd: '!help' }], { chatId }),
    ])
  } catch {
    return blokSah([header('skills · 技能'), teks('Could not render the skill list ♡')])
  }
}

/** Hasil `!skills search` — tabel + tombol buka skill teratas. */
export function skillHitsBlocks(query, hits, { chatId } = {}) {
  try {
    const list = (Array.isArray(hits) ? hits : []).filter((h) => h && h.name)
    if (!list.length) {
      return noticeBlocks({
        title: 'No match · 没有结果', color: 'amber', chatId,
        text: `No skill matched “${sel(query, 40)}”. Try a broader word.`,
        actions: [{ label: 'All skills', cmd: '!skills' }],
      })
    }
    const baris = list.map((h) => [h.name, h.description || '—'])
    const buka = list.slice(0, 2).map((h) => ({ label: `Open ${h.name}`, cmd: `!skills get ${h.name}` }))
    return blokSah([
      header(`skills for “${sel(query, 26)}” · 技能`, { color: 'paper' }),
      ...(table('Best matches · 最佳', ['Skill', 'Good for'], baris, { maxRows: 5 }) || []),
      ...cta([...buka, { label: 'All skills', cmd: '!skills' }], { chatId }),
    ])
  } catch {
    return blokSah([header('skills · 技能'), teks('Could not render the matches ♡')])
  }
}

/** `!skills get <nama>` — isi skill dipecah per paragraf agar nyaman di ponsel. */
export function skillDetailBlocks(j, { chatId } = {}) {
  try {
    const isi = bersih(String(j?.content || '')).slice(0, 2500)
    const potong = String(j?.content || '').length > 2500
    const bagian = []
    let buf = ''
    for (const para of isi.split(/\n{2,}/)) {
      if ((buf + '\n\n' + para).length > 900 && buf) { bagian.push(buf); buf = para } else buf = buf ? `${buf}\n\n${para}` : para
    }
    if (buf) bagian.push(buf)
    const refs = (j?.references || []).slice(0, 6)
    return blokSah([
      label(`${ORN.sparkAlt} ${sel(j?.name, 40)}`, { detail: sel(j?.description, 80), color: 'paper' }),
      ...bagian.slice(0, 3).map((t) => ({ type: 'text', text: t })),
      potong || bagian.length > 3 ? teks('…trimmed. Ask me to read the rest, or open a reference.') : null,
      refs.length ? teks(`References · 参考: ${refs.join(' · ')}`, { maxLines: 2, maxChars: 240 }) : null,
      ...cta([{ label: 'All skills', cmd: '!skills' }, { label: 'Search skills', cmd: '!skills search' }], { chatId }),
    ])
  } catch {
    return blokSah([header('skills · 技能'), teks('Could not render that skill ♡')])
  }
}

/** !diag — pengaturan berlaku dalam tabel + ringkasan yang ditolak 24 jam. */
export function diagBlocks({ rows, ditolak = 0, contoh = [] } = {}, { chatId } = {}) {
  try {
    return blokSah([
      header('diagnostics · 诊断', { color: 'paper' }),
      ...(table('In force · 生效', ['Setting', 'Value'], rows, { maxRows: 8 }) || []),
      label(sectionTitle('Blocked · last 24h · 拦截'), { detail: `${ditolak} events`, color: ditolak ? 'amber' : 'mint' }),
      ditolak && contoh.length ? teks(contoh.map((c) => `${ORN.dot} ${sel(c, 70)}`).join('\n'), { maxLines: 3, maxChars: 240 }) : teks(`Nothing was blocked ${ORN.heart}`),
      teks('Change limits in config/default.yaml (security & ui), then !reload.'),
      ...cta([{ label: 'Health check', cmd: '!health' }, { label: 'Recent log', cmd: '!log' }], { chatId }),
    ])
  } catch {
    return blokSah([header('diagnostics · 诊断'), teks('Could not render the diagnostics ♡')])
  }
}

/** !log — kejadian terakhir sebagai tabel Waktu | Kejadian. */
export function logBlocks(entries, { chatId, total } = {}) {
  try {
    const list = (Array.isArray(entries) ? entries : []).filter(Boolean)
    if (!list.length) {
      return noticeBlocks({ title: 'Log is empty · 日志', color: 'paper', text: 'Nothing has been recorded yet.', actions: [{ label: 'Status', cmd: '!status' }], chatId })
    }
    const baris = list.slice(-12).map((e) => [e.time || '', e.text || ''])
    return blokSah([
      header(`recent events · ${total ?? list.length} · 日志`, { color: 'slate' }),
      ...(table('Latest · 最新', ['Time', 'Event'], baris, { maxRows: 12 }) || []),
      ...cta([{ label: 'More (30)', cmd: '!log 30' }, { label: 'Status', cmd: '!status' }], { chatId }),
    ])
  } catch {
    return blokSah([header('log · 日志'), teks('Could not render the log ♡')])
  }
}

export default {
  label, divider, spacer, cta, header, traceBlocks,
  helpBlocks, searchBlocks, fanoutBlocks, faqBlocks, spamBlocks, healthBlocks,
  table, noticeBlocks, menuBlocks, groupBlocks, statusBlocks, pluginsBlocks, skillsBlocks,
  skillHitsBlocks, skillDetailBlocks, diagBlocks, logBlocks, namaGrup, kunciGrup,
  bersih, rapikan, teks, BRAND, BATAS_BARIS,
}
