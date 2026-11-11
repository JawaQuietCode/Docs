"""main.py — server HTTP inti agent (dipanggil transport Node).

Kontrak:
  GET  /health   -> {"ok": true, ...}
  GET  /status   -> {"active": bool, "chat_id", "step", "tool", "detail", "elapsed"}
  POST /message  -> {"replies": [{"type":"text","text":...}]}
"""
import json
import hashlib
import secrets as _secrets
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from agent.config import CONFIG, redact_config
from agent.consent import is_confirmation
from agent.context import build_history, build_system_prompt
from agent.intelligence import rank_tools
from agent import mechanisms
from agent.loop import run_turn
from agent.llm.client import LLMClient, LLMError
from memory import manager as store_manager
from memory import tenants
from memory.store import Store
from security import tasks
from security import tenant_context
from security import roles as _roles
from security.allowlist import is_allowed, normalize
from security.audit import audit
from security import public_quota
from tools import registry

# Muat semua tool bawaan SEKALI saat modul diimpor. Tanpa ini, registry kosong
# dan bot hanya bisa mengobrol tanpa bisa menjalankan tool apa pun.
registry.discover()
print(f"[agent] tool terdaftar: {[t['name'] for t in registry.all_tools()]}", flush=True)

# REKONSILIASI JOB: bila proses ini RESTART di tengah tugas, job yang dulu
# "running" tidak mungkin masih berjalan (thread-nya hilang bersama proses
# lama). Tanpa langkah ini, transport memantau `/job` selamanya dan pengguna
# hanya melihat "Berpikir…". Muat dari disk lalu tutup yang tertinggal.
try:
    for _j in tasks.reconcile_jobs():
        audit("agent.job_restart", chat=_j.get("chat_id"), job=_j.get("id"))
except Exception as _exc:  # noqa: BLE001
    print(f"[agent] rekonsiliasi job gagal: {_exc}", flush=True)

# ENGINE BAWAAN: kalau config tidak menyebut mekanisme, pakai `canvas`
# (OpenHands) saat perintahnya benar-benar ada; kalau tidak, tetap `native`.
# Ini yang membuat "engine agent" berganti hanya dengan memasang
# @openhands/agent-canvas — tanpa mengubah config.
try:
    if not str(CONFIG.get("defaultMechanism") or "").strip():
        _av = mechanisms.available()
        if _av.get("canvas"):
            CONFIG["defaultMechanism"] = "canvas"
            print("[agent] engine bawaan: canvas (OpenHands)", flush=True)
        else:
            CONFIG["defaultMechanism"] = "native"
except Exception as _exc:  # noqa: BLE001
    print(f"[agent] gagal menentukan engine bawaan: {_exc}", flush=True)

def _on_startup() -> None:
    """Periksa CLI mekanisme eksternal sekali (versi + bisa dijalankan).

    `available()` hanya melihat `which`; kalau flag `-p`/`exec` berubah di versi
    baru, dulu bot baru tahu ketika owner memakainya. Hasilnya di /health.
    """
    def _probe():
        try:
            h = mechanisms.smoke_test()
            for name, info in h.items():
                if not info.get("available"):
                    print(f"[agent] mekanisme {name}: tidak tersedia", flush=True)
                elif info.get("error"):
                    print(f"[agent] mekanisme {name}: {info['error']}", flush=True)
                else:
                    print(f"[agent] mekanisme {name}: {info.get('version')}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[agent] smoke test mekanisme gagal: {exc}", flush=True)

    threading.Thread(target=_probe, daemon=True, name="kwee-mech-probe").start()
    if not _AGENT_TOKEN:
        print("[agent] PERINGATAN: ACTION_TOKEN kosong — endpoint agent TANPA autentikasi",
              flush=True)


def _on_shutdown() -> None:
    """Tutup klien HTTP bersama (keep-alive) saat proses berhenti."""
    try:
        from agent.llm.client import close_client
        close_client()
    except Exception:
        pass
    try:
        from integrations.mcp_bridge import bridge_from_config
        bridge_from_config(CONFIG).close()
    except Exception:
        pass

@asynccontextmanager
async def _lifespan(_app):
    """Siklus hidup aplikasi (startup → shutdown).

    Dulu memakai `@app.on_event("startup"/"shutdown")`. API itu dihapus dari
    Starlette 1.x, dan kombinasi `fastapi==0.115.0` + Starlette baru (ditarik
    naik oleh paket `mcp`/`graphifyy` yang dipasang setup-intelligence.sh)
    membuat `FastAPI()` gagal dibuat dengan TypeError di `APIRouter.__init__`
    — itulah yang membuat agent mati sebelum health check. `lifespan` didukung
    FastAPI >= 0.93 dan semua Starlette, jadi aman di versi lama maupun baru.
    """
    _on_startup()
    try:
        yield
    finally:
        _on_shutdown()


app = FastAPI(title="kweewind blows core", lifespan=_lifespan)

# --------------------------------------------------------------------------- #
# AUTENTIKASI ENDPOINT
#
# Sebelum ini /message, /cancel, /status terbuka di 127.0.0.1 tanpa token: proses
# lain di container (atau agent itu sendiri lewat `curl localhost:8787/message`)
# bisa menyuruh bot sebagai owner. Sekarang setiap request harus membawa header
# `X-Agent-Token` yang sama dengan ACTION_TOKEN (transport Node mengirimkannya
# otomatis). Bila token belum diisi di .env, autentikasi dimatikan supaya
# instalasi lama tidak langsung mati — start.sh akan memperingatkan.
# --------------------------------------------------------------------------- #
_AGENT_TOKEN = str(CONFIG["agent"].get("authToken") or "").strip()


def _guard(x_agent_token: str = Header(default="")) -> None:
    if not _AGENT_TOKEN:
        return
    if not _secrets.compare_digest(str(x_agent_token or ""), _AGENT_TOKEN):
        raise HTTPException(status_code=401, detail="token agent salah")


_AUTH = [Depends(_guard)]

# Kompatibilitas: beberapa tes lama memakai `agent.main.STORE`. Itu Store milik
# tenant UTAMA (path lama). Permintaan nyata memakai Store per-tenant di bawah.
# Dibuat LAZY: dulu `Store("data/memory/kwee.db")` dibuka saat import, sehingga
# DB ikut terbuka (dan bisa termigrasi) walau tidak pernah dipakai.
_STORE_LEGACY = None


def _legacy_store():
    global _STORE_LEGACY
    if _STORE_LEGACY is None:
        _STORE_LEGACY = Store("data/memory/kwee.db")
    return _STORE_LEGACY


class _StoreProxy:
    """Proxy agar `agent.main.STORE` tetap bisa dipakai tes lama."""

    def __getattr__(self, item):
        return getattr(_legacy_store(), item)


STORE = _StoreProxy()
_LOCK = Lock()          # serialisasi per-proses
_CHAT_LOCKS: dict = {}  # serialisasi per-chat

# Aktivitas berjalan per-chat, dipakai endpoint /status supaya transport bisa
# menampilkan "sedang apa" (mirip typing/terminal log) kepada pemilik.
_ACTIVITY: dict = {}
_ACTIVITY_LOCK = Lock()


def _set_activity(chat_id: str, **fields) -> None:
    """Catat status terkini sekaligus jejak event agar polling tak kehilangan langkah."""
    now = time.time()
    with _ACTIVITY_LOCK:
        cur = _ACTIVITY.get(chat_id) or {}
        if fields.get("stage") == "skills" and fields.get("started"):
            cur["events"] = []
            cur["event_seq"] = 0
        cur.update(fields)
        cur["ts"] = now

        event_fields = {"stage", "step", "tool", "detail", "ok", "event_kind", "event_id"}
        if event_fields.intersection(fields):
            event = {
                "stage": cur.get("stage", "think"),
                "step": cur.get("step", "berpikir"),
                "tool": cur.get("tool", ""),
                "detail": cur.get("detail", "") if cur.get("tool") else "",
                "ok": cur.get("ok", True),
                "kind": cur.get("event_kind", "phase"),
                "event_id": cur.get("event_id", ""),
                "ts": now,
            }
            signature = tuple(event.get(k) for k in ("stage", "step", "tool", "detail", "ok", "kind", "event_id"))
            events = cur.setdefault("events", [])
            previous = events[-1] if events else None
            previous_signature = tuple(previous.get(k) for k in ("stage", "step", "tool", "detail", "ok", "kind", "event_id")) if previous else None
            if signature != previous_signature:
                cur["event_seq"] = int(cur.get("event_seq", 0)) + 1
                event["seq"] = cur["event_seq"]
                events.append(event)
                del events[:-40]
        _ACTIVITY[chat_id] = cur


def _clear_activity(chat_id: str) -> None:
    with _ACTIVITY_LOCK:
        _ACTIVITY.pop(chat_id, None)


# --------------------------------------------------------------------------- #
# IDEMPOTENSI PESAN
#
# WhatsApp mengirim ulang pesan yang sama bila ACK hilang (jaringan seluler
# buruk). Tanpa penjagaan ini, satu perintah bisa dieksekusi dua kali —
# berbahaya untuk aksi seperti "hapus file" atau "kirim pesan ke grup".
# --------------------------------------------------------------------------- #
_SEEN_MSG: dict = {}
_SEEN_TTL = 600.0
_SEEN_MAX_PER_CHAT = 200


def _already_handled(chat_id: str, message_id: str) -> bool:
    """True bila message_id ini sudah pernah diproses (dalam TTL)."""
    if not message_id:
        return False
    now = time.time()
    with _ACTIVITY_LOCK:
        bucket = _SEEN_MSG.setdefault(chat_id, {})
        for mid in [k for k, ts in bucket.items() if now - ts > _SEEN_TTL]:
            bucket.pop(mid, None)
        if message_id in bucket:
            return True
        bucket[message_id] = now
        if len(bucket) > _SEEN_MAX_PER_CHAT:
            for mid, _ in sorted(bucket.items(), key=lambda kv: kv[1])[:50]:
                bucket.pop(mid, None)
    return False


