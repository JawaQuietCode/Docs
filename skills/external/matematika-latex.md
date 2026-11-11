---
name: matematika-latex
description: Menulis rumus & perhitungan matematika agar tampil benar
triggers: [rumus, matematika, math, latex, persamaan, integral, turunan, matriks, limit, soal, hitung, aljabar, geometri, statistika, pecahan]
priority: 58
---

# Matematika & LaTeX

## Menulis rumus
WhatsApp tidak merender LaTeX mentah, jadi pakai salah satu (dideteksi otomatis):
- Inline: `$x^2 + y^2 = z^2$`
- Blok: `$$ \int_0^1 x^2 \, dx = \frac{1}{3} $$`
- Fence: ` ```latex ` atau ` ```math ` (isinya RUMUS, bukan dokumen).

Rumus akan dirender jadi gambar dan ditampilkan sebagai rumus asli. **Jangan**
menulis rumus sebagai teks polos ("integral dari x kuadrat") bila notasi jelas
lebih baik.

## Mengerjakan soal
1. Tulis **langkah demi langkah**, bukan hanya jawaban akhir.
2. Sebutkan rumus yang dipakai, lalu substitusi angka, lalu hasil.
3. Cek satuan & tanda. Bila hasil terasa janggal, periksa ulang.
4. Untuk banyak soal (mis. 20 nomor), kelompokkan rapi — jangan satu blok raksasa.

## Hati-hati
- Dokumen LaTeX **utuh** (`\documentclass`, `\begin{document}`) tetap dikirim
  sebagai kode, bukan dirender — itu memang bukan rumus.
