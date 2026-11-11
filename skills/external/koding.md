---
name: koding
description: Menulis, menjalankan, dan memperbaiki kode
triggers: [kode, code, script, python, javascript, node, program, fungsi, function, bug, error, debug, perbaiki, refactor, kelas, class, api, sql, regex]
priority: 70
---

# Menulis & memperbaiki kode

## Alur kerja
1. **Pahami dulu.** Baca berkas terkait sebelum mengubah (pakai `files read`).
   Jangan menebak isi berkas.
2. **Ubah sekecil mungkin.** Perbaiki akar masalah, jangan menulis ulang seluruh
   berkas bila tak perlu.
3. **Jalankan untuk membuktikan.** Setelah menulis, jalankan kode/tes lewat
   `shell`. Jangan mengklaim "berhasil" tanpa output nyata.
4. **Tunjukkan hasilnya.** Sertakan potongan penting + perintah menjalankannya.

## Menulis kode
- Utamakan **stdlib** bila cukup; tambah dependensi hanya bila perlu.
- Tangani error (jangan `except: pass`). Beri pesan error yang berguna.
- Beri nama jelas; komentar untuk bagian yang tidak jelas, bukan yang jelas.
- Untuk file panjang, tulis via tool `files` (hindari heredoc yang mudah rusak).

## Memperbaiki bug
- **Reproduksi dulu** (jalankan sampai errornya muncul), baru perbaiki.
- Cari akar masalah, bukan gejala. Sebutkan penyebabnya di balasan.
- Setelah perbaikan, jalankan lagi untuk membuktikan error hilang + tak ada
  regresi (jalankan tes bila ada).

## Menjalankan
- Perintah jalan di folder kerja pemilik. Pakai `timeout` untuk tugas lama.
- Tugas berat (install/unduh) → beri tahu pemilik dulu, lalu laporkan kemajuan.
