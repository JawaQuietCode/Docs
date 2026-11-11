# Perbaikan menyeluruh: tools, shell, agent, LLM, start/setup

Ringkasan perubahan pada `archive-2026-10-23T130613Z.tar.gz` setelah review.
Semua kode/komentar ditulis dalam bahasa Indonesia. Tidak ada kredensial yang
ditulis ke kode — semua tetap dibaca dari `.env`.

Fokus tiga hal yang diminta:

1. **Semua owner dapat semua tool** (allowlist owner dibuka, tapi tetap aman).
2. **AI benar-benar bisa bekerja**: shell (pipe/redirect/heredoc), `curl`,
   `git`, `browser`, `web_fetch`, `vision`.
3. **`start.sh` dan `setup.sh`** diperbarui + self-check.

---

## 1. Keamanan (P0)

| # | Masalah lama | Sekarang |
|---|---|---|
| 1 | `shell.py` meneruskan `dict(os.environ)` → `env`/`printenv` (ada di allowlist) mencetak `LLM_API_KEY`, `ACTION_TOKEN`, `WA_*` ke WhatsApp | `security/env_sandbox.py`: allowlist POSITIF (`PATH`, `HOME`, `LANG`, …) + saringan prefix `LLM_/WA_/ACTION_/PTERO_/OWNER_` dan suffix `_KEY/_TOKEN/_SECRET/_PASSWORD`. Dipakai `shell.py` **dan** `mechanisms.py` |
| 2 | `/message`, `/cancel`, `/status` terbuka di `127.0.0.1` → proses lain (atau agent lewat `curl localhost:8787/message`) bisa menyuruh bot sebagai owner | Header `X-Agent-Token` (nilai = `ACTION_TOKEN`) wajib untuk **7 endpoint**. Transport mengirimnya otomatis (`transport/agent-auth.js`). `start.sh`/`setup.sh` membuat token bila kosong |
| 3 | `denyPaths` bisa dilewati (`python3 -c "open('.env')"`, `cat .e''nv`) | (a) ekstraksi substring mirip-path dari seluruh perintah, (b) `blockInlineCode` (opsional) menolak `python3 -c`/`node -e`, (c) `bash -lc "…"` di-unwrap sehingga isi wrapper tetap diperiksa |
| 4 | Timeout hanya membunuh anak langsung → cucu proses jadi yatim | `start_new_session=True` + `os.killpg(SIGTERM→SIGKILL)`. Diuji: `sleep 40 & sleep 40` dengan timeout 1s → `pgrep -x sleep` = 0 |
| 5 | `_denied_descendant()` fail-OPEN (setelah 4000 entri return `""` = "aman") | fail-closed; `security` kosong → perintah **ditolak** (bukan dijalankan tanpa allowlist) |

Allowlist executable sekarang **`security.allowAllExecutables: true`** (sesuai
permintaan: `curl`, `git`, `npm`, `ffmpeg`, `yt-dlp`, dll tanpa menambah daftar),
ditambah 77 nama di `allowedExecutables` bila nanti ingin dikunci lagi.

Yang **tetap** aktif meski allowAllExecutables=true:
`security/policy.py` (pola merusak: `rm -rf /`, fork bomb, `mkfs`, …),
`denyPaths` (`.env`, `data/tenants/*/sessions`, dll), pembersihan environment,
gerbang peran, gerbang persetujuan owner.

---

## 2. Owner allowlist: semua tool

- `security/roles.py`: `admin_tools()` sekarang membaca `dangerous=True` dari
  **registry**, bukan `_ADMIN_TOOLS` hardcode. Tool berbahaya baru otomatis
  terlindungi; `_ADMIN_TOOLS` hanya cadangan bila registry belum dimuat.
- `config/default.yaml`: `primary` dan `owner` sama-sama `tools: ["*"]`,
  `deny: []` → shell, curl, browser, `pterodactyl_delete`, `plugin_write`
  semuanya terbuka untuk owner terdaftar.
- Peran `user` (non-owner opt-in) tetap dibatasi: `memory, web_fetch, vision,
  media, skill`. `stranger` tidak dapat apa pun.
- Gerbang KERAS tetap di `loop._run_tool`: model tidak bisa "meminta sendiri"
  tool di luar `tool_allow` (anti prompt-injection).

---

## 3. Kemampuan AI (shell / curl / browse)

- **`shell.py`** (729 baris): pipe `|`, `&&`, `||`, redirect, heredoc, variabel,
  aritmatika semuanya jalan. Output ditampung ke **file sementara** (bukan RAM)
  lalu dipotong sesuai `outputLimitBytes` → `yes`/`npm install --verbose` tidak
  lagi OOM. `cwd` dikurung ke root proyek / workspace / `/tmp`.
- **`browser`** (Playwright) + **`web_fetch`** (httpx) tetap tersedia; `start.sh`
  memastikan Playwright + Chromium ada dan melaporkan bila gagal.
- **`vision`**: gambar diperkecil dulu (sisi terpanjang ≤1568px, JPEG q80) di
  `agent/llm/client.py` → data-URL turun 5–10× sehingga gateway tidak menolak.
  Model vision **tidak lagi** di-hardcode ke model teks (`cbai/deepseek-*`);
  diambil dari `LLM_VISION_MODELS` / `llm.visionModels`.
- **`files.py`**: `read` dengan `offset`/`limit` + `total_lines` (file besar bisa
  dibaca bertahap), deteksi berkas biner yang informatif, `write`/`edit` atomik
  (`tmp` + `os.replace`), `append`, `list`, `mkdir`, `delete`.

---

## 4. Agent

- **`agent/loop.py`**
  - Anggaran jawaban akhir: `reserve = min(max(20s, 15% timeout), 50% timeout)`
    — dulu `_final_answer` hanya dapat sisa ≤1 detik sehingga selalu kosong.
  - Resume `!lanjut` tidak lagi mereset hitungan iterasi
    (`for _it in range(iteration, …)`).
  - Tool independen dieksekusi paralel (`concurrent=True` di registry) tetapi
    hasil diurutkan sesuai `tool_call` asli; `shell`/`wa`/`cron`/`plugin_run`
    ditandai `concurrent=False`.
  - Cache panggilan tool identik dalam satu turn (anti-loop).
  - Hasil tool selalu dipotong (`TOOL_RESULT_LIMIT`) sebagai jaring terakhir.
  - **Aksi tertunda**: bila tool menjawab "ditahan / minta persetujuan", aksi
    disimpan (`agent/consent.ask`) dan balasan **"ya/setuju/gas"** dari owner di
    pesan berikutnya menjalankannya (dulu alur ini mustahil karena
    `owner_confirmed` hanya membaca pesan yang sama).
- **`agent/consent.py`**: `ask/peek/take/clear` + nonce + TTL 5 menit, sekali
  pakai, terikat ke chat. Negasi ("jangan/batal") dan kalimat tanya menang.
- **`agent/context.py`**: urutan blok disusun untuk prompt-cache
  (persona → rules → ringkasan tool → hint **stabil**, lalu skills →
  environment → memori **volatil** di ekor). Skema tool tidak lagi ditulis dua
  kali (teks + `tools`). `persona.md`/`rules.md` di-cache dengan mtime.
- **`agent/config.py`**: `get_config()`/`reload_config()` (mutasi di tempat),
  `redact_config()` (kunci tidak ikut ke handler tool), `_env_int/_env_bool`
  (typo `.env` tidak mematikan bot), validasi + peringatan saat load.
