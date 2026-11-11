---
name: encode-decode
description: Enkripsi dan dekripsi Python level 1-5 (base64/hex/caesar, vigenere/atbash/rail fence, affine/xor, AES-256-GCM, dan rantai ChaCha20+AES) dengan analisis otomatis untuk level klasik
triggers: [enkripsi, dekripsi, encrypt, decrypt, encode, decode, cipher, cryptografi, sandi, caesar, vigenere, base64, hex, aes, kunci, chiper]
priority: 70
---
# Encode / Decode Level 1-5

Skill ini menjalankan `scripts/codec.py` (dan `scripts/decode.py` untuk dekripsi cepat). Hasil harus berasal dari eksekusi nyata: jalankan dulu, baru laporkan keluarannya. Jangan menebak hasil dekripsi.

## Level

| Level | Nama | Metode | Catatan |
|---|---|---|---|
| 1 | mudah | base64, hex, caesar, rot13 | tanpa kunci, kecuali caesar (`--key` angka) |
| 2 | sedang-mudah | vigenere, atbash, railfence | vigenere butuh `--key` kata; railfence `--key` jumlah rel |
| 3 | sedang | affine, xor | affine `--key "a,b"` (a koprima dengan 26); xor `--key` teks |
| 4 | sulit | aes | AES-256-GCM, kunci turunan PBKDF2-SHA256 600.000 iterasi dari frasa sandi `--key` |
| 5 | hampir-mustahil | rantai | ChaCha20-Poly1305 lalu AES-256-GCM, dua kunci acak 256-bit |

## Perintah

```bash
python3 scripts/codec.py levels
python3 scripts/codec.py enc --level 1 --metode base64 --text "halo"
python3 scripts/codec.py dec --level 1 --metode base64 --text "aGFsbw=="
python3 scripts/codec.py enc --level 4 --key "frasa rahasia" --in pesan.txt --out pesan.enc
python3 scripts/codec.py dec --level 4 --key "frasa rahasia" --in pesan.enc
python3 scripts/codec.py enc --level 5 --in data.bin --kunci-out kunci.json --out data.enc
python3 scripts/codec.py dec --level 5 --key kunci.json --in data.enc --out data.bin
python3 scripts/codec.py auto --text "<teks sandi klasik>"
```

Output `enc` / `dec` ke stdout, meta (level, metode) ke stderr. Dengan `--out`, hasilnya masuk berkas dan stdout berisi JSON ringkas. Kegagalan selalu berupa `{"ok": false, "error": ...}` dengan exit code 1.

## Alur kerja

1. Cek input: teks UTF-8 atau biner. Untuk biner, gunakan base64, aes, atau rantai.
2. Untuk teks tak dikenal, jalankan `auto`. Hasil diurutkan dari skor kemiripan bahasa. Kandidat dengan skor tinggi tetap perlu diperiksa dengan membaca hasilnya, karena skor hanya perkiraan.
3. Untuk level 4 dan 5, dekripsi hanya bisa dilakukan dengan kunci yang benar. Jika kunci tidak ada, nyatakan itu dan jangan mengarang kunci.
4. Level 5 menulis kunci ke berkas dengan izin 600. Jangan menampilkan isi kunci di chat kecuali pemilik memintanya.
5. Laporkan hasil apa adanya: metode yang dipakai, keberhasilan autentikasi GCM, dan batasan yang ditemui.

## Batasan

- Level 4 dan 5 memakai algoritme modern. Tanpa kunci tidak ada jalur dekripsi yang sah.
- `auto` hanya mencakup metode klasik (level 1 dan 3 tanpa kunci, ditambah atbash dan caesar).
- Skill ini tidak berisi jawaban untuk tantangan tertentu. Pelajari polanya dari keluaran alat.
