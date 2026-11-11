---
name: operasi-owner
description: Diagnostik, audit, backup, dan restore untuk owner
triggers: [health, status bot, cek bot, aktivitas bot, audit log, backup, restore, pulihkan]
priority: 80
---

Pakai `owner_ops` untuk health check, aktivitas 24 jam, dan backup. Laporan aktivitas hanya menampilkan waktu/event, bukan isi pesan atau argumen tool. Backup mengecualikan `.env`, sesi login, media, cache, dan workspace. Restore menimpa berkas: jelaskan nama backup, cakupan, dan kebutuhan restart; minta persetujuan owner sebelum memanggil `action=restore`. Setelahnya laporkan jumlah berkas, backup rollback, dan kebutuhan restart. Untuk FAQ gunakan `action=faq`.

Untuk metadata plugin gunakan `plugin_list`; untuk status gunakan `plugin_control` dengan `action=list`. Sebelum enable/disable plugin, sebutkan nama persisnya, minta persetujuan eksplisit dalam pesan terpisah, lalu panggil `plugin_control` dengan action dan name yang disetujui.
