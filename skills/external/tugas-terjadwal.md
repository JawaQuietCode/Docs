---
name: tugas-terjadwal
description: Menjadwalkan tugas berkala (cron) untuk pemilik
triggers: [jadwal, jadwalkan, setiap hari, tiap hari, reminder, pengingat, ingatkan, cron, berkala, otomatis tiap, mingguan, harian]
priority: 48
---

# Tugas terjadwal (tool `cron`)

Ekspresi cron 5-field: `menit jam tanggal bulan hari` (waktu lokal pemilik).
- `0 8 * * *` → tiap hari 08:00
- `0 9 * * 1` → tiap Senin 09:00
- `*/30 * * * *` → tiap 30 menit

## Jenis
- `kind: send` — kirim pesan berkala: `args: {to, text}`.
- `kind: agent` — jalankan tugas agent berkala: `args: {prompt}`.

## Aturan
- **Konfirmasi jadwalnya dulu** bila ambigu ("tiap hari" jam berapa?). Sebut
  waktu + zona yang dipakai saat mengonfirmasi.
- Tugas milik pemilik ini saja; jalankan di ruangan pemilik ini.
- Setelah dibuat, sebutkan **kapan berikutnya** akan jalan supaya jelas.
- Untuk mengingatkan sesuatu satu kali, pertimbangkan apakah cukup mengingat
  fakta (tool `memory`) — cron untuk yang benar-benar berulang.
