# Fitur #38 — blok kaya pada RESPONS DEFAULT (manifest untuk reviewer)

## Masalah
Respons default bot hanya menghasilkan **GenAIMarkdownTextUXPrimitive** (teks
Markdown). User minta primitif kaya (product / footer action / metadata /
quickreply) juga muncul dari respons biasa, bukan hanya lewat fence `wa:*`.

## Perubahan (HANYA di lapisan Python `agent/format.py`, + prompt, + tes)
Tidak ada perubahan wire/skema. Blok yang dihasilkan sudah lama didukung
transport (`transport/api/airich.js`) — `product`/`footer`/`metadata`/
`quickreply` ada di `CARD_KINDS` dan `RICH_PASSTHROUGH`.

### `agent/format.py`
1. **Regex baru** (setelah `_MD_LINK_RE`):
   `_PROD_WORDS_RE`, `_CTA_WORDS_RE`, `_QR_WORDS_RE`, `_META_WORDS_RE`,
   `_INLINE_META_RE`, `_PRICE_RE`.
2. **`_heading_kind`** — sekarang mengenali kind tambahan dengan prioritas:
   `sources` > `suggest` > `product` > `footer` > `quickreply` > `metadata`.
   Judul lewat jalur yang sama (bold `**…**`, `# …`, atau baris ≤3 kata
   berakhiran `:`).
3. **`_section_block(kind, pairs, body_lines)`** (fungsi baru) — membangun blok
   dari judul + badan. Mengembalikan None bila tak layak:
   - `product`: HANYA baris **daftar** (bullet/bernomor) atau baris yang isinya
     **hanya tautan** → `{title, price, product_url}`; harga dari `_PRICE_RE`;
     **butuh** minimal satu harga ATAU tautan. Prosa di bawah judul (mis.
     "Produk kami berkualitas…") TIDAK jadi kartu.
   - `footer`: baris daftar/tautan-telanjang ber-URL → `{text, url,
     type:"OPEN_URL"}`. Prosa bertautan bukan tombol.
   - `quickreply`: label polos baris **daftar** tanpa URL → `{items:[...]}`.
   - `metadata`: badan sebagai teks.
4. **Loop di `_auto_sections_from_text`** — memakai `_section_block`; plus
   deteksi **metadata sebaris** (`Catatan: …`) via `_INLINE_META_RE`; badan
   bagian BERHENTI di baris metadata sebaris (agar tidak tertelan blok lain).

### `prompts/rules.md`
Tabel IF/THEN ditambah baris Produk/Tombol/Pilihan/Catatan + contoh tanpa fence.

## Tes
- Python: `tests/test_format.py` +27 tes (Fitur #38); baseline 372 → **399 lulus**.
- Node: `tests/airich.test.mjs` +4 tes (bentuk blok default → satu kartu);
  baseline 249 → **253 lulus**. Total **652**.
- Bukti lintas-bahasa (manual): `to_replies()` → `buildCardParts()` menghasilkan
  `[GenAIMarkdownTextUXPrimitive, GenAIProductItemCardPrimitive,
  GenAIFooterActionPrimitive, GenAIMetadataTextPrimitive]` dalam SATU kartu.
- `resolveBlockMedia` atas product TANPA gambar tidak memanggil unggahan
  (tidak ada `{url:''}`).

## Perbaikan setelah REJECT ronde 1 (reviewer `deleg_39ea6c87`)
Dua bug NYATA ditemukan & diperbaiki:
1. **`_PRICE_RE` cocok "rp" di dalam kata** → "Sharp 5000 TV" jadi kartu produk
   palsu + judul terpotong. **Fix:** lookbehind `(?<![A-Za-z])` sebelum token
   mata uang (`agent/format.py` `_PRICE_RE`).
2. **Badan bagian melewati satu baris kosong** → prosa penutup ("Terima kasih!")
   tertelan ke metadata ATAU hilang total. **Fix:** badan berhenti di satu baris
   kosong yang diikuti PROSA (item daftar lanjut); helper `_is_body_item` +
   `_leftover_body_lines` mengembalikan baris yang tak terwakili sebagai teks
   (tidak ada yang dibuang diam-diam).
+7 tes regresi (jaring: 6 tes baru + 1 tes lama diperbarui agar URL tidak
hilang). Total kini **399 Python + 253 Node = 652**.

## Yang diminta reviewer
Cari bug NYATA + repro minimal (file:line + perintah + output). Fokus:
1. False positive: prosa biasa yang tak sengaja jadi product/footer/metadata.
2. Baris `Catatan:`/metadata tertelan blok sebelumnya (atau sebaliknya).
3. Judul ambigu (mis. "Sumber:" vs "Tautan:" vs "Harga:") — prioritas salah?
4. Regresi perilaku lama (Sumber/Saran/divider/tabel/HTML).
5. Product tanpa harga/tautan harus tetap teks (tidak ada kartu hampa).
6. quickreply menelan baris ber-URL (harus diabaikan, bukan jadi label).
