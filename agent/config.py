"""config.py — baca config/default.yaml + .env (sisi Python).

Perbaikan dibanding versi lama:
  * `get_config()` / `reload_config()` — config bisa dibaca ulang tanpa restart
    (dipakai `!reload`), dan `CONFIG` dimutasi DI TEMPAT supaya modul yang
    sudah memegang referensinya tetap melihat nilai baru.
  * Validasi ringan + laporan masalah saat load (dulu salah indentasi YAML
    hanya menghasilkan `None` yang gagal senyap di tengah turn).
  * Angka dari env dibaca dengan aman: satu typo `AGENT_PORT=abc` tidak lagi
    membuat proses gagal import dengan traceback mentah.
  * `redact_config()` — salinan config tanpa rahasia, untuk diteruskan ke tool
    (`llm.apiKey` / `pterodactyl.apiKey` tidak perlu ikut ke handler).
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CONFIG: dict = {}


def _parse_owner_mechanisms(raw: str) -> dict:
    """Urai ``OWNER_MECHANISMS`` → {nomor: mekanisme}.

    Format: ``628xxx=claude,628yyy=codex``. Nomor dinormalkan (angka saja).
    """
    import re as _re
    out: dict = {}
    for part in str(raw or "").split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        num, _, mech = part.partition("=")
        digits = _re.sub(r"\D", "", num)
        mech = mech.strip().lower()
        if digits and mech:
            out[digits] = mech
    return out


def _load_env(path: Path) -> None:
    """Pemuat .env sederhana. process.env asli MENANG.

    Mendukung `export KEY=value`, nilai berkutip, dan komentar di akhir baris
    yang tidak berada di dalam kutip.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return
    for line in raw.splitlines():
        t = line.strip()
        if not t or t.startswith("#") or "=" not in t:
            continue
        if t.lower().startswith("export "):
            t = t[7:].lstrip()
        k, _, v = t.partition("=")
        k = k.strip()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        else:
            # buang komentar di luar kutip: FOO=bar  # catatan
            for marker in (" #", "\t#"):
                idx = v.find(marker)
                if idx >= 0:
                    v = v[:idx].rstrip()
        if k and k not in os.environ:
            os.environ[k] = v


def _env_int(key: str, default: int) -> int:
    """int dari env tanpa melempar (typo tidak boleh mematikan bot)."""
    try:
        raw = os.environ.get(key, "")
        if str(raw).strip() == "":
            return int(default)
        return int(str(raw).strip())
    except Exception:
        print(f"[config] {key} bukan angka; memakai {default}", flush=True)
        return int(default)


def _env_bool(key: str, default: bool) -> bool:
    raw = str(os.environ.get(key, "")).strip().lower()
    if not raw:
        return bool(default)
    return raw in ("1", "true", "yes", "y", "on")


