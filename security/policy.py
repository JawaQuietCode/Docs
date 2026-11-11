"""policy.py — gerbang izin & deteksi aksi berbahaya.

Model keamanan mengikuti Codex CLI (mode sandbox + kebijakan approval) dan
Claude Code (sintaks izin `Tool(pola)`, urutan deny > ask > allow), dengan
fallback software karena container Pterodactyl kemungkinan tanpa Landlock.
"""
import fnmatch
import os
import re

# Pola perintah berbahaya. Dipakai sebagai lapisan terakhir selain mode sandbox.
DANGEROUS_PATTERNS = [
    # `rm` yang menyasar path kritis (akar sistem / home / root container).
    # `rm -rf` DI DALAM folder kerja DIIZINKAN (lihat shell.py) supaya tugas
    # coding/scraper bisa membersihkan artefaknya; yang diblokir hanya target
    # di luar folder kerja.
    (r"\brm\s+(-[a-zA-Z]+\s+)*/(\s|$|\*|\.\s*$)", "hapus dari akar filesystem"),
    (r"\brm\s+(-[a-zA-Z]+\s+)*(~|\$HOME)(\s|/|$)", "hapus home directory"),
    (r"\brm\s+(-[a-zA-Z]+\s+)*/(etc|usr|var|bin|sbin|lib|boot|opt|root)(\s|/|$)", "hapus direktori sistem"),
    (r"\brm\s+(-[a-zA-Z]+\s+)*/home(\s|/\.\.|$)", "hapus direktori /home"),
    (r"\brm\s+(-[a-zA-Z]+\s+)*/home/container(\s|/\.\.|$)", "hapus akar proyek"),
    (r"\bmkfs(\.\w+)?\b", "format filesystem"),
    (r"\bdd\s+.*of=/dev/", "tulis langsung ke perangkat"),
    (r":\(\)\s*\{.*\}\s*;?\s*:", "fork bomb"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "matikan/reboot sistem"),
    (r"\bDROP\s+(TABLE|DATABASE)\b", "hapus database"),
    (r">\s*/etc/", "tulis ke /etc"),
    (r"\bchmod\s+(-R\s+)?777\s+/", "ubah izin root jadi 777"),
    (r"\bkill\s+-9\s+-1\b", "bunuh semua proses"),
    # BUNUH-DIRI: bot TIDAK BOLEH mematikan prosesnya sendiri (node transport /
    # python agent / start.sh). Insiden nyata: `setsid bash -c 'sleep 2; kill
    # -TERM <pid node> <pid python>'` membuat bot mati total & tak lagi membalas.
    (r"\bsetsid\b", "lepaskan proses dari sesi (bisa membunuh proses bot sendiri)"),
    (r"\b(pkill|killall|fuser)\b", "bunuh proses secara massal (bisa mengenai proses bot)"),
    (r"\bkill\s+(-[A-Za-z0-9]+\s+)*\d", "bunuh proses berdasarkan PID (bisa proses bot sendiri)"),
    # `kill -s TERM 123` / `kill -SIGKILL 123` (sinyal sebagai opsi terpisah).
    (r"\bkill\s+(-s\s+\S+|-SIG[A-Z]+)\b", "kirim sinyal mematikan ke proses"),
    (r"\bkill\s+-[A-Za-z]*(?:TERM|KILL|INT|HUP|QUIT|STOP|9|15|2|1)\b",
     "kirim sinyal mematikan ke proses"),
    # `kill 0` (semua proses di process group) & `kill -- -<pgid>` (process group).
    (r"\bkill\s+(-[A-Za-z0-9]+\s+)*0\b", "bunuh seluruh process group"),
    (r"\bkill\s+(-[A-Za-z0-9]+\s+)*--?\s*-\d", "bunuh seluruh process group"),
    # Bunuh-diri lewat API interpreter (tak bisa ditangkap oleh daftar kata).
    (r"\bkillpg\s*\(", "bunuh seluruh process group lewat os.killpg"),
    (r"\bos\.kill\s*\(", "bunuh proses lewat os.kill (bisa proses bot sendiri)"),
    (r"\bprocess\.kill\s*\(", "bunuh proses lewat process.kill (bisa proses bot sendiri)"),
    (r"\bSignal\b[^\n]*\bkill\b", "kirim sinyal mematikan lewat API Node"),
    (r"\b(getpgid|getppid|getpid)\s*\([^\n]*\b(kill|killpg|terminate)\b",
     "bunuh proses lewat PID/process-group sendiri"),
    (r"\b(child_process|execSync|execFile|spawnSync)\b[^\n]*\b(kill|pkill|killall|killpg)\b",
     "bunuh proses lewat child_process Node"),
    (r"\bctypes\b[^\n]*\b(kill|system|popen)\b", "panggil syscall lewat ctypes"),
    (r"\b(os\.system|os\.popen|subprocess\.(run|Popen|call|check_output))\s*\([^\n]*\b(kill|pkill|killall)\b",
     "bunuh proses lewat subprocess"),
    (r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(sh|bash|zsh)\b", "pipe unduhan ke shell"),
    (r"\bsudo\b", "jalankan sebagai root"),
    (r"\bhistory\s+-c\b", "hapus jejak"),
]

# Urutan evaluasi izin: deny menang atas ask, ask menang atas allow.
_DECISION_ORDER = ("deny", "ask", "allow")


def is_dangerous(cmd: str) -> bool:
    return reason_for(cmd) is not None


def reason_for(cmd: str):
    c = str(cmd or "")
    for pattern, why in DANGEROUS_PATTERNS:
        if re.search(pattern, c, re.I):
            return why
    return None


def _matches(tool: str, target: str, rule: str) -> bool:
    """Cocokkan aturan `Tool(pola)` terhadap tool + target.

    Aturan boleh berupa `Bash` (semua) atau `Bash(git status:*)`.
    `*` di akhir pola = awalan; selain itu pencocokan glob.
    """
    rule = rule.strip()
    if "(" not in rule:
        return rule == tool or rule == "*"
    name, _, rest = rule.partition("(")
    pattern = rest.rstrip(")")
    if name != tool and name != "*":
        return False
    pattern = pattern.strip()
    if not pattern:
        return True
    if pattern.endswith(":*"):
        return target.startswith(pattern[:-2])
    return fnmatch.fnmatch(target, pattern)


def check_tool_permission(tool: str, target: str, rules: dict) -> str:
    """Kembalikan 'deny' | 'ask' | 'allow' untuk pemanggilan tool.

    rules = {"deny": [...], "ask": [...], "allow": [...]}
    Default bila tak ada aturan cocok: 'ask' (tanya dulu).
    """
    rules = rules or {}
    for decision in _DECISION_ORDER:
        for rule in rules.get(decision, []) or []:
            if _matches(tool, target, rule):
                return decision
    return "ask"


def _path_variants(path: str):
    """Bentuk-bentuk sebuah path untuk pencocokan pola deny.

    ``data/./sessions/x``, ``data/a/../sessions/x``, ``data//sessions/x`` dan
    bentuk absolutnya harus sama-sama tertangkap — kalau tidak, penyerang bisa
    menyelipkan ``./`` atau ``..`` untuk melewati denyPaths.
    """
    p = str(path or "")
    out = {p}
    for fn in (os.path.normpath, lambda q: os.path.normpath(os.path.abspath(q))):
        try:
            out.add(fn(p))
        except Exception:  # noqa: BLE001
            pass
    return {v for v in out if v}


def path_allowed(path: str, deny_patterns) -> bool:
    """False bila path cocok salah satu pola terlarang (mis. .ssh, /etc/).

    Pencocokan dilakukan pada beberapa bentuk path (lihat ``_path_variants``)
    supaya trik ``./`` / ``..`` / ``//`` tak bisa melewati larangan.
    """
    variants = _path_variants(path)
    for pattern in deny_patterns or []:
        for v in variants:
            if re.search(pattern, v):
                return False
    return True


def is_executable_allowed(cmd: str, allowed) -> bool:
    """True bila executable pertama perintah ada di allowlist."""
    first = str(cmd or "").strip().split()
    if not first:
        return False
    exe = first[0].split("/")[-1]  # /usr/bin/python3 -> python3
    return exe in set(allowed or [])


def evaluate(config: dict, tool: str, target: str, rules: dict | None = None) -> str:
    """Keputusan izin gabungan: mode sandbox + kebijakan approval + aturan.

    Mengembalikan 'deny' | 'ask' | 'allow'.
    """
    sec = (config or {}).get("security", {}) if isinstance(config, dict) else {}
    sandbox = sec.get("sandboxMode", "workspace-write")
    approval = sec.get("approvalPolicy", "on-request")

    # MODE OWNER: tidak ada lapisan izin. Satu-satunya yang ditahan adalah
    # perintah yang bisa mematikan bot sendiri — dan hanya bila
    # `security.guardSelfKill` tidak dimatikan.
    if access_mode(config) == "owner":
        if sec.get("guardSelfKill", True) and tool in ("shell", "code", "terminal"):
            if self_kill_reason(target):
                return "ask"
        return "allow"

    # read-only: semua eksekusi ditolak
    if sandbox == "read-only" and tool in ("shell", "code", "plugin_write", "apply_patch"):
        return "deny"

    # never = full-auto: setujui semua kecuali yang benar-benar berbahaya
    if approval == "never":
        if tool in ("shell", "code") and is_dangerous(target):
            return "ask"  # tetap tanya untuk yang berbahaya
        return "allow"

    # untrusted: hampir semua harus ditanya
    if approval == "untrusted":
        if tool in ("shell", "code", "plugin_write", "apply_patch"):
            return "ask"

    # on-request (default): pakai aturan, lalu deteksi bahaya
    decision = check_tool_permission(tool, target, rules or {})
    if decision == "deny":
        return "deny"
    if tool in ("shell", "code") and is_dangerous(target):
        return "ask"
    return decision


# ---------------------------------------------------------------------------
# MODE AKSES PENUH (owner)
# ---------------------------------------------------------------------------
# Bot ini milik satu orang. Sandbox path + allowlist executable + larangan baca
# .env membuat pemiliknya sendiri tidak bisa bekerja. Mode `owner` menghapus
# pembatasan itu. Yang TERSISA hanya penjagaan agar bot tidak membunuh prosesnya
# sendiri — kalau bot mati, tidak ada yang bisa memperbaikinya dari chat.
_SELF_KILL_PATTERNS = [
    (r"\bsetsid\b", "melepas proses dari sesi"),
    (r"\b(pkill|killall|fuser)\b", "membunuh proses massal"),
    (r"\bkill\s+(-[A-Za-z0-9]+\s+)*(0|-\d|\d)", "membunuh proses berdasarkan PID"),
    (r"\bkill\s+(-s\s+\S+|-SIG[A-Z]+)\b", "mengirim sinyal ke proses"),
    (r"\bkillpg\s*\(", "os.killpg"),
    (r"\bos\.kill\s*\(", "os.kill"),
    (r"\bprocess\.kill\s*\(", "process.kill"),
]


def self_kill_reason(cmd: str):
    """Alasan perintah berpotensi membunuh bot sendiri (None bila aman)."""
    c = str(cmd or "")
    for pattern, why in _SELF_KILL_PATTERNS:
        if re.search(pattern, c, re.I):
            return why
    return None


def access_mode(config: dict) -> str:
    """'owner' (tanpa sandbox) atau 'sandbox' (ketat). Default: owner."""
    sec = (config or {}).get("security", {}) if isinstance(config, dict) else {}
    return str(sec.get("accessMode") or "owner").strip().lower()
