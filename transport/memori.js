// memori.js — menyusun konteks yang BENAR-BENAR dikirim ke engine.
//
// Kenapa berkas ini ada: memori dan skills dulu hanya dirakit di inti Python
// (`agent/context.py`). Begitu engine jawaban pindah ke OpenHands
// (`transport/engine.js`), teks yang dikirim ke agent-server hanyalah pesan
// pengguna — memori dan skills tidak pernah ikut. Hasilnya persis keluhan
// pemilik: "suntikannya gimmick, skills cuma pajangan".
//
// Berkas ini membaca sumber yang sama dengan inti Python:
//   data/memory/notes/MEMORY.md (+ USER.md)   → memori jangka panjang
//   skills/internal/*.md                      → skill WAJIB (selalu ikut)
//   skills/external/*.md                      → skill relevan (dipilih per topik)
// lalu menempelkannya sebagai pembuka pesan ke engine.
import fs from 'node:fs'
import path from 'node:path'

// Dibaca saat dipakai (bukan saat import) supaya KWEE_ROOT yang diset
// start.sh — atau diubah pengujian — selalu dihormati.
const akar = () => process.env.KWEE_ROOT || process.cwd()

function baca(file, maks) {
  try {
    const teks = fs.readFileSync(file, 'utf8')
    return maks && teks.length > maks ? teks.slice(0, maks) : teks
  } catch {
    return ''
  }
}

function daftar(folder) {
  try {
    return fs.readdirSync(folder, { withFileTypes: true })
  } catch {
    return []
  }
}

