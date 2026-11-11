import { test } from 'node:test'
import assert from 'node:assert/strict'
import { hitungPersen, bilah, bilahHalus, spinner, blokBerjalan, blokSelesai, ratakanBalasan, urutkanBalasan, isAssetReply, targetPersen, haluskanPersen } from '../transport/api/progress-card.js'
import { renderProgressHtml, renderProgressLiveHtml } from '../transport/api/progress-html.js'
import { ekstrakHtml, htmlKeBerkas } from '../transport/api/htmlfile.js'
import { Kemajuan } from '../transport/api/kemajuan.js'

test('persen naik bertahap dan berhenti di 98 sebelum selesai', () => {
  assert.equal(hitungPersen(0), 0)
  assert.ok(hitungPersen(3) > hitungPersen(2))
  assert.equal(hitungPersen(50), 92)
  assert.ok(targetPersen({ fase: 'tool', detikFase: 20, langkahFase: 2 }) <= 76)
  assert.equal(haluskanPersen(20, 80), 22)
  assert.equal(haluskanPersen(20, 15), 20, 'tidak pernah mundur')
  assert.equal(hitungPersen(0, true), 100)
})

test('bilah panjang tetap', () => {
  assert.equal([...bilah(0)].length, 24)
  assert.equal([...bilah(100)].length, 24)
  assert.equal(bilah(100), '█'.repeat(24))
})

test('kartu progres tidak pernah memakai blok kode/terminal', () => {
  const b = blokBerjalan({ langkah: [{ nama: 'bash', detail: 'ls -la' }], persen: 40, tick: 3, fasePernah: ['skills', 'think', 'tool'], fase: 'tool' })
  assert.ok(b.every(x => x.type !== 'code'), 'tidak ada blok code (kartu "Bash / Lihat kode")')
  assert.equal(b[0].type, 'html')
  assert.match(b[0].data.html, /\$ ls -la/, 'command shell muncul sebagai baris HTML')
  assert.match(b[0].data.html, /background:transparent/)
  assert.ok(!b[0].data.html.includes('<script>'))
})

test('bilah halus: resolusi 1/8 sel, panjang tetap, bergerak tiap tick', () => {
  for (const p of [0, 3, 37.5, 99, 100]) assert.equal([...bilahHalus(p, 16, 2)].length, 16)
  assert.equal(bilahHalus(100, 16), '█'.repeat(16))
  // 1 unit (=0.78%) cukup untuk mengubah tampilan: bar tidak melompat per sel penuh
  assert.notEqual(bilahHalus(10, 16, 0), bilahHalus(11, 16, 0))
  // tepi berdenyut: persen sama, tick beda -> tampilan beda (rasa "loading")
  assert.notEqual(bilahHalus(40, 16, 0), bilahHalus(40, 16, 1))
  assert.notEqual(spinner(0), spinner(1))
})

test('daftar fase tetap: yang lewat ✓, yang aktif spinner, yang menunggu ○', () => {
  const b = blokBerjalan({ fase: 'tool', fasePernah: ['skills', 'think', 'tool'], persen: 40, tick: 1 })
  const html = b[0].data.html
  assert.match(html, /Memilih konteks/)
  assert.match(html, /Menjalankan langkah/)
  assert.match(html, /Rangkaian|activity|KWEE/)
  assert.ok(!html.includes('Permintaan dipahami'))
})

test('persen merayap (tidak macet) dan tidak pernah mundur', () => {
  let p = 27.9
  const awal = p
  for (let i = 0; i < 10; i++) p = haluskanPersen(p, 28, 2, 0.4)
  assert.ok(p > awal + 3, 'tetap bergerak walau target fase sudah tercapai')
  assert.ok(p <= 98)
  assert.equal(haluskanPersen(50, 10, 2, 0.4), 50.4, 'target lebih kecil: hanya merayap, tidak mundur')
})

