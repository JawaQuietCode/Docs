---
name: ringkasan-grup
description: Ringkasan harian chat grup dengan opt-in dan retensi terbatas
triggers: [ringkasan grup, ringkas grup, rangkum grup, summary grup, group summary]
priority: 86
---

Perekaman tidak aktif secara default. Di grup target, owner dapat mengetik `!ringkasan on HH:MM` untuk opt-in dan membuat job harian; bot mengumumkan perekaman teks di grup. Waktu mengikuti zona lokal server. `!ringkasan status` memeriksa status dan `!ringkasan off` menghentikan perekaman/jadwal. Pesan teks disimpan maksimal tujuh hari, tanpa media dan dengan redaksi token sederhana. Untuk ringkasan dari job yang aktif, gunakan tool `group_summary`; rangkum topik, keputusan, dan tugas tanpa menampilkan pseudonim atau menebak.