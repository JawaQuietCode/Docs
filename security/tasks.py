"""tasks.py — checkpoint, pembatalan, dan job background PER TENANT.

Kenapa ada: tugas agentic panjang (video, scraper, install besar) sering
melewati plafon waktu satu turn. Sebelum ini, saat plafon tercapai turn
dipotong dan SEMUA progres hilang — user hanya dapat pesan "aku hentikan".
Modul ini memberi tiga hal:

  1. CHECKPOINT  — progres turn disimpan berkala ke ``data/tasks/<slug>.json``
     sehingga tugas bisa DILANJUTKAN (``resume``) dari titik terakhir, bukan
     diulang dari nol.
  2. PEMBATALAN  — penanda ``cancel`` per-chat. Loop memeriksanya tiap langkah,
     jadi ``!stop`` benar-benar menghentikan kerja (bukan sekadar berhenti
     menunggu).
  3. JOB BACKGROUND — turn dijalankan di thread terpisah; transport hanya
     mengambil hasilnya saat selesai, jadi tidak ada lagi plafon tunggu.

Semua path mengikuti tenant AKTIF (``security.tenant_context``), sehingga satu
owner tidak pernah melihat/mengubah tugas owner lain.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path

from security import tenant_context

_LOCK = threading.RLock()

# task_id -> {id, chat_id, tenant, status, result, error, started, updated}
_JOBS: dict = {}
# chat_id -> timestamp permintaan berhenti (dianggap aktif selama TTL)
_CANCEL: dict = {}
CANCEL_TTL_SECONDS = 900

# Batas "tidak ada aktivitas" untuk job background. Worker yang masih hidup
# menyegarkan `updated` lewat touch_job() tiap langkah tool / tiap kali menunggu
# antrean, jadi job yang benar-benar macet (proses restart/OOM, worker hilang)
# bisa dikenali dan DITUTUP — bukan menggantung sampai plafon keselamatan.
# Sengaja longgar: satu panggilan shell boleh sampai ~300s dan satu panggilan
# LLM ~120s, jadi celah wajar antar-heartbeat ≈ 420s.
JOB_STALE_SECONDS = 900

# Batas ukuran checkpoint: riwayat percakapan bisa sangat panjang. Kita simpan
# maksimum N pesan terakhir supaya berkas tidak membengkak tanpa guna.
MAX_CHECKPOINT_MSGS = 400


def _slug(chat_id: str) -> str:
    """Nama berkas aman dari chat_id (mis. ``123@g.us`` → ``123_g.us``)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(chat_id or "unknown"))[:120] or "unknown"


def _tasks_dir() -> Path:
    """Folder tugas tenant aktif; fallback ke ``data/tasks``."""
    p = None
    try:
        p = tenant_context.current_tasks_dir()
    except Exception:
        p = None
    d = Path(p) if p else Path("data/tasks")
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def _checkpoint_file(chat_id: str) -> Path:
    return _tasks_dir() / f"{_slug(chat_id)}.json"