def load_config() -> dict:
    _load_env(ROOT / ".env")
    cfg = {}
    try:
        import yaml
        cfg = yaml.safe_load((ROOT / "config" / "default.yaml").read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        print(f"[config] gagal membaca config/default.yaml: {exc}", flush=True)
        cfg = {}

    cfg.setdefault("identity", {})
    cfg.setdefault("whatsapp", {})
    cfg.setdefault("agent", {})
    cfg.setdefault("security", {})
    cfg.setdefault("llm", {})

    # Normalisasi allowlist: YAML bisa membaca `- true`/`- false` sebagai bool
    # (True/False) dan angka sebagai int. Paksa semuanya jadi string supaya
    # pemeriksaan allowlist tidak pernah melempar TypeError (insiden bunuh-diri).
    _al = cfg["security"].get("allowedExecutables")
    if isinstance(_al, list):
        cfg["security"]["allowedExecutables"] = [
            str(x) for x in _al if x is not None
        ]
    elif _al is not None:
        cfg["security"]["allowedExecutables"] = [str(_al)]

    cfg["owner"] = [s.strip() for s in os.environ.get("WA_OWNER_NUMBERS", "").split(",") if s.strip()]
    # Owner UTAMA (super-owner). Default: owner pertama di WA_OWNER_NUMBERS.
    # Boleh dipaksa lewat WA_PRIMARY_OWNER (mis. saat ingin tetap 6283194678090
    # walau urutan daftar berubah).
    cfg["primaryOwner"] = os.environ.get("WA_PRIMARY_OWNER", "").strip()
    # MULTI-TENANT: mekanisme (engine) per owner + mekanisme default.
    # Format OWNER_MECHANISMS: "628xxx=claude,628yyy=codex,628zzz=hermes"
    cfg["defaultMechanism"] = os.environ.get("DEFAULT_MECHANISM", "native").strip() or "native"
    cfg["ownerMechanisms"] = _parse_owner_mechanisms(os.environ.get("OWNER_MECHANISMS", ""))
    cfg["agent"]["host"] = os.environ.get("AGENT_HOST", "127.0.0.1")
    cfg["agent"]["port"] = _env_int("AGENT_PORT", 8787)
    cfg["llm"]["baseUrl"] = os.environ.get("LLM_BASE_URL", "")
    cfg["llm"]["apiKey"] = os.environ.get("LLM_API_KEY", "")
    cfg["llm"]["model"] = os.environ.get("LLM_MODEL", "")
    # Model cadangan (opsional), dipisah koma. Dipakai bila model utama ditolak
    # gateway (mis. `model_param_invalid`).
    cfg["llm"]["fallbackModels"] = [
        s.strip() for s in os.environ.get("LLM_FALLBACK_MODELS", "").split(",") if s.strip()
    ]
    # Model VISION (multimodal) — dipakai tool `vision` untuk membaca gambar.
    # Bila kosong, tool jatuh ke model utama (bila multimodal) lalu ke daftar
    # cadangan. Nilai default diambil dari YAML (`llm.visionModels`) atau env;
    # JANGAN di-hardcode di kode seperti versi lama (default lama adalah model
    # teks, sehingga vision selalu berakhir "balasan kosong").
    _vision = os.environ.get("LLM_VISION_MODELS", "").strip()
    if not _vision:
        _yaml_vision = cfg["llm"].get("visionModels")
        if isinstance(_yaml_vision, str):
            _vision = _yaml_vision
        elif isinstance(_yaml_vision, list):
            _vision = ",".join(str(x) for x in _yaml_vision)
    cfg["llm"]["visionModels"] = [s.strip() for s in _vision.split(",") if s.strip()]
    # Streaming + retry + batas gambar vision (bisa ditimpa env).
    cfg["llm"]["stream"] = _env_bool("LLM_STREAM", bool(cfg["llm"].get("stream", True)))
    cfg["llm"]["retryMax"] = _env_int("LLM_RETRY_MAX", int(cfg["llm"].get("retryMax", 3) or 3))
    try:
        cfg["llm"]["requestIntervalSeconds"] = max(0.0, float(
            os.environ.get("LLM_REQUEST_INTERVAL_SECONDS", cfg["llm"].get("requestIntervalSeconds", 10))
        ))
    except (TypeError, ValueError):
        cfg["llm"]["requestIntervalSeconds"] = 10.0
    cfg["llm"]["visionMaxPx"] = _env_int("LLM_VISION_MAX_PX", int(cfg["llm"].get("visionMaxPx", 1568) or 1568))
    # Token untuk endpoint agent (header X-Agent-Token). Dipakai bersama dengan
    # transport Node (ACTION_TOKEN) supaya /message tidak bisa dipanggil proses
    # lain di localhost. Kosong = autentikasi dimatikan (kompatibilitas).
    cfg["agent"]["authToken"] = (os.environ.get("ACTION_TOKEN", "")
                                 or os.environ.get("AGENT_TOKEN", "")).strip()
    # PTERODACTYL (tool `pterodactyl`): kelola panel lewat Application API.
    # Rahasia (apiKey) HANYA dari .env; TIDAK pernah masuk konteks LLM.
    # Bila kosong, tool mengembalikan pesan jelas (bukan mengarang).
    def _pt_int(env_key, yaml_key, fallback=0):
        raw = os.environ.get(env_key, "")
        if raw == "":
            raw = (cfg.get("pterodactyl") or {}).get(yaml_key)
        try:
            return int(raw)
        except Exception:
            return fallback

    # Optional Mem0 OSS. Do not enable implicitly: it needs an explicitly
    # configured LLM/embedder and a running vector store (Qdrant by default).
    mem0_settings = ((cfg.get("memory") or {}).get("mem0") or {})
    mem0_settings["enabled"] = _env_bool("KWEE_MEM0_ENABLED", bool(mem0_settings.get("enabled", False)))
    oss = mem0_settings.get("oss_config") or mem0_settings.get("ossConfig") or {}
    qdrant = (oss.get("vector_store") or {}).setdefault("config", {})
    qdrant["host"] = os.environ.get("MEM0_QDRANT_HOST", str(qdrant.get("host") or "127.0.0.1"))
    qdrant["port"] = _env_int("MEM0_QDRANT_PORT", int(qdrant.get("port") or 6333))
    if "oss_config" in mem0_settings:
        mem0_settings["oss_config"] = oss
    else:
        mem0_settings["ossConfig"] = oss
    cfg.setdefault("memory", {})["mem0"] = mem0_settings

    cfg["pterodactyl"] = {
        "apiUrl": os.environ.get("PTERO_API_URL", "").strip(),
        "apiKey": os.environ.get("PTERO_API_KEY", "").strip(),
        "defaultNode": _pt_int("PTERO_DEFAULT_NODE", "defaultNode"),
        "defaultNest": _pt_int("PTERO_DEFAULT_NEST", "defaultNest"),
        "defaultEgg": _pt_int("PTERO_DEFAULT_EGG", "defaultEgg"),
        "defaultUser": os.environ.get("PTERO_DEFAULT_USER", "").strip(),
        "defaultDockerImage": os.environ.get("PTERO_DEFAULT_DOCKER_IMAGE", "").strip(),
        "defaultStartup": os.environ.get("PTERO_DEFAULT_STARTUP", "").strip(),
        "defaultMemory": _pt_int("PTERO_DEFAULT_MEMORY", "defaultMemory", 1024),
        "defaultDisk": _pt_int("PTERO_DEFAULT_DISK", "defaultDisk", 3072),
        "defaultCpu": _pt_int("PTERO_DEFAULT_CPU", "defaultCpu", 100),
    }
    cfg["root"] = str(ROOT)
    _report(_validate(cfg))
    return cfg


def _validate(cfg: dict) -> list:
    """Periksa nilai yang sering salah, kembalikan daftar masalah (string)."""
    problems = []
    llm = cfg.get("llm") or {}
    if not llm.get("baseUrl"):
        problems.append("llm.baseUrl kosong — isi LLM_BASE_URL di .env")
    if not llm.get("model"):
        problems.append("llm.model kosong — isi LLM_MODEL di .env")
    if not llm.get("apiKey"):
        problems.append("llm.apiKey kosong — isi LLM_API_KEY di .env")
    sec = cfg.get("security") or {}
    if not sec:
        problems.append("security kosong — tool shell akan MENOLAK semua perintah (fail-closed)")
    elif not sec.get("allowAllExecutables") and not sec.get("allowedExecutables"):
        problems.append("security.allowedExecutables kosong dan allowAllExecutables=false — "
                        "tool shell tidak bisa menjalankan apa pun")
    if not cfg.get("owner"):
        problems.append("WA_OWNER_NUMBERS kosong — tidak ada yang boleh menyuruh bot")
    return problems


def _report(problems: list) -> None:
    for p in problems:
        print(f"[config] PERINGATAN: {p}", flush=True)


def get_config() -> dict:
    """Config aktif (dimuat sekali, bisa di-reload)."""
    if not CONFIG:
        CONFIG.update(load_config())
    return CONFIG


def reload_config() -> dict:
    """Baca ulang .env + YAML dan perbarui CONFIG DI TEMPAT.

    Dimutasi (bukan diganti objeknya) supaya modul yang sudah menyimpan
    referensi `CONFIG` tetap melihat nilai baru — dipakai perintah `!reload`.
    """
    fresh = load_config()
    CONFIG.clear()
    CONFIG.update(fresh)
    return CONFIG


def redact_config(cfg: dict | None = None) -> dict:
    """Salinan config TANPA rahasia, untuk diteruskan ke handler tool.

    Dulu `dict(CONFIG)` utuh (termasuk `llm.apiKey` dan `pterodactyl.apiKey`)
    diberikan ke setiap tool; satu tool baru yang mengembalikan `config` ke
    model sudah cukup untuk membocorkan kunci.
    """
    import copy
    src = copy.deepcopy(cfg if cfg is not None else CONFIG)
    for key in ("apiKey", "api_key", "token", "authToken", "password", "secret"):
        if isinstance(src.get(key), str):
            src[key] = "***"
    for section in ("llm", "pterodactyl", "whatsapp", "agent"):
        sec = src.get(section)
        if isinstance(sec, dict):
            for key in ("apiKey", "api_key", "token", "authToken", "password", "secret"):
                if isinstance(sec.get(key), str) and sec[key]:
                    sec[key] = "***"
    mcp_cfg = src.get("mcp")
    if isinstance(mcp_cfg, dict):
        for server in (mcp_cfg.get("servers") or {}).values():
            if not isinstance(server, dict):
                continue
            env = server.get("env")
            if isinstance(env, dict):
                for key in list(env):
                    if any(mark in str(key).lower() for mark in ("token", "key", "secret", "password")):
                        env[key] = "***"
    return src


CONFIG.update(load_config())