class Incoming(BaseModel):
    chat_id: str
    sender_id: str
    # Semua identitas pengirim yang diketahui (PN **dan** LID). WhatsApp kini
    # sering mengirim hanya LID, yang angkanya bukan nomor telepon.
    sender_ids: list[str] = []
    text: str = ""
    sender_name: str = ""
    is_group: bool = False
    message_id: str = ""
    is_mention_bot: bool = False
    is_reply_to_bot: bool = False
    # Teks pesan yang DI-REPLY pemilik (kutipan). Dikirim transport dari
    # `contextInfo.quotedMessage` supaya AI paham apa yang sedang dibicarakan.
    quoted_text: str = ""
    quoted_kind: str = ""
    # LAMPIRAN media (gambar/video/audio/dokumen/stiker) — transport sudah
    # mengunduhnya ke `data/inbox` dan mengirim metadatanya di sini. Inti agent
    # menyimpannya ke penyimpanan aset milik owner, lalu memberi tahu model agar
    # bisa "melihat" (vision) atau merujuk aset itu.
    attachments: list[dict] = []
    # Media hanya boleh dianalisis jika user membalas media dengan instruksi,
    # atau men-tag bot pada pesan yang memiliki caption.
    inspect_media: bool = False
    # MULTI-TENANT: tenant eksplisit (dipakai job terjadwal). Bila kosong,
    # tenant disimpulkan dari identitas pengirim.
    tenant: str = ""
    # True bila pesan ini berasal dari PENJADWAL internal (job milik owner).
    # Job sudah dibuat oleh owner, jadi allowlist dilewati — tetapi tenant tetap
    # harus owner terdaftar supaya tetap terisolasi.
    scheduled: bool = False
    # Hanya transport WhatsApp lokal yang boleh mengaktifkan jalur ini. Pengguna
    # publik mendapat tenant sendiri dan role `user`, bukan hak owner.
    public_user: bool = False
    public_identity: str = ""
    model: str = ""
    # MODE: "sync" (default, perilaku lama) atau "background" untuk job panjang.
    mode: str = "sync"


@app.post("/quota", dependencies=_AUTH)
def quota_status(payload: dict | None = None):
    """Quota status for the identity supplied by the authenticated transport."""
    data = payload or {}
    identity = str(data.get("identity") or "").strip()
    if not identity:
        raise HTTPException(status_code=400, detail="identity kosong")
    identities = [identity]
    lid_dir = _resolve_lid_dir(CONFIG)
    if is_allowed(identities, CONFIG.get("owner", []), lid_dir=lid_dir):
        return {"unlimited": True}
    cfg = CONFIG.get("publicChat") or {}
    if not cfg.get("enabled", False):
        raise HTTPException(status_code=403, detail="public chat nonaktif")
    status = public_quota.status(identity, window_seconds=int(cfg.get("windowSeconds", 129600) or 129600), config=CONFIG)
    limit = max(0, int(cfg.get("tokenLimit", 1000000) or 0))
    return {"unlimited": False, "used": status["used"], "limit": limit,
            "remaining": max(0, limit - status["used"]), "reset_at": status["reset_at"]}


def _llm(model_override: str = ""):
    llm_cfg = CONFIG.get("llm", {}) or {}
    choices = {str(llm_cfg.get("model") or ""), *[str(x) for x in (llm_cfg.get("fallbackModels") or [])]}
    selected = str(model_override or "").strip()
    if selected and selected not in choices:
        raise LLMError("model tidak tersedia dalam konfigurasi server")
    return LLMClient(
        base_url=llm_cfg.get("baseUrl", ""),
        api_key=llm_cfg.get("apiKey", ""),
        model=selected or llm_cfg.get("model", ""),
        timeout=llm_cfg.get("timeoutSeconds", 120),
        temperature=llm_cfg.get("temperature", 0.7),
        max_tokens=llm_cfg.get("maxTokens", 2048),
        fallback_models=llm_cfg.get("fallbackModels", []),
        # Streaming + retry + batas gambar vision (lihat agent/llm/client.py).
        stream=bool(llm_cfg.get("stream", True)),
        retry_max=int(llm_cfg.get("retryMax", 3) or 3),
        vision_max_px=int(llm_cfg.get("visionMaxPx", 1568) or 1568),
        request_interval_seconds=float(llm_cfg.get("requestIntervalSeconds", 10) or 0),
    )


def _chat_lock(chat_id: str):  # noqa: D401 - dipertahankan untuk kompatibilitas tes lama
    """Lock per-chat sederhana (dipakai sebagian tes). Produksi memakai lease."""
    with _LOCK:
        if chat_id not in _CHAT_LOCKS:
            _CHAT_LOCKS[chat_id] = Lock()
        return _CHAT_LOCKS[chat_id]


# Pesan saat antrean chat terlalu panjang (tugas lain masih berjalan). Bukan
# "Berpikir…" tanpa akhir: pemilik diberi tahu apa yang terjadi & bisa lanjut.
QUEUE_BUSY_MESSAGE = (
    "Masih ada tugas lain yang sedang berjalan di chat ini dan antreannya sudah "
    "terlalu lama, jadi tugasmu belum aku mulai. Coba kirim lagi sebentar lagi ya."
)


def _queue_wait_seconds() -> float:
    try:
        return float(CONFIG["agent"].get("queueWaitSeconds", 1800))
    except Exception:
        return 1800.0


def _stale_seconds() -> float:
    try:
        return float(CONFIG["agent"].get("jobStaleSeconds", 900))
    except Exception:
        return 900.0


def _lease_seconds() -> float:
    """Berapa lama pemegang lock boleh diam sebelum locknya boleh dirampas.

    Harus LEBIH PANJANG dari satu langkah terpanjang (shell ~300s, LLM ~120s)
    supaya pemegang yang sehat tak pernah kehilangan locknya; tapi cukup pendek
    supaya pemegang yang benar-benar MATI (thread hilang/proses restart) tak
    memblokir antrean selamanya. Nilainya dari config (`agent.leaseSeconds`).
    """
    try:
        return float(CONFIG["agent"].get("leaseSeconds", 600) or 600)
    except Exception:
        return 600.0


# chat_id -> {"job": id, "until": ts}. Lock "sewa": pemegang memperpanjang
# (renew) tiap langkah; penunggu boleh merampas setelah sewa kedaluwarsa.
_CHAT_BUSY: dict = {}


def _acquire_chat_lock(chat_id: str, job_id: str = "", wait_limit: float | None = None) -> bool:
    """Kuasai lock chat dengan BATAS TUNGGU + sewa (lease) + heartbeat.

    Serialisasi per-chat penting (pesan beruntun tidak saling tabrakan), tetapi
    menunggu TANPA batas berbahaya: bila satu tugas panjang — atau tugas yang
    MACET — memegang lock, pesan berikutnya akan "berpikir" tanpa balasan
    selamanya. Karena itu:

      * lock berbasis SEWA: pemegang yang sehat memperpanjangnya tiap langkah,
      * penunggu boleh MERAMPAS lock yang sewanya kedaluwarsa (pemegang mati),
      * penunggu menyerah setelah ``queueWaitSeconds`` dan membalas jelas.

    Return True bila lock didapat; False bila batas tunggu terlampaui.
    """
    limit = _queue_wait_seconds() if wait_limit is None else float(wait_limit)
    lease = _lease_seconds()
    waited = 0.0
    step = 0.5
    first = True
    me = job_id or "-"
    while True:
        now = time.time()
        with _LOCK:
            cur = _CHAT_BUSY.get(chat_id)
            if cur is None or cur.get("job") == me or now >= float(cur.get("until") or 0):
                # Bebas, milik sendiri, atau pemegangnya sudah kedaluwarsa → rampas.
                _CHAT_BUSY[chat_id] = {"job": me, "until": now + lease}
                return True
        if first:
            _set_activity(chat_id, step="menunggu antrean", tool="", detail="")
            first = False
        if job_id:
            tasks.touch_job(job_id)   # tetap "hidup" selagi mengantre
        if waited >= limit:
            return False
        time.sleep(step)
        waited += step


def _renew_chat_lock(chat_id: str, job_id: str = "") -> None:
    """Perpanjang sewa lock (heartbeat pemegang) supaya tidak dirampas penunggu."""
    me = job_id or "-"
    with _LOCK:
        cur = _CHAT_BUSY.get(chat_id)
        if cur is not None and cur.get("job") == me:
            cur["until"] = time.time() + _lease_seconds()


def _release_chat_lock(chat_id: str, job_id: str = "") -> None:
    me = job_id or "-"
    with _LOCK:
        cur = _CHAT_BUSY.get(chat_id)
        if cur is not None and cur.get("job") == me:
            _CHAT_BUSY.pop(chat_id, None)


def _turn_should_stop(chat_id: str, owner: str, job_id: str = "") -> bool:
    """Callback loop tiap-iterasi: heartbeat + cek permintaan !stop.

    Dipakai sebagai `should_stop` run_turn. Karena dipanggil tiap langkah loop,
    ini memastikan (a) sewa lock tetap milik kita selama turn benar-benar hidup,
    dan (b) job background terus menandai dirinya hidup — sehingga pekerja yang
    AKTIF tidak pernah disalah-artikan macet, sementara yang benar-benar mati
    tetap bisa ditutup watchdog.
    """
    _renew_chat_lock(chat_id, owner)
    if job_id:
        tasks.touch_job(job_id)
    return tasks.is_cancelled(chat_id)


# Watchdog: tutup job background yang worker-nya sudah tidak menyegarkan
# heartbeat. Ini jaring supaya tugas yang benar-benar macet (thread hilang,
# proses di-restart di tengah tugas) tidak menggantung "Berpikir…" selamanya —
# transport menerima status akhir dan mengirim pesan yang bisa dilanjutkan.
_WATCHDOG_STARTED = False
_WATCHDOG_LOCK = Lock()


