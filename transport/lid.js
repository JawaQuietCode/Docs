// lid.js — resolusi LID <-> nomor telepon (PN).
//
// Sejak WhatsApp memakai LID, `remoteJid`/`participant` sebuah pesan bisa
// berupa `<lid>@lid` dan TIDAK memuat nomor telepon sama sekali. Sementara
// `WA_OWNER_NUMBERS` di .env memakai nomor telepon. Tanpa resolusi ini, pesan
// dari owner selalu dianggap "bukan owner" lalu diabaikan.
//
// Baileys menyimpan pemetaan dua arah di folder sesi, TAPI tidak selalu lengkap
// (mis. hanya `lid-mapping-<pn>.json` ada, sedangkan `<lid>_reverse.json` tidak).
// Karena itu modul ini MEMINDAI seluruh folder sesi dan membangun indeks dua
// arah, sehingga satu arah saja sudah cukup untuk menurunkan arah lainnya.

import fs from 'node:fs'
import path from 'node:path'

export function isLid(jid) {
  const s = String(jid || '')
  return s.endsWith('@lid') || s.endsWith('@hosted.lid')
}

export function isPn(jid) {
  const s = String(jid || '')
  return s.endsWith('@s.whatsapp.net') || s.endsWith('@hosted')
}

export function bareUser(jid) {
  return String(jid || '').split('@')[0].split(':')[0].trim()
}

const INDEX_TTL_MS = 15000
const indexCache = new Map() // authDir -> { at, pn2lid, lid2pn }

function readJson(p) {
  try {
    const v = JSON.parse(fs.readFileSync(p, 'utf8'))
    return typeof v === 'string' && v ? v : null
  } catch {
    return null
  }
}

/** Indeks dua arah LID<->PN dari seluruh file `lid-mapping-*` (di-cache). */
export function buildIndex(authDir) {
  if (!authDir) return { pn2lid: new Map(), lid2pn: new Map() }
  const hit = indexCache.get(authDir)
  const now = Date.now()
  if (hit && now - hit.at < INDEX_TTL_MS) return hit

  const pn2lid = new Map()
  const lid2pn = new Map()
  try {
    for (const f of fs.readdirSync(authDir)) {
      if (!f.startsWith('lid-mapping-') || !f.endsWith('.json')) continue
      const key = f.slice('lid-mapping-'.length, -'.json'.length)
      const val = readJson(path.join(authDir, f))
      if (!key || !val) continue
      if (key.endsWith('_reverse')) {
        const lid = key.slice(0, -'_reverse'.length)
        lid2pn.set(lid, val)
        pn2lid.set(val, lid)
      } else {
        pn2lid.set(key, val)
        lid2pn.set(val, key)
      }
    }
  } catch {
    /* folder belum ada → indeks kosong */
  }
  const entry = { at: now, pn2lid, lid2pn }
  indexCache.set(authDir, entry)
  return entry
}

/** Baca satu file pemetaan langsung (tanpa indeks). */
export function readMappingFile(authDir, key) {
  if (!authDir) return null
  return readJson(path.join(authDir, `lid-mapping-${key}.json`))
}

/** LID -> nomor telepon. Mengembalikan JID PN (`...@s.whatsapp.net`) atau null.
 *
 * Pencarian SADAR-TIPE: hanya memakai peta `lid2pn` (LID→PN). TIDAK ada fallback
 * ke peta sebaliknya, karena angka LID dan nomor telepon berbagi ruang angka —
 * LID seseorang bisa sama dengan nomor telepon orang lain (tabrakan nyata di
 * panel). Fallback lintas-peta menukar identitas; itu dilarang.
 */
export async function lidToPn(sock, authDir, jid) {
  const lid = String(jid || '')
  if (!isLid(lid)) return null
  const lidUser = bareUser(lid)
  if (!lidUser) return null

  // 1) Store Baileys (cache memori + store persisten)
  try {
    const pn = await sock?.signalRepository?.lidMapping?.getPNForLID?.(lid)
    if (pn) return String(pn)
  } catch {
    /* lanjut ke indeks file */
  }
  // 2) Indeks dua arah dari folder sesi (hanya peta LID→PN)
  const idx = buildIndex(authDir)
  const pnUser = idx.lid2pn.get(lidUser)
    || readMappingFile(authDir, `${lidUser}_reverse`)
  return pnUser ? `${pnUser}@s.whatsapp.net` : null
}

/** Nomor telepon -> LID. Mengembalikan JID LID (`...@lid`) atau null.
 *
 * Sama: SADAR-TIPE, hanya peta `pn2lid` (PN→LID).
 */
export async function pnToLid(sock, authDir, jid) {
  const pn = String(jid || '')
  if (!isPn(pn)) return null
  const pnUser = bareUser(pn)
  if (!pnUser) return null

  try {
    const lid = await sock?.signalRepository?.lidMapping?.getLIDForPN?.(pn)
    if (lid) return String(lid)
  } catch {
    /* lanjut ke indeks file */
  }
  const idx = buildIndex(authDir)
  const lidUser = idx.pn2lid.get(pnUser)
    || readMappingFile(authDir, pnUser)
  return lidUser ? `${lidUser}@lid` : null
}

/**
 * Kumpulkan SEMUA identitas pengirim yang diketahui dari sederet JID.
 *
 * Untuk setiap JID `@lid` ditambahkan padanan nomor teleponnya, dan sebaliknya
 * bila pemetaan tersedia. Hasilnya dipakai allowlist: cocok bila SALAH SATU
 * bentuk identitas cocok.
 */
export async function resolveSenderIds(sock, authDir, jids) {
  const out = new Set()
  const list = (Array.isArray(jids) ? jids : [jids])
    .map((j) => String(j || '').trim())
    .filter(Boolean)

  for (const j of list) out.add(j)

  // Perluasan murni dari indeks file (cepat, tanpa I/O jaringan).
  // SADAR-TIPE: LID hanya lewat peta lid2pn, PN hanya lewat peta pn2lid.
  // Fallback silang DIHAPUS — angka LID & nomor telepon berbagi ruang angka,
  // jadi fallback silang bisa menyisipkan identitas owner LAIN (tabrakan).
  const idx = buildIndex(authDir)
  for (const j of list) {
    const u = bareUser(j)
    if (!u) continue
    if (isLid(j)) {
      const pn = idx.lid2pn.get(u)
      if (pn) out.add(`${pn}@s.whatsapp.net`)
    } else if (isPn(j)) {
      const lid = idx.pn2lid.get(u)
      if (lid) out.add(`${lid}@lid`)
    }
  }

  // Lengkapi lewat store Baileys bila tersedia.
  for (const j of list) {
    try {
      if (isLid(j)) {
        const pn = await lidToPn(sock, authDir, j)
        if (pn) out.add(pn)
      } else if (isPn(j)) {
        const lid = await pnToLid(sock, authDir, j)
        if (lid) out.add(lid)
      }
    } catch {
      /* abaikan; identitas mentah tetap dipakai */
    }
  }
  return [...out]
}
