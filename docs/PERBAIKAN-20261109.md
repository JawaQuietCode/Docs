# Perbaikan 2026-11-09 (v0.2.1)

Tiga hal: crash saat start, persona yang longgar, dan tampilan GenAI Markdown.

## 1. Crash `FastAPI()` saat start (agent mati sebelum health check)

Gejala di console panel: traceback berakhir di
`fastapi/applications.py:896 → routing.APIRouter()` lalu
`fastapi/routing.py:835 → super().__init__(`; status panel `Starting → Offline`.

**Akar masalah.** `fastapi==0.115.0` hanya cocok dengan `starlette<0.39`. Paket
yang dipasang `setup-intelligence.sh` (`mcp`, `graphifyy[mcp]`, `langchain`)
menarik Starlette naik ke versi yang sudah membuang argumen `on_startup` /
`on_shutdown`. pip hanya mencetak peringatan "incompatible" — tidak membatalkan —
sehingga `FastAPI()` meledak saat `agent.main` diimpor.

**Perbaikan.**
- `scripts/ensure_web_stack.py` (baru): benar-benar membuat `FastAPI()` di
  subproses; bila gagal, upgrade `fastapi + starlette + uvicorn` ke pasangan yang
  cocok, dengan cadangan pin klasik. Dipanggil `start.sh` tepat sebelum uvicorn,
  jadi paket apa pun yang dipasang belakangan tidak bisa merusaknya lagi.
- Pin kaku `fastapi==0.115.0` di `start.sh` / `setup.sh` diganti `fastapi>=0.115.0`
  (resolver memilih Starlette yang cocok).
- `agent/main.py`: `@app.on_event("startup"/"shutdown")` (dihapus dari Starlette 1.x)
  diganti `lifespan`, yang jalan di FastAPI lama maupun baru.

Tidak perlu hapus apa pun di server: cukup unggah zip ini lalu start ulang;
penjaga akan memperbaiki paket yang sudah terlanjur rusak di `.pyuser`.

## 2. Persona (PERSONA LOCK)

**Akar masalah.**
1. Persona hanya di DEPAN prompt; rules, skill, dan memori di belakangnya lebih
   "dekat" ke giliran bicara, jadi menang.
2. Tiga tempat melarang persona secara langsung: `WHATSAPP_HINT` ("jangan
   memaksakan persona"), `rules.md` ("jangan paksakan persona, rayuan, emoji") dan
   `JOURNALISM_RULES` (tanda tangan wajib `KWEE ◈ FIELD JOURNAL`). Persona apa pun
   yang dipilih lewat `!prompt set …` otomatis kalah.
3. Setelah tool jalan, konteks terakhir adalah JSON hasil tool, bukan persona.
4. Tidak ada pemeriksaan keluaran.

**Perbaikan** (agnostik terhadap persona yang aktif — `persona`, `persona.mommy-girlfriend`, atau override tenant):
- `agent/persona_guard.py` (baru): blok **KUNCI PERSONA** di EKOR system prompt,
  **pengingat sementara** sebelum model menulis setelah hasil tool (tidak disimpan
  ke riwayat), dan **pemeriksa keluaran** (mengaku Claude/GPT/"model bahasa", atau
  ditutup "semoga membantu" / "ada yang bisa kubantu lagi?"). Satu nudge tulis-ulang
  per turn, lalu lolos. Tidak menegur bila pengguna memang bertanya soal model.
- Larangan persona dihapus dari `WHATSAPP_HINT`, `rules.md`, `JOURNALISM_RULES`;
  `rules.md` kini punya bagian **Persona** (suara = milik persona; aturan = isi,
  keamanan, tool, tata letak; kejujuran tetap menang atas karakter).
- Aturan format editorial dipindah dari `persona.md` ke `rules.md`, supaya tetap
  berlaku saat persona diganti (dulu persona lain kehilangan format kartu).
- Saklar: `agent.personaLock: true` di `config/default.yaml`.

## 3. Aurora Editorial (GenAI Markdown)

`transport/api/markdown-layout.js` + `airich.js`:
- **Callout** GitHub-style → label berwarna + isi native:
  `> [!NOTE|TIP|IMPORTANT|WARNING|DANGER|SUCCESS|QUOTE] judul opsional`.
- **Penomoran bagian** `01 / 04` pada H2 untuk artikel ≥ 3 bagian; H2 "Sumber"
  tampil sebagai label baja yang tenang; H4–H6 jadi baris tebal (dulu `####` bocor
  mentah ke kartu).
- **Napas antar bagian**: spacer sebelum H1/H2 (kecuali setelah pemisah).
- **Poles tipografi** di luar kode: `->` `=>` → → ⇒, `...` → …, `- [ ]`/`- [x]` →
  ☐/☑, `<br>` → baris baru. URL, kode inline, dan fenced code tidak disentuh.
- **Keterangan kode**: `⌘ python · 14 baris` di atas blok kode ≥ 4 baris.
- **Tabel lebar** ditumpuk sebagai daftar rekaman berbullet.
- Palet "Aurora": tinta `#0A1822`, teal sinyal `#A8EADC`, tembaga redup `#F0D3B4`.

## ◈ Kontribusi Claude (bonus) — "Reading Map"

Judul `#` pada jawaban panjang mendapat sublabel `◷ 3 mnt baca · 4 bagian`
(tidak muncul untuk jawaban pendek; blok kode tidak dihitung). Matikan dengan
`KWEE_READING_MAP=0`. Kode: `readingMap()` di `markdown-layout.js`, ditandai
*contributed by Claude (Anthropic, Sonnet 5.5)*. Bonus kedua: seluruh kosakata
callout didokumentasikan di `prompts/rules.md` agar model memakainya dengan hemat.

## Pengujian

- `tests/test_persona_guard.py` (9 tes): deteksi keluar-persona, nudge sekali,
  tidak berulang, nonaktif bila `persona_lock=False`.
- `tests/aurora-style.test.js` (12 tes): callout, H4–H6, poles tipografi aman
  terhadap kode/URL, ordinal, caption, tabel, peta baca, dan render end-to-end
  `buildCardParts` (dilewati otomatis bila `@itsliaaa/baileys` belum terpasang).
- Semua tes Node lama tetap lulus. Dua tes Python (`test_afk_api`,
  `test_runtime_guards`) butuh `fastapi`/`httpx` terpasang; gagal di lingkungan
  tanpa paket itu sejak zip asli.

> Catatan jujur: perbaikan #1 dirancang dari analisis traceback + versi paket,
> bukan direproduksi di sini (lingkungan pengujian tanpa jaringan/FastAPI). Cek
> baris `[web-stack] OK — fastapi … / starlette …` di console saat start.