def _ensure_watchdog() -> None:
    global _WATCHDOG_STARTED
    with _WATCHDOG_LOCK:
        if _WATCHDOG_STARTED:
            return
        _WATCHDOG_STARTED = True

    def _loop():
        while True:
            time.sleep(30)
            try:
                for job in tasks.fail_stale_jobs(_stale_seconds()):
                    audit("agent.job_stale", chat=job.get("chat_id"), job=job.get("id"))
            except Exception:
                pass

    threading.Thread(target=_loop, daemon=True, name="kwee-watchdog").start()


def _resolve_lid_dir(cfg) -> str | None:
    """Folder sesi (tempat file lid-mapping-*.json) sebagai path absolut.

    authDir di config relatif terhadap root proyek, sedangkan proses bisa
    dijalankan dari direktori mana pun — jadi dijadikan absolut di sini.
    """
    raw = (cfg.get("whatsapp") or {}).get("authDir") or ""
    if not raw:
        return None
    from pathlib import Path
    p = Path(raw)
    if not p.is_absolute():
        p = Path(cfg.get("root") or ".") / p
    return str(p)


@app.get("/health", dependencies=_AUTH)
def health():
    return {
        "ok": True,
        "name": CONFIG.get("identity", {}).get("name", "kweewind blows"),
        "model": CONFIG["llm"].get("model", ""),
        "tools": [t["name"] for t in registry.all_tools()],
        "primary_owner": tenants.primary_tenant(CONFIG),
        "owners": tenants.owner_numbers(CONFIG),
        "default_mechanism": tenants.normalize_mechanism(CONFIG.get("defaultMechanism") or "native"),
        "mechanisms_available": mechanisms.available(),
    }


@app.get("/tenants", dependencies=_AUTH)
def list_tenants():
    """Daftar tenant (owner) beserta mekanisme, ruangan, DB/sesi/tugas masing-masing."""
    rows = tenants.list_tenants(CONFIG)
    for r in rows:
        m = tenants.mechanism_for(r["id"], CONFIG)
        room = tenants.room_paths(r["id"], m, CONFIG)
        r["mechanism"] = m
        r["mechanism_effective"] = mechanisms.resolve(m, CONFIG)
        r["room"] = str(room["root"])
        r["db"] = str(room["db"])
        r["workspace"] = str(room["workspace"])
    return {
        "primary": tenants.primary_tenant(CONFIG),
        "tenants": rows,
        "mechanisms_available": mechanisms.available(),
    }


@app.get("/status", dependencies=_AUTH)
def status(chat_id: str = ""):
    """Aktivitas berjalan (mirip typing/terminal log) untuk pemilik.

    Mengembalikan langkah terakhir yang sedang dikerjakan agent, sehingga
    transport bisa menampilkan "sedang apa" tanpa menunggu balasan akhir.
    """
    with _ACTIVITY_LOCK:
        items = [dict(v, chat_id=k) for k, v in _ACTIVITY.items()]
    if chat_id:
        items = [i for i in items if i.get("chat_id") == chat_id]
    for i in items:
        i["elapsed"] = round(time.time() - i.get("ts", time.time()), 1)
    return {"active": bool(items), "activities": items}


@app.get("/whoami", dependencies=_AUTH)
def whoami(sender_id: str = "", sender_ids: str = "", tenant: str = ""):
    """Info tenant + mekanisme + ruangan untuk identitas pengirim.

    ``sender_ids`` boleh dipisah koma (PN + LID). Dipakai perintah ``!tenant``.
    """
    ids = [s for s in [sender_id, *str(sender_ids or "").split(",")] if s.strip()]
    lid_dir = _resolve_lid_dir(CONFIG)
    t = tenants.normalize(tenant) if tenant else ""
    if not (t and tenants.is_owner_tenant(t, CONFIG)):
        t = tenants.resolve_tenant(ids, CONFIG, lid_dir=lid_dir) or tenants.primary_tenant(CONFIG)
    m = tenants.mechanism_for(t, CONFIG)
    room = tenants.room_paths(t, m, CONFIG)
    return {
        "owner": tenants.is_owner_tenant(t, CONFIG),
        "tenant": t,
        "primary": tenants.is_primary(t, CONFIG),
        "mechanism": m,
        "mechanism_effective": mechanisms.resolve(m, CONFIG),
        "room": str(room["root"]),
        "db": str(room["db"]),
        "workspace": str(room["workspace"]),
        "cron": str(room["cron"]),
        "mechanisms_available": mechanisms.available(),
    }


def _is_resume_request(text: str) -> bool:
    """True bila pesan adalah perintah melanjutkan tugas (!lanjut/!resume/!continue)."""
    t = str(text or "").strip().lower()
    return t in ("!lanjut", "!resume", "!continue", "!teruskan", "!continuekan")


@app.get("/job/{job_id}", dependencies=_AUTH)
def get_job(job_id: str):
    """Ambil status/hasil job background. Transport memanggil ini sampai selesai."""
    job = tasks.get_job(job_id)
    if not job:
        return {"ok": False, "error": "job tidak ditemukan", "status": "unknown"}
    out = {
        "ok": True,
        "id": job["id"],
        "status": job["status"],      # running | done | error
        "tenant": job.get("tenant", ""),
        "started": job.get("started"),
        "updated": job.get("updated"),
    }
    if job["status"] == "done":
        hasil = job.get("result") or {}
        out["replies"] = hasil.get("replies") or []
        out["raw"] = hasil.get("raw") or ""
        # Jejak langkah (thinking/tool) ikut dikirim supaya UI kaya tetap
        # bekerja pada tugas background — bukan hanya jawaban langsung.
        out["trace"] = hasil.get("trace") or []
        # Snapshot aktivitas final + laporan HTML ikut dikirim. Tanpa ini transport
        # tidak pernah menerimanya di jalur background (default), sehingga tool
        # cepat hilang dari kartu dan laporan progres tidak pernah terlampir.
        if hasil.get("activity"):
            out["activity"] = hasil["activity"]
        if hasil.get("activity_html"):
            out["activity_html"] = hasil["activity_html"]
    elif job["status"] == "error":
        out["error"] = job.get("error") or "gagal"
    return out


# --------------------------------------------------------------------------- #
# PROMPT & SKILLS (dipakai perintah `!prompt` / `!skills` di WhatsApp)
#
# Dua hal ini sebelumnya hanya "ada di disk": persona tidak bisa dilihat atau
# diganti tanpa restart, dan daftar skill tidak bisa diperiksa owner. Endpoint di
# bawah membuat transport cukup memformat balasan — logika (prioritas persona,
# cache mtime, katalog) tetap satu sumber di Python.
# --------------------------------------------------------------------------- #
class PromptIn(BaseModel):
    action: str = "info"        # info | set | reset | reload
    name: str = ""
    sender_id: str = ""


def _owner_only(sender_id: str) -> bool:
    """Persona hanya boleh diubah OWNER terdaftar (pertahanan berlapis).

    Transport sudah membatasi perintah ke owner, tetapi endpoint HTTP bisa
    dipanggil hal lain di container — jadi kepemilikan diperiksa lagi di sini
    dengan normalisasi nomor (628xxx / +62 / 62-8xx semuanya cocok).
    """
    sid = str(sender_id or "").strip()
    if not sid:
        return True          # dipanggil internal (tanpa identitas) → izinkan
    try:
        return tenants.is_owner_tenant(tenants.normalize(sid), CONFIG)
    except Exception:
        return False


@app.get("/prompt", dependencies=_AUTH)
def prompt_info():
    """Ringkasan prompt aktif (persona + rules + daftar persona tersedia)."""
    from agent import promptlib
    return promptlib.info()


@app.post("/prompt", dependencies=_AUTH)
def prompt_action(m: PromptIn):
    """Ganti / reset / muat ulang persona. Hanya untuk owner."""
    from agent import promptlib
    act = str(m.action or "info").strip().lower()
    if act in ("set", "ganti", "pakai", "use") and not _owner_only(m.sender_id):
        return {"error": "hanya owner yang boleh mengganti persona"}
    if act in ("set", "ganti", "pakai", "use"):
        return promptlib.set_active(m.name)
    if act in ("reset", "default", "bawaan"):
        return promptlib.reset()
    if act in ("reload", "muat"):
        promptlib.clear_cache()
        try:
            from skills import loader as _sk
            _sk.clear_cache()
        except Exception:
            pass
        return {"ok": True, "reload": True, "info": promptlib.info()}
    return promptlib.info()


@app.get("/skills", dependencies=_AUTH)
def skills_info(query: str = "", name: str = "", file: str = "", reload: int = 0):
    """Statistik skill / cari / muat satu skill (hidden TIDAK pernah dibuka)."""
    from skills import loader
    from tools.builtin import skill_tool
    if reload:
        loader.clear_cache()
    if name:
        return skill_tool.handler({"action": "get", "name": name, "file": file},
                                  redact_config(CONFIG))
    if query:
        hits = loader.match_external(query, limit=5)
        return {"ok": True, "query": query,
                "matches": [{"name": x["name"], "description": x["description"],
                             "triggers": x["triggers"][:8],
                             "references": x.get("references", [])[:10]} for x in hits]}
    st = loader.stats()
    st["ok"] = True
    return st


# AFK command routes are isolated from the core request handler. HTTP mutations
# use the same transport auth guard; the WebSocket is protected by a scoped,
# short-lived read-only token embedded in the GenAI HTML card.
from agent.afk_api import create_router as create_afk_router
app.include_router(create_afk_router(_guard))


class VideoVisionIn(BaseModel):
    asset: str = ""
    path: str = ""
    prompt: str = "Ringkas isi video ini."
    max_frames: int = 4


@app.post("/video-vision", dependencies=_AUTH)
def video_vision_endpoint(p: VideoVisionIn):
    from agent.video_vision import analyze_video
    result = analyze_video(p.asset or p.path, p.prompt,
                           max_frames=max(1, min(int(p.max_frames or 4), 6)))
    audit("vision.video", asset=p.asset or p.path, ok=bool(result.get("ok")))
    return result


