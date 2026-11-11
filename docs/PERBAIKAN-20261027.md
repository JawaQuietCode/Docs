# Perbaikan 2026-10-27

## Akar masalah "Failed to parse URL ...:NaN"
`transport/config.js` (parser YAML mini) tidak membuang komentar sebaris, jadi
`port: 8000   # ingress` terbaca sebagai teks → `Number()` = NaN. OpenHands tak pernah
terjangkau dari WhatsApp; tiap pesan jatuh ke inti Python. Efek samping pada ±14 kunci
lain (timeout, `autoStart`, `card.label.*`). Diperbaiki: `stripInlineComment()`.

## Kartu thinking/progres tidak teredit
- `editRichCard` membangun kartu dengan `response_id` baru tiap edit → klien mengabaikannya.
  Kini `response_id` kartu pertama disimpan (`sent.botResponseId`) dan dipakai ulang.
- Kegagalan edit dulu diam-diam; kini dicatat (`[kemajuan] edit kartu gagal`), berhenti
  setelah 2x, dan tidak lagi mencoba edit teks pada kartu (pasti ditolak).

## start.sh
- Satu `npm install` dari package.json (Baileys, boom, pino, playwright sudah dipin).
- Prewarm canvas dicatat PID-nya; `cleanup` menghentikan agent, WA, prewarm, dan stack canvas.
- `wait` hanya pada anak yang dikenal (tidak menggantung); SIGINT/SIGTERM keluar bersih.
- Transport WhatsApp diawasi: restart otomatis maks 5x berturut-turut.
- Chromium Playwright diunduh sekali; modul Node diverifikasi ulang setelah pindah ke Node 24.
- Peringatan bila port canvas bentrok.

## Memori (kwee.db)
`kwee.db` memang TIDAK ada di zip; dibuat saat start pertama. Bug: `memory/seed.py` memanggil
`main()` sebelum `learn_entries()` didefinisikan → `--learn` selalu NameError. Guard dipindah
ke bawah. Ditambah `KOMPETENSI` (coding, agentic, respons, pengetahuan, skill, struktur).

## UI perintah (transport/commands.js + api/ui.js)
`!help` kini menu yang bisa diketuk (tabel kelompok + tombol + balasan angka 1-6) dan
`!help <kelompok>` membuka SEMUA perintah kelompok itu. `!status`, `!plugins`, `!skills`,
`!diag` (header yang dulu hilang), `!log`, `!health` memakai tabel + tombol, bukan teks.
Log "diabaikan (bukan owner)" kini sekali per pengirim + petunjuk WA_OWNER_NUMBERS.