# ---------------------------------------------------------------- checkpoint
def save_checkpoint(chat_id: str, state: dict) -> None:
    """Simpan progres turn (best-effort; tak pernah melempar)."""
    try:
        st = dict(state or {})
        msgs = list(st.get("msgs") or [])
        if len(msgs) > MAX_CHECKPOINT_MSGS:
            st["msgs"] = msgs[-MAX_CHECKPOINT_MSGS:]
        st["chat_id"] = chat_id
        st["updated"] = time.time()
        path = _checkpoint_file(chat_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def load_checkpoint(chat_id: str) -> dict | None:
    """Muat checkpoint turn terakhir untuk chat ini (atau None)."""
    try:
        path = _checkpoint_file(chat_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8")) or None
    except Exception:
        return None


def clear_checkpoint(chat_id: str) -> None:
    """Hapus checkpoint (dipanggil saat turn benar-benar selesai)."""
    try:
        _checkpoint_file(chat_id).unlink(missing_ok=True)
    except Exception:
        pass


def has_checkpoint(chat_id: str) -> bool:
    return load_checkpoint(chat_id) is not None


# ---------------------------------------------------------------- pembatalan
def request_cancel(chat_id: str) -> None:
    """Minta turn berjalan di chat ini BERHENTI (dipakai perintah !stop)."""
    with _LOCK:
        _CANCEL[chat_id] = time.time()


def is_cancelled(chat_id: str) -> bool:
    """True bila ada permintaan berhenti yang masih berlaku untuk chat ini."""
    with _LOCK:
        ts = _CANCEL.get(chat_id)
        if not ts:
            return False
        if time.time() - ts > CANCEL_TTL_SECONDS:
            _CANCEL.pop(chat_id, None)
            return False
        return True


def clear_cancel(chat_id: str) -> None:
    """Bersihkan penanda berhenti (dipanggil saat turn BARU dimulai)."""
    with _LOCK:
        _CANCEL.pop(chat_id, None)


# ------------------------------------------------------------ job background
def _jobs_file() -> Path:
    """Berkas job GLOBAL (bukan per-tenant): ``_JOBS`` memang satu peta global.

    Job di-key oleh task_id acak dan menyimpan ``tenant`` di dalamnya, jadi
    daftarnya disimpan di satu tempat agar rekonsiliasi saat boot (yang berjalan
    TANPA konteks tenant) selalu menemukan berkas yang sama.
    """
    d = Path("data/tasks")
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d / "jobs.json"


def _persist_jobs_locked() -> None:
    """Tulis daftar job ke disk (dipanggil dengan _LOCK dipegang). Best-effort.

    Job background penting untuk DAYA TAHAN RESTART: bila proses mati di tengah
    tugas, transport masih memantau `/job` — dan tanpa rekonsiliasi, job itu
    akan "unknown" (pesan menggantung) atau menggantung "running" selamanya.
    """
    try:
        f = _jobs_file()
        data = list(_JOBS.values())
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(f)
    except Exception:
        pass


def reconcile_jobs() -> list[dict]:
    """Muat job dari disk saat boot; tandai yang tertinggal `running` sebagai error.

    Dipanggil sekali saat inti agent start. Job yang dulu `running` TIDAK MUNGKIN
    masih berjalan (thread-nya sudah hilang bersama proses lama), jadi jangan
    biarkan ia menggantung: ubah jadi `error` dengan pesan yang bisa
    dilanjutkan. Kembalikan daftar job yang direkonsiliasi (untuk audit).
    """
    out: list[dict] = []
    f = _jobs_file()
    if not f.exists():
        return out
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return out
    if not isinstance(data, list):
        return out
    now = time.time()
    with _LOCK:
        for job in data:
            if not isinstance(job, dict) or not job.get("id"):
                continue
            if job.get("status") == "running":
                job["status"] = "error"
                job["error"] = "tugas terputus oleh restart; ketik !lanjut untuk melanjutkan"
                job["updated"] = now
                out.append(dict(job))
            _JOBS[job["id"]] = job
        _persist_jobs_locked()
    return out


def create_job(chat_id: str, tenant: str = "") -> str:
    """Buat job baru; kembalikan task_id."""
    tid = uuid.uuid4().hex[:16]
    with _LOCK:
        _JOBS[tid] = {
            "id": tid,
            "chat_id": chat_id,
            "tenant": tenant,
            "status": "running",
            "result": None,
            "error": None,
            "started": time.time(),
            "updated": time.time(),
        }
        _persist_jobs_locked()
    return tid


def update_job(task_id: str, **fields) -> None:
    with _LOCK:
        job = _JOBS.get(task_id)
        if job is None:
            return
        job.update(fields)
        job["updated"] = time.time()
        _persist_jobs_locked()


def touch_job(task_id: str) -> None:
    """Segarkan `updated` (heartbeat) tanpa mengubah status.

    Dipanggil worker selagi MASIH bekerja (tiap langkah tool / tiap kali
    menunggu antrean) supaya job yang aktif tidak disalah-artikan sebagai macet
    oleh sapuan stale, sekaligus job yang benar-benar mati bisa terdeteksi.
    """
    with _LOCK:
        job = _JOBS.get(task_id)
        if job is not None:
            job["updated"] = time.time()


def get_job(task_id: str) -> dict | None:
    with _LOCK:
        job = _JOBS.get(task_id)
        return dict(job) if job else None


def running_job_for_chat(chat_id: str) -> dict | None:
    """Job yang masih berjalan untuk chat ini (untuk mencegah tumpang tindih)."""
    with _LOCK:
        for job in _JOBS.values():
            if job.get("chat_id") == chat_id and job.get("status") == "running":
                return dict(job)
    return None


def fail_stale_jobs(max_idle_seconds: int | None = None) -> list[dict]:
    """Tutup job `running` yang worker-nya sudah tidak menyegarkan heartbeat.

    Ini jaring KEDUA: kalau proses mati/restart atau thread worker hilang di
    tengah jalan, job tidak boleh menggantung `running` selamanya — transport
    akan memantau `/job` tanpa henti dan pengguna hanya melihat "Berpikir…".
    Job yang lewat batas ditandai `error` (bukan dihapus) supaya transport
    menerima pesan yang jelas dan berhenti menunggu.

    Kembalikan daftar job yang ditutup (untuk audit). Aman dipanggil berkali-kali.
    """
    idle = int(max_idle_seconds if max_idle_seconds is not None else JOB_STALE_SECONDS)
    now = time.time()
    closed: list[dict] = []
    with _LOCK:
        for job in _JOBS.values():
            if job.get("status") != "running":
                continue
            if now - (job.get("updated") or 0) <= idle:
                continue
            job["status"] = "error"
            job["error"] = "tugas berhenti tak terduga (worker tidak lagi aktif)"
            job["updated"] = now
            closed.append(dict(job))
        if closed:
            _persist_jobs_locked()
    return closed


def prune_jobs(max_age_seconds: int = 3600, keep: int = 50) -> None:
    """Buang job lama supaya memori tidak tumbuh tanpa batas (best-effort).

    PENTING: job yang masih `running` TIDAK PERNAH dibuang, berapa pun umurnya
    atau posisinya dalam urutan. Dulu job yang sedang berjalan bisa terbuang
    saat daftar melewati `keep` → `/job` menjawab "unknown" → transport berkata
    "job hilang sebelum selesai" padahal tugas masih dikerjakan.
    """
    now = time.time()
    with _LOCK:
        items = sorted(_JOBS.values(), key=lambda j: j.get("updated") or 0, reverse=True)
        kept_running = 0
        for i, job in enumerate(items):
            if job.get("status") == "running":
                # Hitung hanya job SELESAI terhadap kuota `keep`; job berjalan
                # selalu dipertahankan.
                kept_running += 1
                continue
            old = now - (job.get("updated") or 0) > max_age_seconds
            if i - kept_running >= keep or old:
                _JOBS.pop(job["id"], None)
        _persist_jobs_locked()
