// kemajuan.js — SATU kartu status HTML yang selalu DIEDIT. Tidak pernah dihapus.
//
// Aturan yang dipegang berkas ini:
//   1. Tidak ada `delete` sama sekali. Pesan yang sudah tampil tidak dihapus,
//      karena "pesan ini telah dihapus" terbaca seperti bot kacau.
//   2. Berpikir + tool + shell digabung dalam SATU pesan (bukan dua pesan
//      terpisah: satu untuk thinking, satu untuk tool).
//   3. Pembaruan memakai EDIT. Kalau server ternyata membuat pesan baru
//      (edit tak didukung), pesan baru itu DIPAKAI sebagai pesan status
//      berikutnya — bukan dihapus. Jadi paling banyak dua pesan, tidak menumpuk.
//   4. Jawaban satu-teks: pesan status DIEDIT menjadi jawaban itu.
//      Jawaban kartu: pesan status DIEDIT menjadi ringkasan langkah, lalu kartu
//      dikirim (kartu tidak bisa diedit dari teks — itu batas WhatsApp, bukan
//      pilihan kita; menghapus dilarang, jadi ringkasan yang dipilih).
//
// Semua operasi best-effort: kegagalan WhatsApp tidak boleh menggagalkan
// pengiriman jawaban.

import { blokBerjalan, blokSelesai, ratakanBalasan, urutkanBalasan, isAssetReply, targetPersen, haluskanPersen } from './progress-card.js'
import { laporanDariKemajuan } from './progress-html.js'

const JEDA_MINIMAL = 2500
// Bukan batas jumlah langkah, hanya pengaman agar tidak ada loop edit liar.
const MAKS_EDIT = 200

export class Kemajuan {
  /**
   * @param {object} opsi
   * @param {string} opsi.chatId
   * @param {object} opsi.sock        koneksi Baileys
   * @param {object} [opsi.quoted]    pesan yang dibalas (untuk kutipan)
   * @param {number} [opsi.tungguMs]  diam dulu sebelum status muncul
   * @param {number} [opsi.jedaMs]    jarak antar pembaruan
   * @param {() => Promise<object|null>} opsi.bacaStatus  ambil langkah terakhir
   * @param {(id: string) => void} [opsi.tandai]   catat pesan milik bot
   * @param {(id: string) => void} [opsi.lupakan]  hapus dari catatan
   * @param {{kirim: (blok: object) => Promise<any>, edit: (id: string, blok: object) => Promise<any>}} [opsi.genai]
   *        jalur kartu GenAI: status dikirim sebagai KARTU, lalu diperbarui
   *        dengan EDIT kartu (protocolMessage MESSAGE_EDIT) — bukan pesan baru.
   */
  constructor({ chatId, sock, quoted, tungguMs = 5000, jedaMs = JEDA_MINIMAL, bacaStatus, tandai, lupakan, genai, modul, laporanHtml = false, laporanMode = 'card', jedaMinimalMs = JEDA_MINIMAL }) {
    this.chatId = chatId
    this.sock = sock
    this.quoted = quoted
    this.tungguMs = Math.max(2000, Number(tungguMs) || 5000)
    // Batas bawah bisa diturunkan lewat config (agent.progressTickMs), tetapi tidak
    // pernah di bawah 1500 ms: edit kartu terlalu rapat berisiko dibatasi WhatsApp.
    const lantai = Math.max(1500, Number(jedaMinimalMs) || JEDA_MINIMAL)
    this.jedaMs = Math.max(lantai, Number(jedaMs) || lantai)
    this.bacaStatus = bacaStatus
    this.tandai = tandai || (() => {})
    this.lupakan = lupakan || (() => {})

    this.genai = genai || null  // { kirim, edit } bila status harus berupa kartu
    this.modul = modul || null  // { kirim(blok[]), edit(id, blok[], opsi) } modul inline di kartu
    this.langkahModul = []
    this.fasePernah = []     // urutan fase yang sudah dilalui (untuk daftar ✓ di kartu)
    this.tickModul = 0       // penghitung edit: menggerakkan spinner & denyut bilah
    this.laporanHtml = Boolean(laporanHtml)  // sertakan laporan progres HTML bila kartu pernah tampil
    // 'card' = tertanam di kartu yang sama (blok html, seperti !ping) — SATU pesan.
    // 'file' = dokumen .html terpisah sesudah jawaban.
    this.laporanMode = laporanMode === 'file' ? 'file' : 'card'
    this.kunciModul = ''
    this.mulaiMs = Date.now()
    this.faseModul = ''
    this.faseMulaiMs = this.mulaiMs
    this.langkahFase = 0
    this.eventSeq = 0
    this.skillInfo = ''
    this.persenModul = 0
    this.kartu = false      // true = status sekarang berupa kartu GenAI
    this.responseId = null  // response_id kartu status (wajib sama saat EDIT)
    this.gagalEdit = 0      // edit kartu gagal berturut-turut
    this.id = null          // id pesan status (satu-satunya yang kita pegang)
    this.teksTerakhir = ''  // hindari edit dengan isi sama
    this.editBisa = true    // false kalau server ternyata tak mendukung edit
    this.editDipakai = 0
    this.langkah = 0        // berapa kali status diperbarui
    this.selesai = false
    this.typingTimer = null
    this.pollTimer = null
    this.mulaiTimer = null
  }