test('fase berjalan hanya memakai HTML; tidak mengirim indikator progress native', () => {
  const b = blokBerjalan({ langkah: [{ nama: 'bash', detail: 'ls' }], persen: 18 })
  assert.equal(b[0].type, 'html')
  assert.match(b[0].data.html, /Membaca konteks/)
  assert.match(b[0].data.html, /18%/)
  assert.match(b[0].data.html, /background:transparent/)
  assert.equal(b.length, 1, 'satu pesan HTML tanpa blok native progress')
  assert.ok(b.every((block) => block.type === 'html'))
  assert.ok(!b[0].data.html.includes('Memahami permintaan dan berpikir'))
})

test('HTML progres memakai identitas comic-neobrutalism tanpa dependensi eksternal', () => {
  const html = blokBerjalan({ fase: 'tool', persen: 42, langkah: [{ nama: 'bash', detail: 'npm test' }] })[0].data.html
  assert.match(html, /class="comic-frame live"/)
  assert.match(html, /class="brand-stamp"/)
  assert.match(html, /--ink:\s*#321d32/i)
  assert.match(html, /repeating-linear-gradient/)
  assert.match(html, /border:\s*2px solid var\(--ink\)/)
  assert.match(html, /prefers-reduced-motion/)
  assert.ok(!/https?:\/\//.test(html.replace(/<!doctype[^>]*>/i, '')), 'tidak memuat font, stylesheet, atau aset eksternal')
})

test('progress menampilkan tahap skills sebagai baris tersendiri', () => {
  const b = blokBerjalan({ fase: 'skills', skillInfo: '3 dimuat · 8 eksternal tersedia' })
  assert.match(b[0].data.html, /Skills/)
  assert.match(b[0].data.html, /3 dimuat · 8 eksternal tersedia/)
  assert.match(b[0].data.html, /Memilih konteks/)
})

test('estimasi progress bertahap dan kenaikannya dibatasi per refresh', async () => {
  const { k, kirim, edit } = buat([
    { stage: 'think', step: 'berpikir', detail: 'menganalisis' },
    { stage: 'tool', step: 'menjalankan tool', tool: 'bash', detail: 'ls -la' },
    { stage: 'respond', step: 'menyusun balasan', detail: 'merapikan hasil' },
  ])
  await k.perbaruiModul()
  await k.perbaruiModul()
  await k.perbaruiModul()
  const persentase = [kirim[0][0].data.html, ...edit.map(x => x.b[0].data.html)]
    .map(x => Number(x.match(/class="state">[^<]*?(\d+)%/)?.[1]))
  for (let i = 1; i < persentase.length; i++) {
    assert.ok(persentase[i] >= persentase[i - 1], 'progress harus monoton')
    assert.ok(persentase[i] - persentase[i - 1] <= 2, 'progress tidak boleh meloncat lebih dari 2 poin')
  }
  assert.ok(persentase.at(-1) < 100, '100% hanya setelah proses ditutup')
})

test('jawaban teks didahulukan sebelum asset', () => {
  const r = urutkanBalasan([{ type: 'image', url: 'asset://1' }, { type: 'text', text: 'Jawaban' }, { type: 'document', url: 'asset://2' }])
  assert.deepEqual(r.map(x => x.type), ['text', 'image', 'document'])
  const html = urutkanBalasan([{ type: 'web2ui', html: '<main>preview</main>' }, { type: 'text', text: 'Penjelasan dulu' }])
  assert.deepEqual(html.map(x => x.type), ['text', 'web2ui'])
  const file = urutkanBalasan([{ type: 'html_file', html: '<!doctype html>' }, { type: 'text', text: 'File HTML terlampir.' }])
  assert.deepEqual(file.map(x => x.type), ['text', 'html_file'])
  assert.equal(isAssetReply({ type: 'html_file', html: '<!doctype html>' }), true)
  const nested = urutkanBalasan([{ type: 'richResponse', blocks: [{ type: 'web2ui', html: '<main>asset</main>' }, { type: 'text', text: 'Jawaban dulu' }] }])
  assert.deepEqual(nested.map(x => x.blocks[0].type), ['text', 'web2ui'])
  assert.equal(isAssetReply(nested[0]), false)
  assert.equal(isAssetReply(nested[1]), true)
})

test('ratakanBalasan meratakan richResponse dan menolak media', () => {
  const ok = ratakanBalasan([{ type: 'richResponse', blocks: [{ type: 'text', text: 'a' }, { type: 'code', code: 'x' }] }])
  assert.equal(ok.length, 2)
  assert.equal(ratakanBalasan([{ type: 'richResponse', blocks: [{ type: 'image', url: 'u' }] }]), null)
})

test('blokSelesai: terminal 100% lalu jawaban dalam kartu yang sama', () => {
  const r = blokSelesai({ langkah: [{ nama: 'bash', detail: 'ls' }], detik: 1, replies: [{ type: 'text', text: 'Jawaban' }] })
  assert.equal(r.ok, true)
  assert.match(r.blok[0].text, /Selesai · 1 langkah/)
  assert.equal(r.blok[1].text, 'Jawaban')
})

test('blokSelesai: jawaban panjang tidak digabung', () => {
  const r = blokSelesai({ langkah: [], replies: [{ type: 'text', text: 'x'.repeat(9000) }] })
  assert.equal(r.ok, false)
})

const sockPalsu = () => ({ sendPresenceUpdate: async () => {}, relayMessage: async () => {} })

function buat(statusList, { editGagal = false } = {}) {
  const kirim = []; const edit = []; const biasa = []
  let n = 0
  const k = new Kemajuan({
    chatId: 'c1', sock: sockPalsu(), tandai: () => {},
    bacaStatus: async () => statusList[Math.min(n++, statusList.length - 1)],
    modul: {
      kirim: async (b) => { kirim.push(b); return { key: { id: 'pesan-1' }, botResponseId: 'resp-1' } },
      edit: async (id, b, o) => { if (editGagal) throw new Error('ditolak'); edit.push({ id, b, o }) },
    },
  })
  return { k, kirim, edit, biasa }
}

test('KASUS SCREENSHOT: satu kartu, diedit jadi jawaban, tanpa pesan terpisah', async () => {
  const { k, kirim, edit, biasa } = buat([
    { step: 'berpikir', tool: '', detail: 'cukup' },
    { step: 'menjalankan tool', tool: 'bash', detail: 'ls -la' },
  ])
  await k.perbaruiModul()
  await k.perbaruiModul()
  assert.equal(kirim.length, 1)
  assert.equal(k.id, 'pesan-1')

  const replies = [{ type: 'richResponse', blocks: [{ type: 'text', text: 'Beres, ditutup ya' }] }]
  k.hentikan()
  await k.tutup(replies, async (r) => biasa.push(r))

  assert.equal(biasa.length, 0, 'jawaban tidak boleh jadi pesan kedua')
  const akhir = edit.at(-1)
  assert.equal(akhir.id, 'pesan-1')
  assert.equal(akhir.o.responseId, 'resp-1')
  assert.match(akhir.b[0].text, /Selesai ·/)
  assert.equal(akhir.b[1].text, 'Beres, ditutup ya')
})

test('jawaban berisi gambar: kartu ditutup, gambar dikirim terpisah', async () => {
  const { k, edit, biasa } = buat([{ step: 'berpikir' }])
  await k.perbaruiModul()
  await k.tutup([{ type: 'richResponse', blocks: [{ type: 'image', url: 'u' }] }], async (r) => biasa.push(r))
  assert.equal(biasa.length, 1)
  assert.match(edit.at(-1).b[0].text, /Selesai ·/)
})

test('jawaban ditampilkan sebelum asset; asset baru dikirim sesudahnya', async () => {
  const { k, edit, biasa } = buat([{ step: 'berpikir' }])
  await k.perbaruiModul()
  await k.tutup([{ type: 'richResponse', blocks: [
    { type: 'image', url: 'asset://image' },
    { type: 'text', text: 'Ini penjelasannya.' },
  ] }], async (r) => biasa.push(r))
  assert.equal(biasa.length, 1, 'jawaban sudah tampil di kartu; hanya asset dikirim terpisah')
  assert.equal(biasa[0][0].type, 'image')
  const statusAsset = edit.at(-2).b
  assert.match(statusAsset[0].data.html, /98%/, 'belum boleh 100% selama asset dikirim')
  assert.ok(statusAsset.some(b => b.type === 'text' && /Ini penjelasannya/.test(b.text)))
  assert.equal(statusAsset[0].type, 'html')
  assert.ok(statusAsset.every((block) => block.type !== 'progress'), 'status asset tidak memakai indikator native')
  assert.ok(edit.at(-1).b.some(b => b.type === 'text' && /Selesai/.test(b.text)))
})

test('edit gagal: dicatat, jawaban tetap terkirim (tidak hilang)', async () => {
  const { k, biasa } = buat([{ step: 'berpikir' }], { editGagal: true })
  await k.perbaruiModul()
  const asli = console.warn; const log = []
  console.warn = (...a) => log.push(a.join(' '))
  try {
    await k.tutup([{ type: 'text', text: 'halo' }], async (r) => biasa.push(r))
  } finally { console.warn = asli }
  assert.equal(biasa.length, 1)
  assert.ok(log.some((l) => /edit kartu progres gagal/.test(l)))
})

test('riwayat event tidak hilang ketika polling hanya melihat status terakhir', async () => {
  const events = [
    { seq: 1, stage: 'skills', step: 'memuat skill', detail: 'memindai katalog', kind: 'phase' },
    { seq: 2, stage: 'think', step: 'berpikir', detail: 'menyusun rencana', kind: 'phase' },
    { seq: 3, stage: 'tool', step: 'menjalankan tool', tool: 'bash', detail: 'ls -1 skills/external | nl', kind: 'tool_start', event_id: 'call-1' },
    { seq: 4, stage: 'tool', step: 'menjalankan tool', tool: 'bash', detail: 'ls -1 skills/external | nl', kind: 'tool', event_id: 'call-1', ok: true },
  ]
  const { k } = buat([{ stage: 'respond', step: 'menyusun balasan', events, event_seq: 4 }])
  await k.perbaruiModul()
  assert.deepEqual(k.langkahModul.map(x => x.nama), ['bash'])
  assert.match(k.langkahModul[0].detail, /ls -1 skills\/external/)
  assert.equal(k.eventSeq, 4)
  assert.equal(k.langkahModul[0].pending, false)
  assert.equal(k.langkahModul[0].ok, true)
})

test('snapshot final menangkap tool cepat yang selesai di antara polling', async () => {
  const { k, kirim, edit } = buat([{ stage: 'think', step: 'berpikir', events: [] }])
  await k.perbaruiModul()
  k.adopsiStatus({
    stage: 'respond', step: 'menyusun balasan', skill_info: '2 dimuat · 8 eksternal tersedia',
    events: [
      { seq: 1, stage: 'think', step: 'berpikir', kind: 'phase' },
      { seq: 2, stage: 'tool', step: 'menjalankan tool', tool: 'bash', detail: 'ls -1 skills/external | nl', kind: 'tool_start', event_id: 'fast-1' },
      { seq: 3, stage: 'tool', step: 'menjalankan tool', tool: 'bash', detail: 'ls -1 skills/external | nl', kind: 'tool', event_id: 'fast-1', ok: true },
      { seq: 4, stage: 'respond', step: 'menyusun balasan', kind: 'phase' },
    ],
  })
  await k.tutup([{ type: 'text', text: 'Sudah diperiksa.' }], async () => {})
  assert.equal(kirim.length, 1)
  assert.deepEqual(k.langkahModul.map(x => x.nama), ['bash'])
  assert.match(k.langkahModul[0].detail, /ls -1 skills\/external/)
})

test('laporan HTML dikirim sebagai dokumen mandiri setelah jawaban, bukan widget', async () => {
  const { k, edit, biasa } = buat([{ stage: 'think', step: 'berpikir', events: [] }])
  await k.perbaruiModul()
  await k.tutup([
    { type: 'text', text: 'Selesai.' },
    { type: 'html_file', filename: 'kwee-progress.html', html: '<!doctype html><html></html>' },
  ], async (r) => biasa.push(r))
  assert.equal(biasa.length, 1)
  assert.equal(biasa[0][0].type, 'html_file')
  assert.match(edit.at(-2).b[0].data.html, /98%/)
  assert.match(edit.at(-1).b[0].text, /Selesai ·/)
  assert.equal(edit.at(-1).b[1].text, 'Selesai.')
})

// ---------------------------------------------------------------------------
// Laporan HTML buatan transport + HTML dari jawaban model
// ---------------------------------------------------------------------------

function bukaKartu(opsi = {}, status = [{ stage: 'think', step: 'berpikir', events: [] }]) {
  const kirim = []; const edit = []; const biasa = []
  let n = 0
  const k = new Kemajuan({
    chatId: 'c1', sock: sockPalsu(), tandai: () => {},
    bacaStatus: async () => status[Math.min(n++, status.length - 1)],
    modul: {
      kirim: async (b) => { kirim.push(b); return { key: { id: 'pesan-1' }, botResponseId: 'resp-1' } },
      edit: async (id, b, o) => { edit.push({ id, b, o }) },
    },
    ...opsi,
  })
  return { k, kirim, edit, biasa }
}

test('mode file: laporan HTML dibuat transport sendiri dan dikirim SESUDAH jawaban', async () => {
  const { k, edit, biasa } = bukaKartu({ laporanHtml: true, laporanMode: 'file' })
  await k.perbaruiModul()
  await k.tutup([{ type: 'text', text: 'Ohayo ♡' }], async (r) => biasa.push(r))
  assert.equal(biasa.length, 1)
  const [berkas] = biasa[0]
  assert.equal(berkas.type, 'html_file')
  assert.equal(berkas.filename, 'kwee-progress.html')
  assert.match(berkas.html, /^<!doctype html>/i)
  assert.match(edit.at(-2).b[0].data.html, /98%/)
  assert.ok(edit.at(-2).b.some(b => b.type === 'text' && /Ohayo/.test(b.text)))
  assert.ok(edit.at(-1).b.some(b => b.type === 'text' && b.text === 'Ohayo ♡'))
})

test('laporan HTML tidak ganda bila inti Python sudah mengirim kwee-progress.html', async () => {
  const { k, biasa } = bukaKartu({ laporanHtml: true, laporanMode: 'file' })
  await k.perbaruiModul()
  await k.tutup([
    { type: 'text', text: 'Selesai.' },
    { type: 'html_file', filename: 'kwee-progress.html', html: '<!doctype html><html></html>' },
  ], async (r) => biasa.push(r))
  assert.equal(biasa[0].filter(r => r.type === 'html_file').length, 1)
})

test('laporanHtml dimatikan: tidak ada lampiran', async () => {
  const { k, biasa } = bukaKartu({ laporanHtml: false })
  await k.perbaruiModul()
  await k.tutup([{ type: 'text', text: 'Halo' }], async (r) => biasa.push(r))
  assert.equal(biasa.length, 0)
})

test('engine tanpa telemetri (status kosong): kartu tetap hidup dan fase tidak mundur', async () => {
  const { k, kirim, edit } = bukaKartu({}, [{ stage: 'respond', step: 'menyusun balasan', events: [] }, null, null])
  await k.perbaruiModul()
  await k.perbaruiModul()
  await k.perbaruiModul()
  assert.equal(kirim.length, 1)
  assert.equal(edit.length, 2, 'status null tidak membuat kartu berhenti')
  assert.match(edit.at(-1).b[0].data.html, /Merangkai jawaban/, 'tetap di fase terakhir, tidak mundur ke berpikir')
})

test('spinner/denyut berubah tiap edit walau persen hampir sama', async () => {
  const { k, kirim, edit } = bukaKartu({}, [{ stage: 'think', step: 'berpikir', events: [] }])
  await k.perbaruiModul(); await k.perbaruiModul(); await k.perbaruiModul()
  const frames = [kirim[0][0].data.html, ...edit.map(e => e.b[0].data.html)]
  assert.equal(new Set(frames).size, frames.length, 'tiap frame HTML berubah mengikuti tick')
})

test('renderProgressHtml: final dossier berbeda dari live progress dan teks di-escape', () => {
  const h = renderProgressHtml({
    langkah: [{ nama: 'bash', detail: 'echo "<script>alert(1)</script>"', ok: true, t: 2.5 }, { nama: 'tulis_berkas', detail: 'a&b', ok: false, t: 4 }],
    fasePernah: ['skills', 'think', 'tool'], detik: 29.3, skillInfo: '<b>x</b>',
  })
  assert.match(h, /^<!doctype html>/i)
  assert.ok(!h.includes('<script>alert(1)</script>'), 'detail langkah harus di-escape')
  assert.ok(!h.includes('<b>x</b>'))
  assert.match(h, /class=\"dossier\"/)
  assert.doesNotMatch(h, /class=\"track\"/)
  assert.match(h, /FINAL DOSSIER/)
  assert.match(h, /--paper:#2b192d/i)
  assert.match(h, /Catatan penutup/)
  assert.match(h, /prefers-reduced-motion/)
  assert.match(h, /background:transparent/)
  assert.match(h, /\+2\.5s/)
  assert.match(h, /class="gagal"/)
  assert.ok(!/https?:\/\//.test(h.replace(/<!doctype[^>]*>/i, '')), 'tanpa sumber eksternal')
})


test('alur tanpa tools: loading HTML → skills → HTML final → jawaban teks', async () => {
  const { k, kirim, edit, biasa } = bukaKartu(
    { laporanHtml: true },
    [
      { stage: 'think', step: 'berpikir', events: [] },
      { stage: 'skills', step: 'memuat skill', skill_info: '2 skills terpilih', events: [] },
    ],
  )
  await k.perbaruiModul()
  await k.perbaruiModul()
  await k.tutup([{ type: 'text', text: 'Ini jawaban AI.' }], async (r) => biasa.push(r))
  assert.equal(kirim.length, 1)
  assert.equal(biasa.length, 0)
  const live = kirim[0][0]
  assert.equal(live.type, 'html')
  const akhir = edit.at(-1).b
  assert.equal(akhir[0].type, 'html')
  assert.match(akhir[0].data.html, /Rangkaian selesai/)
  assert.match(akhir[0].data.html, /2 skills terpilih/)
  assert.equal(akhir[1].type, 'text')
  assert.equal(akhir[1].text, 'Ini jawaban AI.')
})

test('alur dengan tool: command dan edit tetap berurutan, skills tampil sebelum jawaban', async () => {
  const { k, kirim, edit } = bukaKartu(
    { laporanHtml: true },
    [{
      stage: 'respond',
      step: 'menyusun balasan',
      skill_info: 'browser · shell',
      events: [
        { seq: 1, stage: 'tool', step: 'menjalankan tool', tool: 'bash', detail: 'cat app.js', kind: 'tool_start', event_id: 'a' },
        { seq: 2, stage: 'tool', step: 'menjalankan tool', tool: 'bash', detail: 'cat app.js', kind: 'tool', event_id: 'a', ok: true },
        { seq: 3, stage: 'tool', step: 'menjalankan tool', tool: 'edit_file', detail: 'app.js', kind: 'tool_start', event_id: 'b' },
        { seq: 4, stage: 'tool', step: 'menjalankan tool', tool: 'edit_file', detail: 'app.js', kind: 'tool', event_id: 'b', ok: true },
        { seq: 5, stage: 'skills', step: 'memuat skill', kind: 'phase' },
      ],
    }],
  )
  await k.perbaruiModul()
  await k.tutup([{ type: 'text', text: 'Hasil sudah diperbarui.' }], async () => {})
  assert.equal(kirim.length, 1)
  const liveHtml = kirim[0][0].data.html
  assert.ok(liveHtml.indexOf('$ cat app.js') !== -1)
  assert.ok(liveHtml.indexOf('edit_file') !== -1)
  const akhir = edit.at(-1).b
  assert.equal(akhir[0].type, 'html')
  const report = akhir[0].data.html
  assert.ok(report.indexOf('$ cat app.js') < report.indexOf('edit_file'))
  assert.match(report, /Skills/)
  assert.equal(akhir.at(-1).type, 'text')
  assert.equal(akhir.at(-1).text, 'Hasil sudah diperbarui.')
})


test('HTML hasil kecil di-embed sebelum jawaban, bukan dikirim sebagai file terpisah', async () => {
  const { k, edit, biasa } = bukaKartu({ laporanHtml: true })
  await k.perbaruiModul()
  await k.tutup([
    { type: 'text', text: 'Preview sudah siap.' },
    { type: 'html_file', filename: 'hasil.html', html: '<!doctype html><html><body><h1>Hasil</h1></body></html>' },
  ], async (r) => biasa.push(r))
  assert.equal(biasa.length, 0)
  const final = edit.at(-1).b
  assert.equal(final[0].type, 'html', 'HTML progres berada paling atas')
  assert.equal(final[1].type, 'html', 'hasil HTML menjadi preview di kartu yang sama')
  assert.match(final[1].data.html, /<h1>Hasil<\/h1>/)
  assert.equal(final[2].type, 'text')
  assert.equal(final[2].text, 'Preview sudah siap.')
})

test('HTML hasil terlalu besar tetap dikirim sebagai dokumen agar kartu tidak meledak', async () => {
  const { k, edit, biasa } = bukaKartu({ laporanHtml: true })
  await k.perbaruiModul()
  await k.tutup([
    { type: 'text', text: 'File besar disiapkan.' },
    { type: 'html_file', filename: 'hasil.html', html: '<!doctype html><html>' + 'x'.repeat(165 * 1024) + '</html>' },
  ], async (r) => biasa.push(r))
  assert.equal(biasa.length, 1)
  assert.equal(biasa[0][0].type, 'html_file')
  const final = edit.at(-1).b
  assert.equal(final[0].type, 'html', 'laporan progres tetap berada di card')
  assert.ok(final.some(b => b.type === 'text' && b.text === 'File besar disiapkan.'))
})

test('HTML di jawaban teks menjadi lampiran .html, bukan blok kode', () => {
  const sumber = 'Ini filenya:\n\n```html\n<!doctype html><html><body><h1>Halo</h1></body></html>\n```\n\nSelamat mencoba!'
  const r = htmlKeBerkas([{ type: 'text', text: sumber }])
  assert.deepEqual(r.map(x => x.type), ['text', 'html_file'])
  assert.ok(!r[0].text.includes('```'), 'tidak ada sisa fence yang akan jadi kartu "Lihat kode"')
  assert.match(r[0].text, /Ini filenya/)
  assert.match(r[1].html, /<h1>Halo<\/h1>/)
  assert.equal(r[1].filename, 'kwee-output.html')
})

test('fence HTML tanpa penutup (jawaban terpotong) dan dokumen tanpa fence tetap jadi file', () => {
  const a = ekstrakHtml('```html\n<div class="a"><p>hai</p></div>')
  assert.equal(a.berkas.length, 1)
  const b = ekstrakHtml('Ini file HTML-nya.\n\n<!doctype html><html><body>Isi</body></html>')
  assert.equal(b.berkas.length, 1)
  assert.equal(b.teks, 'Ini file HTML-nya.')
})

test('kode non-HTML dan prosa yang menyebut tag tidak disentuh', () => {
  const js = 'Contoh:\n```js\nconst el = "<div>"\nconsole.log(el)\n```'
  assert.equal(ekstrakHtml(js).berkas.length, 0)
  const prosa = 'Gunakan tag <div> untuk pembungkus dan <p> untuk paragraf.'
  assert.equal(ekstrakHtml(prosa).berkas.length, 0)
  const py = '```python\nprint("<html>")\n```'
  assert.equal(ekstrakHtml(py).berkas.length, 0)
  assert.deepEqual(htmlKeBerkas([{ type: 'text', text: 'halo' }]), [{ type: 'text', text: 'halo' }])
})

test('dua dokumen HTML dalam satu jawaban diberi nama berbeda', () => {
  const t = '```html\n<!doctype html><html><body>1</body></html>\n```\ndan\n```html\n<!doctype html><html><body>2</body></html>\n```'
  const { berkas } = ekstrakHtml(t)
  assert.deepEqual(berkas.map(b => b.filename), ['kwee-output.html', 'kwee-output-2.html'])
})

test('mode card (bawaan): laporan HTML tertanam di KARTU YANG SAMA, tidak ada pesan terpisah', async () => {
  const { k, edit, biasa } = bukaKartu({ laporanHtml: true })
  await k.perbaruiModul()
  await k.tutup([{ type: 'text', text: 'Ohayo ♡' }], async (r) => biasa.push(r))
  assert.equal(biasa.length, 0, 'tidak ada dokumen/pesan terpisah')
  const akhir = edit.at(-1).b
  const html = akhir.find(b => b.type === 'html')
  assert.ok(html, 'blok html (typename GenAIaeacdsnwHtmlPrimitive, seperti !ping) ada di kartu')
  assert.match(html.data.html, /^<!doctype html>/i)
  assert.equal(html.data.url, 'https://example.com')
  assert.ok(akhir.some(b => b.text === 'Ohayo ♡'), 'jawaban tetap di kartu yang sama')
  assert.ok(akhir.every(b => b.type !== 'code'))
  assert.ok(akhir.indexOf(html) < akhir.findIndex(b => b.type === 'text' && b.text === 'Ohayo ♡'), 'HTML final berada sebelum respon teks')
})

test('mode card: edit kartu gagal -> laporan jatuh ke dokumen cadangan', async () => {
  const kirim = []; const biasa = []
  const k = new Kemajuan({
    chatId: 'c1', sock: sockPalsu(), tandai: () => {}, laporanHtml: true,
    bacaStatus: async () => ({ stage: 'think', step: 'berpikir', events: [] }),
    modul: { kirim: async (b) => { kirim.push(b); return { key: { id: 'p1' }, botResponseId: 'r1' } }, edit: async () => { throw new Error('edit ditolak') } },
  })
  await k.perbaruiModul()
  await k.tutup([{ type: 'text', text: 'Halo' }], async (r) => biasa.push(r))
  const semua = biasa.flat()
  assert.ok(semua.some(r => r.type === 'html_file' && r.filename === 'kwee-progress.html'))
  assert.ok(semua.some(r => r.type === 'text' && r.text === 'Halo'), 'jawaban tidak hilang')
})

test('live activity panel and final dossier are visually distinct surfaces', () => {
  const state = { langkah: [{ nama: 'test', ok: true }], fasePernah: ['think'], detik: 3, skillInfo: 'research' }
  const live = renderProgressLiveHtml(state)
  const final = renderProgressHtml(state)
  assert.match(live, /class="comic-frame live"/)
  assert.match(live, /class="track"/)
  assert.match(final, /class="dossier"/)
  assert.match(final, /FINAL DOSSIER/)
  assert.match(final, /--paper:#2b192d/i)
  assert.match(live, /--paper:#fff0f3/i)
  assert.doesNotMatch(final, /class="track"|@keyframes sweep/)
})