class SearchIn(BaseModel):
    query: str = ""
    chat_id: str = ""
    sender_id: str = ""
    limit: int = 6


@app.post("/search", dependencies=_AUTH)
def search_endpoint(p: SearchIn):
    """Pencarian lintas sumber (riwayat, berkas, memori, skill)."""
    from tools.builtin import search_all
    hasil = search_all.search(
        p.query, chat_id=p.chat_id, limit=max(1, min(int(p.limit or 6), 20)),
        root=str(CONFIG.get("root") or "."),
    )
    audit("search.run", chat=p.chat_id, query=p.query[:80], total=hasil.get("total", 0))
    return hasil


class VisionIn(BaseModel):
    asset: str = ""
    path: str = ""
    url: str = ""
    data_url: str = ""
    prompt: str = "Jelaskan gambar ini secara rinci: objek, teks yang terlihat, dan konteksnya."


@app.post("/vision", dependencies=_AUTH)
def vision_endpoint(p: VisionIn):
    """Lihat satu gambar dan kembalikan deskripsi teks.

    Dipakai transport (engine OpenHands) untuk mengubah lampiran gambar menjadi
    teks yang bisa dipahami engine — engine itu tidak menerima gambar langsung.
    """
    from tools.builtin import vision as vision_tool
    hasil = vision_tool.handler(
        {"asset": p.asset, "path": p.path, "url": p.url,
         "data_url": p.data_url, "prompt": p.prompt},
        dict(CONFIG),
    )
    audit("vision.look", asset=p.asset or p.path or p.url, ok=not hasil.get("error"))
    return hasil


class FanoutIn(BaseModel):
    tasks: list = []
    chat_id: str = ""
    sender_id: str = ""


def _fanout_tool_config(sender_id: str, chat_id: str) -> dict | None:
    """Build tenant-bound tool permissions for the authenticated WhatsApp owner."""
    identities = [str(sender_id or "").strip()]
    if not identities[0] or not str(chat_id or "").strip():
        return None
    tenant = tenants.resolve_tenant(identities, CONFIG, lid_dir=_resolve_lid_dir(CONFIG))
    if not tenant or not tenants.is_owner_tenant(tenant, CONFIG):
        return None
    mechanism = tenants.mechanism_for(tenant, CONFIG)
    tpaths = tenants.ensure_room(tenant, mechanism, CONFIG)
    tpaths["mechanism"] = mechanism
    store = store_manager.store_for(tenant, CONFIG, mechanism=mechanism)
    role = _roles.role_for_tenant(tenant, CONFIG)
    allowed_tools = _roles.filter_tools(role, CONFIG, registry.all_tools())
    if "subagent" not in {t.get("name") for t in allowed_tools}:
        return None
    cfg = redact_config(CONFIG)
    cfg["context"] = {
        "tenant": tenant,
        "chat_id": str(chat_id),
        "memory_db_path": str(getattr(store, "path", "") or ""),
        "tenant_root": str(tpaths.get("root") or ""),
        "is_primary": tenants.is_primary(tenant, CONFIG),
        "role": role,
        "tool_allow": sorted(t.get("name") for t in allowed_tools),
        "owner_confirmed": False,
        "owner_request_text": "!team",
    }
    return cfg


@app.post("/fanout", dependencies=_AUTH)
def fanout_endpoint(p: FanoutIn):
    """Jalankan tim hanya di tenant owner, dengan izin tool yang sama seperti chat utama."""
    from tools.builtin import subagent
    tool_config = _fanout_tool_config(p.sender_id, p.chat_id)
    if not tool_config:
        audit("fanout.rejected", chat=p.chat_id, sender=normalize(p.sender_id), reason="owner/tenant tidak terverifikasi")
        raise HTTPException(status_code=403, detail="Tim sub-agent hanya tersedia untuk owner terdaftar.")
    tugas = []
    for t in (p.tasks or [])[:5]:
        if isinstance(t, str) and t.strip():
            tugas.append({"judul": t.strip()[:60], "goal": t.strip()})
        elif isinstance(t, dict) and (t.get("goal") or t.get("tugas")):
            tugas.append({
                "judul": str(t.get("judul") or "")[:60],
                "goal": str(t.get("goal") or t.get("tugas")),
                "context": str(t.get("context") or ""),
                "agent": str(t.get("agent") or "peer"),
                "remember": bool(t.get("remember", True)),
            })
    if not tugas:
        return {"ok": False, "error": "tidak ada tugas", "tugas": []}
    audit("fanout.start", chat=p.chat_id, tenant=tool_config["context"]["tenant"], jumlah=len(tugas))
    hasil = subagent.fanout(tugas, tool_config)
    audit("fanout.done", chat=p.chat_id, tenant=tool_config["context"]["tenant"], jumlah=len(hasil.get("tugas") or []))
    return hasil


class GroupIn(BaseModel):
    action: str = "ask"
    chat_id: str = ""
    sender_id: str = ""
    pertanyaan: str = ""
    teks: str = ""
    nama: str = ""
    grup: str = ""
    limit: int = 3


@app.post("/group", dependencies=_AUTH)
def group_endpoint(p: GroupIn):
    """Admin grup: FAQ dokumen, sambutan, aturan, penilaian spam.

    Membaca (`ask`, `rules`) boleh untuk anggota ruangan; mengubah/mengindeks
    (`index`, `list`, `welcome`) hanya owner.
    """
    from tools.builtin import group_admin
    act = (p.action or "ask").strip().lower()
    if act not in ("ask", "index", "list", "welcome", "rules", "spam_check"):
        return {"ok": False, "error": f"action tidak dikenal: {act}"}
    if act in ("index", "list", "welcome") and not _owner_only(p.sender_id or p.chat_id):
        return {"ok": False, "error": "khusus owner"}
    cfg = redact_config(CONFIG)
    cfg["context"] = {"chat_id": p.chat_id or "umum", "sender_id": p.sender_id}
    hasil = group_admin.handler({
        "action": act,
        "pertanyaan": p.pertanyaan,
        "teks": p.teks,
        "nama": p.nama,
        "grup": p.grup,
        "limit": p.limit,
    }, cfg)
    audit("group.admin", chat=p.chat_id, action=act,
          ok=bool(hasil.get("ok", True)))
    return hasil


@app.get("/selfcheck", dependencies=_AUTH)
def selfcheck():
    """Pemeriksaan integritas diri: baca/tulis, registry, skill, konfigurasi.

    Dipakai `!sehat` dan `kwee doctor`. Sengaja TIDAK memanggil LLM — harus
    tetap bisa dijalankan saat otak AI sedang bermasalah.
    """
    butir = []

    def cek(nama, fn):
        try:
            info = fn()
            ok = True if info is None else bool(info if isinstance(info, bool) else True)
            butir.append({"nama": nama, "ok": ok,
                          "info": "" if info is None else str(info)[:120]})
        except Exception as exc:  # noqa: BLE001
            butir.append({"nama": nama, "ok": False, "info": str(exc)[:120]})

    def _tulis_baca():
        import tempfile
        from tools.builtin import files as ftool
        cfg = redact_config(CONFIG)
        cfg["security"] = dict(cfg.get("security") or {})
        # Pemeriksaan HARUS di folder sementara: tanpa ini berkas uji tertinggal
        # di root proyek (dan ikut masuk paket ZIP berikutnya).
        tmpdir = tempfile.mkdtemp(prefix="kwee-cek-")
        cfg["root"] = tmpdir
        cfg["security"]["workspaceDir"] = tmpdir
        isi = "kweewind-blows · uji integritas"
        w = ftool.handler({"action": "write", "path": "cek.txt", "content": isi}, cfg)
        if not w.get("ok") or not w.get("verified"):
            raise RuntimeError(w.get("error") or "tulis tidak terverifikasi")
        r = ftool.handler({"action": "read", "path": "cek.txt"}, cfg)
        if r.get("content") != isi:
            raise RuntimeError("isi berbeda setelah dibaca ulang")
        return f"sha {w.get('sha256')}"

    def _registry():
        from tools import registry
        semua = registry.all_tools()
        wajib = {"shell", "terminal", "files", "browser", "search_all", "group_admin", "subagent"}
        kurang = wajib - {t["name"] for t in semua}
        if kurang:
            raise RuntimeError(f"tool hilang: {', '.join(sorted(kurang))}")
        return f"{len(semua)} tool"

    def _skill():
        from skills import loader
        st = loader.stats()
        return f"{st.get('external', 0)} external · {st.get('internal', 0)} internal"

    def _config():
        wajib = ("security", "agent", "llm", "identity")
        kurang = [k for k in wajib if not CONFIG.get(k)]
        if kurang:
            raise RuntimeError(f"bagian config hilang: {kurang}")
        return "lengkap"

    def _llm():
        llm = CONFIG.get("llm") or {}
        if not llm.get("baseUrl") or not llm.get("model"):
            raise RuntimeError("baseUrl/model kosong")
        return f"{llm.get('model')} · {len(llm.get('fallbackModels') or [])} cadangan"

    def _shell():
        sec = CONFIG.get("security") or {}
        if not isinstance(sec, dict) or not sec:
            raise RuntimeError("bagian security kosong — shell fail-closed")
        return f"allowAll={bool(sec.get('allowAllExecutables'))}"

    cek("tulis & baca berkas", _tulis_baca)
    cek("registry tool", _registry)
    cek("katalog skill", _skill)
    cek("konfigurasi", _config)
    cek("otak AI", _llm)
    cek("keamanan shell", _shell)

    lulus = sum(1 for b in butir if b["ok"])
    return {"ok": lulus == len(butir), "total": len(butir), "lulus": lulus,
            "gagal": len(butir) - lulus, "butir": butir}


@app.post("/cancel", dependencies=_AUTH)
def cancel(payload: dict | None = None):
    """Minta turn berjalan di sebuah chat BERHENTI (dipakai perintah !stop)."""
    chat_id = str((payload or {}).get("chat_id") or "").strip()
    if not chat_id:
        return {"ok": False, "error": "chat_id kosong"}
    tasks.request_cancel(chat_id)
    audit("task.cancel", chat=chat_id)
    return {"ok": True, "chat_id": chat_id}


