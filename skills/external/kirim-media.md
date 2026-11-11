---
name: kirim-media
description: Mengirim gambar/video/audio/stiker sebagai media, bukan dokumen
triggers: [kirim gambar, kirim video, kirim audio, voice note, sticker, stiker, media, foto, mp4, mp3, inline, dokumen, document, thumbnail, kompres]
priority: 60
---

# Mengirim media di WhatsApp

Tujuan: penerima bisa **memutar/melihat langsung** — bukan kartu "Unduh".

## Cara
- Pakai plugin **`send-media`** (bila ada) atau tool `wa`:
  `plugin_run {name:'send-media', args:{path|url, type?, caption?, maxMb?, ptt?, gif?}}`
- `type`: `image` | `video` | `audio` | `sticker` | `document`.
- Voice note → `ptt: true`. Stiker → `sticker`. GIF → `gif: true`.

## Agar bisa diputar inline
- **Video:** codec **H.264 + AAC** dalam wadah MP4. Bila sumbernya codec lain
  (mis. H.265/VP9), transcode dulu: `ffmpeg -i in.mp4 -c:v libx264 -c:a aac out.mp4`.
- **Ukuran:** WhatsApp menolak media terlalu besar → kompres otomatis bila
  > `maxMb` (default 15 MB). Kecilkan bitrate/resolusi, jangan potong durasi
  kecuali diminta.
- **Thumbnail & durasi:** sertakan agar preview rapi (ffmpeg menghasilkan
  thumbnail; durasi diisi otomatis).
- **Audio:** MP3/AAC; voice note pakai Opus dalam OGG (`ptt`).

## Verifikasi
Setelah kirim, **buktikan** dari output (mis. `type: video`, ukuran, `to`) —
jangan hanya mengklaim. Bila gagal, sebutkan error + coba format lain.
