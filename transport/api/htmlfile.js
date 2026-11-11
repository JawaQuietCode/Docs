// htmlfile.js — ubah HTML di dalam jawaban teks menjadi lampiran `.html`.
//
// Kenapa ada di transport: inti Python (agent/format.py) sudah melakukan ini,
// tetapi engine OpenHands mengembalikan teks MENTAH. Tanpa langkah ini, fence
// ```html dikirim sebagai teks kartu dan tampil sebagai blok "Lihat kode".
// Aturannya sengaja sama dengan format.py supaya hasilnya konsisten:
//   - fence html/htm/xhtml/html5 yang berisi markup   -> berkas .html
//   - fence bahasa apa pun yang berisi DOKUMEN HTML    -> berkas .html
//   - dokumen HTML utuh di tengah/akhir teks biasa     -> berkas .html
//   - selain itu (js/python/css/prosa yang menyebut <div>) tidak disentuh.
// Preview inline tetap hanya lewat fence wa:web2ui / wa:preview (tidak diubah).

const HTML_LANGS = new Set(['html', 'htm', 'xhtml', 'html5'])

const TAG_RE = new RegExp(
  '</?(?:html|head|body|title|meta|link|style|script|div|p|span|h[1-6]|a|img|' +
  'ul|ol|li|table|thead|tbody|tfoot|tr|td|th|section|article|aside|header|' +
  'footer|nav|main|form|input|button|select|option|textarea|label|svg|canvas|' +
  'iframe|video|audio|source|figure|figcaption|blockquote|pre|code|details|' +
  'summary|template|marquee|center|font)\\b[^>]*>', 'i')
const DOC_RE = /^\s*(?:<!doctype\s+html|<html\b|<body\b|<head\b)/i
const TAG_COUNT_RE = /<\/?(?:div|section|article|header|footer|nav|main|aside|style|script|svg|canvas|button|form|table|ul|ol|h[1-6]|p|span|a|img)\b[^>]*>/gi
// Dokumen utuh tanpa fence harus DIMULAI di awal baris (bukan tag di tengah kode/prosa).
const START_RE = /(?:^|\n)[ \t]*(<!doctype\s+html|<html\b)/i

export function isHtmlDocument(text) {
  const t = String(text ?? '').trim()
  if (!t) return false
  if (DOC_RE.test(t)) return true
  const low = t.toLowerCase()
  const count = (t.match(TAG_COUNT_RE) || []).length
  if (low.includes('<style') || low.includes('<script') || low.includes('<body')) return count >= 2
  // Fragmen kaya (>= 4 tag) dianggap halaman bila tidak terbaca seperti prosa.
  return count >= 4 && !/[A-Za-z]{3,}\s+[A-Za-z]{3,}\s+[A-Za-z]{3,}/.test(t)
}

function namaBerkas(n) {
  return n <= 1 ? 'kwee-output.html' : `kwee-output-${n}.html`
}

// Fence tertutup, atau fence terakhir yang tidak ditutup (jawaban terpotong).
const FENCE_RE = /```([A-Za-z0-9_+.:-]*)[ \t]*\r?\n([\s\S]*?)(?:\r?\n```|$)/g

/**
 * @param {string} text
 * @returns {{ teks: string, berkas: Array<{html: string, filename: string}> }}
 */
export function ekstrakHtml(text) {
  const sumber = String(text ?? '')
  if (!sumber.includes('<')) return { teks: sumber, berkas: [] }
  const berkas = []

  // 1) Fence.
  let sisa = sumber.replace(FENCE_RE, (utuh, bahasa, isi) => {
    const lang = String(bahasa || '').toLowerCase()
    // Fence rich (wa:*) ditangani lapisan lain; jangan diambil.
    if (lang.startsWith('wa:')) return utuh
    const html = String(isi || '').trim()
    if (!html) return utuh
    const htmlLang = HTML_LANGS.has(lang) && TAG_RE.test(html)
    if (!htmlLang && !isHtmlDocument(html)) return utuh
    // Fence non-html yang berisi dokumen HTML hanya diambil bila memang
    // dokumen utuh — potongan kode contoh (mis. js) tidak boleh ikut.
    if (!htmlLang && lang && !['text', 'txt', 'plaintext', 'plain', 'xml'].includes(lang) && !DOC_RE.test(html)) return utuh
    berkas.push({ html, filename: namaBerkas(berkas.length + 1) })
    return '\n'
  })

  // 2) Dokumen HTML utuh tanpa fence (setelah prosa pembuka). Hanya dicari di
  //    teks DI LUAR fence yang tersisa, supaya `print("<html>")` di blok kode
  //    python/js tidak ikut terambil.
  if (!berkas.length) {
    const potong = []
    let akhir = 0
    for (const f of sisa.matchAll(FENCE_RE)) {
      potong.push({ fence: false, teks: sisa.slice(akhir, f.index) })
      potong.push({ fence: true, teks: f[0] })
      akhir = f.index + f[0].length
    }
    potong.push({ fence: false, teks: sisa.slice(akhir) })
    let ketemu = false
    const keluar = []
    for (const seg of potong) {
      if (!ketemu && !seg.fence) {
        const m = START_RE.exec(seg.teks)
        if (m) {
          const mulai = m.index + m[0].length - m[1].length
          const ekor = seg.teks.slice(mulai)
          if (isHtmlDocument(ekor)) {
            berkas.push({ html: ekor.trim(), filename: namaBerkas(1) })
            keluar.push(seg.teks.slice(0, m.index))
            ketemu = true
            continue
          }
        }
      }
      keluar.push(seg.teks)
    }
    if (ketemu) sisa = keluar.join('')
  }

  if (!berkas.length) return { teks: sumber, berkas: [] }
  return { teks: sisa.replace(/\n{3,}/g, '\n\n').trim(), berkas }
}

/**
 * Terapkan ke daftar balasan: tiap balasan `text` yang memuat HTML dipecah
 * menjadi teks (tanpa HTML) + satu `html_file` per dokumen. Balasan lain
 * dibiarkan. Bila teks penjelas kosong, diberi kalimat penanda singkat agar
 * pesan tidak hanya berupa lampiran tanpa konteks.
 */
export function htmlKeBerkas(replies) {
  const hasil = []
  for (const r of Array.isArray(replies) ? replies : []) {
    if (!r || r.type !== 'text' || typeof r.text !== 'string') { hasil.push(r); continue }
    const { teks, berkas } = ekstrakHtml(r.text)
    if (!berkas.length) { hasil.push(r); continue }
    hasil.push({ ...r, text: teks || 'File HTML-nya ada di lampiran ♡' })
    for (const b of berkas) hasil.push({ type: 'html_file', html: b.html, filename: b.filename, mimetype: 'text/html' })
  }
  return hasil
}

export default { isHtmlDocument, ekstrakHtml, htmlKeBerkas }
