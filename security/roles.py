"""roles.py — PERAN (role) & kebijakan tool per pemilik.

Kenapa modul ini ada: registry sudah menyimpan `toolset`/`requires_owner`/
`dangerous` per tool, tetapi TIDAK ADA yang membacanya — `agent/main.py` memberi
SEMUA owner SEMUA tool (termasuk `shell`, `plugin_write`, `pterodactyl_delete`).
Akibatnya tidak ada isolasi: satu owner (atau model yang disuntik prompt jahat)
bisa memakai tool paling berbahaya. Modul ini menutup celah itu.

Dua lapis:
  1. PEMILIHAN (soft): sebelum turn, daftar tool DISARING sesuai peran — model
     hanya melihat tool yang boleh ia pakai.
  2. PENEGAKAN (hard): `agent/loop._run_tool` memeriksa ulang `tool_allow` di
     context — tool di luar daftar DITOLAK walau model tetap memanggilnya
     (mis. lewat prompt injection / menulis `confirm=true` sendiri).

Peran:
  - ``primary`` : owner UTAMA (super-owner). Default: SEMUA tool.
  - ``owner``   : owner terdaftar lain. Default: semua KECUALI tool admin.
  - ``user``    : pemakai non-owner yang diizinkan (opt-in). Default: tool aman.
  - ``stranger``: bukan siapa-siapa → TIDAK boleh apa pun.

Aturan `tools`: ``["*"]`` = semua; selain itu daftar nama tool. `deny` selalu
menang (dikurangi dari hasil).
"""
from __future__ import annotations

ROLE_PRIMARY = "primary"
ROLE_OWNER = "owner"
ROLE_USER = "user"
ROLE_STRANGER = "stranger"

# Tool yang menyentuh panel/admin atau merusak — hanya primary secara default.
_ADMIN_TOOLS = ("pterodactyl_delete", "plugin_write")

def admin_tools() -> set:
    """Tool berbahaya menurut REGISTRY (`dangerous=True`).

    Dulu daftar ini di-hardcode ("pterodactyl_delete", "plugin_write") sehingga
    tool berbahaya BARU otomatis lolos dari pembatasan peran. Sekarang registry
    yang menentukan; `_ADMIN_TOOLS` hanya cadangan bila registry belum dimuat.
    """
    try:
        from tools import registry
        found = registry.dangerous_tools()
        if found:
            return set(found)
    except Exception:
        pass
    return set(_ADMIN_TOOLS)


# Peran bawaan. Bisa ditimpa lewat `security.roles` di config/default.yaml.
#   primary : owner UTAMA — SEMUA tool, tanpa pengecualian.
#   owner   : owner terdaftar lain — SEMUA tool juga (kebijakan bot ini: setiap
#             owner tepercaya mendapat shell/curl/browser penuh).
#   user    : pemakai non-owner yang diizinkan (opt-in) — tool aman saja.
#   stranger: bukan siapa-siapa → tidak boleh apa pun.
_DEFAULT_ROLES = {
    ROLE_PRIMARY: {"tools": ["*"], "deny": []},
    ROLE_OWNER: {"tools": ["*"], "deny": []},
    ROLE_USER: {"tools": ["memory", "web_fetch", "web_search", "vision", "media", "skill"], "deny": []},
    ROLE_STRANGER: {"tools": [], "deny": []},
}


def _clean_spec(spec) -> dict:
    """Normalisasi satu spesifikasi peran → {'tools': [...], 'deny': [...]}."""
    if not isinstance(spec, dict):
        return {"tools": ["*"], "deny": []}
    tools = spec.get("tools")
    if tools is None:
        tools = ["*"]
    if isinstance(tools, str):
        tools = [tools]
    deny = spec.get("deny")
    if isinstance(deny, str):
        deny = [deny]
    return {
        "tools": [str(t) for t in (tools or [])],
        "deny": [str(d) for d in (deny or [])],
    }


def normalize_roles(config: dict | None = None) -> dict:
    """Gabungkan peran bawaan dengan `security.roles` dari config."""
    out = {name: {"tools": list(v["tools"]), "deny": list(v["deny"])}
           for name, v in _DEFAULT_ROLES.items()}
    sec = (config or {}).get("security") or {}
    custom = sec.get("roles") or {}
    if isinstance(custom, dict):
        for name, spec in custom.items():
            out[str(name)] = _clean_spec(spec)
    return out


def role_for_tenant(tenant: str, config: dict | None = None) -> str:
    """Peran sebuah tenant: primary / owner / stranger (user ditentukan pemanggil).

    ``user`` tidak bisa disimpulkan dari tenant (non-owner tidak punya tenant
    owner), jadi pemanggil yang memutuskan lewat allowlist pengguna.
    """
    from memory import tenants
    if tenants.is_primary(tenant, config):
        return ROLE_PRIMARY
    if tenants.is_owner_tenant(tenant, config):
        return ROLE_OWNER
    return ROLE_STRANGER


def allowed_tool_names(role: str, config: dict | None, all_names) -> set:
    """Himpunan nama tool yang boleh dipakai sebuah peran.

    ``["*"]`` = semua nama yang ada; selain itu irisan dengan daftar `tools`.
    `deny` selalu dikurangkan (menang).
    """
    spec = normalize_roles(config).get(role) or {"tools": [], "deny": []}
    names = set(all_names or [])
    tools = spec["tools"]
    if "*" in tools:
        base = set(names)
    else:
        base = names & set(tools)
    return base - set(spec["deny"])


def filter_tools(role: str, config: dict | None, tools: list) -> list:
    """Saring daftar tool (dict) sesuai peran — urutan asli dipertahankan."""
    names = {t.get("name") for t in (tools or [])}
    allow = allowed_tool_names(role, config, names)
    return [t for t in (tools or []) if t.get("name") in allow]


def tool_allowed(role: str, config: dict | None, tool_name: str, all_names=None) -> bool:
    """True bila `tool_name` boleh dipakai peran ini (untuk gerbang keras)."""
    if not tool_name:
        return False
    return tool_name in allowed_tool_names(role, config, all_names or {tool_name})
