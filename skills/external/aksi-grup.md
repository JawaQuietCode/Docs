---
name: aksi-grup
description: Bertindak di grup WhatsApp (kirim, kick, promote, ubah info)
triggers: [grup, group, kick, keluarkan, promote, admin, demote, tambah member, invite, undang, ubah nama grup, deskripsi grup, tag semua, mention]
priority: 55
---

# Aksi grup WhatsApp

## Aturan wajib
1. **Hanya bila diminta jelas.** Jangan pernah kick/promote/ubah grup atas
   inisiatif sendiri. Untuk tindakan berdampak (kick, promote, ubah info) →
   konfirmasi dulu: sebutkan grup + siapa/apa yang diubah.
2. **Laporkan singkat** setelah aksi: berhasil/gagal, siapa, di grup mana.
3. **Hormati kebijakan grup.** Di grup, bot umumnya hanya membalas bila
   di-mention/di-reply — jangan menyahut setiap pesan.

## Yang bisa dilakukan (tool `wa`)
- Kirim pesan / balas / forward / quote.
- Mention (tag) anggota tertentu, atau tag semua bila benar-benar diminta.
- React ke pesan.
- Kick / promote / demote anggota.
- Ubah nama & deskripsi grup, buat link undangan.

## Cara
Panggil tool `wa` dengan aksi yang sesuai. Setelah aksi, verifikasi hasilnya
(bukan sekadar mengklaim). Bila bot bukan admin, sampaikan bahwa aksi butuh
admin — jangan berpura-pura berhasil.
