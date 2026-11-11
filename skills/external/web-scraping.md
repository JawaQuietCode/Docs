---
name: web-scraping
description: Mengambil data dari web (scrape, API, unduh)
triggers: [scrape, scraping, ambil data, crawling, crawl, unduh, download, api, json, html, parse, tiktok, youtube, instagram, twitter, harga, berita, curl]
priority: 65
---

# Mengambil data dari web

## Urutan yang disarankan
1. **`web_fetch`** dulu untuk halaman sederhana (cepat, bersih).
2. Bila butuh interaksi/JS → `shell` dengan `curl`/`node` + library (mis.
   `axios`, `cheerio`, `puppeteer`).
3. Bila butuh banyak halaman → kerjakan lewat `subagent` atau tulis skrip yang
   menyimpan hasil bertahap ke berkas (jangan simpan semua di memori).

## Praktik baik
- **Hormati situs.** Beri jeda antar-request (jangan spam), set `User-Agent`
  wajar, jangan menembus login/paywall.
- **Simpan hasil ke berkas** (`files write` / skrip), lalu ringkas. Jangan
  kirim ribuan baris mentah ke chat.
- **Tangani kegagalan:** timeout, HTTP 4xx/5xx, halaman kosong. Retry terbatas
  dengan backoff.
- **Video/media besar** → unduh ke folder kerja, lalu kirim sebagai media
  (lihat skill `kirim-media`), bukan sebagai dokumen.

## Unduh media (video/audio) — untuk sumber yang SAH
- **Yang sah**: konten milikmu sendiri, domain publik, atau platform berlisensi
  (akunmu di YouTube/Vimeo/IG, materi CC, dsb). Kalau tautannya jelas mengarah ke
  konten berhak cipta milik orang lain di situs bajakan, **jangan unduh/kirim** —
  tawarkan alternatif legal atau bantu hal lain.
- Pakai `yt-dlp` (sudah tersedia; `yt-dlp -f "bv*+ba/b" -o 'f.%(ext)s' <url>`).
- Transcode ke H.264/AAC dengan `ffmpeg` supaya bisa diputar inline:
  `ffmpeg -i in.mp4 -c:v libx264 -c:a aac out.mp4`. `ffprobe` untuk cek durasi/codec.
- Setelah terunduh: kirim via plugin `send-media` / tool `wa` (lihat skill
  `kirim-media`), jangan sebagai dokumen, supaya bisa diputar langsung.