/** Parse the common Agent Skills YAML frontmatter subset, including folded descriptions and list triggers. */
function frontmatter(teks) {
  const m = /^---\s*\n([\s\S]*?)\n---/.exec(teks)
  if (!m) return { meta: {}, isi: teks }
  const meta = {}
  const lines = m[1].split('\n')
  let current = ''
  let mode = ''
  for (const line of lines) {
    const item = /^\s+-\s+(.+?)\s*$/.exec(line)
    if (item && current === 'triggers' && mode === 'list') {
      meta.triggers = meta.triggers || []
      meta.triggers.push(item[1].replace(/^['"]|['"]$/g, ''))
      continue
    }
    if (/^\s+/.test(line) && current && mode === 'folded') {
      meta[current] = `${meta[current] || ''} ${line.trim()}`.trim()
      continue
    }
    const pair = /^([A-Za-z0-9_-]+):\s*(.*)$/.exec(line)
    if (!pair) continue
    current = pair[1].toLowerCase()
    const raw = pair[2].trim()
    mode = ''
    if (raw === '>-' || raw === '>' || raw === '|' || raw === '|-') {
      meta[current] = ''
      mode = 'folded'
    } else if (!raw && current === 'triggers') {
      meta[current] = []
      mode = 'list'
    } else if (raw.startsWith('[') && raw.endsWith(']')) {
      meta[current] = raw.slice(1, -1).split(',').map((v) => v.trim().replace(/^['"]|['"]$/g, '')).filter(Boolean)
    } else {
      meta[current] = raw.replace(/^['"]|['"]$/g, '')
    }
  }
  return { meta, isi: teks.slice(m[0].length).trim() }
}

/** Skor relevansi: trigger yang muncul di teks pengguna + kemiripan kata. */
function skor(meta, teks) {
  const cari = String(teks || '').toLowerCase()
  let n = 0
  const trig = Array.isArray(meta.triggers) ? meta.triggers : []
  for (const t of trig) {
    const s = String(t).toLowerCase().trim()
    if (s && cari.includes(s)) n += 3
  }
  const kata = String(meta.description || '').toLowerCase().split(/[^a-z0-9\u00c0-\uffff]+/).filter((w) => w.length > 3)
  for (const w of kata) if (cari.includes(w)) n += 1
  return n
}

/**
 * Baca skill flat (`external/foo.md`) DAN paket Agent Skills
 * (`external/foo/SKILL.md`). Pemindai lama hanya membaca berkas di root,
 * sehingga sebagian besar paket skill tidak pernah masuk ke prompt OpenHands.
 */
export function daftarSkill() {
  const hasil = []
  const visit = (folder, jenis, depth = 0) => {
    if (depth > 5) return
    for (const ent of daftar(folder)) {
      const file = path.join(folder, ent.name)
      if (ent.isFile() && ent.name.endsWith('.md')) {
        // SKILL.md hanya dipakai sebagai entrypoint paket, tidak diduplikasi
        // ketika visit sudah turun ke folder skill.
        if (ent.name === 'SKILL.md' && depth === 0) continue
        add(file, jenis, folder)
      } else if (ent.isDirectory()) {
        const entry = path.join(file, 'SKILL.md')
        const skillText = baca(entry, 80000)
        if (skillText) {
          add(entry, jenis, file)
        } else {
          visit(file, jenis, depth + 1)
        }
      }
    }
  }
  const add = (file, jenis, base) => {
    const raw = baca(file, 80000)
    if (!raw) return
    const { meta, isi } = frontmatter(raw)
    if (!isi.trim()) return
    const refs = []
    if (path.basename(file) === 'SKILL.md') {
      const walk = (dir, depth = 0) => {
        if (depth > 3) return
        for (const ent of daftar(dir)) {
          const child = path.join(dir, ent.name)
          if (ent.isDirectory()) walk(child, depth + 1)
          else if (ent.isFile() && /\.(md|txt)$/i.test(ent.name) && child !== file) {
            refs.push(path.relative(base, child))
          }
        }
      }
      walk(base)
    }
    hasil.push({
      jenis,
      nama: String(meta.name || (path.basename(file) === 'SKILL.md' ? path.basename(base) : path.basename(file, '.md'))),
      deskripsi: String(meta.description || ''),
      prioritas: Number(meta.priority || 0),
      triggers: Array.isArray(meta.triggers) ? meta.triggers : String(meta.triggers || '').split(',').map(x => x.trim()).filter(Boolean),
      isi, file, references: refs,
    })
  }
  for (const jenis of ['internal', 'external']) {
    const folder = path.join(akar(), 'skills', jenis)
    visit(folder, jenis)
  }
  return hasil
}

const PREAMBLE_LOADS = new Map()
let LATEST_PREAMBLE_ID = 'default'
let PREAMBLE_SEQUENCE = 0

function preambleKey(requestId = '') {
  return String(requestId || 'default')
}

function savePreambleStatus(requestId, record) {
  const key = preambleKey(requestId)
  PREAMBLE_LOADS.set(key, { ...record })
  LATEST_PREAMBLE_ID = key
  while (PREAMBLE_LOADS.size > 200) {
    const oldest = PREAMBLE_LOADS.keys().next().value
    if (oldest === LATEST_PREAMBLE_ID) break
    PREAMBLE_LOADS.delete(oldest)
  }
}

export function getPreambleStatus(requestId = '') {
  const record = (requestId
    ? PREAMBLE_LOADS.get(preambleKey(requestId))
    : PREAMBLE_LOADS.get(LATEST_PREAMBLE_ID))
    || { status: 'not-yet-loaded', sequence: 0, at: null, internal: [], external: [], prompt_chars: 0, loaded_count: 0 }
  return { ...record, internal: [...record.internal], external: [...record.external] }
}

export function reloadSkillCache(requestId = '') {
  savePreambleStatus(requestId, {
    status: 'not-yet-loaded', sequence: PREAMBLE_SEQUENCE,
    at: new Date().toISOString(), internal: [], external: [], prompt_chars: 0, loaded_count: 0,
  })
}

/**
 * Susun pembuka konteks untuk engine.
 *
 * @param {object} config  config Kwee (dipakai `memory.maxChars`, `skills.*`)
 * @param {string} teks    pesan pengguna (untuk memilih skill relevan)
 * @returns {string} blok konteks, atau '' bila tidak ada apa-apa
 */
export function buildPreamble(config, teks = '', requestId = '') {
  const memCfg = (config && config.memory) || {}
  const batasMemori = Number(memCfg.maxChars || 25000)
  const memDir = path.join(akar(), 'data', 'memory', 'notes')
  const bagian = []
  const memori = baca(path.join(memDir, 'MEMORY.md'), batasMemori)
  const user = baca(path.join(memDir, 'USER.md'), 4000)
  const combinedMemory = [memori, user].filter(Boolean).join('\n\n').trim()
  if (combinedMemory) bagian.push('## MEMORI JANGKA PANJANG (fakta yang sudah diketahui — pakai, jangan tanya ulang)\n' + combinedMemory)

  const semua = daftarSkill()
  const internal = semua.filter((s) => s.jenis === 'internal').sort((a, b) => b.prioritas - a.prioritas)
  const eksternal = semua.filter((s) => s.jenis === 'external')
  if (internal.length) {
    const isi = internal.map((s) => `### ${s.nama}${s.deskripsi ? ` — ${s.deskripsi}` : ''}\n${s.isi}`).join('\n\n')
    bagian.push('## SKILL WAJIB (selalu berlaku, jangan diabaikan)\n' + (isi.length > 12000 ? isi.slice(0, 12000) + '\n… [internal skills truncated]' : isi))
  } else {
    bagian.push('## SKILL LOADER WARNING\nTidak ada skill internal yang ditemukan di skills/internal/. Jangan mengklaim bahwa system skills sudah dimuat.')
  }
  if (eksternal.length) {
    const katalogRaw = eksternal.slice().sort((a, b) => a.nama.localeCompare(b.nama))
      .map((s) => `- ${s.nama}: ${s.deskripsi || '(tanpa deskripsi)'}`).join('\n')
    const katalog = katalogRaw.length > 8000 ? katalogRaw.slice(0, 8000) + '\n… [catalogue clipped; use skill search for the full index]' : katalogRaw
    bagian.push('## KATALOG SKILL LAIN (muat lewat tool `skill` bila perlu)\n' + katalog)
    const cocok = eksternal.map((s) => ({ s, n: skor(s, teks) })).filter((x) => x.n > 0)
      .sort((a, b) => b.n - a.n || b.s.prioritas - a.s.prioritas).slice(0, 3)
    if (cocok.length) {
      bagian.push('## SKILL RELEVAN UNTUK PESAN INI\n' + cocok.map(({ s }) => {
        const refs = s.references?.length ? `\nRujukan tersedia (muat hanya bila perlu): ${s.references.slice(0, 12).join(', ')}` : ''
        return `### ${s.nama}${s.deskripsi ? ` — ${s.deskripsi}` : ''}\n${s.isi.slice(0, 3500)}${refs}`
      }).join('\n\n'))
    }
  }
  const output = bagian.length ? `[KONTEKS INTERNAL — jangan dikutip mentah ke pengguna]\n\n${bagian.join('\n\n')}\n\n---\n` : ''
  PREAMBLE_SEQUENCE += 1
  savePreambleStatus(requestId, {
    status: internal.length ? 'prepared' : 'error', sequence: PREAMBLE_SEQUENCE,
    at: new Date().toISOString(), internal: internal.map((s) => s.nama),
    external: (eksternal.map((s) => ({ s, n: skor(s, teks) })).filter((x) => x.n > 0)
      .sort((a, b) => b.n - a.n || b.s.prioritas - a.s.prioritas).slice(0, 3).map((x) => x.s.nama)),
    prompt_chars: output.length, loaded_count: 0,
  })
  return output
}

/** Mark the preamble injected only after the accepted request contains every selected skill. */
export function confirmPreambleIncluded(payload, requestId = '') {
  const text = String(payload || '')
  const current = getPreambleStatus(requestId)
  if (current.status !== 'prepared') return false
  const expected = [...current.internal, ...current.external]
  if (!expected.length) return false
  const missing = expected.filter((name) => !text.includes(`### ${name}`))
  const updated = {
    ...current,
    status: missing.length ? 'not_injected' : 'injected',
    loaded_count: missing.length ? 0 : expected.length,
    missing,
    payload_chars: text.length,
  }
  savePreambleStatus(requestId, updated)
  return missing.length === 0 && expected.length > 0
}

export default { buildPreamble, confirmPreambleIncluded, daftarSkill, getPreambleStatus, reloadSkillCache }
