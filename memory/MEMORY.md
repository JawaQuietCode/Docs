# MEMORY

> Memori kerja jangka panjang. Simpan fakta yang berasal dari owner atau sumber yang dapat diverifikasi; jangan isi kekosongan dengan pengetahuan bawaan model.

## Fakta proyek terverifikasi
- Proyek ini adalah Kwee Agentic: transport WhatsApp Node.js ESM dan core agent Python.
- Root runtime menggunakan `config/default.yaml`, `prompts/`, `skills/`, `memory/`, `agent/`, `tools/`, dan `transport/`.
- SQLite utama berada di `data/memory/kwee.db`; database dibuat/migrasi saat runtime dan tidak disertakan dalam source archive.

## Kebijakan sumber pengetahuan
- Fakta khusus owner/proyek harus berasal dari percakapan owner, file proyek, atau database yang benar-benar dibaca.
- Informasi yang berubah harus diverifikasi melalui pencarian web atau sumber primer dan dicatat dengan tanggal/sumber.
- Pengetahuan umum model adalah hipotesis awal, bukan fakta proyek. Tandai sebagai belum diverifikasi atau lakukan riset.
- Jangan pernah menyimpan API key, token, password, cookie, session secret, atau data autentikasi.

## Preferensi operasional
- Owner menginginkan hand-off otonom 24/7 pada VPS/Pterodactyl dengan sedikit interupsi untuk pekerjaan rutin.
- Jangan meminta izin berulang untuk langkah rutin yang jelas; pertahankan approval untuk tindakan berisiko tinggi dan irreversibel.
- Skill yang benar-benar dimuat harus dapat dilihat melalui status visual loader.
- Request LLM diberi jarak minimum 10 detik secara default untuk mengurangi lonjakan dan risiko HTTP 429; tetap hormati Retry-After.

## Keputusan
- Katalog skill eksternal dipasang on-demand; isi panjang tidak seluruhnya dimasukkan ke setiap prompt.
- Sumber eksternal Agent-Reach dan Scientific Agent Skills diambil oleh installer yang mem-pin branch/commit pada saat instalasi dan dicatat dalam manifest.
