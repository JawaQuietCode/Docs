"""shell.py — jalankan perintah di server seperti coding-agent (Hermes/Codex/Claude Code).

Perintah boleh memakai pipa (|), penyambung (&&, ||, ;), pengalihan (>, >>),
variabel, heredoc — seperti terminal sungguhan, supaya bot benar-benar bisa
bekerja (``git log | head``, ``npm install && npm test``, ``curl -s URL | jq``).

Tiga lapis pengamanan (urut):

  1. ``security/policy.py``  — pola merusak/berbahaya (``rm -rf /``, ``sudo``,
     pipe unduhan ke shell, bunuh-diri proses bot, …).
  2. ``denyPaths``           — path yang tak boleh disentuh (``.env``, sesi
     WhatsApp, ``skills/hidden``, ``/etc``, …), diperiksa pada token path DAN
     pada substring mirip-path (supaya ``python3 -c "open('.env')"`` tertangkap),
     termasuk penelusuran rekursif (``grep -r``/``find``/``ls -R``).
  3. allowlist executable    — SETIAP segmen diperiksa (bukan hanya yang pertama),
     termasuk isi pembungkus ``bash -lc "..."``, ``env``, ``timeout``, ``xargs``.
     Bisa dimatikan untuk owner lewat ``security.allowAllExecutables: true``.

Catatan jujur soal batas keamanan: selama interpreter (``python3``, ``node``)
ada di allowlist, allowlist TIDAK bisa menjadi sandbox — ``python3 -c`` selalu
bisa menjalankan apa pun. Lapisan yang benar-benar mengurung eksekusi adalah
container/OS (Pterodactyl, bubblewrap, seccomp). Yang menjaga rahasia di sini
adalah: environment TIDAK mewarisi kunci (lihat ``_child_env``), ``denyPaths``,
dan ``blockInlineCode`` (opsional, mati secara default agar kemampuan penuh).

Eksekusi memakai ``bash -c`` (bukan ``-lc``): ``-l`` memuat ``.bashrc``/
``.profile`` dari HOME proyek, sehingga berkas yang bisa ditulis agent akan
otomatis dieksekusi pada setiap perintah.
"""
import os
import re
import shlex
import signal
import subprocess
import tempfile
import time

from security.audit import audit
from security.policy import is_dangerous, reason_for
from tools.registry import register
from tools.rtk import rewrite_command
from tools.schema import coerce_int, output_limit, truncate

SCHEMA = {
    "description": (
        "Jalankan perintah shell di server bot. Boleh memakai pipa (|), "
        "penyambung (&&, ||, ;), pengalihan (>, >>), heredoc, dan variabel — "
        "seperti terminal biasa (mis. 'git log | head -5', 'curl -s URL | jq .', "
        "'npm install && npm test'). Tulis perintah non-interaktif: tidak ada "
        "yang bisa menjawab prompt. Output dipotong; gunakan pipe (head/tail/grep) "
        "untuk perintah yang banyak keluarannya. PAKAI TOOL INI untuk benar-benar "
        "MENJALANKAN perintah — jangan menulis perintah di balasan untuk di-copy "
        "pengguna."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Perintah, mis. 'git log | head -5'"},
            "cwd": {"type": "string", "description": "Folder kerja (opsional; relatif ke root proyek)"},
            "timeout": {"type": "integer", "description": "Batas detik (opsional, dibatasi config)"},
        },
        "required": ["command"],
    },
}

# Kata kunci shell bawaan yang boleh memulai segmen tanpa cek allowlist
# (mis. `cd workspace && node x.js`, `export X=1`).
_BUILTINS = {"cd", "export", "set", "unset", "source", ".", "true", "false",
             "echo", "printf", "read", "test", "[", ":", "wait", "shift", "ulimit"}
# Kata kunci shell yang boleh MENDAHULUI perintah di segmen yang sama
# (`do curl ...`, `then echo ...`, `if [ ... ]`, `while read ...`) — perintah
# setelahnya TETAP diperiksa allowlist.
_KEYWORDS_WITH_CMD = {"do", "then", "else", "elif", "while", "until", "if", "time", "!", "{"}
# Kata kunci yang hanya membuka/menutup konstruk (tak ada perintah di segmen ini):
# `for x in 1 2`, `done`, `fi`, `}`. CATATAN: `case` sengaja TIDAK dimasukkan —
# pada `case x in a) cmd ;; esac` perintah berada di segmen yang sama, jadi
# membiarkannya diperiksa allowlist = gagal-tertutup (aman).
_KEYWORDS_BARE = {"for", "in", "select", "esac", "done", "fi", "}", "function"}
# Definisi fungsi: `nama() {` — bukan executable.
_FUNC_DEF = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\(\)$")
# Penugasan variabel di awal segmen: FOO=bar cmd
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Penanda awal heredoc: <<EOF, <<'EOF', <<-"EOF"
_HEREDOC = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")
# Pembungkus yang meneruskan perintah ke argumennya: perintah SEBENARNYA ada
# setelah flag-flag pembungkus ini dan harus ikut diperiksa allowlist.
_WRAPPERS = {"env", "command", "exec", "nohup", "time", "timeout", "nice",
             "ionice", "stdbuf", "xargs", "setsid", "unbuffer", "chrt", "taskset"}
