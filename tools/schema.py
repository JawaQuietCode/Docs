"""schema.py — util untuk tool: potong output, validasi/normalisasi argumen."""
from typing import Any

# Batas default (byte) bila config tidak menyediakan `security.outputLimitBytes`.
DEFAULT_OUTPUT_LIMIT = 10240


def truncate(text: Any, limit: int = DEFAULT_OUTPUT_LIMIT) -> str:
    """Potong output panjang supaya konteks tidak meledak.

    Memotong berdasarkan BYTE (UTF-8), bukan karakter: nama setelannya
    ``outputLimitBytes`` dan teks Indonesia bisa 2–3 byte/karakter, sehingga
    pemotongan per-karakter bisa melampaui batas 3x. Pemotongan dilakukan di
    batas karakter yang aman (decode ``ignore``) supaya tidak ada
    ``UnicodeDecodeError``.
    """
    s = text if isinstance(text, str) else str(text)
    limit = max(256, int(limit or DEFAULT_OUTPUT_LIMIT))
    raw = s.encode("utf-8", "replace")
    if len(raw) <= limit:
        return s
    head = raw[: int(limit * 0.7)].decode("utf-8", "ignore")
    tail = raw[-int(limit * 0.2):].decode("utf-8", "ignore")
    cut = len(raw) - len(head.encode("utf-8")) - len(tail.encode("utf-8"))
    return f"{head}\n... [dipotong {cut} byte] ...\n{tail}"


def coerce_args(args: Any) -> dict:
    """Pastikan argumen tool selalu dict.

    Model kadang mengirim JSON `null`, list, atau string mentah. Dulu fungsi ini
    tidak pernah dipakai sehingga handler menerima bentuk apa pun dan melempar
    AttributeError di tengah turn.
    """
    if isinstance(args, dict):
        return args
    if isinstance(args, str) and args.strip():
        import json
        try:
            parsed = json.loads(args)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def coerce_int(value: Any, default: int, minimum: int | None = None,
               maximum: int | None = None) -> int:
    """int() yang tidak pernah melempar (model bisa mengirim 'abc' atau None)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    if minimum is not None:
        n = max(minimum, n)
    if maximum is not None:
        n = min(maximum, n)
    return n


def coerce_bool(value: Any, default: bool = False) -> bool:
    """bool() yang paham string 'true'/'1'/'ya' dari model."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "ya", "on")
    return default


def output_limit(config: Any, default: int = DEFAULT_OUTPUT_LIMIT) -> int:
    """Ambil `security.outputLimitBytes` dari config tool (fallback aman)."""
    try:
        sec = (config or {}).get("security", {}) if isinstance(config, dict) else {}
        return int(sec.get("outputLimitBytes") or default)
    except Exception:
        return default