@app.post("/message", dependencies=_AUTH)
def message(m: Incoming):
    # /addown writes this private runtime allowlist only after a second owner
    # confirmation. Merge it on every request so the Python authorization layer
    # observes the same additions as the WhatsApp transport without editing .env.
    try:
        owner_file = Path(str(CONFIG.get("root") or ".")).resolve() / "data" / "owners.json"
        runtime_owners = json.loads(owner_file.read_text(encoding="utf-8")) if owner_file.is_file() else []
        if isinstance(runtime_owners, list):
            CONFIG["owner"] = list(dict.fromkeys([
                *[str(x).strip() for x in (CONFIG.get("owner") or []) if str(x).strip()],
                *[str(x).strip() for x in runtime_owners if str(x).strip()],
            ]))
    except Exception as exc:  # malformed overlay never weakens the env allowlist
        audit("owner.runtime_list_error", error=str(exc))
    # 1) Allowlist — non-owner diabaikan total.
    #    Cek SEMUA identitas (PN + LID); LID bukan nomor telepon. Bila perlu,
    #    perluas dengan pemetaan LID<->PN dari store sesi (defense-in-depth).
    identities = list(m.sender_ids) + [m.sender_id]
    lid_dir = _resolve_lid_dir(CONFIG)
    if m.public_user and not _AGENT_TOKEN:
        audit("public_chat.rejected", reason="agent authentication token is not configured")
        return {"replies": [{"type": "text", "text": "Akses chat umum belum aktif dengan aman. Admin perlu mengatur ACTION_TOKEN yang sama pada transport dan agent."}]}
    # Job terjadwal: tenant harus owner terdaftar (job dibuat owner); allowlist
    # dilewati karena pengirimnya bukan orang, melainkan penjadwal internal.
    is_owner_sender = is_allowed(identities, CONFIG.get("owner", []), lid_dir=lid_dir)
    if m.scheduled:
        if not (m.tenant and tenants.is_owner_tenant(m.tenant, CONFIG)):
            audit("message.rejected", reason="job terjadwal tanpa tenant owner")
            return {"replies": []}
    elif not is_owner_sender and not (
        m.public_user and not m.is_group
        and bool((CONFIG.get("publicChat") or {}).get("enabled", False))
    ):
        audit("message.rejected", sender=normalize(m.sender_id),
              identities=[normalize(i) for i in identities if i], reason="bukan owner")
        return {"replies": []}

    is_public_user = bool(m.public_user and not is_owner_sender and not m.is_group
                          and (CONFIG.get("publicChat") or {}).get("enabled", False))
    public_identity = str(m.public_identity or m.sender_id or "").strip()
    if is_public_user and not public_identity:
        return {"replies": [{"type": "text", "text": "Identitas akun tidak terbaca; kirim ulang lewat chat pribadi."}]}

    # 1b) Pilih tenant owner dari identitas pengirim. Setiap owner memakai ruang data terpisah.
    tenant = tenants.normalize(m.tenant or "") if m.tenant else ""
    if not (tenant and tenants.is_owner_tenant(tenant, CONFIG)):
        tenant = tenants.resolve_tenant(identities, CONFIG, lid_dir=lid_dir)
    if is_public_user:
        # Isolasi tenant berdasarkan identitas PN/LID yang stabil; hanya digit
        # hash yang menjadi nama folder sehingga JID asli tidak masuk path.
        digest = hashlib.sha256(public_identity.encode("utf-8")).hexdigest()[:20]
        tenant = str(int(digest, 16))
    if not tenant:
        audit("message.rejected",
              sender=normalize(m.sender_id),
              identities=[normalize(i) for i in identities if i],
              reason="tenant owner tidak dapat dipastikan")
        return {"replies": []}
    role = _roles.ROLE_USER if is_public_user else _roles.role_for_tenant(tenant, CONFIG)
    if is_public_user:
        quota_cfg = CONFIG.get("publicChat") or {}
        limit = max(0, int(quota_cfg.get("tokenLimit", 1000000) or 0))
        window = max(3600, int(quota_cfg.get("windowSeconds", 129600) or 129600))
        used = public_quota.usage(public_identity, window_seconds=window, config=CONFIG)
        if used >= limit:
            audit("public_chat.quota_blocked", user=hashlib.sha256(public_identity.encode()).hexdigest()[:12], used=used)
            return {"replies": [{"type": "text", "text": f"Kuota AI kamu sudah habis ({used:,}/{limit:,} token). Kuota direset bergulir setiap 36 jam."}]}
    # 1b2) IDEMPOTENSI: pesan yang dikirim ulang WhatsApp tidak dieksekusi dua kali.
    if _already_handled(m.chat_id, m.message_id):
        audit("message.duplicate", chat=m.chat_id, message_id=m.message_id, tenant=tenant)
        return {"replies": [], "duplicate": True}
    # 1c) MEKANISME (engine) owner ini + RUANGAN memori-nya. Tiap owner boleh
    #     memakai mesin berbeda (native/Hermes/Claude Code/Codex) dan tiap
    #     mekanisme punya ruangan sendiri — memori tidak bercampur.
    mechanism = tenants.mechanism_for(tenant, CONFIG)
    tpaths = tenants.ensure_room(tenant, mechanism, CONFIG)
    tpaths["mechanism"] = mechanism
    # Siapkan berkas memori ruangan (MEMORY.md/CLAUDE.md/AGENTS.md) — mekanisme
    # memori gaya hermes/claude/codex, terpisah per owner.
    try:
        from memory import roomfiles
        roomfiles.ensure_room_files(tpaths, mechanism)
    except Exception:
        pass

    # MODE BACKGROUND: jalankan turn di thread terpisah supaya transport tidak
    # perlu menunggu (tidak ada lagi plafon ±11 menit yang memotong tugas).
    # Transport mengambil hasilnya lewat GET /job/{id} dan mengirimkannya ke WA
    # saat selesai. Ini yang membuat tugas panjang (video/scraper) SELESAI.
    if str(m.mode or "sync").lower() == "background":
        tasks.clear_cancel(m.chat_id)   # turn BARU → bersihkan penanda berhenti lama
        _ensure_watchdog()              # jaring: tutup job yang macet/tertinggal
        tid = tasks.create_job(m.chat_id, tenant=tenant)
        tasks.prune_jobs()
        th = threading.Thread(
            target=_background_worker,
            args=(tid, m, identities, tenant, tpaths, mechanism, lid_dir, role),
            daemon=True,
        )
        th.start()
        audit("message.accepted", chat=m.chat_id, tenant=tenant,
              job=tid, background=True, role=role)
        return {"accepted": True, "background": True, "job_id": tid, "tenant": tenant}

    return _process(m, identities, tenant, tpaths, mechanism, lid_dir, role=role)


def _background_worker(tid, m, identities, tenant, tpaths, mechanism, lid_dir, role=""):
    """Jalankan satu turn di thread terpisah; simpan hasilnya di job.

    Job SELALU ditutup (done/error) apa pun yang terjadi — termasuk bila thread
    dibatalkan/`BaseException` — supaya transport tidak memantau `/job` tanpa
    henti (gejala "berpikir terus tanpa balasan").
    """
    try:
        out = _process(m, identities, tenant, tpaths, mechanism, lid_dir,
                       job_id=tid, role=role)
        tasks.update_job(tid, status="done", result=out)
    except Exception as exc:  # noqa: BLE001
        audit("agent.background_error", chat=m.chat_id, job=tid, error=str(exc))
        tasks.update_job(tid, status="error", error=str(exc))
    except BaseException as exc:  # noqa: BLE001 - KeyboardInterrupt/SystemExit thread
        audit("agent.background_error", chat=m.chat_id, job=tid, error=f"fatal: {exc}")
        tasks.update_job(tid, status="error", error=str(exc))
    finally:
        # Jaring terakhir: bila karena alasan apa pun job masih "running",
        # tutup sekarang. Tanpa ini, satu jalur yang lolos meninggalkan job
        # menggantung selamanya → transport menunggu tanpa henti.
        try:
            j = tasks.get_job(tid)
            if j and j.get("status") == "running":
                tasks.update_job(
                    tid, status="error",
                    error="tugas berakhir tanpa hasil (worker berhenti)",
                )
                audit("agent.job_orphan", chat=m.chat_id, job=tid)
        except Exception:
            pass


