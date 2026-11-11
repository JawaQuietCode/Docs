# Fix Pterodactyl — manifest review (ronde 2)

**Tugas pemilik**: "fix kwee AI agent gamau buat aku pterodactly pada sudah di beri akses"
(panel `https://panel.marrlabs.my.id`, Application API key sudah diberikan).

## Akar masalah (BUKTI PRODUKSI, bukan dugaan)

Dibaca dari `data/audit.log` PANEL (via Client API `files/contents`):

```
{"event":"tool.call","tool":"plugin_write","args":{"name":"pterodactyl","code":"<disunting>"},"ok":false}
```

1. Bot **tidak punya tool Pterodactyl** (registry hanya 14 tool).
2. Bot mencoba menulis plugin bernama "pterodactyl" → **gagal**: `plugin.py:107`
   menahan bila `ctx["owner_confirmed"]` belum true, DAN plugin berjalan di sisi
   Node yang **tidak memegang kunci Application API**.
3. `.env` panel **tidak memuat kunci `PTERO*`** sama sekali.

## Perubahan (ronde 2)

| Berkas | Isi |
|---|---|
| `tools/builtin/pterodactyl.py` | Tool `pterodactyl` (AMAN) + `pterodactyl_delete` (MERUSAK, tool TERPISAH). |
| `tests/test_pterodactyl_tool.py` | 18 tes. |
| `tools/builtin/shell.py` | Scrub `PTERO_*` dari env anak proses shell (temuan #5). |
| `tests/test_shell_agent.py` | +1 tes scrub env (terbukti gagal bila scrub dilepas). |
| `agent/config.py` | Muat `PTERO_*` dari `.env` → `cfg["pterodactyl"]`. |
| `.env.example` | Blok `PTERO_*` + cara membuat kunci. |
| `prompts/rules.md` | Bagian "Mengelola panel Pterodactyl" (tool terpisah untuk hapus). |

## Perbaikan atas REJECT ronde 1

### BLOCKER #1 — `server_id` identifier vs id numerik → **DIPERBAIKI**
Reviewer benar: Application API mengikat `{server:id}` **numerik**; mengirim
`'0007cd1c'` ke path berbahaya (DB MySQL mengubahnya jadi `7` → bisa menghapus
server LAIN). Kini `_resolve_server_id()`:
- id numerik → dipakai langsung;
- identifier → **diresolusi lewat `GET /api/application/servers`** dengan
  mencocokkan kolom `identifier`, lalu memakai `id` numeriknya;
- identifier tak dikenal → **ditolak, tidak menebak**.

**Bukti langsung ke panel asli (read-only)**: `get_server server_id=0007cd1c`
→ `{'id': 14, 'identifier': '0007cd1c', 'name': 'Kwee bot'}` (bukan 7).
Identifier ngawur `zzzzzzzz` → error "tidak ditemukan". Tes baru:
`test_get_server_identifier_diresolusi_ke_id_numerik`,
`test_identifier_tidak_dikenal_ditolak_tanpa_menebak`,
`test_delete_identifier_diresolusi_bukan_dikirim_mentah`.

### MINOR #2 — `int()` tanpa penjagaan → **DIPERBAIKI**
Helper `_as_int()`: argumen non-numerik → pesan jelas
("argumen 'memory' harus angka"), bukan `ValueError`. Dipakai di
node/egg/nest/memory/disk/cpu/allocation_id.

### MINOR #3 — `quote(sid)` safe="/" → **SIRNA**
Karena `server_id` kini SELALU int (hasil resolusi), path dibangun dari int —
tidak ada lagi string user yang masuk path.

### MINOR #4 — `dangerous=False` untuk aksi destruktif → **DIPERBAIKI**
Aksi destruktif dipindah ke tool terpisah **`pterodactyl_delete`**
(`dangerous=True`). Alasan dipisah: bila digabung, penegakan flag kelak akan
ikut menahan `create_server` — mengulang bug yang sedang diperbaiki.
Tool `pterodactyl` tetap `dangerous=False` (semua aksinya aman).
`handler()` sengaja menolak `action=delete_server` dan mengarahkan ke tool itu.

### CATATAN #5 — kunci bocor lewat `shell` env → **DIPERBAIKI**
`tools/builtin/shell.py` kini membuang `PTERO_API_KEY` & `PTERO_API_URL` dari env
anak proses. Tool `pterodactyl` tetap membacanya dari `os.environ` proses bot.
Tes `test_rahasia_pterodactyl_tidak_diwariskan_ke_shell` (diverifikasi: GAGAL
bila scrub dilepas, LULUS dengan scrub). Catatan jujur: `LLM_API_KEY` &
`ACTION_TOKEN` tetap diwariskan — itu pola lama di luar cakupan fix ini.

## Bukti uji

- **418 tes Python + 253 tes Node = 671 lulus** (ronde 1: 663).
- **Verifikasi LANGSUNG ke panel asli (read-only)**: 10 server; node JAKARTA;
  5 nest; 6 user; nest 5 → 5 egg; node 1 → 38 alokasi bebas;
  `_egg_details(5,26)` → image `ghcr.io/parkervcp/yolks:nodejs_22`;
  resolusi identifier `0007cd1c` → id **14**; identifier ngawur ditolak.
- `create_server` **jalan tanpa `owner_confirmed`** (inti fix);
  `delete` **ditahan** tanpa konfirmasi (terbukti tidak menyentuh jaringan).
- Payload `create_server` cocok skema resmi Pterodactyl v0.7.

## sha256 (ronde 2)

- `tools/builtin/pterodactyl.py` — 19711 b — `a0e0fce36954ad6e9c8c119eb6500367fbe08330e491ff1ae9da5c7da7daad9a`
- `tests/test_pterodactyl_tool.py` — 13412 b — `4295775fa138aaebc6b718d9e38ed564aafb0001670f3bccdd3c018f22b9d4cd`
- `tools/builtin/shell.py` — 14186 b — `f1b89de8f1223a9e94df6f3b26a78da0b3bf54779e9bbad2fa4aaf100580ad29`
- `tests/test_shell_agent.py` — 14710 b — `ef0cefd5278d403bc82e73ae08641185314740266a1281291233a1e78687cd0e`
- `agent/config.py` — 5337 b — `58a0b822c891423101ae827feb30b11c364eb1e25e03cb063b093dec48bc5544`
- `.env.example` — 2585 b — `4427cb809c4fb0c0d5b16ac6cfc48012d6b52c79b2e8cd34de3a8926d08eea34`
- `prompts/rules.md` — 29392 b — `10d1176aaedf845182dd003843c341c5c310eb94e822f44a5b32afd5268fa402`

## Catatan untuk reviewer

- Kunci Application API nyata **TIDAK** ada di repo/dokumen; hanya di `.env`
  panel (dan `/tmp/kwee_ptla.txt` sementara di laptop, dihapus setelah selesai).
- `tools/builtin/shell.py` di panel identik dengan lokal kecuali tambahan scrub
  (sudah diff: 13 baris diff, hanya blok scrub).
- Minta verdict **teks biasa**: `APPROVE` atau `REJECT` + alasan + temuan.
