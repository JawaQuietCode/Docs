"""vision.py — tool `vision`: baca/analisis GAMBAR (multimodal).

Kegunaan: agent bisa "melihat" — membaca teks di screenshot, mengenali objek,
menjawab pertanyaan tentang gambar yang dikirim pemilik (termasuk gambar yang
DI-REPLY), atau memeriksa hasil screenshot browser.

Sumber gambar yang didukung:
  * `asset`/`path` — id aset media (`m_xxxx`) atau path berkas lokal,
  * `url`          — URL http(s) langsung,
  * `data_url`     — data-URL (jarang dipakai langsung).

Gambar lokal diubah menjadi data-URL base64 → TIDAK perlu diunggah ke mana pun.
"""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from tools.registry import register

SCHEMA = {
    "description": (
        "Lihat & analisis GAMBAR (multimodal). Pakai untuk membaca teks di "
        "screenshot, mengenali objek, atau menjawab pertanyaan tentang gambar. "
        "Sumber: `asset` (id aset `m_xxxx`), `path` (berkas lokal), `url` (http), "
        "atau `data_url`. Bila gambar yang DI-REPLY oleh pemilik, ambil id/path-nya "
        "dari daftar lampiran di konteks lalu kirim lewat `asset`."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "asset": {"type": "string", "description": "id aset media (m_xxxx) atau path berkas"},
            "path": {"type": "string", "description": "path berkas gambar (alternatif `asset`)"},
            "url": {"type": "string", "description": "URL gambar http(s)"},
            "data_url": {"type": "string", "description": "data-URL gambar (data:image/...;base64,...)"},
            "prompt": {"type": "string", "description": "Pertanyaan/instruksi tentang gambar"},
            "detail": {"type": "string", "description": "'auto' (default), 'low', atau 'high'"},
        },
        "required": [],
    },
}

_MAX_BYTES = 12 * 1024 * 1024  # batas aman ukuran gambar (12 MB)


def _to_data_url(path: str) -> str:
    p = Path(path)
    raw = p.read_bytes()
    if len(raw) > _MAX_BYTES:
        raise ValueError(f"gambar terlalu besar ({len(raw)} bytes)")
    mime = mimetypes.guess_type(str(p))[0] or "image/png"
    if not mime.startswith("image/"):
        mime = "image/png"
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def _resolve_source(args: dict) -> str:
    """Kembalikan URL/data-URL gambar dari argumen tool."""
    if args.get("data_url"):
        return str(args["data_url"])
    if args.get("url"):
        u = str(args["url"])
        if not u.startswith(("http://", "https://", "data:")):
            return ""
        return u
    ref = str(args.get("asset") or args.get("path") or "").strip()
    if not ref:
        return ""
    if ref.startswith(("http://", "https://", "data:")):
        return ref
    # Cari lewat penyimpanan media (id atau nama berkas), lalu path langsung.
    try:
        from media import store as media_store
        resolved = media_store.resolve_path(ref)
        if resolved:
            return _to_data_url(resolved)
    except Exception:
        pass
    p = Path(ref)
    if p.exists() and p.is_file():
        return _to_data_url(str(p))
    return ""


def handler(args, config=None):
    args = args or {}
    src = _resolve_source(args)
    if not src:
        return {"error": "tidak ada gambar yang bisa dibaca (isi `asset`, `path`, `url`, atau `data_url`)"}
    prompt = str(args.get("prompt") or "Jelaskan isi gambar ini dengan detail.").strip()
    detail = str(args.get("detail") or "auto").lower()
    if detail not in ("auto", "low", "high"):
        detail = "auto"

    # Impor di dalam fungsi supaya tool ini tetap ringan saat dimuat.
    from agent.config import CONFIG
    from agent.llm.client import LLMClient, LLMError
    from security import public_quota

    llm_cfg = CONFIG.get("llm", {})
    models = list(llm_cfg.get("visionModels") or [])
    client = LLMClient(
        base_url=llm_cfg.get("baseUrl", ""),
        api_key=llm_cfg.get("apiKey", ""),
        model=llm_cfg.get("model", ""),
        timeout=int(CONFIG.get("llm", {}).get("timeoutSeconds", 120)),
        fallback_models=llm_cfg.get("fallbackModels") or [],
    )
    context = (config or {}).get("context") or {}
    public_identity = str(context.get("public_identity") or "")
    public_user = bool(context.get("public_user") and public_identity)
    if public_user:
        limit = max(0, int(context.get("public_credit_limit", 1000000) or 0))
        window = max(3600, int(context.get("public_credit_window", 129600) or 129600))
        main_usage = context.get("main_usage")

        def guard_vision(messages, max_tokens, tools=None):
            used = public_quota.usage(public_identity, window_seconds=window, config=CONFIG)
            live_main = int(main_usage() or 0) if callable(main_usage) else 0
            live_vision = int((client.total_usage or {}).get("total_tokens") or 0)
            reserve = LLMClient._estimated_input_tokens(messages, tools)
            reserve += max(0, int(max_tokens or 0))
            if used + live_main + live_vision + reserve > limit:
                raise LLMError("kuota token tersisa tidak cukup untuk analisis gambar",
                               status=402, code="public_credit_exhausted")

        client.request_guard = guard_vision
    try:
        out = client.vision(src, prompt=prompt, models=models,
                            timeout=int(CONFIG.get("llm", {}).get("timeoutSeconds", 120)))
    except LLMError as exc:
        return {"error": f"gagal menganalisis gambar: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"gagal menganalisis gambar: {exc}"}
    finally:
        if public_user:
            spent = int((client.total_usage or {}).get("total_tokens") or 0)
            if spent > 0:
                try:
                    public_quota.record(public_identity, spent, config=CONFIG)
                except Exception:
                    context["public_quota_error"] = True
                    raise
    return {"ok": True, "text": out.get("text", ""), "model": out.get("model", "")}


register("vision", SCHEMA, handler, toolset="core", dangerous=False)
