---
name: status-grup
description: Kirim SWGC (status di dalam grup) — video/gambar/teks yang tampil sebagai status grup, bukan chat biasa
triggers: [swgc, status grup, swgc, group status, status di grup, groupStatus, status grup video, story grup, bikin status grup, kirim status grup]
priority: 58
---

# Status Grup (SWGC)

**SWGC = "status di dalam grup"**: media/teks tampil sebagai **status grup**
(bukan chat biasa), dan hilang/berlaku seperti status. Ini fitur khusus yang
**didukung fork Baileys** yang dipakai bot (`@itsliaaa/baileys`), bukan fitur
WhatsApp standar.

Jangan bilang "WhatsApp tidak punya fitur ini" — **bot ini punya.** Kalau ada
yang menyebut SWGC/status grup, langsung pakai panduan ini.

## Cara kerja (mekanisme)
Cukup tambahkan opsi **`groupStatus: true`** pada isi pesan. Baileys otomatis:
1. menandai `contextInfo.isGroupStatus = true`, dan
2. membungkus pesan jadi `groupStatusMessageV2`, serta
3. menambah atribut `is_group_status = true` pada stanza.

Sumber: `node_modules/@itsliaaa/baileys/lib/Utils/messages.js`
(Lia@Changes 31-01-26) dan `lib/Socket/messages-send.js` (Lia@Changes 12-05-26).

## Aturan penting
- **Hanya untuk grup** (`…@g.us`). Status grup tidak berlaku untuk chat pribadi.
- Bot **harus sudah jadi anggota** grup tujuan (kalau belum, tidak bisa kirim).
- Ini **bukan** Status/Story biasa ke kontak — Status/Story ke kontak hanya bisa
  dari HP pemilik, bot tidak punya aksinya.
- **Caption: hanya isi kalau diminta.** Kalau pemilik tidak menyebut caption/teks
  tertentu, **JANGAN mengarang caption** — kirim tanpa caption (kosong). Jangan
  menambahkan judul, emoji, atau teks apa pun atas inisiatif sendiri. Media
  (video/gambar) tetap boleh tampil tanpa caption.

## Cara pakai

### 1. Lewat plugin (cara utama)
Di dalam plugin, `ctx.wa.send(to, content)` menerima opsi `groupStatus`:

```js
// status grup berupa VIDEO
await ctx.wa.send('1203…@g.us', {
  video: { stream: fs.createReadStream('workspace/swgc.mp4') },
  caption: '🔥 Judul',
  groupStatus: true,
})

// status grup berupa GAMBAR
await ctx.wa.send(grupId, {
  image: { stream: fs.createReadStream('workspace/foto.jpg') },
  caption: 'Halo',
  groupStatus: true,
})

// status grup berupa TEKS
await ctx.wa.send(grupId, { text: 'Halo semua', groupStatus: true })
```

### 2. Lewat skrip Node langsung (tanpa plugin)
```js
await sock.sendMessage(grupId, { video: {...}, caption, groupStatus: true })
```

## Wajib: media harus H.264/AAC
Video yang tampil inline **harus H.264 + AAC** dalam MP4. Bila sumbernya HEVC
(H.265), VP9, atau AV1 → **transcode dulu** dengan ffmpeg:

```bash
ffmpeg -i in.mp4 -c:v libx264 -preset medium -crf 20 -c:a aac -b:a 128k \
  -movflags +faststart out.mp4
```

Plugin **`send-media`** sudah menangani remux/transcode H.264 otomatis — pakai
itu untuk menyiapkan file, lalu kirim dengan `groupStatus: true`.

## Alur kerja yang disarankan
1. Pastikan media sudah H.264/AAC (mis. lewat `send-media` atau ffmpeg).
2. Kirim ke ID grup (`…@g.us`) dengan `groupStatus: true`.
3. **Verifikasi** dari hasil kirim (ada `id`, `to`, ukuran) — jangan hanya mengklaim.
4. Bila status belum muncul di HP, minta tarik-refresh grup (butuh beberapa detik).

## Kesalahan yang harus dihindari
- ❌ Bilang "status grup tidak ada / tidak bisa".
- ❌ Kirim sebagai pesan biasa padahal diminta SWGC.
- ❌ Kirim video HEVC → tidak terputar; transcode ke H.264 dulu.
- ❌ Kirim ke chat pribadi lalu berharap jadi status grup.
- ❌ **Mengarang caption** saat pemilik tidak minta caption — biarkan kosong.
  Hanya pakai caption bila pemilik menyebut teksnya secara eksplisit.