  /** True bila pesan status sudah tampil di chat. */
  get terlihat() {
    return Boolean(this.id)
  }

  /** Mulai indikator "mengetik…" dan jadwalkan status pertama. */
  mulai() {
    const ketik = async (state) => {
      try { await this.sock.sendPresenceUpdate(state, this.chatId) } catch { /* abaikan */ }
    }
    this.ketik = ketik
    ketik('composing')
    // WhatsApp melepas indikator mengetik setelah ~8 detik.
    this.typingTimer = setInterval(() => { if (!this.selesai) ketik('composing') }, 8000)

    this.mulaiTimer = setTimeout(() => {
      if (this.selesai) return
      this.pollTimer = setInterval(() => this.perbarui(), this.jedaMs)
      this.perbarui()
    }, this.tungguMs)
    return this
  }

  /**
   * Susun isi status: berpikir + tool + shell dalam SATU blok, maks 4 baris.
   * Ini yang membuat "thinking" dan "tool/shell" tidak muncul sebagai dua pesan.
   */
  isiStatus(a) {
    // Tampilan status dibikin senada dengan kartu GenAI (bukan gelembung polos):
    // judul bermerek + aksen aksara Tionghoa, langkah berbutir, dan satu baris
    // penutup. Tetap SATU pesan yang diedit — kartu GenAI tidak bisa diedit,
    // jadi meniru rupanya adalah satu-satunya cara tanpa memicu spam.
    const baris = []
    const judul = String(a?.step || 'sedang bekerja').trim()
    baris.push('✦ *𝓴𝔀𝓮𝓮𝔀𝓲𝓷𝓭 𝓫𝓵𝓸𝔀𝓼* · 思考中')
    baris.push(`▸ ${judul}`)
    if (a?.tool) {
      const detail = a.detail ? ` — ${a.detail}` : ''
      baris.push(`▸ \`${a.tool}\`${detail}`)
    }
    if (a?.shell) baris.push(`▸ $ ${a.shell}`)
    const n = this.langkah + 1
    baris.push(`♡ 进行中 · langkah ${n}`)
    return baris.slice(0, 5).join('\n')
  }

