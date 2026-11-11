"""web_search.py — tool `web_search`: cari di internet, hasil TERSTRUKTUR.

Kenapa tool ini ada: sebelumnya tidak ada tool pencarian sungguhan — hanya
`web_fetch` (ambil 1 URL) dan `browser` (kendali penuh, berat). Tanpa tool
pencarian, model terpaksa mengarang `curl` ke endpoint pencarian secara
manual (format query salah, parsing HTML gagal, atau permintaan ditolak
sumbernya) — lambat dan sering gagal diam-diam di tengah turn.

Tool ini membungkus pencarian DuckDuckGo HTML-lite dan MENGURAI hasilnya
jadi daftar {title, url, snippet} supaya model tidak perlu menulis ulang
parser tiap kali. Untuk platform spesifik (Reddit/X/YouTube/GitHub/dll)
skill `agent-reach` tetap prioritas (hasilnya lebih relevan untuk platform
itu); tool ini adalah jalur UMUM/bawaan yang SELALU tersedia tanpa
tergantung CLI eksternal terpasang atau tidak.
"""
from __future__ import annotations

import html as _html
import re
from urllib.parse import unquote

import httpx

from security.audit import audit
from tools.registry import register

SCHEMA = {
    "description": (
        "Cari di internet (DuckDuckGo) dan kembalikan daftar hasil terstruktur: "
        "judul, url, cuplikan. PAKAI INI untuk pertanyaan tentang orang/peristiwa/"
        "fakta/harga/rilis terbaru yang belum kamu ketahui pasti — jangan menunggu "
        "diminta eksplisit 'cari/search', dan jangan mengarang `curl` manual ke "
        "endpoint pencarian (tool ini sudah menangani parsing hasilnya). Untuk "
        "platform spesifik (Reddit, X/Twitter, YouTube, GitHub, dll) pakai skill "
        "`agent-reach` dulu bila terpasang — hasilnya lebih relevan untuk platform "
        "itu; tool ini jalur umum yang selalu tersedia."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "kata kunci pencarian"},
            "max_results": {
                "type": "integer",
                "description": "jumlah hasil maksimum (bawaan 8, maks 15)",
            },
        },
        "required": ["query"],
    },
}

_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?class="result__snippet"[^>]*>(.*?)</a>',
    re.S,
)
_RESULT_RE_DIV = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?class="result__snippet"[^>]*>(.*?)</div>',
    re.S,
)


def _strip_tags(s: str) -> str:
    return _html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _clean_url(u: str) -> str:
    """DuckDuckGo HTML membungkus hasil lewat redirect `//duckduckgo.com/l/?uddg=…`."""
    m = re.search(r"uddg=([^&]+)", u)
    if m:
        try:
            return unquote(m.group(1))
        except Exception:
            return u
    if u.startswith("//"):
        return "https:" + u
    return u


def _parse(body: str, limit: int) -> list:
    out: list = []
    seen = set()
    for pattern in (_RESULT_RE, _RESULT_RE_DIV):
        for m in pattern.finditer(body):
            url = _clean_url(m.group(1))
            title = _strip_tags(m.group(2))
            snippet = _strip_tags(m.group(3))
            if not url or not title or url in seen:
                continue
            seen.add(url)
            out.append({"title": title, "url": url, "snippet": snippet})
            if len(out) >= limit:
                return out
    return out


def handler(args, config=None):
    args = args or {}
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "query kosong"}
    limit = max(1, min(15, int(args.get("max_results") or 8)))
    try:
        resp = httpx.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            timeout=20,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; Kwee-agentic/0.2; "
                    "+https://github.com/) web_search tool"
                )
            },
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"gagal mencari: {exc}"}
    if resp.status_code != 200:
        return {"error": f"pencarian gagal (HTTP {resp.status_code})"}
    results = _parse(resp.text, limit)
    audit("web.search", query=query, hasil=len(results))
    if not results:
        return {
            "ok": True,
            "query": query,
            "results": [],
            "note": (
                "tidak ada hasil terstruktur untuk query ini — coba kata kunci "
                "lain, atau pakai skill agent-reach untuk platform tertentu"
            ),
        }
    return {"ok": True, "query": query, "results": results}


register("web_search", SCHEMA, handler, toolset="web")