- **`agent/main.py`**: 7 endpoint ber-token, idempotensi `message_id` (WhatsApp
  mengirim ulang saat ACK hilang → perintah tidak dieksekusi dua kali),
  `leaseSeconds` dari YAML, `STORE` lazy, balasan error disimpan sebagai
  `assistant` agar riwayat tetap valid, smoke-test mekanisme saat startup.
- **`agent/mechanisms.py`**: prompt >60KB lewat **stdin** (argv dibatasi
  ±128KB), `killpg` saat timeout, env bersih, folder sesi engine diarahkan ke
  ruangan owner (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`), `smoke_test()` (`--version`).
- **`tools/builtin/subagent.py`**: meneruskan `timeoutSeconds`, `stream`,
  `retryMax`, `visionMaxPx`; konteks pemanggil (tenant/chat_id) diturunkan,
  tetapi `owner_confirmed` sengaja **tidak** diwarisi.

---

## 5. LLM client (`agent/llm/client.py`)

| Sebelum | Sesudah |
|---|---|
| `httpx.post()` baru tiap panggilan (TLS handshake ulang) | satu `httpx.Client` keep-alive (http2 bila tersedia) |
| Satu 502/429 = model dinyatakan gagal | retry berjenjang untuk 408/409/425/429/5xx + gangguan jaringan, menghormati `Retry-After` |
| `timeout` fallback tidak adil (model ke-2 dapat ≤1s) | anggaran dibagi rata antar model |
| `usage` dibuang | `last_usage` disimpan (`prompt_tokens`/`completion_tokens`) |
| Non-stream, 12k token bisa kena timeout | streaming SSE + akumulasi delta, otomatis fallback ke non-stream |
| `context_length_exceeded` dianggap soal `max_tokens` | `ContextOverflowError` terpisah → pemanggil bisa memadatkan riwayat |
| `temperature` selalu dikirim (ditolak model reasoning) | tidak dikirim untuk `o*`/`gpt-5`/`*-reasoning` |
| Model rusak dicoba ulang tiap turn | circuit breaker: 3 gagal → istirahat 5 menit |
| Base64 gambar mentah (8MB → 11MB) | diperkecil ≤1568px JPEG q80 |
| `LLMError` hanya string | `LLMError(model, status, code, retryable)` |

---

## 6. `start.sh` / `setup.sh`

`start.sh`:
- `--workers 1` **eksplisit** (lock per-chat, peta aktivitas, dan watchdog job
  hidup di memori proses — >1 worker membuat semuanya diam-diam rusak).
- `ACTION_TOKEN` dibuat otomatis bila kosong, ditulis ke `.env`, dipakai
  health check (`-H "X-Agent-Token: …"`).
- **Self-test tool**: registry harus memuat `shell/files/browser/web_fetch/
  vision/cron/owner_ops/plugin_control/group_summary`, dan `shell` benar-benar
  menjalankan `echo kwee-ok`. Gagal → start berhenti dengan pesan jelas.
- **Laporan kemampuan**: `curl wget git jq ffmpeg ffprobe yt-dlp unzip tar` +
  status Playwright.
- Pillow diperiksa (dipakai untuk memperkecil gambar vision).

`setup.sh`:
- Menambah `pillow`, `yt-dlp`, Playwright + Chromium (kegagalan Chromium tidak
  mematikan setup, tetapi dilaporkan).
- Membuat `ACTION_TOKEN` bila `.env` belum punya.
- Self-test tool yang sama + laporan kemampuan di akhir.

`.env.example` baru: mendokumentasikan semua variabel (tanpa rahasia),
termasuk `LLM_FALLBACK_MODELS`, `LLM_VISION_MODELS`, `LLM_STREAM`,
`LLM_RETRY_MAX`, `DEFAULT_MECHANISM`, `OWNER_MECHANISMS`, `ACTION_TOKEN`.

---

### Dependensi CLI Python (yt-dlp) — `start.sh` + `plugins/`

Gejala di produksi: `_log: yt-dlp gagal Hislerim: yt-dlp: /usr/bin/python3: No
module named yt_dlp_`, padahal paketnya "sudah dipasang". Tiga sebab sekaligus:

1. `start.sh` **tidak pernah** memasang yt-dlp (hanya `setup.sh` yang memasang),
   jadi instalasi yang cuma menjalankan `start.sh` tidak punya modulnya.
2. `plugins/spdl.js` memanggil `python3` mentah → `/usr/bin/python3` milik
   sistem, bukan interpreter yang punya dependensi proyek.
3. `start.sh` menimpa `PYTHONUSERBASE` ke `$ROOT/.pyuser`, sehingga paket yang
   sudah terpasang di `~/.local` (hasil `setup.sh`) jadi **tidak terlihat**.

Perbaikan:

- `start.sh`: memasang `yt-dlp` bila `import yt_dlp` gagal (best-effort, bot
  tetap jalan), mengekspor `KWEE_PY` = path absolut interpreter proyek, dan
  memasukkan **dua** user site ke `PYTHONPATH` (milik `PYTHONUSERBASE` dan
  `~/.local` bawaan). Laporan kemampuan kini mengenali `yt-dlp(modul)`.
- `transport/py.mjs` (baru): satu tempat memilih interpreter (`KWEE_PY` →
  `python3` → `python`) yang benar-benar bisa `import sys`.
- `plugins/spdl.js`: `runYtDlp()` mencoba modul → binary `yt-dlp` → memasang
  modul **di latar** (tidak menahan chat, karena host mematikan plugin di 30
  detik) lalu memberi pesan yang jelas. `plugins/tourl.js` dan
  `plugins/backup-full.js` ikut memakai `PY_BIN`.
- `security/env_sandbox.py`: `KWEE_ROOT` dan `KWEE_PY` diteruskan ke proses anak
  (bukan rahasia), supaya aturan "coba `$KWEE_ROOT/agent`" bisa dijalankan.

## 7. Perbaikan `/prompt` & `/skills` + tool `terminal`

### `/prompt` — persona yang bisa dilihat & diganti tanpa restart
Sebelumnya persona hanya bisa diubah dengan mengedit `prompts/persona.md` lalu
me-restart: tidak ada cara melihat yang aktif, `prompts/persona.mommy-girlfriend.md`
tidak pernah terpakai, dan tidak ada cara memuat ulang setelah diedit.

- `agent/promptlib.py` (baru): pilihan persona disimpan di
  `data/prompt_active.txt` (tulis atomik). Urutan prioritas:
  **override tenant** (`<ruangan>/persona.md`) -> **persona aktif** ->
  `prompts/persona.md`. Isi berkas di-cache berdasarkan mtime, jadi edit
  langsung berlaku pada pesan berikutnya.
- Perintah WhatsApp: `!prompt` (ringkasan + daftar persona), `!prompt set <nama>`,
  `!prompt reset`, `!prompt reload`. Prefix `/prompt` juga diterima.
- Endpoint: `GET /prompt`, `POST /prompt` (`action=set|reset|reload`), ber-token
  dan **hanya owner** boleh mengganti persona (diperiksa ulang di Python dengan
  normalisasi nomor, bukan hanya di transport).
- Bug yang ditemukan saat uji: `set_active("persona.mommy-girlfriend")` selalu
  gagal karena prefix `persona.` dibuang lebih dulu sehingga nama berkas menjadi
  `mommy-girlfriend.md` yang tidak ada. Sekarang kandidat nama dicoba berurutan.

### `/skills` — katalog yang bisa diperiksa & dicari
- `skills/loader.py`: **cache pemindaian** berbasis mtime+ukuran. Dulu satu pesan
  memicu 4-6 pemindaian penuh (isi semua berkas dibaca ulang): 0,13 s menjadi
  0,004 s setelah cache. `clear_cache()` dipakai `!skills reload` dan tool
  `skill action=reload` (yang sebelumnya sama sekali tidak memuat ulang).
- Satu berkas rusak tidak lagi mematikan seluruh sistem skill: `priority: tinggi`
  dulu membuat `int()` melempar, `skills_block` menelan errornya, dan **semua**
  skill hilang tanpa jejak. Sekarang `_as_int()` + per-berkas `try/except` + log.
- Pencarian: skor utama tetap trigger (word-boundary) + prioritas; ditambah skor
  cadangan dari kata pada nama/deskripsi, supaya "perbaiki script python" tetap
  menemukan skill `koding` walau trigger-nya tidak persis sama.
- Perintah: `!skills`, `!skills cari <kata>`, `!skills get <nama> [file]`,
  `!skills reload` (prefix `/skills` juga). Endpoint: `GET /skills`
  (`?query=`, `?name=`, `?file=`, `?reload=1`). Skill **hidden tetap tidak bisa
  dibuka** lewat jalur ini (diuji).

### Tool `terminal` — shell dengan cwd yang bertahan
- `tools/builtin/terminal.py` (baru): perintah dijalankan lewat tool `shell`
  (semua lapisan keamanan tetap berlaku), lalu `pwd` akhir dicatat sebagai cwd
  sesi per percakapan. Jadi `cd proyek/src` lalu `cat main.py` berjalan di folder
  yang sama, seperti terminal sungguhan. Aksi: `run`, `state`, `reset`.
- `cd` ke luar area izin **tidak** disimpan sebagai cwd sesi (model diberi tahu).
- Jujur soal batasnya: hanya CWD yang bertahan; `export` dan proses latar tidak,
  karena tiap perintah tetap proses baru.

## 8. Gerbang eksekusi: agent, bukan pemberi instruksi

Gejala yang dilaporkan pemilik: **"kwee malah berikan code terminal bukan
execution shell sendiri, dia agent bukan chat bot"**. Tool-nya tidak rusak —
masalahnya perilaku model: saat ragu ia menulis

    Jalankan ini di terminal:
    ```bash
    npm install && npm test
    ```

lalu selesai, padahal bot punya `terminal`/`shell` dan seharusnya menjalankan
sendiri. Diperbaiki di tiga lapis, karena satu lapis saja tidak cukup:

1. **Aturan di prompt** (`prompts/rules.md` bagian "Eksekusi", `prompts/persona.md`,
   `skills/internal/inti-alat.md`): jalankan sendiri, jangan menyerahkan blok
   perintah; blok kode hanya untuk hasil yang memang deliverable; satu-satunya
   pengecualian bila pengguna eksplisit minta teks perintahnya. Deskripsi tool
   `shell`/`terminal` juga dipertegas ("PAKAI TOOL INI untuk MENJALANKAN").
2. **Gerbang di loop** (`agent/execution_guard.py` + `agent/loop.py`): balasan
   akhir yang berisi perintah untuk dijalankan **tidak dikirim**. Loop menyuntik
   pesan sistem dan memaksa satu langkah lagi; maksimal 2 kali, lalu balasan
   dilepas supaya tidak berputar selamanya.
3. **`tool_choice="required"`** (`agent/llm/client.py`): panggilan setelah
   gerbang menyala memaksa model memanggil tool. Bila gateway menolak parameter
   ini, loop mengulang tanpa paksaan — fitur opsional tidak boleh menggagalkan
   turn.

Detektornya sengaja konservatif. Yang dianggap pelanggaran: blok `bash`/`sh`/
`console`/`powershell` berisi perintah disertai ajakan ("jalankan", "copy",
"buka terminal"), blok perintah yang mendominasi balasan, kode sebaris
(`Jalankan `npm install``), baris `$ perintah`, balasan pendek yang isinya
memang cuma perintah, dan **deretan perintah tanpa blok kode** — pola yang
dilaporkan pemilik setelah uji pertama:

    ls -la /                                # lihat isi root
    which agent                             # kalau maksudnya binary
    find / -maxdepth 3 -iname '*agent*' | head -50

Pola terakhir ini sengaja butuh minimal 2 baris berurutan dan satu penanda khas
perintah (flag/path/pipe/wildcard), supaya daftar poin biasa tidak tertangkap. Yang TIDAK dianggap: kode sebagai hasil (```python```),
contoh perintah di tengah penjelasan panjang, laporan hasil ("sudah kujalankan,
outputnya ..."), dan permintaan eksplisit pengguna untuk teks perintah.

## 9. Hasil uji

- `py_compile` 50 berkas `.py` (di luar `skills/`): 0 error.
- `node --check` seluruh `transport/`, `tools/`, `plugins/`: 0 error.
- `bash -n start.sh`, `bash -n setup.sh`: OK.
- `test_all.py`: **42/42** lulus (registry, roles, config, shell, files,
  consent, context, loop, mechanisms).
- `test_llm.py`: streaming + usage, retry 502, fallback model, circuit breaker,
  `temperature` tidak dikirim ke model reasoning — lulus.
- Uji bypass shell: `bash -lc "curl"`, `env curl`, `timeout 5 curl`,
  `xargs curl`, `python3 -c open('.env')`, `node -e readFileSync('.env')`,
  `cat .env` — semuanya ditolak.
- Uji fungsional shell: pipe `printf|sort|head` → `a`, `$((2+3))` → `5`,
  `env` tanpa kebocoran kunci, `curl --version` jalan.
- Endpoint: tanpa token → 401, token salah → 401, token benar → 200,
  semua 7 route memakai dependency auth; duplikat `message_id` ditolak.
- Aksi tertunda: tool "ditahan" → `pending_confirmation` + nonce → `"ya"`
  berikutnya mengambil aksi dengan argumen yang benar.
- `test_prompt_skills.py`: **26/26** (persona aktif/set/reset/reload, penolakan
  non-owner, statistik skill, pencarian, muat skill, skill hidden tetap tertutup).
- `test_shell_pro.py` (E2E shell/terminal): **42/42** - pipe/redirect/heredoc/
  substitusi, kode keluar & stderr terpisah, output 3 MB dipotong tepat di batas,
  timeout 2 s membunuh seluruh grup proses (0 `sleep` yatim), environment tanpa
  kunci, cwd terkunci, cwd `terminal` bertahan, `git init+commit`, `tar`, `curl`,
  `python3 -c`, `node -e`, dan penolakan berkas rahasia.
- `run.mjs` (E2E Node untuk `!prompt`/`!skills`): **23/23** - memakai
  `transport/commands.js` asli dengan fixture dari kode Python asli; header
  `X-Agent-Token` terkirim di semua panggilan; regex prefix `/` diuji terhadap
  `/prompt`, `/skills cari ...`, dan dipastikan TIDAK menelan `/etc/hosts rusak`.
- `test_exec_guard.py` (gerbang eksekusi): **38/38** — 10 bentuk handoff
  tertangkap, 8 bentuk wajar lolos, dan pada loop sungguhan: handoff → nudge →
  tool `terminal` benar-benar dijalankan → jawaban berisi output nyata, dengan
  `tool_choice="required"` terkirim; gateway yang menolak `tool_choice` tetap
  menghasilkan eksekusi; kasus nyata `cat /agent` (dump 4 perintah) ditahan lalu
  dieksekusi; `$KWEE_ROOT` tersedia di dalam perintah shell.
- `test.mjs` / `test2.mjs` (jalur yt-dlp): **5/5** — modul Python hilang →
  jatuh ke binary `yt-dlp`; keduanya hilang → pesan jelas + pemasangan di latar,
  tanpa crash dan tanpa menahan permintaan.

---

## 10. Yang perlu dilakukan di server

1. `./setup.sh` lalu `./start.sh` (dependensi baru: `pillow`, `yt-dlp`,
   `playwright`).
2. **Rotasi kredensial** yang tadinya bisa terbaca lewat `env`/`printenv`
   (`LLM_API_KEY`, `ACTION_TOKEN`, `WA_*`, `PTERO_API_KEY`) — kode lama
   membocorkannya ke proses anak, jadi anggap sudah terekspos.
3. Isi `LLM_VISION_MODELS` dengan model yang benar-benar multimodal bila ingin
   tool `vision` bekerja (kosong = ikut model utama).
4. Bila ingin allowlist benar-benar mengikat: set
   `security.allowAllExecutables: false` + `security.blockInlineCode: true`.
   Catatan jujur: selama `python3`/`node` diizinkan, allowlist **bukan** sandbox.
   Batas nyata hanya bisa dicapai dengan container/bubblewrap terpisah.

## 11. Yang sengaja TIDAK diubah (keputusan pemilik)

- `.env` ikut ter-backup oleh `backup_full.py` (akan dirotasi manual).
- `assets/kwee-intro.mp4` 0 byte + `media.enabled: true` (fitur video belum
  dipakai).
- Archive bukan hasil `backup_full.py` (dibiarkan).

---

# Bagian II — fitur interaktif (paket 10, 12, 13)

## 12. Pencarian lintas sumber — `search_all` + `!cari`

Satu kata kunci dicari ke empat tempat sekaligus, tanpa jaringan dan tanpa
model tambahan:

| Sumber | Isi yang dicari |
|---|---|
| riwayat | pesan lama ruangan ini (`data/memory/kwee.db`) |
| berkas | isi workspace (teks; biner & `node_modules` dilewati) |
| memori | `memory/MEMORY.md` |
| skill | nama, deskripsi, dan pemicu tiap skill |

Pembobotan: jumlah kata kunci unik yang muncul + `log(jumlah kemunculan)`, jadi
berkas yang menyebut istilah berkali-kali naik, tapi tidak mendominasi. Kata
umum Indonesia (`yang`, `untuk`, `gimana`, …) dibuang lebih dulu supaya hasil
tidak penuh noise. Hasil akhir di-dedupe per (sumber, judul).

Dipakai dua cara: model memanggil tool `search_all` sebelum bilang "tidak tahu",
atau owner mengetik `!cari <kata>` yang langsung menampilkan kartu.

## 13. Tim paralel — `subagent` dengan `tasks` + `!tim`

Sebelumnya `subagent` hanya satu tugas. Sekarang menerima `tasks: [{judul, goal,
context}]` (maks 5) dan menjalankannya lewat `ThreadPoolExecutor`. Urutan hasil
tetap sesuai urutan permintaan, dan tiap tugas punya status sendiri sehingga satu
kegagalan tidak membatalkan yang lain.

`!tim bandingkan A | bandingkan B | cari ulasan keduanya` memanggil endpoint
`/fanout` — jalur tanpa LLM utama, jadi lebih murah dan hasilnya deterministik.

Batas: 3 pekerja bersamaan (bisa diubah lewat `max_workers`). Sub-agent tetap
mewarisi `security` dari config induk, jadi allowlist shell dan `denyPaths` tidak
hilang di dalam sub-agent.

## 14. Admin grup — `group_admin` + `!grup`

Tiga kemampuan, semuanya berbasis berkas (bukan karangan model):

1. **FAQ dokumen** — owner menaruh `.md/.txt/.json/.csv` di
   `data/knowledge/<ruangan>/`. `index` memecahnya jadi potongan ~700 karakter,
   `ask` mengembalikan potongan paling relevan **beserta rujukan berkas**.
2. **Sambutan** — teks dari `sapaan.md`; `{nama}` dan `{grup}` diganti otomatis.
   Bisa otomatis lewat `groupAdmin.welcome: true`.
3. **Penilaian spam** — heuristik: 3+ tautan, mayoritas kapital, frasa penipuan
   (`bonus deposit`, `kirim kode otp`, …), mention massal, karakter berulang.
   Hanya **menilai**; penghapusan tetap keputusan owner. Bisa otomatis lewat
   `groupAdmin.autoSpam: true` (bawaan mati).

Perintah: `!grup tanya <pertanyaan>`, `!grup daftar`, `!grup indeks`,
`!grup aturan`, `!grup sapaan`.

## 15. Kartu GenAI: thinking, tool, shell

`agent/loop.py` sekarang menerima `on_step` dan mencatat tiap langkah:
`{kind: think|tool, name, detail, ok, ms}`. `agent/main.py` mengumpulkannya
(maks 40 langkah) dan mengirimnya sebagai `trace` — baik pada balasan langsung
maupun pada `/job` (tugas background).

Di transport, `withTrace()` mengubah balasan pertama menjadi **satu kartu**:

```
[label]  𝓴𝔀𝓮𝓮𝔀𝓲𝓷𝓭 𝓫𝓵𝓸𝔀𝓼 · 4 langkah · 3,2 dtk
[text]   ✓ berpikir · 2 tool (820 ms)
         ✓ shell · ls -la (120 ms)
         ✕ terminal · npm test (4200 ms)
[divider]
[text]   …jawaban akhir apa adanya…
```

Kartu disusun `transport/api/ui.js` dari primitive `__typename` GenAI yang sudah
ada (`GenAILabelPrimitive`, `GenAIFollowUpSuggestionPillPrimitive`,
`GenAIQuickReplyPrimitive`). Palet feminin (pink, lilac, mint, amber, rose) dan
font `Mathematical Bold Script` ada di `transport/api/style.js`. Bisa dimatikan:
`ui.showTrace: false`.

## 16. Menu `!help` baru

Diganti dari satu paragraf panjang menjadi kartu berkategori: sehari-hari,
ingatan & pencarian, kerja bersama, berkas & terminal, kendali tugas, dan
tambahan terpasang. Semua label berbahasa Indonesia, tanpa aksara Tionghoa/
Jepang/Korea, dan perintah plugin ikut masuk otomatis. Ada jaring pengaman:
perintah yang lupa dimasukkan ke kategori tetap muncul di kelompok "lain-lain".

Nama bot menjadi **kweewind blows** (`𝓴𝔀𝓮𝓮𝔀𝓲𝓷𝓭 𝓫𝓵𝓸𝔀𝓼`) di config, persona,
kartu, dan pesan sistem sub-agent.

## 17. Integritas baca/tulis milik sendiri

`tools/builtin/files.py` sekarang membuktikan pekerjaannya:

- `write`/`edit` membaca ulang berkas dan membandingkan byte-per-byte; hasil
  memuat `verified`, `bytes`, `sha256`. Gagal verifikasi = `ok: false`, bukan
  "berhasil" palsu.
- `read` menyertakan `sha256` seluruh berkas, `bytes`, dan `utf8_valid` (false
  bila ada byte yang tidak sah dan diganti `U+FFFD`).
- `verify` membandingkan sidik berkas dengan yang diharapkan — dipakai sebelum
  deploy atau setelah agent menulis kodenya sendiri.

`GET /selfcheck` (dan `!sehat`) menjalankan enam pemeriksaan tanpa LLM: tulis &
baca berkas, registry tool, katalog skill, konfigurasi, otak AI, keamanan shell.
Semuanya harus tetap jalan saat model sedang bermasalah.

---

## Bagian III — satu giliran, satu pesan

Keluhan: "edit teks dan proses tugas jangan dobel pesan atau spam".

### Yang salah sebelumnya
- Pesan status "💭 Berpikir…" dikirim lewat `edit`. Kalau klien/fork tidak
  mendukung `edit`, server membuat pesan BARU setiap pembaruan (tiap 2–8 detik)
  → chat penuh pesan kembar.
- Saat jawaban siap, pesan status dihapus. WhatsApp menampilkan
  "pesan ini telah dihapus", lalu jawaban datang sebagai pesan kedua → terasa
  dobel.
- Balasan yang terdiri dari beberapa potongan teks dikirim satu per satu.

### Yang berlaku sekarang (`transport/api/kemajuan.js`)
1. Status hanya muncul kalau tugas melewati `agent.progressAfterMs` (bawaan 8 dtk).
2. Satu pesan status, diperbarui dengan EDIT. Kalau hasil edit mengembalikan id
   berbeda (artinya server membuat pesan baru), pesan baru itu langsung dihapus
   dan pembaruan dihentikan — tidak ada penumpukan.
3. Kalau jawaban akhir berupa satu teks, pesan status itu DIEDIT menjadi jawaban.
   Tidak ada pesan sisa, tidak ada "pesan dihapus".
4. Kalau jawaban berupa kartu/gambar, status dihapus sekali lalu kartu dikirim.
5. Teks berurutan digabung jadi satu pesan (`gabungTeks`), maksimal 3.500 huruf.
6. Teks identik dalam satu giliran hanya dikirim sekali.
7. Jejak langkah (`withTrace`) selalu menyatu ke pesan yang sama — kalau balasan
   pertama berupa kartu, blok langkah disisipkan ke dalam kartu itu.

Berkas uji: `transport/api/kemajuan.js` diuji lewat `pesan_test.mjs`
(17 pemeriksaan: tugas cepat, tugas lama, klien tanpa edit, jawaban kartu,
trace menyatu).

---

## Bagian IV — UI berbahasa Inggris, aksen Tionghoa, CTA yang benar-benar diketuk

### Bahasa
- Seluruh teks UI perintah kembali ke **bahasa Inggris** (`!help`, `!status`,
  `!skills`, `!search`, `!team`, `!group`, `!health`, `!summary`, `!resume`).
- Nama Indonesia tetap hidup sebagai alias (`!bantuan`, `!gaya`, `!kemampuan`,
  `!cari`, `!tim`, `!grup`, `!sehat`, `!ringkasan`, `!lanjut`) — tidak ditampilkan,
  tapi tidak pernah mematahkan perintah lama.
- Aksen aksara Tionghoa dipakai sebagai hiasan judul: `Everyday · 日常`,
  `Memory & Search · 记忆`, `Working on it · 处理中`.
- Pembersih teks tidak lagi membuang aksara CJK; hanya karakter kontrol dan
  zero-width yang dibuang.

### CTA
Dua bentuk, keduanya native dan bisa diketuk:

| blok | primitive | perilaku |
|---|---|---|
| `quickreply` | `GenAIQuickReplyPrimitive` | diketuk → mengirim teks (mis. `!help`) |
| `footer` | `GenAIFooterActionPrimitive` (`OPEN_URL`) | diketuk → membuka tautan |

`transport/api/ui.js` → `cta(items, { chatId, link })`. Tautan diambil dari
`ui.helpUrl` di `config/default.yaml`; kosong berarti tombol URL tidak muncul.
Cadangan tetap ada: pilihan juga didaftarkan ke `transport/api/pilihan.js`,
jadi kalau klien tidak merender tombol, membalas angka tetap menjalankan
perintahnya.

Berkas uji: `cta_test.mjs` (22) dan `cta_render.mjs` (6 — memastikan
`GenAIQuickReplyPrimitive`, `GenAIFooterActionPrimitive`, dan URL benar-benar
ikut di payload kartu).

---

## Bagian V — akses penuh (mode owner), video, dan batas yang dicabut

Keluhan yang memicu bagian ini: bot menolak mengirim video, menolak membaca
berkas, mengaku "tidak tahu /agent" padahal foldernya ada, shell menolak
perintah, dan banyak pekerjaan berhenti dengan alasan "over limit". Akarnya
bukan satu bug, tapi **lapisan pembatasan yang saling menumpuk**.

### 1. `security.accessMode: owner` (default sekarang)
`security/policy.py` punya jalur baru: bila mode `owner`, `evaluate()`
mengembalikan `allow` untuk semua tool. Yang tersisa hanya
`security.guardSelfKill` — menahan `kill`, `pkill`, `killall`, `setsid`,
`os.kill`, karena bot yang membunuh dirinya sendiri tidak bisa diperbaiki
lewat chat. Matikan dengan `guardSelfKill: false` bila ingin benar-benar nol.

Efeknya di `tools/builtin/shell.py`:
- allowlist executable dilewati (`allow_all = True`),
- cwd boleh folder mana pun (dibuat kalau belum ada),
- `denyPaths` hanya berisi kredensial sesi WhatsApp,
- batas baca mentah 4 MB → 16 MB.

### 2. Berkas: tidak ada lagi "di luar folder proyek"
`tools/builtin/files.py`:
- mode owner → path absolut mana pun boleh,
- alias: `/agent` yang tidak ada sebagai path absolut otomatis dibaca sebagai
  `<root>/agent` — ini penyebab "tidak tahu /agent",
- `DEFAULT_READ_LINES` 800 → 2000.

### 3. Batas keluaran yang membuat model menyerah
| batas | lama | baru | kenapa |
|---|---|---|---|
| `security.outputLimitBytes` | 10 KB | 200 KB | satu `npm install` / `git diff` langsung terpotong |
| `security.toolResultLimitBytes` | 16 KB (hardcode) | 64 KB (bisa diatur) | hasil tool dipotong sebelum masuk konteks |
| `security.execTimeoutSeconds` | 300 s | 1800 s | render video / install / deploy kena timeout |

### 4. Video dan media
`transport/api/actions.js` → `send_file`:
- path absolut mana pun diterima (`KWEE_ALLOW_ANY_PATH=0` untuk mengurung),
- `.env` dan `data/sessions` tetap ditolak (bisa dibuka dengan
  `KWEE_ALLOW_SECRET_FILES=1`),
- video > 15 MB **dipadatkan otomatis** dengan ffmpeg (H.264/AAC, tinggi maks
  720p, `-fs` 15 MB),
- kalau masih kebesaran atau server menolak codec-nya → dikirim sebagai
  **dokumen** dengan catatan ukuran, jadi tetap sampai,
- dokumen > 95 MB ditolak dengan pesan angka yang jelas (bukan "gagal").

Hasil uji nyata: video 21 MB → terkirim sebagai video, `bytes` < 15 MB,
`compressed: true`, `from_bytes` mencatat ukuran asli.

### 5. WhatsApp: konfirmasi ganda dicabut untuk owner
`tools/builtin/wa.py` mewajibkan `confirm=true` **dan** `owner_confirmed` untuk
aksi yang mengubah data (kick, promote, ubah grup, …). Di mode owner keduanya
otomatis — pemilik memang sedang menjalankan aksinya sendiri. Kembalikan dengan
`security.waRequireConfirm: true`.

### 6. Panel Pterodactyl
Pesan 403/401 sekarang menjelaskan penyebabnya: kunci `ptlc_` (client) tidak
bisa dipakai tool ini — butuh kunci **Application** (`ptla_`) dengan izin
read & write. Dulu pesannya hanya "panel menolak (403)".

### 7. Model tidak lagi mengarang "tidak tahu"
`agent/context.py` menyisipkan ke prompt: mode akses, peta folder proyek
(`agent/`, `transport/`, `tools/builtin/`, `prompts/`, `skills/`, `config/`,
`docs/`, `data/`, `workspace/`, `plugins/`), dan aturan "cek dulu dengan
`files list` / `shell ls` sebelum bilang tidak ada". `prompts/rules.md` dapat
bagian **Akses dan peta proyek** dengan aturan yang sama, plus larangan menolak
tugas karena batas yang sebenarnya bisa diatasi (potong, bagi, kompres).

### Berkas uji
- `/tmp/tes_akses.py` — 17 pemeriksaan: policy owner/sandbox, `/agent`,
  `/tmp`, `/etc`, pipe & `&&`, penjagaan bunuh-diri, fail-closed tanpa config.
- `/tmp/uji_kirim.mjs` — 9 pemeriksaan: kirim dari `/tmp`, `.env` ditolak,
  video 21 MB dipadatkan, mode ketat menolak path luar root.

---

## Bagian VI — satu pesan, tanpa hapus, wajib kartu

### 1. Tidak ada `delete` lagi
`transport/api/kemajuan.js` ditulis ulang. Semua panggilan
`{ delete: {…} }` hilang. Kalau server ternyata membuat pesan baru saat
diedit, pesan baru itu **diadopsi** sebagai pesan status (`this.id = sent.key.id`)
lalu pembaruan berhenti — bukan dihapus. Jadi maksimal dua pesan, tanpa
"pesan ini telah dihapus".

### 2. Berpikir + tool + shell = satu blok
`isiStatus()` menyusun satu pesan:

```
♡ aku sedang bekerja
· files — agent/loop.py
· $ npm install
```

Maksimal 4 baris. Tidak ada lagi dua pesan terpisah (satu "💭 Berpikir…",
satu lagi untuk tool). Kalau pesan status sudah tampil, jejak langkah **tidak**
dimasukkan lagi ke kartu (`withTrace(replies, kemajuan.terlihat ? [] : trace)`)
— itu yang dulu terbaca sebagai "thinking dua kali".

### 3. Progres = edit, bukan kirim/hapus
- Jawaban satu teks → pesan status **diedit** menjadi jawaban itu.
- Jawaban kartu → pesan status **diedit** menjadi `✓ selesai · n langkah`.
  Kartu tidak bisa diedit dari teks (batas WhatsApp), dan menghapus dilarang,
  jadi ringkasan satu baris yang dipilih.
- `MAKS_EDIT` 6 → 200 (bukan batas langkah, hanya pengaman loop).

### 4. Wajib GenAI
`transport/router.js` mengirim balasan teks lewat `sendRich(… {type:'text'} …)`
sehingga setiap balasan dirakit jadi kartu `_typename`. Setelan:
`ui.forceCard: true` (default). Bila perakitan kartu gagal, baru jatuh ke teks
polos supaya pengguna tetap dapat jawaban.

### 5. Batas yang masih terasa "over limit"
| setelan | lama | baru |
|---|---|---|
| `llm.timeoutSeconds` | 120 s | 600 s |
| `agent.loop MAX_CONTINUE` (sambung jawaban terpotong) | 4 | 8 |
| `security.execTimeoutSeconds` | 300 s | 1800 s |
| `security.outputLimitBytes` | 10 KB | 200 KB |
| `security.toolResultLimitBytes` | 16 KB | 64 KB |

### 6. `!diag`
Perintah baru (tanpa AI) yang mencetak apa yang **benar-benar** berlaku:
mode akses, penjagaan, batas output/tool/shell, model + maxTokens + timeout,
status kartu, jumlah `denyPaths`, serta berapa penolakan tercatat di
`data/audit.log` dalam 24 jam beserta alasannya. Ini mengganti tebakan
"kenapa ditolak" dengan angka.

### 7. Agent bisa membaca & menulis dirinya sendiri
- `files` dan `shell` tidak lagi dikurung: tulis ke `agent/`, `transport/`,
  `tools/` berhasil (diuji: `agent/_selftest.txt`, `transport/_selftest.js`).
- **cwd bawaan mode owner = root proyek**, bukan `workspace/`. Dulu
  `sed -i transport/router.js` gagal karena cwd-nya `workspace/` → model
  menyimpulkan berkasnya tidak ada.
- `!reload` memuat ulang prompt/skill tanpa restart; perubahan Python butuh
  restart bot (start.sh).

### Berkas uji
- `/tmp/t2/kemajuan_test.mjs` — 14 pemeriksaan: satu pesan, gabung thinking+tool,
  edit (bukan kirim), tanpa delete, kartu → ringkasan, adopsi id baru.
- `/tmp/t2/kartu_self_test.mjs` — 8 pemeriksaan: blok teks jadi kartu
  `_typename`, `forceCard`, trace tidak dobel, `kemajuan.js` tanpa `delete`.
- `/tmp/t2/self_test.py` — 6 pemeriksaan: baca/tulis/edit `agent/`,
  shell tulis di `transport/`, bersih-bersih.

---

## Bagian VII — engine OpenHands (`@openhands/agent-canvas`)

### Kenapa bukan argv
`agent-canvas` bukan CLI sekali-jalan seperti `codex exec`. Ia menyalakan
**stack**: agent-server (uvx, port 18000), automation (18001), dan frontend
statis di balik ingress (port 8000). Karena itu integrasinya berupa **klien
HTTP**, bukan daftar argumen:

```
agent/canvas_engine.py   ← satu tempat untuk semua logika
  ensure_stack()  nyalakan `agent-canvas --backend-only -p 8000` (sekali, dipakai ulang)
  ask()           POST /api/conversations → polling → agent_final_response
  status()        hidup? port? binary? pid?
  stop()          hentikan stack yang kita nyalakan
```

Bentuk respons dibaca **toleran**: id dari `id`/`conversation_id`/
`conversation.id`, status dari `status`/`execution_status`/`state`, teks dari
`response`/`final_response`/`content`/`message`. Body pembuatan percakapan
dicoba berurutan (`initial_user_msg` → `+workspace_mode` → `initialUserMsg` →
`task`) sampai ada yang diterima. Header dikirim dua-duanya
(`X-API-Key` + `Authorization: Bearer`) karena versi agent-server berbeda.

### Pintu masuk: engine + CLI + shell (BUKAN perintah chat)
Tidak ada `!canvas`, tidak ada plugin, tidak ada endpoint `/canvas`. Engine ini
bagian dari agent, dipakai lewat dua jalur:

| jalur | cara |
|---|---|
| engine bawaan | otomatis `canvas` bila `agent-canvas` terpasang, `native` bila tidak (dipilih saat startup, lihat `agent/main.py`) |
| engine per owner | `defaultMechanism: canvas` atau `ownerMechanisms: {"628…": canvas}` |
| CLI | `kwee ask "…"` · `kwee canvas status|start|stop` · `kwee shell "…"` · `kwee mechanisms` · `kwee diag` · `kwee serve` |
| shell langsung | `./canvas.sh status` · `python3 -m agent.cli …` · `python3 -m agent.canvas_engine …` |

`bin/kwee` (dan `package.json → bin`) memanggil `agent/cli.py`, yang memakai
lapisan yang sama dengan bot: `agent/config.py` → tool → engine. Jadi policy,
audit, dan batas yang berlaku di WhatsApp juga berlaku di CLI.

### Pemasangan (BAWAAN, bukan opt-in)
OpenHands adalah **engine bawaan Kwee**. `setup.sh` dan `start.sh` memasangnya
tanpa diminta; `KWEE_CANVAS=0` (atau `start.sh --no-canvas`) mematikannya.
Urutan yang dijalankan:
1. memeriksa Node ≥ 24 (syarat paket),
2. `npm install -g @openhands/agent-canvas` bila `agent-canvas` belum ada,
3. memasang `uv` (astral) bila `uvx` belum ada — agent-server dijalankan via uvx,
4. mencetak `agent-canvas --info` (versi stack + port).

`defaultMechanism: canvas` sudah tertulis di `config/default.yaml`, jadi setelah
paket terpasang engine-nya langsung OpenHands. Kalau paketnya tidak ada,
`mechanisms.resolve()` mengembalikan `native` — bot tetap jalan.

`kwee install` melakukan hal yang sama dari terminal (npm global + uv).

Stack **tidak** dinyalakan saat start bot: bot menyalakannya saat pertama
dipakai, supaya start tetap cepat. Log: `data/canvas.log`. PID: `data/canvas.pid`.
Kunci API dibuat otomatis di `data/canvas.key` (0600) dan dipakai konsisten —
bisa ditimpa `LOCAL_BACKEND_API_KEY` atau `mechanisms.canvas.apiKey`.

### Kalau gagal
- `agent-canvas` tidak ada → pesan: `npm install -g @openhands/agent-canvas`.
- stack belum siap → menyebut batas `startTimeoutSeconds` dan `data/canvas.log`
  (pertama kali bisa lama: uvx mengunduh agent-server).
- 401/403 → dijelaskan bahwa stack harus dinyalakan oleh bot atau kuncinya
  disamakan lewat `LOCAL_BACKEND_API_KEY`.
- Tugas melewati batas → menyebut percakapan masih berjalan di canvas beserta id-nya.

### Berkas uji
`/tmp/t2/cli_test.py` — 5 pemeriksaan: `kwee mechanisms`, `diag`, `shell`,
`canvas status`, dan `ask` tanpa binary (kode keluar 3 + instruksi pasang).

`/tmp/t2/canvas_test.py` — 9 pemeriksaan dengan server HTTP tiruan:
ping, status, `ask` (create → poll → final), body `initial_user_msg`, polling
berhenti saat `done`, stack mati → error jelas, dan dispatch
`mechanisms.run_external("canvas", …)`.

---

## Bagian VIII — engine jawaban: OpenHands menggantikan agent native

Keluhan: "native ganti openhand saja, agent native jelek (/agent, /security)".

### Yang berubah
Pesan biasa (bukan perintah `!`) tidak lagi dijawab oleh inti agent Python.
Transport sekarang punya pemilih engine di `transport/engine.js`:

```yaml
engine:
  mode: openhands     # bawaan
  fallback: true      # canvas mati → inti Python menangani (bot tidak diam)
```

Alur: pesan → `router.js` → `engineReply()` → stack agent-canvas
(`POST /api/conversations` → polling → `agent_final_response`) → kartu GenAI.

Kalau `engine.mode: native`, jalur lama (Python `agent/main.py`) dipakai seperti
sebelumnya. Kalau canvas mati dan `fallback: false`, balasan berisi alasan yang
jelas — bukan jawaban native diam-diam.

### Yang tetap di transport
Perintah `!`, kartu `_typename`, pengiriman media, aksi grup, plugin, anti-dobel,
dan progres satu-pesan. Engine hanya menggantikan "siapa yang menjawab".

### Konsekuensi yang perlu diketahui
- Konteks percakapan sekarang dipegang agent-server OpenHands, bukan
  `memory/store.py`. Riwayat Python tidak dipakai untuk menjawab.
- Tool Python (files/shell/search/skill/wa) tetap hidup untuk perintah `!` dan
  untuk mode `native`; OpenHands memakai tool bawaannya sendiri (bash, editor,
  browser) di dalam workspace-nya.
- `!diag` dan `kwee diag` sekarang mencetak `engine` yang aktif.

### Kredensial model untuk OpenHands
agent-server memakai nama env sendiri. Saat stack dinyalakan,
`canvas_engine.ensure_stack()` meneruskan dari config Kwee:
`llm.model → LLM_MODEL`, `llm.apiKey → LLM_API_KEY`,
`llm.baseUrl → LLM_BASE_URL`, plus `LLM_TEMPERATURE`. Tanpa ini stack menyala
tapi tidak bisa berpikir ("model not configured") — nilainya tidak pernah
diketik ulang di kode.

### Berkas uji
`/tmp/t2/engine_test.mjs` — 8 pemeriksaan dengan server HTTP tiruan:
mode bawaan `openhands`, `native` bisa dipilih, deteksi stack hidup,
`askOpenHands` (create → poll → final), body `initial_user_msg`,
`engineReply` menghasilkan `replies`, dan canvas mati → error rapi.

---

## Bagian IX — unduh media dari tautan (TikTok dkk)

Gejala di panel: owner kirim `https://vt.tiktok.com/...` → bot menjawab "tidak
bisa diunduh", lalu berceramah soal Ketentuan Layanan. Dua sebab:

1. `command -v yt-dlp` kosong — yt-dlp hanya terpasang sebagai **modul Python**,
   jadi pemanggilan dari shell gagal (`exit=1`). Bukan TikTok yang memblokir.
2. Tidak ada jalur tetap untuk tugas ini, jadi model mengarang alasan.

### Perbaikan
| berkas | isi |
|---|---|
| `start.sh` | shim `~/.local/bin/yt-dlp` → `python -m yt_dlp "$@"`, `curl_cffi` best-effort untuk `--impersonate`, ringkasan `yt-dlp siap: <versi>` |
| `tools/builtin/downloader.py` | tool **`download`**: `url`, `kind` (video/audio), `quality`, `cookies`; memakai modul `yt_dlp`, memasangnya sekali bila absen, menyimpan ke `data/media/downloads`, mengembalikan `path` + `bytes` |
| `prompts/rules.md` | larangan menolak dengan alasan ToS/hak cipta; wajib jalankan tool lalu kirim lewat `wa` |
| `start.sh` self-test | `download` masuk daftar tool wajib |

Alur yang benar sekarang: tautan → `download` → `path` → `wa` (`send_media`).

### Uji
`/tmp/t2/download_test.py` — 5 pemeriksaan dengan modul `yt_dlp` tiruan:
unduh berhasil, berkas benar-benar ada, ukuran terbaca, tersimpan di
`data/media/downloads`, dan URL salah ditolak.

---

## Bagian X — memori awal, unduhan 1080p, browser penuh, UI status

### 1. `memory/seed.py` — kwee.db + memori ruangan diisi tangan
`python3 -m memory.seed --reset` mengosongkan riwayat, profil, izin, dan berkas
memori ruangan, lalu menulis 9 fakta + 4 preferensi yang **sudah pasti benar**
(peta folder, engine OpenHands, daftar perintah, aturan unduh, aturan UI).
`--if-empty` dipakai `start.sh` sehingga tuning manual tidak pernah ditimpa.
Profil DB juga diisi: `seed:version`, `ui:lang`, `engine:default`,
`download:quality`.

### 2. `download` — 1080p dengan fallback 720p, playlist opsional
`-S res:1080,ext:mp4:m4a` memilih resolusi terdekat di bawah target, jadi kalau
sumbernya hanya 720p/480p yt-dlp turun sendiri (tidak perlu rantai format
manual). `playlist: true` untuk serial/anime; bawaan satu item.

### 3. `browser` — 43 aksi (sebelumnya hanya 16 yang diizinkan)
`tools/builtin/browser.py` punya `_ALLOWED` berisi 16 aksi, padahal
`transport/browser.mjs` mendukung 43. Aksi seperti `download`, `upload`, `tabs`,
`attribute`, `elements`, `a11y`, `metrics`, `trace`, `scroll`, `forward` ditolak
di lapisan Python — itu "belum terintegrasi". Sekarang daftarnya lengkap dan
argumen skalar (`attribute`, `index`, `limit`, `x`, `y`, `deltaY`, `tab`,
`path`, `quality`) diteruskan. `browser.mjs` juga menambah stealth ringan
(`navigator.webdriver`) dan `--blockImages` untuk hemat kuota.

### 4. UI status (thinking) bergaya GenAI
Kartu GenAI tidak bisa diedit, jadi status tetap SATU pesan teks — tapi
rupanya disamakan dengan kartu: merek `𝓴𝔀𝓮𝓮𝔀𝓲𝓷𝓭 𝓫𝓵𝓸𝔀𝓼 · 思考中`, langkah
berbutir `▸`, penutup `♡ 进行中 · langkah N`. Saat selesai:
`✦ … · 完成 / ✓ N langkah`.

### 5. Aksara Tionghoa di `!help`
Tiap kategori kini berlabel ganda: `Everyday · 日常 · 每天都会用`,
`Memory & Search · 记忆与搜索`, `Teamwork · 团队 · 一起干活`,
`Files & Terminal · 文件与终端`, `Task Control · 控制 · 开始与停止`.

---

## Bagian XI — status GenAI, memori 25k, plugin, tambah anggota

### 1. Status (thinking/progres) = kartu GenAI yang DIEDIT
`transport/api/rich.js` menambah `editRichCard()`: kartu dibangun ulang lalu
dikirim sebagai `protocolMessage` bertipe `MESSAGE_EDIT` (mekanisme yang sama
dengan trik bypass "Unduh"), jadi pesan yang sama diperbarui — bukan pesan baru.
`kemajuan.js` memakai jalur ini bila `genai.kirim/edit` tersedia:
kirim pertama = kartu, pembaruan = edit kartu, dan bila server menolak edit
kartu, jatuh ke edit teks biasa. Tidak ada `delete` sama sekali.

### 2. Memori 25k: `memory.seed --learn`
`--learn` memindai proyek dan menulis fakta dari isi nyata: pohon folder,
daftar perintah `!` (dibaca dari `transport/commands.js`), skema tool
(nama + deskripsi + parameter + wajib), kalimat pembuka setiap berkas sumber,
daftar plugin, kunci config, dan bagian di `prompts/`. Target 25.000 karakter,
dipotong otomatis. `memory.maxChars: 25000` membuat blok memori yang masuk ke
system prompt tidak lagi terpotong di 8.000.

### 3. Plugin: dijalankan, bukan disuruh pemilik
`plugin_write` dan `plugin_control` dulu menuntut `owner_confirmed` walau mode
`owner` — pemilik sendiri yang meminta. Sekarang mode owner (tanpa
`pluginRequireConfirm`) boleh langsung. `prompts/rules.md` menambah aturan:
`plugin_list` → `plugin_run`, jangan menyuruh pemilik menjalankan manual.

### 4. Tambah anggota grup
`transport/api/actions.js` belum punya aksi `add` — hanya kick/promote/demote.
Sekarang ada, dan JID `…@bot` dinormalkan ke `…@s.whatsapp.net` sebelum dikirim
(WhatsApp menolak JID bot). Hasil per target dikembalikan apa adanya
(`status` 200/403/409) sehingga "ditolak server" hanya muncul kalau benar-benar
ditolak. `add` juga masuk `DESTRUCTIVE` di `wa.py` agar mode owner otomatis
mengonfirmasi.

### Uji
`/tmp/t_kemajuan_genai.mjs` — 6 pemeriksaan: status dikirim sebagai kartu,
pembaruan lewat edit kartu, tidak ada pesan teks baru, id kartu dipakai ulang,
edit kartu gagal → edit teks, tanpa delete.

---

## Bagian XII — memori & skills benar-benar masuk ke engine

### Akar masalah (bukan salah isi suntikan)
Setelah engine jawaban pindah ke OpenHands, `transport/engine.js` mengirim
**teks pengguna saja** ke agent-server. Memori dan skills dirakit di
`agent/context.py` — jalur yang tidak lagi dipakai untuk menjawab. Jadi apa pun
yang disuntikkan ke `MEMORY.md` tidak pernah terbaca, dan skill hanya pajangan.
Ditambah dua hal: `read_memory` memotong di 8.000 karakter, dan tidak ada
jembatan dari OpenHands ke tool Kwee.

### Perbaikan
| berkas | isi |
|---|---|
| `transport/memori.js` (baru) | `buildPreamble()` membaca `data/memory/notes/MEMORY.md` (+`USER.md`), semua `skills/internal/*.md`, katalog `skills/external/*.md`, dan memilih 3 skill relevan berdasarkan `triggers` + deskripsi |
| `transport/engine.js` | pesan ke engine = preamble + `Tugas dari pemilik: …` (bukan teks mentah) |
| `agent/cli.py` | `kwee tool <nama> '<json>'` — jembatan tool Kwee untuk bash di dalam OpenHands |
| `start.sh` | shim `~/.local/bin/kwee` supaya CLI bisa dipanggil dari shell engine |
| `agent/context.py` | `memory.maxChars` (25.000) diteruskan ke `read_memory` |

### Bukti
`/tmp/t_memori.mjs` — 8/8: skill terbaca dari disk, skill internal ada, memori
ikut, SKILL WAJIB ikut, katalog ikut, `aksi-grup` terpilih untuk "kick anggota
grup", penanda konteks ada.

`/tmp/t_engine_body.mjs` — 4/4: body `POST /api/conversations` yang diterima
server tiruan benar-benar memuat `Pemilik: vir`, `SKILL WAJIB`, dan tugas asli.
Itu bukti konteks sampai ke engine, bukan hanya tersimpan di disk.

---

## Bagian XIII — melihat gambar dan web

### Kenapa bot "tidak bisa memahami gambar"
Tool `vision` sehat, tapi engine yang menjawab (OpenHands) tidak menerima
gambar — transport hanya mengirim teks. Ditambah lagi, tidak ada satu perintah
pun yang menjawab "lihat web ini" untuk halaman yang dirender JavaScript.

### Perbaikan
| berkas | isi |
|---|---|
| `agent/main.py` | `POST /vision` (auth `X-Agent-Token`): `{asset|path|url, prompt}` → `{text}` |
| `transport/engine.js` | `describeAttachments()` mengubah lampiran gambar jadi deskripsi lewat `/vision`, lalu menempelkannya ke tugas: `[Gambar yang dikirim pemilik — ini yang terlihat]` |
| `transport/router.js` | `engineReply(text, config, attachments)` — lampiran tidak lagi dibuang |
| `tools/builtin/web_see.py` (baru) | buka URL → screenshot → `vision` → `{look, text, title, screenshot}` |
| `prompts/rules.md` | lihat gambar sebelum menjawab; `web_fetch` gagal → `web_see` |
| `start.sh` | `vision` dan `web_see` masuk daftar tool wajib |

### Uji
`/tmp/t_vision_engine.mjs` — 6/6 dengan dua server tiruan (Python `/vision` dan
canvas): gambar dijelaskan, id aset benar-benar dikirim, deskripsi masuk ke body
engine, memori tetap ikut, tugas asli ada, lampiran non-gambar dilewati.

`web_see` diuji dengan handler browser/vision tiruan: hasil `{ok, url, title,
look}`, dan URL salah ditolak.
