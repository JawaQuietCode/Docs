# Kwee Agentic Field Journal — perubahan v2.2

## Memori adaptif native

Ditambahkan `memory/intelligence.py`, penyimpanan SQLite per ruang/tenant. Delapan jenis entri tersedia: episodic, semantic, procedural, working, long-term, associative, temporal, dan hierarchical. Indeks lokal mendukung pencarian berbobot relevansi + kebaruan, retrieval, relasi antar-entri, provenance, reflection statistik, serta kompresi deterministik yang menyimpan daftar sumber entri asal. Working memory dapat diberi TTL. Tidak dibutuhkan Docker atau provider vektor eksternal.

Tool `memory_intelligence` terdaftar otomatis oleh registry native (bukan plugin `!`). Aksi: `add`, `search`, `retrieve`, `link`, `compress`, `reflect`, dan `provenance`. Data berada di `memory/intelligence.sqlite3` di ruang aktif.

## Persistent Mission Center

Tool native `mission_center` menyimpan misi ke basis data lokal yang sama. Mendukung `create`, `list`, serta perubahan status ke active, paused, completed, atau cancelled. Misi bertahan setelah proses bot dimulai ulang.

## Tim agent dan kartu interaktif

Proyek sudah mempunyai `subagent` native dengan fan-out sampai lima pekerja dan UI AI Rich di `transport/api/ui.js` / `airich.js`. Registry menemukan kedua tool baru secara otomatis; tidak perlu menambahkan perintah chat atau plugin.

## Startup browser

`start.sh` sekarang menetapkan cache Playwright di direktori proyek, mencoba memasang dependensi sistem Chromium saat berjalan sebagai root, memakai CLI Playwright yang sudah terpasang, dan memvalidasi browser melalui launch/close headless nyata. Pemeriksaan kemampuan kini membedakan modul Playwright yang terpasang dengan Chromium yang benar-benar bisa dijalankan. Kegagalan browser tetap non-fatal bagi bot dan log diagnostik ditampilkan.

## Validasi

- `bash -n start.sh`: lulus.
- `python3 -m unittest tests.test_memory_intelligence -v`: 5 tes lulus.
- `python3 -m unittest tests.test_startup_bootstrap -v`: 7 tes lulus.
- Suite penuh: 97 tes ditemukan; sejumlah impor/tes gagal di sandbox karena dependensi proyek belum diinstal (`httpx`, `PyYAML`, `cryptography`).