  /**
   * Edit kartu status (pesan yang sama). Mengembalikan true bila terkirim.
   * Kegagalan DICATAT (dulu diam-diam) dan setelah 2 kali berturut-turut kita
   * berhenti mengedit supaya tidak membanjiri log/jaringan.
   */
  async editKartu(teks) {
    if (!this.genai?.edit || !this.id || !this.editBisa) return false
    try {
      await this.genai.edit(this.id, { type: 'text', text: teks }, { responseId: this.responseId || undefined })
      this.gagalEdit = 0
      return true
    } catch (e) {
      this.gagalEdit += 1
      console.warn(`[kemajuan] edit kartu gagal (${this.gagalEdit}x):`, e?.message || e)
      if (this.gagalEdit >= 2) {
        this.editBisa = false
        console.warn('[kemajuan] edit kartu dimatikan untuk pesan ini; kartu status dibiarkan apa adanya')
      }
      return false
    }
  }

  /** Kirim/ubah pesan status berdasarkan langkah terakhir dari inti agent. */
  async perbarui() {
    if (this.selesai) return
    if (this.modul) return this.perbaruiModul()
    let a = null
    try { a = await this.bacaStatus() } catch { a = null }
    if (this.selesai || !a) return

    const baris = this.isiStatus(a)
    if (!baris || baris === this.teksTerakhir) return

    if (!this.id) {
      // Kirim pertama: kartu GenAI bila jalurnya tersedia, teks bila tidak.
      if (this.genai?.kirim) {
        try {
          const kartu = await this.genai.kirim({ type: 'text', text: baris })
          const id = kartu?.key?.id || null
          if (id) {
            this.id = id
            this.kartu = true
            this.responseId = kartu?.botResponseId || null
            this.tandai(id)
            this.teksTerakhir = baris
            this.langkah += 1
            return
          }
        } catch (e) { console.warn('[kemajuan] kirim kartu status gagal, pakai teks:', e?.message || e) }
      }
      try {
        const sent = await this.sock.sendMessage(this.chatId, { text: baris }, this.quoted ? { quoted: this.quoted } : {})
        this.id = sent?.key?.id || null
        if (this.id) this.tandai(this.id)
        this.teksTerakhir = baris
        this.langkah += 1
      } catch { /* abaikan */ }
      return
    }

    if (!this.editBisa || this.editDipakai >= MAKS_EDIT) return

    // Kartu: perbarui lewat EDIT kartu (pesan yang sama). Kartu TIDAK bisa
    // diedit sebagai teks biasa, jadi bila edit kartu gagal kita berhenti
    // (dengan log), bukan mencoba edit teks yang pasti ditolak.
    if (this.kartu) {
      if (await this.editKartu(baris)) {
        this.teksTerakhir = baris
        this.editDipakai += 1
        this.langkah += 1
      }
      return
    }

    try {
      const sent = await this.sock.sendMessage(this.chatId, {
        text: baris,
        edit: { remoteJid: this.chatId, id: this.id, fromMe: true },
      })
      // Server membuat pesan BARU (edit tak didukung). Jangan hapus — pakai
      // pesan baru itu sebagai status berikutnya, lalu berhenti mengedit.
      if (sent?.key?.id && sent.key.id !== this.id) {
        this.id = sent.key.id
        this.tandai(this.id)
        this.editBisa = false
      }
      this.teksTerakhir = baris
      this.editDipakai += 1
      this.langkah += 1
    } catch (e) {
      // Klien menolak edit → jangan coba lagi, jangan kirim pesan baru.
      console.warn('[kemajuan] edit teks ditolak:', e?.message || e)
      this.editBisa = false
    }
  }

