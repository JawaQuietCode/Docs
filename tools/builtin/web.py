"""web.py — cari di internet + ambil isi halaman."""
import httpx

from security.audit import audit
from tools.registry import register
from tools.schema import truncate

SCHEMA = {
    "description": "Ambil isi sebuah URL (http/https) dan kembalikan teksnya.",
    "parameters": {
        "type": "object",
        "properties": {"url": {"type": "string", "description": "URL yang diambil"}},
        "required": ["url"],
    },
}


def handler(args, config=None):
    url = str((args or {}).get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        return {"error": "url harus http:// atau https://"}
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True,
                         headers={"User-Agent": "Kwee-agentic/0.1"})
        text = resp.text
    except Exception as exc:  # noqa: BLE001
        return {"error": f"gagal mengambil: {exc}"}
    audit("web.fetch", url=url, status=resp.status_code)
    limit = (config or {}).get("security", {}).get("outputLimitBytes", 10240)
    return {"status": resp.status_code, "content": truncate(text, limit)}


register("web_fetch", SCHEMA, handler, toolset="web")