def _ingest_attachments(m, inspect_media: bool = False) -> str:
    """Simpan lampiran pesan ke penyimpanan aset owner; kembalikan catatan konteks.

    Transport mengunduh lampiran ke `data/inbox` (staging). Di sini berkas itu
    dipindah ke penyimpanan media tenant AKTIF (dedup via sha256), lalu disusun
    catatan ringkas untuk model — memuat **id aset** yang bisa dirujuk tool
    `vision`/`media`/`wa`. Ini juga yang membuat lampiran yang DI-REPLY akhirnya
    bisa dibaca agent (dulu hanya teksnya yang diteruskan).
    """
    items = list(getattr(m, "attachments", None) or [])
    if not items:
        return ""
    lines = []
    try:
        from media import store as media_store
    except Exception:
        return ""
    for a in items:
        if not isinstance(a, dict):
            continue
        src = str(a.get("path") or "")
        kind = str(a.get("kind") or "")
        note = f"lampiran {kind}"
        entry = None
        try:
            entry = media_store.save_file(src, kind=kind, mime=str(a.get("mime") or ""),
                                          note=str(a.get("caption") or ""), source="whatsapp")
        except Exception as exc:
            entry = {"error": str(exc)}
        if not entry or entry.get("error"):
            reason = str((entry or {}).get("error") or "penyimpanan aset gagal")[:180]
            lines.append(f"- {kind or 'lampiran'} tidak tersedia untuk dianalisis: {reason}")
            continue
        try:
            import os as _os
            if src and _os.path.exists(src):
                _os.unlink(src)   # staging sudah disalin → bersihkan
        except Exception:
            pass
        aid = entry.get("id")
        fname = entry.get("filename") or ""
        kind_id = entry.get("kind") or kind
        size_kb = round((entry.get("bytes") or 0) / 1024, 1)
        cap = str(a.get("caption") or "").strip()
        desc = f"- `{aid}` ({kind_id}{', ' + fname if fname else ''}, {size_kb} KB)"
        workspace_copy = ""
        try:
            workspace = Path(str((tenant_context.get_current() or {}).get("workspace") or ""))
            stored_path = Path(str(entry.get("path") or ""))
            if workspace and stored_path.is_file():
                media_dir = workspace / "media-replies"
                media_dir.mkdir(parents=True, exist_ok=True)
                target = media_dir / f"{aid}{stored_path.suffix.lower()}"
                shutil.copy2(stored_path, target)
                workspace_copy = str(target)
        except Exception as exc:  # noqa: BLE001
            print(f"[media] salinan workspace gagal untuk {aid}: {exc}", flush=True)
        if workspace_copy:
            desc += f" — file workspace: `{workspace_copy}`"
        if cap:
            desc += f" — keterangan: {cap}"
        if inspect_media and kind_id in ("image", "sticker"):
            desc += "  → user meminta analisis: pakai tool `vision` dengan asset=`" + str(aid) + "`"
        elif inspect_media and kind_id == "video":
            desc += "  → user meminta analisis: pakai tool `video_vision` dengan asset=`" + str(aid) + "`"
        lines.append(desc)
        audit("media.ingest", chat=getattr(m, "chat_id", ""), asset=aid, kind=kind_id,
              bytes=entry.get("bytes"))
    if not lines:
        return ""
    failed = any("tidak tersedia untuk dianalisis" in line for line in lines)
    if inspect_media and failed:
        head = "[Permintaan Vision diterima, tetapi setidaknya satu lampiran gagal disimpan. JANGAN menebak isi media; jelaskan kegagalan dan minta pengguna mengirim ulang.\n"
    else:
        head = ("[Lampiran dari pemilik — sudah tersimpan sebagai aset. "
                + ("inspect_media=true: user meminta analisis visual pada pesan ini; gunakan tool Vision yang sesuai.\n" if inspect_media else "JANGAN menganalisis gambar/video/stiker tanpa permintaan eksplisit; lampiran nonvisual tetap boleh diproses sesuai tugas.\n"))
    return head + "\n".join(lines)


def _user_entry(m) -> str:
    """Teks user yang disimpan ke riwayat, LENGKAP dengan konteks kutipan.

    Kalau pemilik me-REPLY sebuah pesan, AI harus tahu teks pesan yang dibalas
    itu — bukan cuma pesan barunya. Kita sisipkan blok kutipan di depan teks
    supaya model punya konteks penuh (pola yang sama seperti klien chat lain
    menandai "> kutipan").
    """
    text = m.text or ""
    qtext = (getattr(m, "quoted_text", "") or "").strip()
    qkind = (getattr(m, "quoted_kind", "") or "").strip()
    if not qtext and not qkind:
        return text
    label = qkind if qkind and qkind != "text" else "teks"
    quoted = qtext or f"({label} tanpa teks)"
    return (
        f"[Kutipan pesan yang dibalas — {label}]\n"
        f"> {quoted}\n"
        f"[/Kutipan]\n\n{text}"
    )


