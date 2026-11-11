"""manager.py — cache Store per RUANGAN OWNER (satu DB per owner).

`Store` membuka koneksi SQLite; membuatnya baru tiap pesan boros. Di sini kita
simpan satu instance per DB (satu owner = satu DB).

- Owner utama → path LAMA (``data/memory/kwee.db``) supaya riwayat lama terbaca.
- Owner lain → ``data/tenants/<nomor>/memory/kwee.db``.
"""
from __future__ import annotations

import threading

from memory import tenants
from memory.store import Store

_STORES: dict = {}
_STORES_LOCK = threading.RLock()


def store_for(tenant: str | None, config: dict | None = None,
              mechanism: str | None = None) -> Store:
    """Store milik RUANGAN owner. Satu owner = SATU DB (mekanisme tidak memecah).

    ``mechanism`` diterima demi kompatibilitas pemanggil, tetapi TIDAK dipakai
    untuk memilih DB — satu owner selalu punya satu DB.
    """
    t = tenants.normalize(tenant or "")
    if t:
        p = tenants.room_paths(t, None, config)
        db = str(p["db"])
    else:
        db = "data/memory/kwee.db"
    with _STORES_LOCK:
        st = _STORES.get(db)
        if st is None:
            st = Store(db)
            _STORES[db] = st
    return st


def reset() -> None:
    """Tutup & lupakan semua store (dipakai tes)."""
    with _STORES_LOCK:
        for st in list(_STORES.values()):
            try:
                st.conn.close()
            except Exception:
                pass
        _STORES.clear()