  /** Modul inline: event tool bertambah, persen dihitung dari fase dan waktu. */
  rekamStatus(a) {
    const fase = String(a.stage || (a.step === 'berpikir' ? 'think' : a.step === 'menyusun balasan' ? 'respond' : 'tool'))
    if (fase !== this.faseModul) {
      this.faseModul = fase
      this.faseMulaiMs = Date.now()
      this.langkahFase = 0
    }
    this.catatFase(fase)

    // Gunakan event log dari backend, bukan hanya snapshot terakhir /status.
    // Polling 2.5 detik dapat melewatkan tool yang cepat bila hanya menyimpan
    // status terkini. tool_start membuat baris; tool_done menutup baris itu.
    if (Array.isArray(a.events)) {
      for (const ev of a.events) {
        const seq = Number(ev?.seq) || 0
        if (!seq || seq <= this.eventSeq) continue
        this.eventSeq = seq
        const eventFase = String(ev.stage || 'tool')
        this.catatFase(eventFase)
        if (eventFase === this.faseModul) this.langkahFase += 1
        // Phase marker adalah status, bukan pekerjaan yang dieksekusi.
        // Hanya event tool_start yang menjadi baris timeline; event selesai
        // memperbarui baris sebelumnya agar setiap operasi tampil satu kali.
        if (ev.kind === 'phase' || eventFase === 'skills' || eventFase === 'respond') continue
        if (ev.kind === 'tool' || ev.kind === 'tool_done') {
          const prior = this.langkahModul.findLast?.(l => l.eventId && l.eventId === ev.event_id)
            || [...this.langkahModul].reverse().find(l => l.nama === String(ev.tool || '') && l.detail === String(ev.detail || '') && l.pending)
          if (prior) {
            prior.pending = false
            prior.ok = ev.ok !== false
          }
          continue
        }
        this.langkahModul.push({
          nama: String(ev.tool || ev.step || 'sedang bekerja'),
          detail: String(ev.tool ? (ev.detail || '') : ''),
          ok: ev.ok !== false,
          pending: ev.kind === 'tool_start',
          eventId: ev.event_id || '',
          t: this.detikModul(),
        })
      }
    } else {
      // Kompatibilitas untuk agent versi lama yang belum mengirim event log.
      // Status fase lama seperti "berpikir" / "menyusun balasan" bukan
      // tool dan tidak boleh menyamar sebagai langkah kerja.
      const namaTool = String(a.tool || a.shell || a.command || '').trim()
      const detailTool = String(a.detail || a.shell || a.command || '').trim()
      const kunci = `${namaTool}|${detailTool}`
      if (namaTool && kunci !== this.kunciModul) {
        this.kunciModul = kunci
        this.langkahFase += 1
        this.langkahModul.push({
          nama: namaTool,
          detail: detailTool,
          ok: a.ok !== false,
          t: this.detikModul(),
        })
      }
    }

    const namaSkill = Array.isArray(a.skill_names) ? a.skill_names.slice(0, 3).join(', ') : ''
    const ringkasSkill = a.skill_info || (a.skill_count ? `${a.skill_count} skill terdeteksi` : '')
    this.skillInfo = [ringkasSkill, namaSkill].filter(Boolean).join(' · ')
    return { fase, skillInfo: this.skillInfo }
  }

  /** Catat fase yang pernah dilalui, sekali saja, sesuai urutan kemunculan. */
  catatFase(fase) {
    const f = String(fase || '')
    if (f && f !== 'done' && !this.fasePernah.includes(f)) this.fasePernah.push(f)
  }

  adopsiStatus(a) {
    if (this.modul && a && typeof a === 'object') this.rekamStatus(a)
  }

  async perbaruiModul() {
    if (this.selesai) return
    let a = null
    try { a = await this.bacaStatus() } catch { a = null }
    if (this.selesai) return
    // Engine tanpa telemetri (mis. OpenHands) atau status yang baru dibersihkan
    // di akhir giliran: tetap hidupkan kartu memakai fase terakhir yang
    // diketahui, supaya bilah dan waktu terus bergerak dan fase tidak mundur.
    if (!a) a = { stage: this.faseModul || 'think', events: [] }

    const { fase, skillInfo } = this.rekamStatus(a)
    const target = targetPersen({ fase, detikFase: (Date.now() - this.faseMulaiMs) / 1000, langkahFase: this.langkahFase })
    // Merayap sedikit tiap edit (makin pelan mendekati ujung) agar bilah tidak
    // terlihat macet saat menunggu LLM; tetap monoton dan maksimal +2 per edit.
    const merayap = 0.8 * (1 - this.persenModul / 100) ** 2
    this.persenModul = haluskanPersen(this.persenModul, target, 2, merayap)
    this.tickModul += 1
    const ok = await this.tampilkanModul(blokBerjalan({
      langkah: this.langkahModul, detik: this.detikModul(), fase, skillInfo,
      persen: this.persenModul, tick: this.tickModul, fasePernah: this.fasePernah,
    }))
    // Kirim pertama gagal: jatuh ke status teks lama, bukan diam.
    if (!ok && !this.id) this.modul = null
  }

