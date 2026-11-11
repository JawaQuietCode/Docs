# Dokumen grup (untuk `!grup` dan admin grup)

Folder ini menjelaskan cara menyiapkan bahan agar kweewind blows bisa menjawab
pertanyaan anggota grup dari **dokumen asli**, bukan karangan.

## 1. Taruh berkasnya

Buat satu folder per ruangan, namanya bebas (dipakai apa adanya sebagai kunci):

```
data/knowledge/
├── grup-jualan/
│   ├── harga.md          ← daftar harga & paket
│   ├── aturan.md         ← aturan grup (dipakai `!grup aturan`)
│   ├── sapaan.md         ← teks sambutan anggota baru
│   └── faq.md            ← pertanyaan yang sering muncul
└── grup-komunitas/
    └── panduan.md
```

Berkas yang dikenali: `.md`, `.txt`, `.json`, `.csv`, `.yaml`, `.yml`.
Berkas lebih dari 400 KB dilewati supaya jawaban tetap cepat.

Bisa dibuat dari WhatsApp tanpa menyentuh server:

```
!grup indeks        ← bangun ulang indeks setelah menambah berkas
!grup daftar        ← lihat berkas yang sudah terbaca
!grup tanya berapa harga paket B?
```

Atau lewat agent: "tulis data/knowledge/grup-jualan/harga.md berisi …" — agent
memakai tool `files` (yang memverifikasi tulisnya dengan SHA-256).

## 2. Bentuk isi yang bagus

Tulis seperti catatan yang dibaca cepat: satu topik per baris, angka eksplisit,
tanpa basa-basi.

```markdown
# Harga

- Paket A — Rp100.000/bulan — 1 nomor, 500 pesan/hari
- Paket B — Rp150.000/bulan — 3 nomor, tanpa batas pesan
- Paket C — Rp400.000/bulan — 10 nomor + prioritas balasan

Pembayaran: transfer BCA 1234567890 a/n PT Contoh.
```

Yang bikin jawaban jelek: paragraf panjang tanpa angka, tabel bergambar,
atau istilah internal yang tidak dipakai anggota.

## 3. Sambutan anggota baru

`sapaan.md` mendukung dua penanda: `{nama}` dan `{grup}`.

```markdown
Hai {nama} ♡ selamat datang di {grup}.
Baca aturan yang dipin dulu ya. Kalau mau tanya harga, tulis "harga" —
aku jawab dari daftar yang resmi.
```

Nyalakan sambutan otomatis di `config/default.yaml`:

```yaml
groupAdmin:
  welcome: true
```

## 4. Penjaga spam

`groupAdmin.autoSpam: true` membuat setiap pesan grup dinilai heuristik
(3+ tautan, mayoritas kapital, frasa penipuan, mention massal, karakter
berulang). Bila terindikasi spam, bot mengirim kartu peringatan ke grup.

Bot **tidak menghapus** pesan siapa pun. Penghapusan tetap dilakukan owner.

## 5. Batas yang perlu diketahui

- Pencarian berbasis kata, bukan makna. Pertanyaan "berapa duitnya?" tidak akan
  menemukan "harga" — ajari anggota memakai kata yang ada di dokumen, atau
  tambahkan baris sinonim di `faq.md`.
- Satu jawaban memuat maksimal 3 potongan (bisa diubah lewat argumen `limit`).
- Dokumen tidak boleh berisi data pribadi anggota; berkas ini dibaca model saat
  menjawab.
