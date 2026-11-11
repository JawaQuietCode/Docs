---
name: pengingat-kontekstual
description: Menawarkan pengingat dari tanggal atau tenggat di percakapan
triggers: [ingatkan, pengingat, reminder, tenggat, deadline, besok, lusa, jadwalkan]
priority: 84
---

Jika percakapan menyebut tanggal/tenggat yang relevan, tawarkan pengingat singkat; jangan membuatnya otomatis. Setelah owner setuju, pastikan tanggal, jam, zona waktu, tujuan chat, dan teks pengingat. Gunakan `cron` dengan `kind=send` untuk pengingat sederhana. Ubah/hapus job hanya sesuai permintaan dan laporkan ID serta jadwal hasilnya.