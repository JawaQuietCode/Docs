// pilihan.js — CTA yang BENAR-BENAR bisa dipakai.
//
// Masalah yang diselesaikan: pill "saran lanjutan" (GenAIFollowUpSuggestionPill)
// hanya bisa diketuk di dalam chat bot resmi Meta. Di chat WhatsApp biasa ia
// tampil sebagai hiasan — dikira tombol, ternyata mati. Jadi CTA di sini
// memakai cara yang selalu bekerja di semua klien: **balas angkanya**.
//
// Alurnya:
//   1. Kartu menampilkan "♡ 1 · ganti gaya bicaraku".
//   2. `simpan(chatId, [...])` mencatat pilihan itu (maks 6, berlaku 15 menit).
//   3. Pengguna membalas "1" → `ambil()` mengembalikan perintah aslinya
//      (`!gaya`), lalu router memprosesnya seperti perintah biasa.
//
// Tanpa database: pilihan hanya relevan beberapa menit dan hilang saat restart.
// Itu cukup — kalau hilang, pengguna tinggal mengetik perintahnya.
const TTL = 15 * 60 * 1000
const MAKS = 6

const peta = new Map()

/** Simpan pilihan terakhir untuk satu ruangan. */
export function simpan(chatId, items) {
  if (!chatId) return
  const bersih = (Array.isArray(items) ? items : [])
    .map((x) => (typeof x === 'string' ? { label: x, cmd: x } : x))
    .filter((x) => x && x.cmd)
    .slice(0, MAKS)
  if (!bersih.length) return
  peta.set(String(chatId), { items: bersih, ts: Date.now() })
}

/** Ambil perintah dari balasan angka ("1", "2.", "3 ya"). null bila tak ada. */
export function ambil(chatId, teks) {
  const data = peta.get(String(chatId || ''))
  if (!data) return null
  if (Date.now() - data.ts > TTL) {
    peta.delete(String(chatId))
    return null
  }
  const t = String(teks || '').trim().toLowerCase()
  const cocok = t.match(/^([1-9])(?:[.)\s]|$)/)
  if (!cocok) return null
  const item = data.items[Number(cocok[1]) - 1]
  return item ? item.cmd : null
}

/** Buang pilihan (mis. setelah dipakai). */
export function lupakan(chatId) {
  peta.delete(String(chatId || ''))
}

/** Untuk pengujian / diagnostik. */
export function lihat(chatId) {
  const data = peta.get(String(chatId || ''))
  if (!data || Date.now() - data.ts > TTL) return null
  return data.items.map((x, i) => `${i + 1}. ${x.label} → ${x.cmd}`)
}
