"""allowlist.py — siapa yang boleh menyuruh bot.

Sejak WhatsApp memakai **LID** (identitas berbasis server), identitas pengirim
sebuah pesan bisa berupa salah satu (atau hanya salah satu) dari:

  - nomor telepon (PN) : ``6283194678090:5@s.whatsapp.net``
  - LID                : ``237533619302450@lid``

Angka di dalam LID **bukan** nomor telepon, jadi membandingkan bagian lokalnya
dengan nomor owner tidak akan pernah cocok. Transport (Node) sudah me-resolve
LID -> nomor telepon dan mengirim SEMUA bentuk identitas yang diketahui
(``sender_ids``), sehingga di sini cukup membandingkan himpunan identitas.

Dengan begitu owner boleh ditulis sebagai nomor telepon **atau** LID.
"""
import re

from security.lidmap import expand, lid_for_pn, pn_for_lid


def normalize(jid: str) -> str:
    """Ambil bagian pengguna dari sebuah JID.

    Buang ``:device`` dan ``@server``. Untuk LID, ini mengembalikan angka LID
    apa adanya (bukan nomor telepon) — cocok dipakai sebagai token identitas.

    6288973387893:5@s.whatsapp.net -> 6288973387893
    6288973387893@s.whatsapp.net   -> 6288973387893
    237533619302450@lid            -> 237533619302450
    """
    if not jid:
        return ""
    local = str(jid).split("@")[0]
    return re.sub(r"[:\-].*$", "", local).strip()


def is_lid(jid: str) -> bool:
    """True bila JID ini memakai identitas LID (bukan nomor telepon)."""
    s = str(jid or "")
    return s.endswith("@lid") or s.endswith("@hosted.lid")


def candidates(value) -> set:
    """Kumpulkan semua token identitas dari sebuah nilai.

    ``value`` boleh berupa string tunggal atau daftar (mis. ``sender_ids`` dari
    transport yang berisi PN **dan** LID sekaligus).
    """
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set, frozenset)):
        out: set = set()
        for v in value:
            out |= candidates(v)
        return out
    s = str(value).strip()
    if not s:
        return set()
    n = normalize(s)
    return {n} if n else set()


def kind_of(jid) -> str:
    """Jenis identitas sebuah JID: ``"lid"`` atau ``"pn"``.

    Nomor telepon dan LID BERBAGI ruang angka (angka LID seseorang bisa sama
    dengan nomor telepon orang lain), jadi jenis identitas WAJIB diperhatikan
    saat mencocokkan — kalau tidak, identitas bisa tertukar.
    """
    return "lid" if is_lid(jid) else "pn"


def typed_candidates(value) -> set:
    """Kumpulkan ``(jenis, angka)`` dari sebuah identitas/daftar identitas.

    Berbeda dari :func:`candidates` yang membuang jenis, fungsi ini MEMPERTAHANKAN
    jenis (``"pn"``/``"lid"``) sehingga pencocokan bisa sadar-tipe.
    """
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set, frozenset)):
        out: set = set()
        for v in value:
            out |= typed_candidates(v)
        return out
    s = str(value).strip()
    if not s:
        return set()
    n = normalize(s)
    return {(kind_of(s), n)} if n else set()


def identity_matches(sender_typed, owner_typed, lid_dir=None) -> bool:
    """True bila himpunan identitas pengirim membuktikan salah satu identitas owner.

    Sadar-tipe DAN dua arah:
      - PN pengirim == PN owner  → cocok
      - LID pengirim == LID owner → cocok
      - LID pengirim dipetakan (``lid2pn``) ke PN owner → cocok
      - PN pengirim dipetakan (``pn2lid``) ke LID owner → cocok

    Karena tiap arah hanya memakai peta yang benar, tabrakan angka LID↔PN
    (LID seseorang = nomor telepon orang lain) TIDAK bisa menukar identitas.
    """
    for skind, sdigits in sender_typed or ():
        if not sdigits:
            continue
        for okind, odigits in owner_typed or ():
            if not odigits:
                continue
            if skind == okind and sdigits == odigits:
                return True
            if not lid_dir:
                continue
            if skind == "lid" and okind == "pn" and pn_for_lid(sdigits, lid_dir) == odigits:
                return True
            if skind == "pn" and okind == "lid" and lid_for_pn(sdigits, lid_dir) == odigits:
                return True
    return False


def owner_matches(sender_typed, owner_pn, lid_dir=None) -> bool:
    """True bila identitas pengirim (bertipe) membuktikan owner ``owner_pn``.

    ``owner_pn`` boleh berupa nomor telepon (``628…``) **atau** LID
    (``237…@lid``). Pencocokan sadar-tipe — lihat :func:`identity_matches`.
    """
    owner_typed = typed_candidates(owner_pn)
    if not owner_typed:
        return False
    return identity_matches(sender_typed, owner_typed, lid_dir)


def is_allowed(sender, owners, lid_dir=None) -> bool:
    """True bila salah satu identitas pengirim cocok dengan salah satu owner.

    ``sender`` boleh berupa string tunggal atau daftar berisi PN + LID.
    Pencocokan **sadar-tipe** (lihat :func:`owner_matches`): PN dicocokkan
    dengan nomor owner, LID hanya lewat pemetaan ``lid2pn`` yang sah. Ini
    mencegah dua owner bertukar identitas karena LID dan nomor telepon berbagi
    ruang angka.
    """
    typed = typed_candidates(sender)
    if not typed:
        return False
    owner_list = owners if isinstance(owners, (list, tuple, set, frozenset)) else [owners]
    for owner in owner_list:
        if owner_matches(typed, owner, lid_dir):
            return True
    return False
