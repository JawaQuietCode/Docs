"""pterodactyl.py — tool `pterodactyl`: kelola panel lewat Application API.

Kenapa tool ini ada (akar masalah nyata di produksi): pemilik meminta "buatkan
server Pterodactyl", tetapi bot TIDAK punya jalur resmi ke panel. Bot lalu
mencoba menulis plugin (`plugin_write`) — ditahan gerbang konfirmasi, dan plugin
berjalan di sisi Node yang tidak memegang kunci Application API. Hasilnya:
permintaan pemilik selalu gagal.

Dua tool terpisah (PENTING untuk granularitas izin):
  - ``pterodactyl`` (AMAN, dangerous=False): list_servers, get_server,
    list_nodes, list_nests, list_eggs, list_users, list_allocations,
    create_server.
  - ``pterodactyl_delete`` (MERUSAK, dangerous=True): delete_server — wajib
    persetujuan pemilik (gerbang ``owner_confirmed``).

Kenapa dipisah: bila aksi destruktif digabung dengan ``create_server`` dalam satu
tool ber-flag `dangerous=True`, maka kebijakan yang menegakkan flag itu kelak
akan IKUT menahan ``create_server`` — justru mengulang bug yang sedang
diperbaiki. Dipisah = ``create_server`` tetap jalan; aksi merusak membawa
tanda bahaya sendiri.

Keamanan:
  - Kunci (``ptla_...``) dibaca dari env/config, TIDAK pernah dikembalikan ke
    pemanggil dan TIDAK pernah masuk konteks LLM. Audit menyunting nilai ``ptla_``.
  - Tanpa kunci terisi, semua aksi mengembalikan pesan jelas (bukan mengarang).
  - ``server_id`` di Application API adalah **id numerik**. Identifier (mis.
    ``0007cd1c``) DI-RESOLUSI ke id numerik lebih dulu — supaya tak pernah
    mengirim nilai non-numerik ke path (DB MySQL bisa mengubahnya jadi angka
    lain → salah server / salah hapus).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from security.audit import audit
from tools.registry import register

# Batas jumlah baris yang dikembalikan ke LLM (hemat token).
_LIST_LIMIT = 40

SCHEMA = {
    "description": (
        "Kelola panel Pterodactyl lewat Application API resmi. "
        "Aksi AMAN: list_servers, get_server, list_nodes, list_nests, list_eggs, "
        "list_users, list_allocations, create_server. "
        "create_server langsung dikerjakan bila pemilik memintanya. "
        "Untuk MENGHAPUS server, pakai tool pterodactyl_delete (butuh persetujuan)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "aksi (lihat deskripsi)"},
            "name": {"type": "string", "description": "nama server (create_server)"},
            "server_id": {"type": "string",
                          "description": "id numerik server ATAU identifier-nya "
                                         "(mis. 0007cd1c); identifier diresolusi otomatis"},
            "user": {"type": "string",
                     "description": "id angka ATAU email pemilik server (create_server)"},
            "egg_id": {"type": "integer", "description": "id egg (default dari config)"},
            "nest_id": {"type": "integer", "description": "id nest (default dari config)"},
            "node_id": {"type": "integer", "description": "id node (default dari config)"},
            "allocation_id": {"type": "integer",
                              "description": "id alokasi (default: alokasi bebas pertama di node)"},
            "memory": {"type": "integer", "description": "RAM MB (default dari config)"},
            "disk": {"type": "integer", "description": "disk MB (default dari config)"},
            "cpu": {"type": "integer", "description": "batas CPU % (default dari config)"},
            "docker_image": {"type": "string", "description": "image Docker (default dari egg)"},
            "startup": {"type": "string", "description": "perintah startup (default dari egg)"},
        },
        "required": ["action"],
    },
}

DELETE_SCHEMA = {
    "description": (
        "HAPUS server Pterodactyl secara PERMANEN (Application API). "
        "Aksi MERUSAK & tak bisa dibatalkan: hanya dijalankan bila pemilik sudah "
        "menyatakan setuju di pesan terakhirnya. Untuk melihat/membuat server, "
        "pakai tool pterodactyl."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "server_id": {"type": "string",
                          "description": "id numerik server ATAU identifier-nya "
                                         "(mis. 0007cd1c); identifier diresolusi otomatis"},
        },
        "required": ["server_id"],
    },
}


# --- Pengaturan & HTTP -------------------------------------------------------

def _settings(config) -> tuple:
    """Kembalikan (base_url, api_key, dict_pterodactyl)."""
    cfg = (config or {}).get("pterodactyl") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    base = (os.environ.get("PTERO_API_URL") or cfg.get("apiUrl") or "").strip().rstrip("/")
    key = (os.environ.get("PTERO_API_KEY") or cfg.get("apiKey") or "").strip()
    return base, key, cfg


def _api(config, method: str, path: str, body=None) -> tuple:
    """Panggil Application API. Kembalikan (data, error_dict|None).

    Kunci TIDAK pernah dikembalikan atau dicatat — hanya dipakai di header.
    """
    base, key, _ = _settings(config)
    if not base or not key:
        return None, {"error": (
            "panel Pterodactyl belum dikonfigurasi: PTERO_API_URL / PTERO_API_KEY "
            "kosong di .env. Isi keduanya lalu coba lagi."
        )}
    headers = {"Accept": "application/json", "Authorization": "Bearer " + key}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            return (json.loads(raw) if raw.strip() else {}), None
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:600]
        except Exception:
            pass
        pesan = f"panel menolak ({exc.code})"
        # 403/401 di Application API hampir selalu berarti kunci yang dipakai
        # kunci CLIENT (ptlc_) atau kunci Application tanpa izin baca/tulis.
        try:
            _, kunci, _ = _settings(config)
        except Exception:
            kunci = ""
        if exc.code in (401, 403):
            if kunci.startswith("ptlc_"):
                pesan += (" — kunci ini kunci CLIENT (ptlc_). Tool ini memakai "
                          "Application API; buat kunci Application (ptla_) di "
                          "Admin → Application API dengan izin read & write.")
            else:
                pesan += (" — periksa izin kunci Application (ptla_): butuh "
                          "read & write untuk nodes, servers, users, nests.")
        return None, {"error": pesan, "detail": detail}
    except Exception as exc:  # noqa: BLE001
        return None, {"error": f"gagal menghubungi panel: {exc}"}


def _defaults(config) -> dict:
    """Default pembuatan server: env MENANG, lalu config `pterodactyl`.

    Dibaca dari env langsung supaya tool tetap benar walau dipanggil tanpa
    `config` lengkap (mis. di test atau jalur lain).
    """
    _, _, cfg = _settings(config)

    def _pick(env_key, cfg_key, fallback=""):
        raw = os.environ.get(env_key, "")
        if raw == "":
            raw = cfg.get(cfg_key, "")
        return raw if raw != "" else fallback

    def _int(env_key, cfg_key, fallback=0):
        try:
            return int(_pick(env_key, cfg_key, fallback) or fallback)
        except Exception:
            return fallback

    return {
        "node": _int("PTERO_DEFAULT_NODE", "defaultNode"),
        "nest": _int("PTERO_DEFAULT_NEST", "defaultNest"),
        "egg": _int("PTERO_DEFAULT_EGG", "defaultEgg"),
        "user": str(_pick("PTERO_DEFAULT_USER", "defaultUser")).strip(),
        "image": str(_pick("PTERO_DEFAULT_DOCKER_IMAGE", "defaultDockerImage")).strip(),
        "startup": str(_pick("PTERO_DEFAULT_STARTUP", "defaultStartup")).strip(),
        "memory": _int("PTERO_DEFAULT_MEMORY", "defaultMemory", 1024) or 1024,
        "disk": _int("PTERO_DEFAULT_DISK", "defaultDisk", 3072) or 3072,
        "cpu": _int("PTERO_DEFAULT_CPU", "defaultCpu", 100) or 100,
    }


def _as_int(value, label, fallback=None):
    """Ubah nilai jadi int dengan pesan jelas (bukan ValueError).

    Kembalikan (int|None, error|None). Nilai kosong → fallback.
    """
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return fallback, None
    try:
        return int(value), None
    except (TypeError, ValueError):
        return None, {"error": f"argumen '{label}' harus angka (diberikan: {value!r})"}


# --- Aksi baca ---------------------------------------------------------------

def _brief_server(a: dict) -> dict:
    return {
        "id": a.get("id"),
        "identifier": a.get("identifier"),
        "name": a.get("name"),
        "status": a.get("status"),
        "node": a.get("node"),
        "suspended": a.get("suspended"),
    }


def _list_servers(config) -> dict:
    data, err = _api(config, "GET", "/api/application/servers")
    if err:
        return err
    rows = [_brief_server(it.get("attributes", {})) for it in data.get("data", [])]
    return {"ok": True, "count": len(rows), "servers": rows[:_LIST_LIMIT]}


def _is_ascii_int(s: str) -> bool:
    """True hanya untuk digit ASCII murni ('0'-'9').

    ``str.isdigit()`` juga true untuk digit eksotis (``²``, ``①``, ``١``)
    yang justru membuat ``int()`` melempar ValueError → crash. Pembatas ASCII
    ini membuat keputusan numerik-vs-identifier aman.
    """
    return bool(s) and s.isascii() and s.isdigit()


def _looks_like_identifier(s: str) -> bool:
    """True bila token berbentuk identifier Pterodactyl.

    Identifier panel SELALU 8 karakter alfanumerik. Termasuk identifier yang
    kebetulan seluruhnya angka (mis. ``12345678``) atau berawalan nol
    (mis. ``00000007``) — keduanya BERBAHAYA bila dikirim mentah ke path
    (MySQL/MariaDB men-coerce ``00000007`` → ``7``, mengenai server LAIN).
    """
    return len(s) == 8 and s.isascii() and s.isalnum()


def _resolve_server_id(config, sid):
    """Ubah `server_id` (id numerik ATAU identifier) → id numerik.

    Application API mengikat path ke kolom `id` numerik (`/{server:id}`).
    Mengirim identifier mentah ke path berbahaya: DB MySQL/MariaDB mengubah
    '0007cd1c' menjadi 7, sehingga bisa mengenai server LAIN. Karena itu
    identifier DI-RESOLUSI dulu lewat daftar server.

    Id numerik "jelas" (pendek, tanpa awalan nol) dipakai langsung; SEMUA
    token berbentuk identifier (8 karakter alfanumerik) selalu diresolusi —
    termasuk yang seluruhnya angka — supaya tak pernah salah server/hapus.

    Kembalikan (int|None, error|None).
    """
    s = str(sid or "").strip()
    if not s:
        return None, {"error": "server_id wajib"}
    # Fast-path HANYA untuk id numerik yang tak ambigu. Token berbentuk
    # identifier SELALU diresolusi lewat daftar server — jangan pernah dikirim
    # mentah ke path.
    if _is_ascii_int(s) and not _looks_like_identifier(s):
        return int(s), None
    data, err = _api(config, "GET", "/api/application/servers")
    if err:
        return None, err
    for it in data.get("data", []):
        a = it.get("attributes", {})
        ident = str(a.get("identifier", "")).strip()
        if ident and ident.lower() == s.lower():
            return int(a.get("id")), None
        # Cadangan: token 8-digit yang sebenarnya id numerik panel.
        if _is_ascii_int(s) and str(a.get("id")) == s:
            return int(a.get("id")), None
    return None, {"error": (
        f"server '{s}' tidak ditemukan. Masukkan id numerik (mis. 14) atau "
        "identifier persis (mis. 0007cd1c)."
    )}


def _get_server(args, config) -> dict:
    sid, err = _resolve_server_id(config, args.get("server_id"))
    if err:
        return err
    data, err = _api(config, "GET", f"/api/application/servers/{int(sid)}")
    if err:
        return err
    a = (data or {}).get("attributes", {})
    return {"ok": True, "server": _brief_server(a),
            "limits": a.get("limits"), "feature_limits": a.get("feature_limits")}


def _list_generic(config, path: str, keys) -> dict:
    data, err = _api(config, "GET", path)
    if err:
        return err
    rows = []
    for it in data.get("data", []):
        a = it.get("attributes", {})
        rows.append({k: a.get(k) for k in keys})
    return {"ok": True, "count": len(rows), "items": rows[:_LIST_LIMIT]}


def _list_eggs(args, config) -> dict:
    d = _defaults(config)
    nest, err = _as_int(args.get("nest_id"), "nest_id", d["nest"] or None)
    if err:
        return err
    if not nest:
        data, e = _api(config, "GET", "/api/application/nests")
        if e:
            return e
        nests = [it.get("attributes", {}) for it in data.get("data", [])]
        return {"ok": True, "note": "nest_id belum diisi; daftar nest:",
                "nests": [{"id": n.get("id"), "name": n.get("name")} for n in nests]}
    return _list_generic(config, f"/api/application/nests/{int(nest)}/eggs",
                         ("id", "name", "docker_image", "startup"))


def _list_allocations(args, config) -> dict:
    d = _defaults(config)
    node, err = _as_int(args.get("node_id"), "node_id", d["node"] or None)
    if err:
        return err
    if not node:
        return {"error": "node_id wajib (atau isi PTERO_DEFAULT_NODE di .env)"}
    data, e = _api(config, "GET", f"/api/application/nodes/{int(node)}/allocations")
    if e:
        return e
    free = []
    for it in data.get("data", []):
        a = it.get("attributes", {})
        if not a.get("assigned"):
            free.append({"id": a.get("id"), "ip": a.get("ip"), "port": a.get("port")})
    return {"ok": True, "node": int(node), "free_count": len(free), "free": free[:_LIST_LIMIT]}


# --- Bantu create ------------------------------------------------------------

def _resolve_user_id(config, user: str):
    """Kembalikan int user id, atau dict error."""
    u = str(user or "").strip()
    if not u:
        return {"error": "pemilik server belum ditentukan (args.user = id atau email)"}
    # Hanya digit ASCII murni yang aman di-int(); token lain (email, digit
    # eksotis seperti '²') jatuh ke pencarian email agar tak crash.
    if _is_ascii_int(u):
        return int(u)
    q = urllib.parse.urlencode({"filter[email]": u})
    data, err = _api(config, "GET", f"/api/application/users?{q}")
    if err:
        return err
    rows = data.get("data", [])
    if not rows:
        return {"error": f"user '{u}' tidak ditemukan di panel"}
    return rows[0].get("attributes", {}).get("id")


def _egg_details(config, nest: int, egg: int):
    """Kembalikan (docker_image, startup, environment) dari egg, atau dict error."""
    if not nest:
        data, err = _api(config, "GET", "/api/application/nests")
        if err:
            return err
        for it in data.get("data", []):
            nid = it.get("attributes", {}).get("id")
            probe, perr = _api(config, "GET", f"/api/application/nests/{nid}/eggs")
            if perr:
                continue
            if any(e.get("attributes", {}).get("id") == egg for e in probe.get("data", [])):
                nest = nid
                break
    if not nest:
        return {"error": f"nest untuk egg {egg} tidak ditemukan (isi PTERO_DEFAULT_NEST)"}
    q = urllib.parse.urlencode({"include": "variables"})
    data, err = _api(config, "GET", f"/api/application/nests/{nest}/eggs/{egg}?{q}")
    if err:
        return err
    a = (data or {}).get("attributes", {})
    images = a.get("docker_images") or {}
    image = next(iter(images.values()), "") if isinstance(images, dict) else ""
    startup = a.get("startup") or ""
    env = {}
    try:
        for v in a.get("relationships", {}).get("variables", {}).get("data", []):
            va = v.get("attributes", {})
            if va.get("env_variable"):
                env[va["env_variable"]] = va.get("default_value", "")
    except Exception:
        pass
    return image, startup, env


def _first_free_allocation(config, node: int):
    data, err = _api(config, "GET", f"/api/application/nodes/{int(node)}/allocations")
    if err:
        return err
    for it in data.get("data", []):
        a = it.get("attributes", {})
        if not a.get("assigned"):
            return a.get("id")
    return {"error": f"node {node} tidak punya alokasi bebas — tambahkan alokasi dulu"}


# --- Aksi tulis --------------------------------------------------------------

def _create_server(args, config) -> dict:
    d = _defaults(config)
    name = str(args.get("name") or "").strip()
    if not name:
        return {"error": "nama server wajib (args.name)"}

    node, err = _as_int(args.get("node_id"), "node_id", d["node"] or None)
    if err:
        return err
    egg, err = _as_int(args.get("egg_id"), "egg_id", d["egg"] or None)
    if err:
        return err
    if not node:
        return {"error": "node belum ditentukan (args.node_id atau PTERO_DEFAULT_NODE)"}
    if not egg:
        return {"error": "egg belum ditentukan (args.egg_id atau PTERO_DEFAULT_EGG)"}
    node, egg = int(node), int(egg)

    user_id = _resolve_user_id(config, args.get("user") or d["user"])
    if isinstance(user_id, dict):
        return user_id

    nest, err = _as_int(args.get("nest_id"), "nest_id", d["nest"] or 0)
    if err:
        return err
    nest = int(nest or 0)

    details = _egg_details(config, nest, egg)
    if isinstance(details, dict):
        return details
    egg_image, egg_startup, egg_env = details

    image = str(args.get("docker_image") or d["image"] or egg_image or "").strip()
    startup = str(args.get("startup") or d["startup"] or egg_startup or "").strip()
    if not image or not startup:
        return {"error": "image/startup server tidak bisa ditentukan dari egg; "
                         "isi docker_image & startup"}

    mem, err = _as_int(args.get("memory"), "memory", d["memory"])
    if err:
        return err
    disk, err = _as_int(args.get("disk"), "disk", d["disk"])
    if err:
        return err
    cpu, err = _as_int(args.get("cpu"), "cpu", d["cpu"])
    if err:
        return err

    allocation, err = _as_int(args.get("allocation_id"), "allocation_id", None)
    if err:
        return err
    if not allocation:
        allocation = _first_free_allocation(config, node)
    if isinstance(allocation, dict):
        return allocation

    payload = {
        "name": name,
        "user": user_id,
        "egg": egg,
        "docker_image": image,
        "startup": startup,
        "environment": egg_env or {},
        "limits": {
            "memory": int(mem),
            "swap": 0,
            "disk": int(disk),
            "io": 500,
            "cpu": int(cpu),
        },
        "feature_limits": {"databases": 5, "allocations": 5, "backups": 3},
        "allocation": {"default": int(allocation)},
        "start_on_completion": True,
    }

    data, err = _api(config, "POST", "/api/application/servers", payload)
    if err:
        return err
    a = (data or {}).get("attributes", {})
    audit("pterodactyl.create", name=name, node=node, egg=egg, server_id=a.get("id"))
    return {"ok": True, "created": _brief_server(a),
            "message": f"server '{name}' dibuat (id={a.get('id')}, identifier={a.get('identifier')})"}


def _delete_server(args, config) -> dict:
    # Gerbang konfirmasi DIPERIKSA DULU — sebelum resolusi/nama pun, supaya
    # aksi yang ditahan benar-benar tidak menyentuh jaringan sama sekali.
    ctx = (config or {}).get("context") or {}
    if not ctx.get("owner_confirmed"):
        return {"error": (
            "hapus server ditahan: pemilik belum menyatakan setuju di pesan terakhirnya. "
            "Tanya dulu ('Setuju saya hapus server X?'), tunggu jawaban ya, baru jalankan."
        )}
    sid, err = _resolve_server_id(config, args.get("server_id"))
    if err:
        return err
    data, err = _api(config, "DELETE", f"/api/application/servers/{int(sid)}")
    if err:
        return err
    audit("pterodactyl.delete", server_id=sid)
    return {"ok": True, "deleted": sid}


# --- Handler -----------------------------------------------------------------

def handler(args, config=None):
    args = args or {}
    action = str(args.get("action") or "").strip().lower()
    if action in ("list_servers", "servers", "list"):
        return _list_servers(config)
    if action == "get_server":
        return _get_server(args, config)
    if action == "list_nodes":
        return _list_generic(config, "/api/application/nodes", ("id", "name", "fqdn", "maintenance_mode"))
    if action == "list_nests":
        return _list_generic(config, "/api/application/nests", ("id", "name", "description"))
    if action == "list_eggs":
        return _list_eggs(args, config)
    if action == "list_users":
        return _list_generic(config, "/api/application/users", ("id", "username", "email", "root_admin"))
    if action == "list_allocations":
        return _list_allocations(args, config)
    if action == "create_server":
        return _create_server(args, config)
    if action == "delete_server":
        return {"error": (
            "menghapus server memakai tool terpisah: pterodactyl_delete "
            "(aksi merusak, butuh persetujuan pemilik)."
        )}
    return {"error": f"aksi tidak dikenal: '{action}'. Boleh: list_servers, get_server, "
                     "list_nodes, list_nests, list_eggs, list_users, list_allocations, "
                     "create_server"}


def delete_handler(args, config=None):
    return _delete_server(args or {}, config)


register("pterodactyl", SCHEMA, handler, toolset="core", dangerous=False)
register("pterodactyl_delete", DELETE_SCHEMA, delete_handler, toolset="core", dangerous=True)