  detikModul() {
    return (Date.now() - this.mulaiMs) / 1000
  }

  /** Kirim kartu (pertama) atau EDIT kartu yang sama. True bila berhasil. */
  async tampilkanModul(blok) {
    const kunci = JSON.stringify(blok)
    if (!this.id) {
      try {
        const kartu = await this.modul.kirim(blok)
        const id = kartu?.key?.id || null
        if (!id) return false
        this.id = id
        this.kartu = true
        this.responseId = kartu?.botResponseId || null
        this.tandai(id)
        this.teksTerakhir = kunci
        this.langkah += 1
        return true
      } catch (e) {
        console.warn('[kemajuan] kirim kartu progres gagal, pakai status teks:', e?.message || e)
        return false
      }
    }
    if (!this.editBisa) return false
    if (kunci === this.teksTerakhir) return true
    try {
      await this.modul.edit(this.id, blok, { responseId: this.responseId || undefined })
      this.gagalEdit = 0
      this.teksTerakhir = kunci
      this.editDipakai += 1
      this.langkah += 1
      return true
    } catch (e) {
      this.gagalEdit += 1
      console.warn(`[kemajuan] edit kartu progres gagal (${this.gagalEdit}x):`, e?.message || e)
      if (this.gagalEdit >= 2) this.editBisa = false
      return false
    }
  }

  /**
   * Laporan HTML mandiri dari catatan langkah milik Kemajuan sendiri. Dibuat di
   * sini (bukan menunggu `activity_html` dari Python) supaya tetap ada apa pun
   * enginenya, dan tidak hilang di jalur job background.
   */
  buatLaporanHtml(ringkasan = '') {
    const fase = [...this.fasePernah]
    return {
      type: 'html_file',
      filename: 'kwee-progress.html',
      mimetype: 'text/html',
      caption: 'Laporan progres Kwee · HTML mandiri',
      html: laporanDariKemajuan({
        langkah: this.langkahModul, fasePernah: fase, detik: this.detikModul(),
        skillInfo: this.skillInfo, ringkasan,
      }),
    }
  }

  /** Blok HTML ringkas untuk kartu, transparan dan berada sebelum jawaban. */
  blokLaporan() {
    const f = this.buatLaporanHtml()
    return {
      type: 'html',
      data: { html: f.html, url: 'https://example.com', title: 'Laporan progres', tab_header: 'Progres', trusted_sources: [] },
    }
  }

