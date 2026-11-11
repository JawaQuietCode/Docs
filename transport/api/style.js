// style.js — identitas visual "kweewind blows".
//
// Semua gaya teks di sini dipakai lapisan kartu (ui.js) dan perintah cepat
// (commands.js). Tujuannya: satu tempat untuk nama bot, font, dan palet warna,
// supaya mengganti identitas tidak perlu menyisir seluruh kode.
//
// Catatan penting soal font Unicode: karakter di bawah ini BUKAN dekorasi asal.
// WhatsApp merender blok Unicode "Mathematical Bold Script" sebagai teks tebal
// miring yang rapi di Android/iOS/Web. Tetap ada fallback ASCII untuk terminal
// (lihat `plain`) karena sebagian font monospace tidak punya glyph-nya.

export const BRAND = 'KWEE ◈ FIELD JOURNAL'

// KWEE COMIC NOIR: mature neo-brutalist palette. Paper, ink, punchy primaries.
// Thick outlines and hard shadows are expressed by native labels/cards; no gradients.
export const PALETTE = {
  ink: { background: '#171717', textColor: '#FFF8E8' },
  paper: { background: '#FFF2D2', textColor: '#171717' },
  teal: { background: '#8DE4E0', textColor: '#102B2A' },
  copper: { background: '#FF8A65', textColor: '#32130C' },
  steel: { background: '#B9D7FF', textColor: '#10243D' },
  mint: { background: '#B8EF8A', textColor: '#1C3210' },
  amber: { background: '#FFD447', textColor: '#382900' },
  rose: { background: '#FFB5C8', textColor: '#421324' },
  slate: { background: '#D5D0C5', textColor: '#24221E' },
  pink: { background: '#FFB5C8', textColor: '#421324' },
  lilac: { background: '#D9C2FF', textColor: '#2B1748' },
}

// ------------------------------------------------------------------ peta font
// Mathematical Bold Script: A-Z U+1D4D0.., a-z U+1D4EA..
function scriptMap() {
  const map = new Map()
  const upper = 0x1d4d0
  const lower = 0x1d4ea
  for (let i = 0; i < 26; i += 1) {
    map.set(String.fromCharCode(65 + i), String.fromCodePoint(upper + i))
    map.set(String.fromCharCode(97 + i), String.fromCodePoint(lower + i))
  }
  return map
}
const SCRIPT = scriptMap()

// Small caps (huruf kapital kecil) — untuk label kategori.
const SMALL_CAPS = {
  a: 'ᴀ', b: 'ʙ', c: 'ᴄ', d: 'ᴅ', e: 'ᴇ', f: 'ꜰ', g: 'ɢ', h: 'ʜ', i: 'ɪ', j: 'ᴊ',
  k: 'ᴋ', l: 'ʟ', m: 'ᴍ', n: 'ɴ', o: 'ᴏ', p: 'ᴘ', q: 'ǫ', r: 'ʀ', s: 'ꜱ', t: 'ᴛ',
  u: 'ᴜ', v: 'ᴠ', w: 'ᴡ', x: 'x', y: 'ʏ', z: 'ᴢ',
}

/** Teks bergaya "bold script" (dipakai untuk nama bot & judul kartu). */
export function fem(text) {
  return String(text ?? '').replace(/[A-Za-z]/g, (c) => SCRIPT.get(c) || c)
}

/** Judul kategori: huruf biasa, awal kata kapital.
 *
 * Dulu dipakai small-caps (ᴀ ʙ ᴄ). Terlihat cantik di layar besar, tapi di
 * sebagian ponsel glyph-nya jatuh ke font cadangan dan terbaca seperti aksara
 * asing — persis yang tidak diinginkan. Huruf biasa selalu aman.
 */
export function smallCaps(text) {
  return String(text ?? '').replace(/(^|\s)(\p{L})/gu, (m, spasi, huruf) => spasi + huruf.toUpperCase())
}

/** Versi ASCII polos — untuk log terminal dan nama berkas. */
export function plain(text) {
  return String(text ?? '')
}

/** Nama bot bergaya, dipakai di header kartu. */
export function brandStyled() {
  return fem(BRAND)
}

// Ornamen kecil. Dipilih yang aman di WhatsApp (tidak memicu emoji besar).
export const ORN = {
  spark: '✦',
  sparkAlt: '✧',
  dot: '·',
  heart: '◈',
  leaf: '⌁',
  arrow: '→',
  check: '✓',
  cross: '✕',
}

/** Judul bagian: "✦ 𝓹𝓮𝓻𝓲𝓷𝓽𝓪𝓱". */
export function sectionTitle(text, orn = ORN.spark) {
  return `${orn} ${fem(text)}`
}

/** Baris menu: "♡ !cari — mencari di seluruh isi". */
export function menuLine(cmd, desc, orn = ORN.heart) {
  return `${orn} *!${cmd}* — ${desc}`
}

/** Palet aman (fallback ke ink bila nama tidak dikenal). */
export function palette(name) {
  return PALETTE[String(name || 'ink')] || PALETTE.ink
}