# Flag pembungkus yang memakan satu nilai (bukan perintah): `-u VAR`, `-n 5`, `-s 3`.
_WRAPPER_ARG_FLAGS = {"-u", "--unset", "-n", "--max-args", "-I", "--replace",
                      "-s", "--max-chars", "-P", "--max-procs", "-d", "--delimiter",
                      "-a", "--arg-file", "-L", "--max-lines", "-E", "-e"}
# Shell yang bisa membawa perintah di argumen `-c`.
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "ash", "fish"}
# Pola "kode inline" pada interpreter: `python3 -c "..."`, `node -e "..."`.
# Ini pintu belakang allowlist yang paling mudah (interpreter bisa apa saja).
_INLINE_CODE = re.compile(r"(?:^|[\s;&|(])(python3?|node|deno|bun|perl|ruby|php|osascript)\b"
                          r"[^\n]*?(?<!\S)(-c|-e|--eval|-p|-E)\b")
# Substring yang MIRIP path di dalam satu token (mis. isi string kode Python:
# `open('.env')`). Dipakai supaya `denyPaths` tak bisa dilewati lewat interpreter.
_PATH_LIKE = re.compile(r"[\w.@~+/-]*(?:\.[\w-]+|/[\w.@~+/-]+)")
# Environment yang boleh diwariskan ke anak proses. Semua kunci rahasia
# (LLM_API_KEY, ACTION_TOKEN, WA_*, PTERO_*, *_SECRET) TIDAK ikut — dulu
# `dict(os.environ)` membuat `env | grep KEY` membocorkan kunci LLM ke model.
_KEEP_ENV = {"PATH", "HOME", "LANG", "TMPDIR", "TZ", "TERM", "SHELL", "USER",
             "LOGNAME", "PWD", "PYTHONPATH", "PYTHONUSERBASE", "PYTHONUNBUFFERED",
             "NODE_PATH", "NODE_OPTIONS", "NPM_CONFIG_PREFIX", "NPM_CONFIG_CACHE",
             "VIRTUAL_ENV", "CONDA_PREFIX", "EDITOR", "GIT_TERMINAL_PROMPT"}
_DENY_ENV_PREFIX = ("LLM_", "WA_", "ACTION_", "PTERO_", "OWNER_", "AGENT_")
_DENY_ENV_SUFFIX = ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_PASS", "_APIKEY")


def _strip_heredoc_bodies(cmd: str) -> str:
    """Buang isi heredoc (baris antara penanda dan terminator).

    Baris perintah yang memuat ``<<TAG`` tetap dipertahankan supaya executable-nya
    tetap diperiksa; hanya isi (yang bukan perintah) yang dibuang.
    """
    lines = cmd.split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = _HEREDOC.search(line)
        i += 1
        if m:
            tag = m.group(1)
            while i < len(lines) and lines[i].strip() != tag:
                i += 1
            i += 1  # lewati baris terminator
    return "\n".join(out)