  /**
   * Penutup: kartu yang SAMA diedit menjadi jawaban. Berkas `.html` laporan
   * progres dikirim SESUDAH jawaban (sebagai dokumen, bukan blok kode/widget).
   * Jawaban dikirim terpisah hanya bila memuat media/widget, atau bila edit
   * benar-benar gagal (dicatat di log, bukan diam).
   */
  async tutupModul(replies, kirim) {
    const sumber = Array.isArray(replies) ? replies : []
    const laporanDariInti = sumber.some(r =>
      r?.type === 'html_file' && /kwee-progress\.html$/i.test(String(r.filename || '')),
    )
    // Pada mode card, laporan lama dari Python diganti laporan transport yang
    // lebih kecil. Dokumen HTML hasil kerja model dipertahankan sebagai preview.
    const tanpaLaporanProgres = this.laporanHtml && this.laporanMode === 'card'
      ? sumber.filter(r => !(r?.type === 'html_file' && /kwee-progress\.html$/i.test(String(r.filename || ''))))
      : sumber
    const daftar = urutkanBalasan(tanpaLaporanProgres)

    const jawaban = []
    const aset = []
    const htmlHasil = []
    const batasPreviewBytes = 160 * 1024
    for (const item of daftar) {
      const blocks = (item?.type === 'richResponse' || item?.type === 'card')
        ? (item.blocks || item.richResponse || [])
        : [item]
      for (const block of blocks) {
        if (!block) continue
        if (
          block.type === 'html_file'
          && !/kwee-progress\.html$/i.test(String(block.filename || ''))
          && typeof block.html === 'string'
          && block.html.trim()
          && Buffer.byteLength(block.html, 'utf8') <= batasPreviewBytes
        ) {
          htmlHasil.push({
            type: 'html',
            data: {
              html: block.html,
              url: 'https://example.com',
              title: String(block.filename || 'Hasil HTML').slice(0, 80),
              tab_header: 'Hasil',
              trusted_sources: [],
            },
          })
        } else if (isAssetReply(block)) {
          aset.push(block)
        } else {
          jawaban.push(block)
        }
      }
    }

    const hasil = blokSelesai({
      langkah: this.langkahModul.length
        ? this.langkahModul
        : htmlHasil.length ? [{ nama: 'HTML', ok: true }] : [],
      detik: this.detikModul(),
      replies: jawaban,
    })

    const tanam = this.laporanHtml && this.laporanMode === 'card'
    const blokJawaban = hasil.ok ? ratakanBalasan(jawaban) : null
    const ringkasanFallback = hasil.ok || htmlHasil.length
      ? []
      : [{ type: 'text', text: '✓ Proses selesai. Jawaban lengkap menyusul di bawah.' }]

    // Urutan final: HTML progres → hasil HTML (jika ada) → jawaban AI biasa.
    // Jadi command/loading dan respon berada di satu kartu, bukan respon lalu file HTML.
    const memakaiHtmlFinal = tanam || htmlHasil.length > 0
    const blokAkhir = memakaiHtmlFinal
      ? [
          ...(tanam ? [this.blokLaporan()] : []),
          ...htmlHasil,
          ...(blokJawaban || ringkasanFallback),
        ]
      : hasil.blok

    if (aset.length) {
      // Selama lampiran lain dikirim, kartu yang sama tetap menampilkan HTML
      // progres aktif dan jawaban; file HTML kecil sudah menjadi preview di card.
      const statusAsset = blokBerjalan({
        langkah: this.langkahModul,
        detik: this.detikModul(),
        fase: 'asset',
        skillInfo: this.skillInfo,
        persen: 98,
        tick: ++this.tickModul,
        fasePernah: this.fasePernah,
      })
      const statusTampil = await this.tampilkanModul([
        ...statusAsset,
        ...(blokJawaban || ringkasanFallback),
      ])
      if (!hasil.ok && jawaban.length) await kirim(jawaban)
      await kirim(aset)

      if (this.laporanHtml && this.laporanMode === 'file' && !laporanDariInti) {
        await kirim([this.buatLaporanHtml()])
      }
      if (statusTampil) {
        const ujung = await this.tampilkanModul(blokAkhir)
        if (!ujung && tanam) await kirim([this.buatLaporanHtml()])
      } else {
        if (tanam) await kirim([this.buatLaporanHtml()])
        if (blokJawaban && jawaban.length) await kirim(jawaban)
      }
      return
    }

    // Mode file tetap tersedia sebagai pilihan eksplisit; mode bawaan adalah
    // card, sesuai alur satu pesan yang diminta.
    if (this.laporanHtml && this.laporanMode === 'file' && !laporanDariInti) {
      const statusAsset = blokBerjalan({
        langkah: this.langkahModul,
        detik: this.detikModul(),
        fase: 'asset',
        skillInfo: this.skillInfo,
        persen: 98,
        tick: ++this.tickModul,
        fasePernah: this.fasePernah,
      })
      const blokTeks = blokJawaban || ringkasanFallback
      const statusTampil = await this.tampilkanModul([...statusAsset, ...htmlHasil, ...blokTeks])
      await kirim([this.buatLaporanHtml()])
      if (statusTampil) {
        const akhir = await this.tampilkanModul([...htmlHasil, ...blokTeks])
        if (akhir && hasil.ok) return
      }
      if (jawaban.length) await kirim(jawaban)
      return
    }

    const tersunting = await this.tampilkanModul(blokAkhir)
    if (tersunting && (hasil.ok || tanam || htmlHasil.length)) return
    if (!tersunting) console.warn('[kemajuan] kartu tidak bisa diedit ke jawaban akhir; jawaban dikirim terpisah')
    if (tanam && !tersunting) await kirim([this.buatLaporanHtml()])
    await kirim(daftar)
  }

