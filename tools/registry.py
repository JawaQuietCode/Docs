"""registry.py — daftar tool + auto-discovery.

Pola Hermes: setiap modul tool memanggil register() di level modul; registry
memindai folder builtin/ dan mengimpor modul yang punya fungsi register.
Handler WAJIB mengembalikan dict/objek (bukan raise).

Metadata per tool — dan SEKARANG benar-benar dibaca (dulu disimpan lalu mati):

  toolset        kelompok tool ("core", "web", …) untuk penyaringan
  requires_owner hanya owner yang boleh (dipakai security/roles.py)
  dangerous      aksi merusak/berkuasa → ditolak untuk peran non-primary
  concurrent     boleh dijalankan paralel dalam satu turn (default True;
                 shell/wa/cron/plugin_run diset False karena berebut
                 state/urutan efek samping)
"""
import importlib
import os
import pkgutil
import sys
from pathlib import Path

_REGISTRY: dict = {}


def register(name: str, schema: dict, handler, toolset: str = "core",
             requires_owner: bool = True, dangerous: bool = False,
             concurrent: bool = True) -> None:
    """Daftarkan satu tool — dengan validasi fail-fast.

    Skema yang cacat dulu baru ketahuan saat gateway LLM menolak request di
    tengah turn (muncul sebagai "model bermasalah" yang membingungkan). Lebih
    baik meledak saat import, ketika `setup.sh`/`start.sh` masih bisa
    melaporkannya dengan jelas.
    """
    name = str(name or "").strip()
    if not name:
        raise ValueError("register(): nama tool kosong")
    if not callable(handler):
        raise TypeError(f"register({name}): handler bukan callable")
    schema = schema or {}
    if not str(schema.get("description") or "").strip():
        raise ValueError(f"register({name}): 'description' wajib diisi")
    params = schema.get("parameters") or {}
    if not isinstance(params, dict) or params.get("type") != "object":
        raise ValueError(f"register({name}): parameters harus {'type': 'object', …}")
    if "properties" not in params:
        raise ValueError(f"register({name}): parameters wajib punya 'properties'")
    required = params.get("required") or []
    if required and not isinstance(required, list):
        raise ValueError(f"register({name}): parameters.required harus list")
    missing = [r for r in required if r not in params["properties"]]
    if missing:
        raise ValueError(f"register({name}): required {missing} tidak ada di properties")

    prev = _REGISTRY.get(name)
    if prev is not None and prev.get("handler") is not handler:
        # Bukan fatal (bisa karena modul di-reload), tapi harus terlihat di log.
        print(f"[tools] PERINGATAN: tool '{name}' didaftarkan ulang (handler diganti)",
              flush=True)

    _REGISTRY[name] = {
        "name": name,
        "schema": {"name": name, **schema},
        "handler": handler,
        "toolset": toolset,
        "requires_owner": requires_owner,
        "dangerous": dangerous,
        "concurrent": concurrent,
    }


def get(name: str):
    return _REGISTRY.get(name)


def all_tools() -> list:
    return list(_REGISTRY.values())


def dangerous_tools() -> set:
    """Nama tool yang ditandai `dangerous=True` (dipakai security/roles.py).

    Dulu `security/roles.py` meng-hardcode ("pterodactyl_delete", "plugin_write")
    sehingga tool berbahaya BARU otomatis lolos dari pembatasan peran.
    """
    return {t["name"] for t in _REGISTRY.values() if t.get("dangerous")}


def toolsets() -> dict:
    """Peta toolset → daftar nama tool (untuk penyaringan per mekanisme)."""
    out: dict = {}
    for t in _REGISTRY.values():
        out.setdefault(t.get("toolset") or "core", []).append(t["name"])
    return {k: sorted(v) for k, v in out.items()}


def discover() -> dict:
    """Impor semua modul di tools/builtin/ yang punya register().

    Reload modul hanya bila `KWEE_RELOAD_TOOLS=1` (mis. saat mengembangkan tool
    secara live). Default: cukup import. Reload berisik, mengganti objek handler
    yang mungkin sedang dipegang turn berjalan, dan tidak ikut me-reload modul
    lain (security.policy dll) sehingga bisa menghasilkan campuran versi.
    """
    reload = os.environ.get("KWEE_RELOAD_TOOLS", "") not in ("", "0", "false", "False")
    pkg_dir = Path(__file__).resolve().parent / "builtin"
    for mod in pkgutil.iter_modules([str(pkg_dir)]):
        if mod.name.startswith("_"):
            continue
        fq = f"tools.builtin.{mod.name}"
        try:
            if fq in sys.modules:
                if reload:
                    importlib.reload(sys.modules[fq])
            else:
                importlib.import_module(fq)
        except Exception as exc:  # noqa: BLE001 - tool yang gagal dimuat tidak mematikan bot
            print(f"[tools] gagal memuat {mod.name}: {exc}", flush=True)
    return _REGISTRY


def clear() -> None:
    _REGISTRY.clear()
