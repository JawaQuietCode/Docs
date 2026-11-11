"""Gerbang eksekusi: deteksi balasan yang MENYERAHKAN perintah, bukan menjalankannya.

Keluhan nyata pemilik: "kwee malah berikan code terminal bukan execution shell
sendiri, dia agent bukan chat bot". Penyebabnya bukan tool yang rusak, tapi
kebiasaan model: saat ragu, ia menulis

    Jalankan ini di terminal:
    ```bash
    npm install && npm test
    ```

lalu selesai — padahal bot ini punya tool `terminal`/`shell` dan seharusnya
menjalankannya sendiri, lalu melaporkan output nyata.

Modul ini membedakan dua hal yang permukaannya mirip:

* **SERAHKAN perintah** — teks berisi blok shell berisi perintah, disertai
  ajakan menjalankan ("jalankan", "copy", "buka terminal"). Ini yang dicegah:
  `loop` menyuntik pesan sistem dan memaksa model memanggil tool.
* **KODE sebagai hasil** — mis. owner minta "buatkan fungsi python". Blok
  ```python``` bukan perintah shell, jadi tidak pernah dianggap pelanggaran.
  Blok ```bash``` juga tidak dianggap pelanggaran bila isinya cuma contoh di
  tengah penjelasan panjang (tanpa ajakan menjalankan).

Sengaja konservatif: lebih baik meloloskan satu balasan malas daripada memaksa
model menjalankan perintah yang memang tidak diminta.
"""
from __future__ import annotations

import re

# Bahasa fence yang dianggap "perintah untuk dijalankan".
SHELL_LANGS = {
    "bash", "sh", "shell", "zsh", "console", "terminal", "shell-session",
    "powershell", "ps1", "cmd", "bat", "batcmd", "fish", "ksh",
}

# Baris yang bentuknya perintah shell (bukan sekadar teks di dalam fence).
_CMD_LINE = re.compile(
    r"^\s*(?:sudo\s+(?:-E\s+)?)?"
    r"(?:apt|apt-get|apk|yum|dnf|brew|npm|pnpm|yarn|npx|bun|pip|pip3|python3?|"
    r"node|deno|git|curl|wget|docker|docker-compose|podman|systemctl|service|"
    r"journalctl|cd|ls|cat|cp|mv|rm|mkdir|rmdir|touch|chmod|chown|tar|unzip|zip|"
    r"ssh|scp|rsync|export|source|bash|sh|zsh|nohup|pm2|supervisorctl|"
    r"uvicorn|gunicorn|flask|psql|mysql|sqlite3|redis-cli|mongo|mongosh|"
    r"kill|pkill|killall|ps|top|htop|df|du|free|uname|whoami|hostname|"
    r"grep|rg|find|sed|awk|jq|ffmpeg|yt-dlp|playwright|echo|printf|pwd|"
    r"sleep|test|date|env|which|type|tee|xargs|cut|sort|uniq|wc|head|tail|"
    r"less|more|nano|vim|vi|make|cmake|go|cargo|rustc|java|javac|php|ruby|"
    r"perl|conda|mamba|pyenv|nvm|snap|flatpak|crontab|mount|umount|ufw|"
    r"iptables|nginx|apachectl|certbot|kubectl|helm|terraform|aws|gcloud)\b"
    # Cmdlet PowerShell: Verb-Noun (Restart-Service, Get-ChildItem, ...).
    r"|^\s*[A-Z][A-Za-z]+-[A-Z][A-Za-z]+\b"
)
# Baris bergaya prompt: "$ npm install", "# systemctl restart kwee"
_PROMPT_LINE = re.compile(r"^\s*(?:\$|#|>|PS>|C:\\>)\s+\S")
# Sama, tapi boleh muncul di tengah baris ("Jalankan: $ curl ...").
_PROMPT_ANY = re.compile(r"(?:\$|>|PS>)\s+(\S.*)$")