  /** Ubah pesan status menjadi teks tertentu (tanpa pernah menghapusnya). */
  async ubah(teks) {
    const isi = String(teks || '').trim()
    if (!this.id || !isi) return false
    if (this.kartu) {
      // Kartu hanya bisa diedit sebagai kartu. Gagal → false (pemanggil mengirim
      // jawaban biasa), TANPA fallback ke edit teks yang pasti ditolak.
      const ok = await this.editKartu(isi)
      if (ok) this.teksTerakhir = isi
      return ok
    }
    try {
      const sent = await this.sock.sendMessage(this.chatId, {
        text: isi,
        edit: { remoteJid: this.chatId, id: this.id, fromMe: true },
      })
      if (sent?.key?.id && sent.key.id !== this.id) {
        // Edit menghasilkan pesan baru: adopsi, jangan hapus.
        this.id = sent.key.id
        this.tandai(this.id)
        this.editBisa = false
      }
      this.teksTerakhir = isi
      return true
    } catch {
      this.editBisa = false
      return false
    }
  }

  /** Hentikan timer & indikator mengetik. */
  hentikan() {
    this.selesai = true
    clearTimeout(this.mulaiTimer)
    clearInterval(this.pollTimer)
    clearInterval(this.typingTimer)
    try { this.ketik?.('paused') } catch { /* abaikan */ }
  }

  /**
   * Tutup sesi lalu kirim jawaban akhir — tanpa menghapus pesan apa pun.
   *
   * @param {object[]} replies balasan yang sudah final
   * @param {(replies: object[]) => Promise<void>} kirim pengirim balasan biasa
   * @param {(teks: string) => string} [rapikan] pembersih teks
   */
  async tutup(replies, kirim, rapikan = (t) => t) {
    this.hentikan()
    if (this.modul && this.id) return this.tutupModul(replies, kirim)

    const daftar = Array.isArray(replies) ? replies : []
    const satuTeks = daftar.length === 1 && daftar[0]?.type === 'text' && String(daftar[0]?.text || '').trim()

    // Kasus paling umum: satu jawaban teks → pesan status DIEDIT jadi jawaban.
    if (this.id && satuTeks) {
      const isi = rapikan(String(daftar[0].text))
      if (await this.ubah(isi)) return
      // Edit ditolak server: kirim jawaban seperti biasa. Status lama dibiarkan
      // (tidak dihapus) — lebih baik satu baris sisa daripada "pesan dihapus".
    }

    // Jawaban bukan satu teks (kartu/gambar): status dijadikan ringkasan
    // singkat, lalu jawabannya dikirim. Tidak ada penghapusan.
    if (this.id) {
      const n = this.langkah || 1
      await this.ubah(`✦ *𝓴𝔀𝓮𝓮𝔀𝓲𝓷𝓭 𝓫𝓵𝓸𝔀𝓼* · 完成\n✓ ${n} langkah · detailnya di kartu bawah\n♡ 由 kwee 组装`)
    }

    await kirim(daftar)
  }

  /** Batalkan tanpa mengirim apa pun (pesan status dibiarkan apa adanya). */
  async batal() {
    this.hentikan()
  }
}

export default Kemajuan