def _split_segments(cmd: str) -> list:
    """Pecah perintah pada operator, abaikan operator di dalam kutipan/substitusi."""
    segs, buf = [], []
    i, n = 0, len(cmd)
    quote = None
    depth = 0  # kedalaman $( ... ) atau ` ... `
    while i < n:
        c = cmd[i]
        if quote:
            buf.append(c)
            if c == "\\" and quote == '"' and i + 1 < n:
                buf.append(cmd[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "\"'":
            quote = c
            buf.append(c)
            i += 1
            continue
        if c == "$" and i + 1 < n and cmd[i + 1] == "(":
            depth += 1
            buf.append("$(")
            i += 2
            continue
        if c == "`":
            depth += 1
            buf.append(c)
            i += 1
            continue
        if depth > 0 and c == ")":
            depth -= 1
            buf.append(c)
            i += 1
            continue
        if depth > 0 and c == "`":
            depth -= 1
            buf.append(c)
            i += 1
            continue
        if depth == 0:
            two = cmd[i:i + 2]
            if two in ("&&", "||"):
                segs.append("".join(buf))
                buf = []
                i += 2
                continue
            # Pengalihan yang memakai `&` BUKAN pemisah segmen:
            #   `2>&1`, `>&2`, `&>`, `|&`, `2>&-`
            # Tanpa penanganan ini, `python3 scrape.py 2>&1` terpecah menjadi
            # segmen `... 2>` + `1` dan allowlist menolak executable `1`.
            if two == "&>":
                buf.append(c)
                i += 1
                continue
            if c == "&" and i > 0 and cmd[i - 1] == ">":
                buf.append(c)
                i += 1
                continue
            if c in "|;&\n":
                segs.append("".join(buf))
                buf = []
                i += 1
                continue
        buf.append(c)
        i += 1
    segs.append("".join(buf))
    return [s for s in segs if s.strip()]


def _path_candidates(text: str, root: str = "") -> list:
    """Kumpulkan kandidat path dari sebuah teks perintah.

    Dua sumber:
      a) token hasil shlex (kata utuh), dan
      b) substring MIRIP path di dalam token apa pun — ini yang menangkap
         ``python3 -c "print(open('.env').read())"`` (dulu lolos karena token
         utuhnya tidak diawali ``.`` dan tidak memuat ``/``).

    Untuk tiap kandidat, kembalikan bentuk apa adanya, bentuk ternormalisasi,
    dan bentuk ABSOLUT relatif ``root`` — supaya ``cat .env``,
    ``cat /app/.env``, ``cat ./sub/../.env``, dan ``open('.env')`` sama-sama
    diperiksa.
    """
    out: list = []
    seen: set = set()

    def add(cand: str) -> None:
        cand = (cand or "").strip().strip("'\"")
        if not cand:
            return
        forms = {cand, os.path.normpath(cand)}
        if root:
            forms.add(os.path.normpath(os.path.join(root, cand)))
        for f in forms:
            if f and f not in seen:
                seen.add(f)
                out.append(f)

    try:
        parts = shlex.split(_strip_heredoc_bodies(text), comments=False, posix=True)
    except ValueError:
        parts = text.split()
    for p in parts:
        if not p or p.startswith("-"):
            continue
        if "/" in p or p.startswith(".") or "." in p.strip("."):
            add(p)
        # (b) substring mirip path di dalam token (isi kode/string)
        if any(ch in p for ch in ("/", ".")):
            for m in _PATH_LIKE.finditer(p):
                frag = m.group(0).strip("()[]{},;:'\"")
                if frag and (frag.startswith((".", "/", "~")) or "/" in frag):
                    add(frag)
    return out


def _command_path_tokens(cmd: str, root: str = "") -> list:
    """Nama lama dipertahankan: kandidat path dari seluruh perintah."""
    return _path_candidates(cmd, root)


def _recursive_scan_targets(cmd: str, root: str) -> list:
    """Target direktori yang akan DITELUSURI REKURSIF oleh perintah.

    ``grep -r``/``find``/``ls -R`` bisa menyentuh isi ``skills/hidden`` walau
    yang disebut adalah INDUK-nya (``<root>/skills``). Kembalikan daftar dir
    yang jadi target rekursif supaya pemanggil bisa memeriksa isinya.
    """
    recursive = False
    try:
        parts = shlex.split(_strip_heredoc_bodies(cmd), comments=False, posix=True)
    except ValueError:
        parts = cmd.split()
    # `find` menelusuri rekursif SECARA BAWAAN (tanpa flag), jadi ia juga
    # dianggap rekursif. `grep`/`ls` butuh -r/-R.
    if parts and os.path.basename(parts[0]) == "find":
        recursive = True
    for p in parts:
        if p in ("-r", "-R", "--recursive"):
            recursive = True
        elif re.match(r"^-[A-Za-z]*[rR][A-Za-z]*$", p):  # mis. -rR, -rn
            recursive = True
    if not recursive:
        return []
    dirs = []
    for p in parts:
        if not p or p.startswith("-"):
            continue
        cand = p if os.path.isabs(p) else os.path.join(root, p)
        cand = os.path.normpath(cand)
        if os.path.isdir(cand):
            dirs.append(cand)
    return dirs


def _denied_descendant(directory: str, deny, path_allowed, limit: int = 4000) -> str:
    """Cari path NYATA di dalam `directory` yang cocok denyPaths (bounded walk).

    Hanya memeriksa path yang BENAR-BENAR ADA, sehingga perintah rekursif ke
    folder lain (mis. `/tmp`) tidak salah-ditolak hanya karena kebetulan ada
    string 'skills/hidden'.

    FAIL-CLOSED: bila batas walk tercapai, kembalikan penanda (bukan "") supaya
    pemanggil MENOLAK perintah. Dulu ia `return ""` = "aman", jadi cukup membuat
    4000 entri di folder lain untuk melewati pemeriksaan.
    """
    if not directory or not os.path.isdir(directory):
        return ""
    seen = 0
    for dirpath, dirnames, filenames in os.walk(directory):
        for name in list(dirnames) + list(filenames):
            seen += 1
            if seen > limit:
                return f"{directory} (batas pemindaian {limit} entri tercapai)"
            full = os.path.join(dirpath, name)
            if not path_allowed(full, deny):
                return full
    return ""


def _scrub_denied_names(text: str, deny: list) -> str:
    """Sembunyikan NAMA folder terlarang dari keluaran perintah.

    ``ls ../skills`` memperlihatkan folder ``hidden`` walau isinya ditolak — nama
    itu sendiri sudah membocorkan bahwa ada sesuatu yang disembunyikan. Fungsi
    ini mengganti entri listing yang cocok dengan nama path terlarang menjadi
    ``[tersembunyi]``.

    Sengaja HANYA menyasar baris listing (entri berdiri sendiri, atau baris
    ``ls -l`` yang diawali mode berkas) supaya prosa biasa yang kebetulan
    memuat kata itu TIDAK ikut diubah.
    """
    if not text or not deny:
        return text
    names = set()
    for d in deny:
        d = str(d).strip().strip("^$")
        if "/" in d:
            base = d.rstrip("/").split("/")[-1]
            if base and base not in (".", ".."):
                names.add(base)
    if not names:
        return text
    out = []
    for line in text.split("\n"):
        s = line.strip()
        if s.rstrip("/") in names:                      # entri `ls` polos
            out.append(line.replace(s, "[tersembunyi]"))
            continue
        if re.match(r"^[dlbcps-][rwxsSt-]{9}", s):      # baris `ls -l`
            parts = line.split()
            if parts and parts[-1].rstrip("/") in names:
                line = line[:line.rfind(parts[-1])] + "[tersembunyi]"
        out.append(line)
    return "\n".join(out)


def _segment_tokens(seg: str) -> list:
    """Token satu segmen, dengan pengalihan awal & penugasan variabel dibuang."""
    seg = seg.strip()
    if not seg:
        return []
    seg = re.sub(r"^\d*[<>]{1,2}\s*", "", seg).strip()   # buang `> file` di awal
    if not seg:
        return []
    try:
        parts = shlex.split(seg, comments=False, posix=True)
    except ValueError:
        parts = seg.split()
    while parts and _ASSIGN.match(parts[0]):             # FOO=bar node x.js
        parts = parts[1:]
    return parts


def _unwrap_wrappers(parts: list) -> list:
    """Buang pembungkus (`env`, `timeout 5`, `xargs -n1`, …) → perintah asli.

    Tanpa ini, `env curl evil` atau `timeout 5 curl evil` lolos karena yang
    diperiksa hanya `env`/`timeout` (keduanya ada di allowlist).
    """
    i = 0
    while i < len(parts) and os.path.basename(parts[i]) in _WRAPPERS:
        i += 1
        while i < len(parts):
            tok = parts[i]
            if tok in _WRAPPER_ARG_FLAGS:
                i += 2          # flag + nilainya
                continue
            if tok.startswith("-"):
                i += 1
                continue
            break
        # `timeout 5 cmd` → lewati durasi numerik / durasi gaya `5s`
        if i < len(parts) and re.fullmatch(r"\d+(\.\d+)?[smhd]?", parts[i]):
            i += 1
    return parts[i:]


def _shell_bodies(parts: list) -> list:
    """Perintah di dalam `bash -lc "..."` / `sh -c '...'` (rekursif diperiksa).

    Dulu pola regex hanya mengenali ``-c`` PERSIS, sehingga ``bash -lc "curl x"``
    (satu flag gabungan) tidak pernah dibuka — allowlist bisa dilewati dengan
    satu huruf tambahan. Sekarang berbasis token, jadi ``-lc``, ``-l -c``,
    ``-ec``, dan ``--command`` semuanya tertangkap.
    """
    if not parts:
        return []
    if os.path.basename(parts[0]) not in _SHELLS:
        return []
    bodies = []
    for i, tok in enumerate(parts[1:], start=1):
        if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", tok) or tok == "--command":
            if i + 1 < len(parts):
                body = parts[i + 1].strip()
                if body:
                    bodies.append(body)
            break
    return bodies


def _segment_commands(cmd: str) -> list:
    """Ambil executable setiap segmen (lewati penugasan, redirection, pembungkus)."""
    out = []
    for raw in _split_segments(_strip_heredoc_bodies(cmd)):
        parts = _segment_tokens(raw)
        if not parts:
            continue
        # lewati kata kunci shell. `do`/`then`/`while`/… mendahului perintah,
        # jadi lanjut ke token berikutnya; `for`/`in`/`done`/`fi`/… hanya
        # membuka/menutup konstruk (tak ada perintah di segmen ini).
        while parts and parts[0] in (_KEYWORDS_WITH_CMD | _KEYWORDS_BARE):
            kw = parts[0]
            parts = parts[1:]
            if kw in _KEYWORDS_BARE:
                parts = []  # sisa baris hanya daftar nilai / penutup
                break
        parts = _unwrap_wrappers(parts)
        if not parts:
            continue
        first = parts[0]
        if _FUNC_DEF.match(first):     # definisi fungsi: `nama() {`
            continue
        out.append(first.split("/")[-1])
    return out


def _check_allowlist(cmd: str, allowed: list, _depth: int = 0) -> str | None:
    """Kembalikan pesan error bila ada executable segmen di luar allowlist."""
    # Normalisasi: entri allowlist bisa datang dari YAML sebagai bool (mis.
    # `- true`/`- false` terbaca sebagai True/False), angka, dsb. Tanpa ini,
    # `', '.join(allowed)` melempar TypeError → loop agent mencoba ulang TANPA
    # config → allowlist MATI → perintah berbahaya lolos (insiden bunuh-diri).
    allowed = [str(x) for x in (allowed or []) if x is not None]
    if not allowed:
        return None
    allow = set(allowed)
    for exe in _segment_commands(cmd):
        if exe in allow or exe in _BUILTINS:
            continue
        return f"executable '{exe}' tidak diizinkan. Boleh: {', '.join(allowed)}"
    # periksa isi pembungkus shell (`bash -lc "..."`) — maks 3 tingkat.
    if _depth < 3:
        for raw in _split_segments(_strip_heredoc_bodies(cmd)):
            for body in _shell_bodies(_segment_tokens(raw)):
                err = _check_allowlist(body, allowed, _depth + 1)
                if err:
                    return err
    return None


def _child_env(root: str) -> dict:
    """Environment untuk anak proses: HANYA yang aman (tanpa kunci rahasia).

    Dulu `dict(os.environ)` diteruskan apa adanya, jadi `env` / `printenv`
    (keduanya ada di allowlist) mencetak LLM_API_KEY dan ACTION_TOKEN ke
    percakapan. Logika allowlist-nya dipusatkan di `security/env_sandbox.py`
    supaya `mechanisms.py` (CLI eksternal) memakai aturan yang sama.
    """
    from security.env_sandbox import child_env
    return child_env(root)


def _allowed_cwd(cwd: str, config) -> tuple:
    """Kurung folder kerja ke area izin. Kembalikan (cwd, catatan)."""
    sec = (config or {}).get("security", {}) or {}
    root = os.path.normpath(str((config or {}).get("root") or "."))
    extra = [str(x) for x in (sec.get("execCwdRoots") or []) if str(x).strip()]
    roots = [root]
    ws = sec.get("workspaceDir")
    if ws:
        roots.append(os.path.normpath(ws if os.path.isabs(str(ws)) else os.path.join(root, str(ws))))
    for e in extra:
        roots.append(os.path.normpath(e if os.path.isabs(e) else os.path.join(root, e)))
    roots.extend([tempfile.gettempdir(), "/tmp", "/var/tmp"])
    for r in roots:
        try:
            if cwd == r or cwd.startswith(r.rstrip(os.sep) + os.sep):
                return cwd, ""
        except Exception:
            continue
    return root, f"cwd '{cwd}' di luar area izin; memakai root proyek"


def _kill_group(proc: subprocess.Popen) -> None:
    """Bunuh SELURUH process group anak (bukan hanya bash-nya).

    Dengan `start_new_session=True`, anak punya pgid sendiri; `subprocess.run`
    hanya membunuh proses langsung sehingga cucu (`npm`, `ffmpeg`, `chromium`)
    menjadi yatim dan terus berjalan.
    """
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            proc.wait(timeout=2)
            return
        except Exception:
            continue


def _run_command(cmd: str, cwd: str, env: dict, timeout: int, cancel=None) -> dict:
    """Jalankan perintah dengan output TERBATAS di disk (bukan di RAM).

    `subprocess.run(capture_output=True)` menampung seluruh stdout di memori
    lebih dulu, baru setelah selesai dipotong — `yes`, log build besar, atau
    `ffmpeg` verbose bisa menghabiskan RAM bot. Di sini output langsung ke file
    sementara, lalu dibaca berbatas.
    """
    limit = 16 * 1024 * 1024         # batas baca mentah; pemotongan akhir di handler
    with tempfile.TemporaryFile() as fo, tempfile.TemporaryFile() as fe:
        try:
            proc = subprocess.Popen(
                ["bash", "-c", cmd], stdin=subprocess.DEVNULL,
                stdout=fo, stderr=fe, cwd=cwd, env=env,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            return {"exit_code": 127, "stdout": "", "stderr": f"perintah tidak ditemukan: {exc}"}
        except Exception as exc:  # noqa: BLE001
            return {"exit_code": 126, "stdout": "", "stderr": f"gagal menjalankan: {exc}"}

        deadline = time.time() + max(1, timeout)
        timed_out = cancelled = False
        rc = None
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            if cancel is not None and getattr(cancel, "is_set", None) and cancel.is_set():
                cancelled = True
                _kill_group(proc)
                rc = -signal.SIGTERM
                break
            if time.time() >= deadline:
                timed_out = True
                _kill_group(proc)
                rc = -signal.SIGKILL
                break
            time.sleep(0.1)
        try:
            proc.wait(timeout=3)
        except Exception:
            pass

        fo.seek(0, os.SEEK_END)
        out_size = fo.tell()
        fo.seek(0)
        err_size = 0
        fe.seek(0, os.SEEK_END)
        err_size = fe.tell()
        fe.seek(0)
        stdout = fo.read(min(out_size, limit)).decode("utf-8", "replace")
        stderr = fe.read(min(err_size, limit)).decode("utf-8", "replace")

    if out_size > limit:
        stderr += f"\n[stdout {out_size} byte, hanya {limit} byte pertama dibaca]"
    return {"exit_code": rc, "stdout": stdout, "stderr": stderr,
            "timed_out": timed_out, "cancelled": cancelled}



def _validate_rewritten_command(cmd: str, *, root: str, sec: dict,
                               owner: bool, allow_all: bool, allowed: list,
                               deny: list) -> str | None:
    """Re-check a transformed command against the same Kwee shell policy.

    RTK is an output optimizer, never a security boundary. Rewritten text is
    untrusted input and must not gain privileges or bypass denyPaths.
    """
    if owner:
        from security.policy import self_kill_reason
        reason = self_kill_reason(cmd) if sec.get("guardSelfKill", True) else ""
        if reason:
            return f"self-kill guard: {reason}"
    elif is_dangerous(cmd):
        return f"dangerous command: {reason_for(cmd)}"

    if sec.get("blockInlineCode") and _INLINE_CODE.search(cmd):
        return "inline interpreter code is disabled by security.blockInlineCode"
    if not allow_all:
        err = _check_allowlist(cmd, allowed)
        if err:
            return err

    if deny:
        from security.policy import path_allowed
        for directory in _recursive_scan_targets(cmd, root):
            found = _denied_descendant(directory, deny, path_allowed)
            if found:
                return f"rewritten command would traverse denied path: {found}"
        candidates = _path_candidates(cmd, root)
        ancestors = []
        for token in candidates:
            parent_path = token
            for _ in range(6):
                parent = os.path.dirname(parent_path.rstrip("/"))
                if not parent or parent == parent_path:
                    break
                ancestors.append(parent)
                parent_path = parent
        for token in candidates + ancestors:
            if token and not path_allowed(token, deny):
                return f"rewritten command touches denied path: {token}"
    return None


def handler(args, config=None):
    args = args or {}
    cmd = str(args.get("command", "")).strip()
    if not cmd:
        return {"error": "command kosong"}

    sec = (config or {}).get("security", {}) or {}
    mode = str(sec.get("accessMode") or "owner").strip().lower()
    owner = mode == "owner"
    allowed = sec.get("allowedExecutables", [])
    # Mode owner = tanpa allowlist. Tetap hormati allowAllExecutables bila
    # seseorang sengaja memilih mode sandbox.
    allow_all = owner or bool(sec.get("allowAllExecutables"))
    # FAIL-CLOSED: bila config/`security` tak sampai (mis. pemanggil salah),
    # JANGAN jalankan perintah. Dulu `allowed=[]` membuat allowlist MATI total;
    # sekarang config kosong = perintah ditolak, bukan bebas.
    if config is None or not isinstance(sec, dict) or not sec:
        audit("shell.blocked", command=cmd, reason="config keamanan tidak tersedia (fail-closed)")
        return {"error": "perintah ditolak: konfigurasi keamanan tidak tersedia"}

    # Batas waktu perintah: nilai dari model DIBATASI oleh batas config. Agent
    # loop menurunkan `execTimeoutSeconds` ke sisa anggaran turn, sehingga satu
    # perintah panjang tidak boleh melewati plafon turn.
    cap = coerce_int(sec.get("execTimeoutSeconds"), 120, minimum=1)
    timeout = coerce_int(args.get("timeout"), cap, minimum=1, maximum=cap)
    limit = output_limit(config)
    root = str((config or {}).get("root") or ".")
    workdir = sec.get("workspaceDir", "./workspace")
    # Multi-tenant: bila ada tenant AKTIF, folder kerja default adalah
    # workspace MILIK tenant itu, bukan folder global.
    try:
        from security.tenant_context import current_workspace
        tw = current_workspace()
        if tw:
            workdir = tw
    except Exception:
        pass
    # Mode owner: folder kerja bawaan adalah ROOT PROYEK, bukan workspace/.
    # Alasannya praktis: perintah seperti `sed -i transport/router.js` atau
    # `cat agent/loop.py` ditulis relatif terhadap proyek. Dulu cwd-nya
    # workspace/, jadi semua path terlihat "tidak ada" dan model menyimpulkan
    # berkasnya hilang.
    if owner and not args.get("cwd"):
        workdir = root

    # 1) pola berbahaya.
    #    Mode owner: HANYA bunuh-diri yang ditahan (bot mati = tak ada yang bisa
    #    memperbaiki dari chat). Bisa dimatikan lewat security.guardSelfKill: false.
    if owner:
        from security.policy import self_kill_reason
        if sec.get("guardSelfKill", True) and self_kill_reason(cmd):
            audit("shell.blocked", command=cmd, reason=self_kill_reason(cmd))
            return {"error": (
                f"perintah ditahan: {self_kill_reason(cmd)} — bisa mematikan bot ini sendiri. "
                "Kalau memang perlu, matikan security.guardSelfKill di config."
            )}
    elif is_dangerous(cmd):
        audit("shell.blocked", command=cmd, reason=reason_for(cmd))
        return {"error": f"perintah ditolak: {reason_for(cmd)}"}

    # 1b) kode inline interpreter (opsional, `security.blockInlineCode`).
    #     Default MATI supaya kemampuan penuh; hidupkan bila ingin allowlist
    #     benar-benar mengikat.
    if sec.get("blockInlineCode") and _INLINE_CODE.search(cmd):
        audit("shell.blocked", command=cmd, reason="kode inline interpreter")
        return {"error": ("perintah ditolak: kode inline interpreter (python3 -c / node -e) "
                          "dimatikan oleh security.blockInlineCode")}

    # 2) allowlist untuk SETIAP segmen (kecuali owner mengaktifkan allowAllExecutables)
    if not allow_all:
        err = _check_allowlist(cmd, allowed)
        if err:
            audit("shell.blocked", command=cmd, reason=err)
            return {"error": err}

    # 3) denyPaths: path yang selalu ditolak (mis. `.ssh`, `/etc/`, `.env`,
    #    dan RAHASIA `skills/hidden`). Dua uji:
    #      a) tiap kandidat path (relatif + absolut + ternormalisasi + substring
    #         mirip path di dalam token/kode), DAN
    #      b) bila ada flag REKURSIF (grep -r / find / ls -R), target direktori
    #         diperiksa sampai ke isinya.
    deny = sec.get("denyPaths", [])
    if deny:
        from security.policy import path_allowed
        kandidat = _path_candidates(cmd, root)
        # Periksa juga setiap leluhur tiap kandidat (…/skills/hidden/x → …/skills).
        leluhur = []
        for tok in kandidat:
            p = tok
            for _ in range(6):
                parent = os.path.dirname(p.rstrip("/"))
                if not parent or parent == p:
                    break
                leluhur.append(parent)
                p = parent
        # Target REKURSIF: periksa path NYATA di dalamnya (grep -r / find / ls -R).
        for d in _recursive_scan_targets(cmd, root):
            found = _denied_descendant(d, deny, path_allowed)
            if found:
                audit("shell.blocked", command=cmd, reason=f"path terlarang: {found}")
                return {"error": f"perintah ditolak: menyentuh path terlarang ({found})"}
        for tok in kandidat + leluhur:
            if not tok:
                continue
            if not path_allowed(tok, deny):
                audit("shell.blocked", command=cmd, reason=f"path terlarang: {tok}")
                return {"error": f"perintah ditolak: menyentuh path terlarang ({tok})"}

    # 4) siapkan folder kerja (dikurung ke area izin)
    cwd_raw = args.get("cwd") or workdir
    cwd = str(cwd_raw)
    if not os.path.isabs(cwd):
        cwd = os.path.normpath(os.path.join(root, cwd))
    else:
        cwd = os.path.normpath(cwd)
    if owner:
        # Tanpa pengurungan folder: pemilik bebas bekerja di mana saja.
        cwd_note = ""
        if not os.path.isdir(cwd):
            try:
                os.makedirs(cwd, exist_ok=True)
            except Exception as exc:  # noqa: BLE001
                cwd = os.path.normpath(root)
                cwd_note = f"folder kerja tidak bisa dibuat ({exc}); memakai root proyek"
    else:
        cwd, cwd_note = _allowed_cwd(cwd, config)
    try:
        os.makedirs(cwd, exist_ok=True)
    except Exception:
        cwd = os.path.normpath(root)
        cwd_note = cwd_note or "folder kerja tidak bisa dibuat; memakai root proyek"

    cancel = ((config or {}).get("context") or {}).get("cancel") if isinstance(config, dict) else None
    env = _child_env(root)

    # RTK integration belongs in Kwee's own runner (shell and terminal share
    # this path). In sandbox mode it is skipped unless `rtk` itself is allowed.
    rtk_cfg = sec.get("rtk") if isinstance(sec.get("rtk"), dict) else {}
    rtk_enabled = bool(rtk_cfg.get("enabled", True))
    if not allow_all and "rtk" not in {str(x).strip() for x in (allowed or [])}:
        rtk_enabled = False
    rtk_result = rewrite_command(
        cmd, env, enabled=rtk_enabled,
        timeout=coerce_int(rtk_cfg.get("timeoutSeconds"), 2, minimum=1, maximum=5),
    )
    execute_cmd = cmd
    if rtk_result.get("applied"):
        rewritten = str(rtk_result.get("command") or "")
        rewrite_error = _validate_rewritten_command(
            rewritten, root=root, sec=sec, owner=owner, allow_all=allow_all,
            allowed=allowed, deny=deny,
        ) if rewritten else "RTK returned an empty command"
        if rewrite_error:
            audit("shell.blocked", command=cmd, reason=f"RTK rewrite rejected: {rewrite_error}")
            return {"error": f"hasil rewrite RTK ditolak oleh kebijakan keamanan Kwee: {rewrite_error}",
                    "optimization": {"engine": "rtk", "applied": False, "reason": "security_revalidation_failed"}}
        execute_cmd = rewritten

    res = _run_command(execute_cmd, cwd, env, timeout, cancel=cancel)

    if res.get("cancelled"):
        audit("shell.cancelled", command=cmd, cwd=cwd)
        return {"error": "perintah dihentikan atas permintaan pemilik", "cancelled": True,
                "stdout": truncate(res.get("stdout", ""), limit)}
    if res.get("timed_out"):
        audit("shell.timeout", command=cmd, timeout=timeout)
        return {"error": f"perintah melebihi batas {timeout} detik",
                "stdout": truncate(res.get("stdout", ""), limit),
                "stderr": truncate(res.get("stderr", ""), limit)}

    out = {
        "exit_code": res.get("exit_code"),
        "stdout": _scrub_denied_names(truncate(res.get("stdout", ""), limit), deny),
        "stderr": _scrub_denied_names(truncate(res.get("stderr", ""), limit), deny),
        "cwd": cwd,
        "optimization": {
            "engine": "rtk",
            "applied": bool(rtk_result.get("applied")),
            "reason": rtk_result.get("reason", "unchanged"),
        },
    }
    if cwd_note:
        out["note"] = cwd_note
    audit("shell.run", command=cmd, exit_code=res.get("exit_code"), cwd=cwd,
          rtk_applied=bool(rtk_result.get("applied")))
    return out


register("shell", SCHEMA, handler, toolset="core", dangerous=True, concurrent=False)