# Ajakan menjalankan yang TIDAK ambigu — cukup satu ini untuk memicu gerbang.
_STRONG_PHRASES = (
    "jalankan ini", "jalankan perintah", "jalankan berikut", "coba jalankan",
    "silakan jalankan", "tinggal jalankan", "jalankan dulu", "kamu jalankan",
    "buka terminal", "di terminal:", "lewat terminal", "copy paste", "copy-paste",
    "copas", "salin dan tempel", "tempel di terminal", "paste this", "copy this",
    "run this", "run the following", "execute this", "execute the following",
    "in your terminal", "on your server", "di server:", "di vps", "lewat ssh",
    "via ssh", "masuk ssh", "ketik perintah",
)
# Ajakan lemah: hanya dihitung bila tidak ada penanda "ini sudah dijalankan".
_WEAK_PHRASES = (
    "jalankan", "ketik", "copy", "salin", "tempel", "paste",
    "perintah berikut", "perintah ini", "perintahnya",
)
# Penanda bahwa teks ini LAPORAN hasil, bukan instruksi.
_REPORT_HINTS = (
    "sudah kujalankan", "sudah dijalankan", "sudah saya jalankan", "sudah jalan",
    "berhasil dijalankan", "baru kujalankan", "exit code", "kode keluar",
    "output:", "outputnya", "keluaran:", "already ran", "has run",
)

# Permintaan EKSPLISIT untuk teks perintah (jangan dicegah).
_TEXT_REQUEST_PHRASES = (
    "kasih perintah", "kasih command", "perintahnya apa", "perintah untuk",
    "jangan dijalankan", "jangan jalankan", "biar aku jalankan",
    "biar saya jalankan", "aku jalankan sendiri", "saya jalankan sendiri",
    "buat aku copy", "buat saya copy", "biar aku copy", "biar bisa aku copy",
    "cuma mau lihat perintah", "hanya mau lihat perintah", "tanpa dijalankan",
    "give me the command", "just the command", "don't run",
)

_FENCE = re.compile(r"```([A-Za-z0-9_+.-]*)\n(.*?)```", re.S)
# Kode sebaris: "Jalankan `npm install` dulu"
_INLINE_CODE = re.compile(r"`([^`\n]{2,140})`")

# Pesan sistem yang disuntikkan saat model menyerahkan perintah.
NUDGE = (
    "(Sistem) Kamu AGENT yang MENJALANKAN sendiri, bukan chatbot yang menyuruh "
    "orang mengetik perintah. JANGAN menyerahkan perintah terminal/kode untuk "
    "dijalankan pengguna. Sekarang panggil tool `terminal` (cwd bertahan) atau "
    "`shell` dan jalankan sendiri, lalu laporkan OUTPUT NYATA yang kamu dapat — "
    "termasuk kode keluar dan pesan error aslinya bila gagal. Kalau memang tidak "
    "diizinkan oleh keamanan atau tool tidak tersedia, sebutkan alasan persisnya."
)


def requested_text(user_text: str) -> bool:
    """True bila pengguna memang minta teks perintah untuk dijalankan sendiri."""
    low = str(user_text or "").lower()
    return any(p in low for p in _TEXT_REQUEST_PHRASES)


def _phrase(low: str):
    """(frasa, kuat?) — frasa ajakan menjalankan yang ditemukan, bila ada."""
    for p in _STRONG_PHRASES:
        if p in low:
            return p, True
    for p in _WEAK_PHRASES:
        if re.search(rf"(?<!\w){re.escape(p)}(?!\w)", low):
            return p, False
    return "", False


def _fences(text: str):
    for m in _FENCE.finditer(text):
        lang = (m.group(1) or "").strip().lower()
        yield lang, m.group(2), m.start(), m.end()


def _command_lines(body: str):
    """Baris yang jelas perintah; kalau tidak ada yang cocok, pakai isi fence."""
    out = []
    for line in body.splitlines():
        if not line.strip():
            continue
        if _PROMPT_LINE.match(line) or _CMD_LINE.match(line):
            out.append(line.strip())
    if not out:
        # Fence bahasa shell yang isinya tidak dikenali (mis. `bun run x`) tetap
        # dianggap perintah — di dalam fence bash, teks prosa hampir tidak ada.
        out = [l.strip() for l in body.splitlines() if l.strip()]
    return out


