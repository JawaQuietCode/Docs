---
name: rtk-cli
description: Gunakan Rust Token Killer untuk merapikan output perintah coding yang didukung RTK; fallback ke command asli bila RTK tidak tersedia atau tidak mendukung perintah.
triggers: [rtk, rust token killer, hemat token, ringkas output terminal]
priority: 4
---

# RTK CLI

- Pastikan binary tersedia dengan `rtk --version`; bila tidak tersedia, jangan menganggap RTK sudah terpasang.
- Gunakan prefix `rtk` hanya untuk subcommand yang didukung oleh `rtk --help` / dokumentasi RTK, misalnya perintah Git atau pencarian yang punya filter RTK.
- Untuk perintah arbitrary, aplikasi, script, build, test, atau operasi yang butuh stdout/stderr utuh, jalankan command asli. Jangan mengubah semantik command demi penghematan token.
- Jangan jalankan `rtk init -g` pada server Kwee: itu mengubah konfigurasi global agent coding lain dan bukan integrasi tool-loop Kwee.
- Bila RTK tidak ada, lanjutkan dengan command asli dan laporkan hanya bila relevan.
