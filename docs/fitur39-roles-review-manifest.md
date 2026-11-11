# Fitur #39 — Per-User Sandbox & Policy (manifest review ronde 1)

**Permintaan pemilik**: "Per-User Sandbox & Policy — Belum ada isolasi per-user
(siapa boleh pakai tool apa). Penting kalau jadibot dipakai banyak orang."

## Akar masalah (dikonfirmasi dari kode, bukan dugaan)

1. `tools/registry.py:14-23` menyimpan `toolset`/`requires_owner`/`dangerous`
   per tool — tetapi **tidak ada satu pun pembaca** di seluruh repo.
2. `agent/main.py:622` (dulu) `tools = registry.all_tools()` → **SETIAP owner
   mendapat SEMUA tool**, termasuk `shell`, `plugin_write`, `pterodactyl_delete`.
3. `security/policy.py` punya `evaluate()` — **nol pemanggil**.
4. `tools/builtin/subagent.py` menyusun daftar tool-nya sendiri → **jalur
   eskalasi**: peran terbatas bisa `subagent(tools=["shell"])` dan dapat shell.

Kesimpulan: tidak ada isolasi per-pemakai sama sekali.

## Perubahan

| Berkas | Isi |
|---|---|
| `security/roles.py` (BARU) | Peran `primary`/`owner`/`user`/`stranger` + `allowed_tool_names`, `filter_tools`, `tool_allowed`, `role_for_tenant`. |
| `agent/loop.py` | **GERBANG KERAS** di `_run_tool`: tool di luar `context.tool_allow` DITOLAK. |
| `agent/main.py` | Resolusi peran; saring tool per peran; isi `context.role`/`tool_allow`; dukung pengguna non-owner. |
| `agent/config.py` | Baca `WA_USER_NUMBERS` → `cfg["user"]`. |
| `memory/tenants.py` | `user_numbers()` + `resolve_user_tenant()` (ruang terpisah utk pengguna). |
| `tools/builtin/subagent.py` | Sub-agent dibatasi `tool_allow` pemanggil (tutup eskalasi). |
| `config/default.yaml` | Blok `security.roles` (dapat ditimpa). |
| `.env.example` | `WA_USER_NUMBERS`. |
| `prompts/rules.md` | Aturan #0: tool yang terlihat = yang boleh dipakai. |
| `tests/test_roles.py` (BARU) | 14 tes (pemilihan + gerbang keras + subagent). |
| `tests/test_roles_flow.py` (BARU) | 4 tes END-TO-END lewat `/message`. |

## Desain

- **primary** = owner utama → semua tool.
- **owner** = owner lain → semua KECUALI tool admin (`pterodactyl_delete`,
  `plugin_write`).
- **user** = non-owner terdaftar (`WA_USER_NUMBERS`) → tool aman saja.
- **stranger** = tidak boleh apa pun (sudah ditolak di ingress).
- Dua lapis: **penyaringan** (model tak melihat tool terlarang) + **gerbang
  keras** (`loop._run_tool` menolak walau model tetap memanggilnya).
- **Kompatibel**: `tool_allow = None` → perilaku lama (tanpa batasan), jadi
  jalur sub-agent internal & tes lama tidak berubah.

### Jalur bypass yang DITEMUKAN & DITUTUP saat self-audit

1. **`subagent`** menyusun daftar tool sendiri → peran terbatas bisa
   `subagent(tools=["shell"])`. Kini dibatasi `tool_allow` pemanggil.
2. **Mekanisme eksternal** (`claude`/`codex`/`hermes`) berjalan di LUAR sistem
   tool kita (akses shell penuh) → peran `user` bisa menembus isolasi. Kini
   `user` SELALU `native`; hanya primary/owner boleh engine eksternal.

## Bukti uji

- **437 tes Python + 253 tes Node = 690 lulus** (sebelum: 671).
- Verifikasi nyata: `primary`=16 tool, `owner`=14 (tanpa `pterodactyl_delete`/
  `plugin_write`), `user`=5, `stranger`=0.
- End-to-end `/message`: peran `user` memanggil `shell` → tool **tidak terlihat**
  model DAN audit mencatat `tool.call shell ok=false`; `primary` → terlihat &
  jalan; orang asing → `replies: []`, LLM tak dipanggil.

## sha256

- `security/roles.py` — 4459 b — `87fe6fd49625243c5cfc7abd49de19e1825046962bad0dd81e532de894e2252c`
- `tests/test_roles.py` — 6776 b — `99d950fabb520659098b30ca6b2f1ca445eefc4b93a3d3d28b0d9f34f76f411a`
- `tests/test_roles_flow.py` — 4821 b — `622158d0ce189e7f5f70a2f0d173d30a2173ebd653ee2e31bb5161f98119d0b6`
- `agent/loop.py` — 18547 b — `a64c5e6ff38dbd5dc92545118756598f723517aebec04ee631eee6eb250b139a`
- `agent/main.py` — 35031 b — `2fde7a37430938b703a65b94b3a2392e47bc31c28b2cc62fc7d1d2d3f4d5c746`
- `agent/config.py` — 5671 b — `f0e445b3a3f256ad557edd14a0d0798b633c42f414ec94ebe5d17e3846ee3163`
- `memory/tenants.py` — 9614 b — `808530461d20e117994d4443abc5f7af7e82a5dc1eab9a7a08994e48a20bfacc`
- `tools/builtin/subagent.py` — 5760 b — `6a78d3b0b35adba29395ef5fd353bd110fb7d91566316f61c2ff6685b7272a0a`
- `config/default.yaml` — 6904 b — `241f7254b323cee60d71e24a3b86814e71001e67838768292d8eb5a65867293d`
- `.env.example` — 2985 b — `cf2d3607f2069917ffaa129d4f067953efaaf9ce2c9ab6c41e54eb5ce89d5ff4`
- `prompts/rules.md` — 29795 b — `debf21748071c9f0f8625cfabb9a899bdd93d92505e05c10b2a5fac9274fc41f`

## Catatan untuk reviewer

- Uji khusus yang diminta: apakah ada jalan MEMBYPASS penyaringan (plugin,
  curl, sub-agent, `tool_allow=None`)? Apakah pengguna non-owner benar-benar
  dapat ruang terpisah? Apakah `role` bisa kosong di jalur tertentu?
- Minta verdict **teks biasa**: `APPROVE` atau `REJECT` + alasan + temuan.
