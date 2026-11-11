"""lidmap.py — pemetaan LID <-> nomor telepon dari store sesi Baileys.

WhatsApp memakai **LID** (identitas berbasis server). Angka di dalam LID BUKAN
nomor telepon, jadi membandingkannya langsung dengan nomor owner tidak akan
pernah cocok.

Baileys menyimpan pemetaan di folder sesi:

    lid-mapping-<pn>.json          -> "<lid>"     (PN  -> LID)
    lid-mapping-<lid>_reverse.json -> "<pn>"      (LID -> PN)

Berkas `_reverse` TIDAK selalu ada. Karena itu modul ini MEMINDAI seluruh folder
dan membangun indeks dua arah, sehingga satu arah saja sudah cukup untuk
menurunkan arah lainnya. Tanpa jaringan, tanpa dependensi.
"""
import json
import time
from pathlib import Path

_INDEX: dict = {}          # lid_dir -> {"at": ts, "pn2lid": {}, "lid2pn": {}}
_TTL_SECONDS = 15.0


def _build_index(lid_dir):
    key = str(lid_dir)
    hit = _INDEX.get(key)
    now = time.time()
    if hit and now - hit["at"] < _TTL_SECONDS:
        return hit

    pn2lid: dict = {}
    lid2pn: dict = {}
    try:
        for p in Path(lid_dir).glob("lid-mapping-*.json"):
            name = p.name[len("lid-mapping-"):-len(".json")]
            try:
                val = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(val, (str, int)) or not str(val):
                continue
            val = str(val)
            if name.endswith("_reverse"):
                lid = name[: -len("_reverse")]
                lid2pn[lid] = val
                pn2lid[val] = lid
            else:
                pn2lid[name] = val
                lid2pn[val] = name
    except Exception:
        pass

    entry = {"at": now, "pn2lid": pn2lid, "lid2pn": lid2pn}
    _INDEX[key] = entry
    return entry


def pn_for_lid(lid: str, lid_dir):
    """LID (angka saja) -> nomor telepon, atau None.

    PENTING: pencarian ini **sadar-tipe**. Hanya memakai peta ``lid2pn``
    (LID→PN). TIDAK ada fallback ke peta sebaliknya, karena angka LID dan
    nomor telepon BERBAGI ruang angka — bisa terjadi tabrakan (LID seseorang
    sama dengan nomor telepon orang lain). Fallback lintas-peta membuat
    identitas tertukar; itu dilarang di sini.
    """
    if not lid_dir or not lid:
        return None
    idx = _build_index(lid_dir)
    return idx["lid2pn"].get(str(lid))


def lid_for_pn(pn: str, lid_dir):
    """Nomor telepon (angka saja) -> LID, atau None.

    Sama seperti ``pn_for_lid``: **sadar-tipe**, hanya peta ``pn2lid``.
    """
    if not lid_dir or not pn:
        return None
    idx = _build_index(lid_dir)
    return idx["pn2lid"].get(str(pn))


def expand(tokens, lid_dir):
    """Tambahkan padanan (PN<->LID) untuk setiap token bila pemetaannya ada.

    Sadar-tipe: token yang berupa LID hanya dipetakan lewat ``lid2pn``, token
    berupa PN hanya lewat ``pn2lid``. Karena ``tokens`` di sini sudah kehilangan
    sufiks JID, fungsi ini hanya dipakai untuk PEPERLUAS (bukan penentu tunggal);
    penentuan identitas yang tegas memakai ``security.allowlist.match_owner``.
    """
    out = set(tokens or [])
    if not lid_dir:
        return out
    for t in list(out):
        if not t:
            continue
        other = pn_for_lid(t, lid_dir) or lid_for_pn(t, lid_dir)
        if other:
            out.add(other)
    return out


def clear_cache():
    _INDEX.clear()