def _process(m, identities, tenant, tpaths, mechanism, lid_dir, job_id: str = "",
             role: str = ""):
    """Kerjakan satu pesan: siapkan konteks tenant, jalankan turn, simpan hasil.

    Dipisah dari `message()` supaya bisa dijalankan baik sinkron maupun di
    thread background. Selalu membersihkan konteks tenant di akhir.
    """
    # Pasang konteks tenant AKTIF untuk thread ini, sehingga tool (shell/files/
    # cron/wa/subagent) & audit bekerja di ruang MILIK tenant+mekanisme ini.
    tenant_context.set_current(tpaths)
    store = store_manager.store_for(tenant, CONFIG, mechanism=mechanism)

    # LAMPIRAN: pindahkan berkas staging (data/inbox) ke penyimpanan ASET milik
    # owner ini, lalu susun catatan konteks supaya model tahu aset apa yang
    # tersedia — termasuk aset yang DI-REPLY (dulu tak terbaca).
    attachment_note = _ingest_attachments(m, inspect_media=bool(m.inspect_media))

    # 2) Serialisasi per-chat DENGAN BATAS: pesan beruntun tidak saling
    #    tabrakan, tetapi antrean tidak boleh menunggu selamanya di belakang
    #    tugas panjang yang macet (dulu → "berpikir terus tanpa balasan").
    #    Token pemilik unik per-pemanggilan (job background pakai id job-nya).
    owner = job_id or ("sync-" + uuid.uuid4().hex[:12])
    if not _acquire_chat_lock(m.chat_id, owner):
        _clear_activity(m.chat_id)
        return {"replies": [{"type": "text", "text": QUEUE_BUSY_MESSAGE}]}
    try:
        # PERAN: saring tool sesuai peran owner. Model hanya melihat tool
        # yang boleh ia pakai; gerbang KERAS menolak tool di luar daftar.
        if not role:
            role = _roles.role_for_tenant(tenant, CONFIG)
        all_tools = registry.all_tools()
        tools = _roles.filter_tools(role, CONFIG, all_tools)
        # Learn tool ordering from measured success/latency, without changing the
        # role allowlist or removing tools from the agent.
        tools = rank_tools(tools, m.text, store=store)
        tool_allow = sorted(t.get("name") for t in tools)
        _set_activity(m.chat_id, stage="skills", step="memuat skill", tool="", detail="memindai katalog dan memilih skill relevan", event_kind="phase", started=time.time())
        _skill_request_id = f"{tenant}:{m.chat_id}"
        system = build_system_prompt(CONFIG, tools, user_text=m.text,
                                     store=store, chat_id=m.chat_id, tenant=tenant,
                                     request_id=_skill_request_id)
        _internal_names, _matched_names, _match_reasons, _deferred_names = [], [], {}, []
        try:
            from skills import loader as _skill_loader
            _skill_stats = _skill_loader.stats(request_id=_skill_request_id)
            _last_load = _skill_stats.get("last_prompt_load") or {}
            _internal_names = list(_last_load.get("internal") or [])
            _matched_names = list(_last_load.get("external") or [])
            _match_reasons = _last_load.get("external_reasons") or {}
            _deferred_names = list(_last_load.get("deferred_external") or [])
            # UI SEBELUMNYA: menunjukkan gabungan internal+external (selalu sama
            # tiap giliran, karena internal selalu aktif) → terasa seperti
            # pajangan statis. SEKARANG: baris "Skills" menyorot skill EXTERNAL
            # yang benar-benar cocok untuk PESAN INI (beda-beda tiap giliran,
            # dengan alasan trigger yang menyebabkannya terpilih), dan hanya
            # menyebut jumlah internal sebagai konteks latar belakang.
            if _last_load.get("status") == "prepared":
                _skill_info = f"{len(_matched_names)} skill tambahan disiapkan; {len(_deferred_names)} ditunda oleh batas prompt; menunggu konfirmasi engine"
            elif _last_load.get("status") == "failed":
                _skill_info = "pemuatan skill gagal; tidak ada skill yang dihitung dimuat"
            else:
                _skill_info = "status injeksi skill belum tersedia"
            _skill_names = []
        except Exception:
            _skill_names, _skill_info = [], "status skill belum tersedia"
        _set_activity(m.chat_id, stage="think", step="berpikir", tool="", detail="", event_kind="phase", skill_names=_skill_names[:12], skill_count=len(_skill_names), skill_info=_skill_info, started=time.time())

        user_entry = _user_entry(m)
        if attachment_note:
            user_entry = user_entry + "\n\n" + attachment_note
        # AKSI TERTUNDA: bila pesan ini menyetujui aksi yang bot tanyakan di
        # pesan sebelumnya, sisipkan konfirmasi EKSPLISIT ke konteks model.
        # Tanpa ini, alur "bot: mau kuhapus 40 file? → owner: ya" tidak pernah
        # bisa jalan karena konfirmasi hanya dibaca dari pesan yang sama.
        confirmed = is_confirmation(m.text)
        try:
            from agent import consent as _consent
            _pending = _consent.take(m.chat_id, m.text)
            if _pending:
                confirmed = True
                audit("consent.granted", chat=m.chat_id, tool=_pending.get("tool"))
                user_entry += (
                    "\n\n[Persetujuan owner] Owner MENYETUJUI aksi yang kamu tanyakan "
                    f"sebelumnya: tool `{_pending.get('tool')}` dengan argumen "
                    f"{json.dumps(_pending.get('args') or {}, ensure_ascii=False)}. "
                    "Jalankan sekarang.]"
                )
        except Exception:
            pass
        store.append(m.chat_id, "user", user_entry)
        # Manajemen memori: bila riwayat sudah panjang, rangkum yang lama dulu
        # (pola Hermes/Claude Code) supaya konteks tidak meledak.
        protect = int(CONFIG["agent"].get("historyProtectLastN", 20))
        threshold = float(CONFIG["agent"].get("compactionThreshold", 0.5))
        max_msgs = int(CONFIG["agent"].get("maxHistoryMessages", 60))
        if store.count(m.chat_id) > max(protect, int(max_msgs * threshold)):
            info = store.compact(m.chat_id, protect_last_n=protect)
            if info.get("compacted"):
                audit("memory.compact", chat=m.chat_id, summarized=info["summarized"], kept=info["kept"])
        history = build_history(system, store.compacted_history(m.chat_id, limit=max_msgs, protect_last_n=protect))
        if m.public_user:
            # Tokenizer berbeda antar-provider; gunakan batas byte UTF-8 yang
            # konservatif (tidak mengasumsikan rasio 4 karakter/token). Sisakan
            # ruang untuk keluaran agar context window target 128K tidak penuh.
            ctx_cfg = CONFIG.get("publicChat") or {}
            context_cap = max(8192, int(ctx_cfg.get("maxContextTokens", 128000) or 128000))
            output_reserve = max(1024, int((CONFIG.get("llm") or {}).get("maxTokens", 12288) or 12288))
            input_byte_cap = max(4096, context_cap - output_reserve)
            while len(history) > 2 and len(json.dumps(history, ensure_ascii=False).encode("utf-8")) > input_byte_cap:
                # Pertahankan system prompt dan pesan terbaru; buang riwayat lama.
                history.pop(1)
            if len(history) > 1:
                current_bytes = len(json.dumps(history, ensure_ascii=False).encode("utf-8"))
                if current_bytes > input_byte_cap:
                    overflow = current_bytes - input_byte_cap
                    latest = history[-1]
                    content = latest.get("content") if isinstance(latest, dict) else None
                    if isinstance(content, str):
                        latest["content"] = content.encode("utf-8")[:max(0, len(content.encode("utf-8")) - overflow)].decode("utf-8", "ignore")

        _set_activity(m.chat_id, stage="think", step="berpikir", tool="", detail="", event_kind="phase", skill_names=_skill_names[:12], skill_count=len(_skill_names), skill_info=_skill_info, started=time.time())

        # JEJAK LANGKAH: dikirim ke transport untuk dirender sebagai kartu
        # (thinking → tool → shell). Isinya ringkas, bukan isi berkas.
        trace = []

        def on_step(info):
            if len(trace) < 40:
                trace.append(info)
            kind = str(info.get("kind") or "")
            name = str(info.get("name") or "")
            detail = str(info.get("detail") or "")[:160]
            if kind == "tool":
                try:
                    store.record_tool_metric(name, info.get("ok") is not False, info.get("ms") or 0)
                except Exception:
                    pass
            if kind in ("tool_start", "tool"):
                stage = "tool"
                _set_activity(m.chat_id, stage=stage, step="menjalankan tool",
                              tool=name, detail=detail, ok=info.get("ok", True),
                              event_kind=kind, event_id=info.get("event_id", ""),
                              skill_names=_skill_names[:12], skill_count=len(_skill_names),
                              skill_info=_skill_info)
            elif kind == "think":
                _set_activity(m.chat_id, stage="think", step="berpikir", tool="", detail="",
                              event_kind="phase", skill_names=_skill_names[:12],
                              skill_count=len(_skill_names), skill_info=_skill_info)

        def on_tool(name, args, result):
            # Heartbeat: turn masih hidup (job tak boleh disalah-artikan macet).
            if job_id:
                tasks.touch_job(job_id)
            _renew_chat_lock(m.chat_id, owner)
            audit("tool.call", tool=name, args=args,
                  ok=not (isinstance(result, dict) and result.get("error")),
                  error_type=(result.get("error_type") if isinstance(result, dict) else None))
            # Jejak tool_start/tool_done direkam dari on_step. Web sources
            # are captured here as provenance for the active chat only.
            try:
                if name == "web_search" and isinstance(result, dict):
                    for item in (result.get("results") or [])[:15]:
                        if isinstance(item, dict):
                            store.record_source(m.chat_id, item, source_type="web_search")
                elif name == "web_fetch" and isinstance(result, dict):
                    status_code = int(result.get("status") or 0)
                    url = str((args or {}).get("url") or "").strip()
                    if 200 <= status_code < 400 and url:
                        from urllib.parse import urlsplit
                        host = urlsplit(url).hostname or url
                        store.record_source(m.chat_id, {
                            "title": host, "url": url,
                            "snippet": str(result.get("content") or "")[:1800],
                        }, source_type="web_fetch")
            except Exception as exc:
                audit("research_journal.capture_failed", tool=name, error=str(exc)[:180])

        def on_reply(text):
            # Model menulis teks akhir (bukan tool call) — tandai hampir selesai.
            _set_activity(m.chat_id, stage="respond", step="menyusun balasan", tool="", detail="", event_kind="phase", skill_names=_skill_names[:12], skill_count=len(_skill_names), skill_info=_skill_info)

        llm_client = None
        quota_recorded = False

        def account_public_usage():
            nonlocal quota_recorded
            if quota_recorded or not m.public_user or not llm_client:
                return 0
            quota_recorded = True
            usage = getattr(llm_client, "total_usage", {}) or {}
            total = int(usage.get("total_tokens") or 0)
            if total <= 0:
                total = int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
            if total <= 0:
                return 0
            try:
                current = public_quota.record(m.public_identity or m.sender_id, total, config=CONFIG)
                audit("public_chat.usage", user=hashlib.sha256((m.public_identity or m.sender_id).encode()).hexdigest()[:12], tokens=total, total=current)
                return current
            except Exception as exc:  # quota persistence must fail closed for public chat
                audit("public_chat.quota_error", error=str(exc))
                return -1

        try:
            # Konteks percakapan ini diteruskan ke handler tool supaya tool `wa`
            # tahu grup/chat mana yang sedang dibicarakan (untuk aksi grup).
            # Config untuk tool: TANPA rahasia. `llm.apiKey` dan
            # `pterodactyl.apiKey` tidak dibutuhkan handler; satu tool baru yang
            # mengembalikan `config` ke model sudah cukup untuk membocorkannya.
            tool_cfg = redact_config(CONFIG)
            tool_cfg["context"] = {
                "chat_id": m.chat_id,
                "sender_id": m.sender_id,
                "sender_ids": identities,
                "is_group": m.is_group,
                "message_id": m.message_id,
                "sender_name": m.sender_name,
                # Info tenant untuk tool yang perlu tahu "siapa saya".
                "tenant": tenant,
                "memory_db_path": str(getattr(store, "path", "") or ""),
                "tenant_root": str(tpaths.get("root") or ""),
                "is_primary": tenants.is_primary(tenant, CONFIG),
                # PERAN + daftar tool yang boleh: gerbang KERAS di loop._run_tool
                # menolak tool di luar `tool_allow` (cegah prompt injection).
                "role": role,
                "tool_allow": tool_allow,
                # Persetujuan TIDAK diambil dari perkataan model — hanya dari
                # kata-kata pemilik sendiri. Model yang menulis confirm=true
                # tanpa pemilik benar-benar menyetujui tetap ditolak.
                "owner_confirmed": confirmed,
                "owner_request_text": (m.text or "")[:4000],
            }
            # MEKANISME: bila owner ini memakai engine eksternal (Hermes/
            # Claude Code/Codex) DAN perintahnya tersedia, jalankan itu.
            # Jika tidak, pakai loop bawaan (native). Format balasan tetap
            # lewat lapisan yang sama, jadi kartu GenAI* tetap bekerja.
            # GERBANG PERAN: engine eksternal berjalan di LUAR sistem tool kita
            # (punya akses shell penuh) — jadi HANYA untuk primary/owner. Peran
            # `user` SELALU native, supaya tak bisa menembus isolasi tool.
            mech_ok = role in (_roles.ROLE_PRIMARY, _roles.ROLE_OWNER)
            if mech_ok and mechanisms.resolve(mechanism, CONFIG) != "native":
                try:
                    from skills import loader as _skill_loader
                    _skill_loader.mark_prompt_unused("external_engine", request_id=_skill_request_id)
                except Exception:
                    pass
                _skill_names, _skill_info = [], "skill native tidak dipakai oleh engine eksternal"
                _set_activity(m.chat_id, stage="think", step="berpikir", tool="", detail="",
                              event_kind="phase", skill_names=[], skill_count=0, skill_info=_skill_info)
                res = mechanisms.run_external(
                    mechanism, m.text,
                    cwd=str(tpaths.get("workspace") or "") or None,
                    sessions_dir=tpaths.get("sessions"),
                    timeout=int(CONFIG["agent"].get("timeoutSeconds", 600)),
                    config=CONFIG,
                )
                if res.get("ok"):
                    text = res.get("text") or ""
                    from agent.format import to_replies as _to_replies
                    out = {"replies": _to_replies(text), "raw": text}
                    audit("mechanism.run", tenant=tenant, mechanism=mechanism,
                          ok=True, chars=len(text))
                else:
                    audit("mechanism.error", tenant=tenant, mechanism=mechanism,
                          error=res.get("error"))
                    out = {"replies": [{"type": "text",
                                        "text": f"Maaf, mekanisme {mechanism} gagal: {res.get('error')}"}],
                           "raw": ""}
            else:
                # RESUME: bila ini perintah !lanjut DAN ada checkpoint,
                # lanjutkan dari state terakhir (bukan mulai dari nol).
                resume_state = None
                if _is_resume_request(m.text):
                    resume_state = tasks.load_checkpoint(m.chat_id)
                    if resume_state:
                        audit("task.resume", chat=m.chat_id, tenant=tenant,
                              iteration=resume_state.get("iteration"))
                llm_client = _llm(m.model)
                if m.public_user:
                    quota_cfg = CONFIG.get("publicChat") or {}
                    credit_limit = max(0, int(quota_cfg.get("tokenLimit", 1000000) or 0))
                    credit_window = max(3600, int(quota_cfg.get("windowSeconds", 129600) or 129600))

                    def live_main_usage():
                        return int((getattr(llm_client, "total_usage", {}) or {}).get("total_tokens") or 0)

                    def guard_public_request(messages, max_tokens, tools=None):
                        if tool_cfg["context"].get("public_quota_error"):
                            raise LLMError("ledger kredit tidak tersedia; request publik dihentikan",
                                           status=503, code="public_credit_exhausted")
                        used_now = public_quota.usage(
                            m.public_identity or m.sender_id,
                            window_seconds=credit_window, config=CONFIG,
                        )
                        reserve = LLMClient._estimated_input_tokens(messages, tools)
                        reserve += max(0, int(max_tokens or 0))
                        if used_now + live_main_usage() + reserve > credit_limit:
                            raise LLMError(
                                "kuota token tersisa tidak cukup untuk memulai panggilan model dengan aman",
                                model=getattr(llm_client, "last_model", ""),
                                status=402, code="public_credit_exhausted",
                            )

                    llm_client.request_guard = guard_public_request
                    tool_cfg["context"].update({
                        "public_user": True,
                        "public_identity": m.public_identity or m.sender_id,
                        "public_credit_limit": credit_limit,
                        "public_credit_window": credit_window,
                        "main_usage": live_main_usage,
                    })
                def should_stop_turn():
                    if _turn_should_stop(m.chat_id, owner, job_id):
                        return True
                    if m.public_user and llm_client:
                        cfg = CONFIG.get("publicChat") or {}
                        max_credit = max(0, int(cfg.get("tokenLimit", 1000000) or 0))
                        used_credit = public_quota.usage(
                            m.public_identity or m.sender_id,
                            window_seconds=int(cfg.get("windowSeconds", 129600) or 129600),
                            config=CONFIG,
                        )
                        live = int((getattr(llm_client, "total_usage", {}) or {}).get("total_tokens") or 0)
                        return used_credit + live >= max_credit
                    return False

                try:
                    from skills import loader as _skill_loader
                    _prompt_skills_ok = _skill_loader.confirm_prompt_inclusion(
                        history[0].get("content", "") if history else "",
                        request_id=_skill_request_id,
                    )
                    if _prompt_skills_ok:
                        _skill_names = _matched_names[:12] + _internal_names[: max(0, 12 - len(_matched_names))]
                        if _matched_names:
                            _skill_info = f"{len(_matched_names)} skill tambahan masuk prompt · {len(_internal_names)} skill inti masuk prompt · {len(_deferred_names)} ditunda"
                        else:
                            _skill_info = f"0 skill tambahan dipilih · {len(_internal_names)} skill inti masuk prompt · {len(_deferred_names)} ditunda"
                    else:
                        _skill_names, _skill_info = [], "skill tidak terverifikasi di prompt yang dikirim"
                    _set_activity(m.chat_id, stage="think", step="berpikir", tool="", detail="",
                                  event_kind="phase", skill_names=_skill_names[:12],
                                  skill_count=len(_skill_names), skill_info=_skill_info)
                except Exception:
                    _skill_names, _skill_info = [], "status injeksi skill tidak tersedia"
                out = run_turn(
                    llm=llm_client, history=history, tools=tools,
                    max_iterations=CONFIG["agent"].get("maxIterations", 25),
                    timeout_seconds=CONFIG["agent"].get("timeoutSeconds", 600),
                    on_tool=on_tool,
                    on_reply=on_reply,
                    on_step=on_step,
                    # config diteruskan ke handler tool supaya allowlist executable,
                    # denyPaths, dan batas output benar-benar berlaku.
                    tool_config=tool_cfg,
                    # PEMBATALAN: !stop menghentikan kerja di tengah langkah.
                    # Sekaligus HEARTBEAT: callback ini dipanggil tiap iterasi
                    # loop, jadi sewa lock & tanda hidup job selalu segar selama
                    # turn benar-benar berjalan (tak disalah-artikan macet).
                    should_stop=should_stop_turn,
                    # CHECKPOINT: simpan progres tiap langkah → bisa !lanjut.
                    on_checkpoint=lambda st: tasks.save_checkpoint(m.chat_id, st),
                    resume_state=resume_state,
                    # PERSONA LOCK: suara persona aktif dijaga di tiap balasan.
                    persona_lock=bool(CONFIG["agent"].get("personaLock", True)),
                )
                account_public_usage()
        except LLMError as exc:
            account_public_usage()
            audit("llm.error", error=str(exc), status=getattr(exc, "status", 0),
                  model=getattr(exc, "model", ""))
            _clear_activity(m.chat_id)
            if getattr(exc, "code", "") == "public_credit_exhausted":
                _reply_err = "Kuota AI kamu tidak cukup untuk memulai respons berikutnya dengan aman. Ketik /credit untuk melihat sisa dan tunggu kuota bergulir pulih."
            else:
                _reply_err = f"Maaf, otak AI-nya sedang bermasalah: {exc}"
            # Simpan sebagai balasan assistant: tanpa ini riwayat berisi dua
            # pesan `user` berurutan dan model bingung di turn berikutnya.
            try:
                store.append(m.chat_id, "assistant", _reply_err)
            except Exception:
                pass
            return {"replies": [{"type": "text", "text": _reply_err}]}
        except Exception as exc:  # noqa: BLE001 - jangan pernah balas HTTP 500
            account_public_usage()
            # Kesalahan tak terduga (bug format, tool crash, dll) TIDAK BOLEH
            # menjadi HTTP 500: router memperlakukan itu sebagai "inti agent
            # tidak bisa dihubungi" dan pengguna tidak dapat informasi apa pun.
            # Kembalikan balasan teks yang sah supaya bot tidak pernah diam.
            import traceback
            audit("agent.exception", chat=m.chat_id, error=str(exc),
                  where=traceback.format_exc().splitlines()[-1])
            _clear_activity(m.chat_id)
            _reply_err = "Maaf, ada gangguan saat memproses pesanmu. Coba lagi ya."
            try:
                store.append(m.chat_id, "assistant", _reply_err)
            except Exception:
                pass
            return {"replies": [{"type": "text", "text": _reply_err}]}

        # Simpan teks ASLI model (bukan hasil transformasi) ke riwayat. Bila
        # yang disimpan adalah keluaran `strip_markdown`, model akan meniru
        # penanda hasil konversi (mis. ```kick```) dan mengulanginya tiap turn.
        raw_text = (out.get("raw") or "").strip()
        if not raw_text:
            raw_text = "\n".join(
                r.get("text", "") for r in (out.get("replies") or []) if r.get("type") == "text"
            ).strip()
        try:
            from agent import cognitive as _cognitive
            _intent = _cognitive.understand(m.text)["intents"][0]
            _reward, _evidence = _cognitive.rlaif_score(m.text, raw_text)
            store.record_learning_event(
                f"response_quality:{_intent}", "quality:heuristic_v1", _reward,
                source="rlaif", context=_intent, chat_id=m.chat_id,
                evidence=_evidence,
            )
        except Exception as _learning_exc:
            audit("agent_learning.rlaif_unavailable", error=str(_learning_exc)[:180])
        if m.public_user:
            quota_cfg = CONFIG.get("publicChat") or {}
            max_credit = max(0, int(quota_cfg.get("tokenLimit", 1000000) or 0))
            used_credit = public_quota.usage(
                m.public_identity or m.sender_id,
                window_seconds=int(quota_cfg.get("windowSeconds", 129600) or 129600),
                config=CONFIG,
            )
            if used_credit >= max_credit:
                raw_text = (raw_text + "\n\nKuota AI kamu untuk periode 36 jam ini sudah habis; akses pulih otomatis saat pemakaian tertua keluar dari jendela kuota.").strip()
        store.append(m.chat_id, "assistant", raw_text)
        # CHECKPOINT: bila turn benar-benar selesai (bukan dipotong batas /
        # dibatalkan), hapus checkpoint — tak ada yang perlu dilanjutkan.
        if not out.get("checkpoint") and not out.get("cancelled"):
            tasks.clear_checkpoint(m.chat_id)
            tasks.clear_cancel(m.chat_id)
        audit("message.handled", chat=m.chat_id, reply_chars=len(raw_text))
        # Snapshot final harus ikut ke transport: tugas singkat bisa selesai di
        # antara dua polling /status, sehingga jejak tool hilang dari kartu.
        with _ACTIVITY_LOCK:
            activity_snapshot = dict(_ACTIVITY.get(m.chat_id) or {})
            activity_snapshot["events"] = [dict(e) for e in activity_snapshot.get("events", [])]
        _clear_activity(m.chat_id)
        # Jejak langkah ikut dikirim; transport yang memutuskan mau ditampilkan
        # atau tidak (config `ui.showTrace`). Tidak pernah berisi rahasia.
        if isinstance(out, dict):
            out["trace"] = trace
            out["activity"] = activity_snapshot
            try:
                from agent.progres_html import render_html
                langkah_html = []
                tool_rows = {}
                for ev in activity_snapshot.get("events", []):
                    stage = str(ev.get("stage") or "")
                    kind = str(ev.get("kind") or "")
                    if stage in {"skills", "respond"}:
                        continue
                    if kind in {"tool", "tool_done"}:
                        row = tool_rows.get(str(ev.get("event_id") or ""))
                        if row is not None:
                            row["ok"] = ev.get("ok") is not False
                        continue
                    nama = str(ev.get("tool") or ev.get("step") or "langkah")
                    detail = str(ev.get("detail") or "")
                    if nama.lower() in {"bash", "shell", "terminal", "exec", "run_shell"} and detail:
                        nama, detail = "$ " + detail, ""
                    row = {"nama": nama, "detail": detail, "ok": ev.get("ok") is not False}
                    langkah_html.append(row)
                    if kind == "tool_start" and ev.get("event_id"):
                        tool_rows[str(ev["event_id"])] = row
                skill_info = str(activity_snapshot.get("skill_info") or "")
                skill_names = activity_snapshot.get("skill_names") or []
                if skill_names:
                    skill_info = " · ".join(x for x in (skill_info, ", ".join(map(str, skill_names[:12]))) if x)
                out["activity_html"] = render_html({
                    "judul": "Kwee · laporan progres",
                    "persen": 100,
                    "langkah": langkah_html,
                    "selesai": True,
                    "fase": "done",
                    "skill_info": skill_info,
                    "detik": round(time.time() - float(activity_snapshot.get("started") or time.time()), 1),
                })
            except Exception as exc:
                audit("progress.html.failed", error=str(exc))
        return out
    finally:
        _release_chat_lock(m.chat_id, owner)
        # Bersihkan konteks tenant setelah selesai supaya thread yang dipakai
        # ulang oleh FastAPI tidak "membawa" tenant pemanggil sebelumnya.
        tenant_context.clear()
