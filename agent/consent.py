"""consent.py — deteksi persetujuan pemilik dari pesannya sendiri.

Kenapa modul ini ada: untuk aksi merusak (kick/promote/ubah grup, hapus panel,
tulis plugin), kita TIDAK boleh mempercayai model AI. Model bisa saja menulis
``confirm=true`` sendiri tanpa pemilik benar-benar menyetujui. Jadi gerbang
konfirmasi harus membaca **kata-kata pemilik**, bukan argumen tool.

Aturan:
  - Hanya kata AFIRMATIF yang jelas yang dianggap setuju ("ya", "setuju",
    "lanjut", "gas", "ok", "boleh", "silakan", "acc", …).
  - Kata penyangkalan ("jangan", "batal", "tidak", "belum", "tunggu", …)
    langsung membatalkan, apa pun kata afirmatif lain di pesan itu.
  - Kalimat TANYA bukan persetujuan. Pertanyaan biasanya memuat "?" atau
    kata tanya (apa, siapa, kenapa, bagaimana, berapa, kapan, mana).
  - Kata kerja aksi ("kick", "hapus", "ubah", …) BUKAN persetujuan — itu
    deskripsi perintah, bukan izin.

TAMBAHAN: **aksi tertunda** (pending action).

Dulu `is_confirmation()` hanya melihat pesan SAAT ITU, sehingga alur alami
"bot: mau kuhapus 40 file? → owner: ya" tidak pernah bisa jalan: model harus
memanggil tool dengan `confirm` di pesan yang sama. Sekarang bot bisa mencatat
aksi tertunda (`ask`) dan memverifikasinya di pesan berikutnya (`take`), dengan
batas waktu + nonce sehingga "ya" dari percakapan lain tidak bisa dipakai.
"""
import re
import secrets
import threading
import time

# Kata afirmatif yang benar-benar berarti "ya, lakukan".
_AFFIRM = re.compile(
    r"\b(ya|yap|yes|yoi|iya|setuju|sip|oke|ok|okay|okey|boleh|lanjut|lanjutkan|"
    r"gas|gaskan|kerjakan|lakukan|jalankan|silakan|silahkan|silah|"
    r"betul|benar|konfirmasi|approve|acc|proceed|go ahead|do it|ayo|ayok)\b",
    re.I,
)
# Kata penyangkalan — membatalkan persetujuan.
_NEGATE = re.compile(
    r"\b(jangan|batal|cancel|tidak|tdk|gak|nggak|engga|enggak|belum|tunggu|stop|"
    r"no|nope|batalkan|urungkan|jgn|hold|dont|don't|wait)\b",
    re.I,
)
# Penanda kalimat tanya.
_QUESTION = re.compile(
    r"\?|\b(apa|apakah|siapa|siapakah|kenapa|mengapa|bagaimana|gimana|berapa|"
    r"kapan|dimana|di mana|kemana|mana)\b",
    re.I,
)


def is_confirmation(text: str) -> bool:
    """True bila teks ini adalah persetujuan eksplisit dari pemilik."""
    s = str(text or "").strip()
    if not s:
        return False
    if _NEGATE.search(s):
        return False
    if _QUESTION.search(s):
        return False
    return bool(_AFFIRM.search(s))


# --------------------------------------------------------------------------- #
# Aksi tertunda (pending action)
# --------------------------------------------------------------------------- #

_PENDING: dict = {}
_LOCK = threading.Lock()
DEFAULT_TTL = 300.0          # detik


def ask(chat_id: str, tool: str, args: dict, *, reason: str = "",
        ttl: float = DEFAULT_TTL) -> str:
    """Catat aksi yang menunggu persetujuan. Kembalikan nonce.

    Pemanggil (tool) lalu mengirim pertanyaan ke owner, mis.
    "Aku akan menghapus 40 file. Balas *ya* untuk lanjut (kode: ab12cd)."
    """
    nonce = secrets.token_hex(3)
    with _LOCK:
        _PENDING[str(chat_id)] = {
            "tool": str(tool),
            "args": dict(args or {}),
            "reason": str(reason or ""),
            "nonce": nonce,
            "created": time.time(),
            "expires": time.time() + float(ttl or DEFAULT_TTL),
        }
    return nonce


def peek(chat_id: str) -> dict | None:
    """Lihat aksi tertunda yang masih berlaku (tanpa menghapus)."""
    with _LOCK:
        item = _PENDING.get(str(chat_id))
        if not item:
            return None
        if time.time() > float(item.get("expires") or 0):
            _PENDING.pop(str(chat_id), None)
            return None
        return dict(item)


def take(chat_id: str, text: str) -> dict | None:
    """Ambil aksi tertunda BILA pesan ini adalah persetujuan. Sekali pakai.

    Nonce opsional: bila owner menulis kode nonce di pesannya, kode itu harus
    cocok. Tanpa nonce, cukup kata afirmatif — tetapi aksi hanya berlaku untuk
    chat yang sama dan dalam batas waktu, jadi "ya" dari percakapan lain tidak
    bisa dipakai.
    """
    item = peek(chat_id)
    if not item:
        return None
    s = str(text or "")
    if not is_confirmation(s):
        # Kata penyangkalan eksplisit → batalkan aksi tertunda.
        if _NEGATE.search(s):
            clear(chat_id)
        return None
    nonce = str(item.get("nonce") or "")
    if nonce and re.search(r"\b" + re.escape(nonce) + r"\b", s, re.I) is None:
        # Pemilik tidak menyebut nonce: tetap boleh, asalkan tidak ada nonce
        # LAIN di pesan (mencegah "ya, kode lain").
        others = re.findall(r"\b[0-9a-f]{6}\b", s, re.I)
        if others and nonce not in others:
            return None
    clear(chat_id)
    item.pop("expires", None)
    return item


def clear(chat_id: str) -> None:
    with _LOCK:
        _PENDING.pop(str(chat_id), None)


def pending_count() -> int:
    """Jumlah aksi tertunda aktif (untuk /status & tes)."""
    with _LOCK:
        now = time.time()
        for k in [k for k, v in _PENDING.items() if now > float(v.get("expires") or 0)]:
            _PENDING.pop(k, None)
        return len(_PENDING)