def find_handoff(text: str, user_text: str = ""):
    """Kembalikan detail pelanggaran, atau None bila balasan wajar.

    Return: {"reason": str, "commands": [str], "lang": str} atau None.
    """
    text = str(text or "")
    if not text.strip():
        return None
    if requested_text(user_text):
        return None

    low = text.lower()
    phrase, strong = _phrase(low)
    # Laporan hasil ("sudah kujalankan, outputnya ...") bukan instruksi —
    # kecuali ada ajakan kuat yang eksplisit.
    if not strong and any(h in low for h in _REPORT_HINTS):
        return None

    total = max(1, len(text))
    best = None
    for lang, body, start, end in _fences(text):
        if lang not in SHELL_LANGS:
            continue
        cmds = _command_lines(body)
        if not cmds:
            continue
        # Porsi balasan yang ditempati blok perintah ini.
        ratio = (end - start) / total
        # Sinyal tambahan wajib: ajakan menjalankan, ATAU blok mendominasi
        # balasan (artinya jawabannya memang cuma "ini perintahnya").
        if not (phrase or ratio >= 0.5):
            continue
        cand = {"reason": phrase or "blok perintah mendominasi balasan",
                "commands": cmds[:5], "lang": lang}
        if best is None or len(cand["commands"]) > len(best["commands"]):
            best = cand

    if best:
        return best

    # Tanpa fence: baris bergaya "$ perintah" yang jelas perintah untuk dijalankan.
    if phrase:
        prompt_lines = []
        for line in text.splitlines():
            m = _PROMPT_ANY.search(line)
            if m and _CMD_LINE.match(m.group(1)):
                prompt_lines.append(m.group(0).strip())
        if prompt_lines:
            return {"reason": phrase, "commands": prompt_lines[:5], "lang": "inline"}

    # Tanpa fence: kode SEBARIS yang isinya perintah, disertai ajakan menjalankan.
    # Contoh nyata: "Jalankan `npm install` dulu ya".
    if phrase:
        inline = [m.group(1).strip() for m in _INLINE_CODE.finditer(text)
                  if _CMD_LINE.match(m.group(1).strip())]
        if inline:
            return {"reason": phrase, "commands": inline[:5], "lang": "inline-code"}

    # Deretan perintah TANPA fence. Inilah bentuk yang lolos dari semua aturan
    # di atas, dan persis yang dikeluhkan pemilik:
    #     ls -la /                                 # lihat isi root
    #     which agent                              # kalau maksudnya binary
    #     find / -maxdepth 3 -iname '*agent*' | head -50
    # Minimal 2 baris berurutan berbentuk perintah, dan setidaknya satu baris
    # punya penanda khas perintah (flag, path, pipe, redirect, wildcard) supaya
    # daftar bernomor/poin biasa tidak ikut tertangkap.
    if not phrase:
        _MARK = (" -", "--", "/", "|", ">", "#", "*", "=")
        best_run, cur = [], []
        for line in text.splitlines():
            t_line = line.strip()
            if not t_line or t_line.startswith("#"):
                continue            # baris komentar tidak memutus deretan
            if _CMD_LINE.match(t_line) or _PROMPT_LINE.match(t_line):
                cur.append(t_line)
                if len(cur) > len(best_run):
                    best_run = list(cur)
            else:
                cur = []
        if len(best_run) >= 2 and any(m in "\n".join(best_run) for m in _MARK):
            return {"reason": "deretan perintah tanpa blok kode",
                    "commands": best_run[:5], "lang": "plain"}

    # Tanpa fence, tanpa ajakan: balasan PENDEK yang isinya memang cuma perintah
    # ("sudo apt install ffmpeg"). Ini pola paling malas dari model: menyalin
    # perintah tanpa kalimat pengantar apa pun.
    if not phrase and len(text) <= 300:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        cmds = [l for l in lines if _CMD_LINE.match(l) or _PROMPT_LINE.match(l)]
        if cmds and len(cmds) == len(lines):
            return {"reason": "balasan hanya berisi perintah",
                    "commands": cmds[:5], "lang": "bare"}

    return None


def describe(hit: dict) -> str:
    """Ringkasan satu baris untuk log."""
    if not hit:
        return ""
    cmds = " | ".join(c[:60] for c in (hit.get("commands") or [])[:3])
    return f"{hit.get('reason')} -> {cmds}"
