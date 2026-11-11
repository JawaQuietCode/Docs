#!/usr/bin/env python3
"""codec.py — enkripsi dan dekripsi berjenjang (level 1-5).

    python3 codec.py levels
    python3 codec.py enc --level N [--metode M] [--key K] [--kunci-out F] [--in F | --text T]
    python3 codec.py dec --level N [--metode M] [--key K] [--in F | --text T]
    python3 codec.py auto [--in F | --text T]
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import secrets
import sys
import time
from pathlib import Path

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
except ImportError:  # level 4-5 butuh paket cryptography
    AESGCM = ChaCha20Poly1305 = PBKDF2HMAC = hashes = None

ITERASI_PBKDF2 = 600_000
ABJAD = "abcdefghijklmnopqrstuvwxyz"
FREKUENSI_UMUM = set("etaoinshrdlu ")

LEVEL = {
    1: {"nama": "mudah", "metode": ["base64", "hex", "caesar", "rot13"], "default": "base64"},
    2: {"nama": "sedang-mudah", "metode": ["vigenere", "atbash", "railfence"], "default": "vigenere"},
    3: {"nama": "sedang", "metode": ["affine", "xor"], "default": "affine"},
    4: {"nama": "sulit", "metode": ["aes"], "default": "aes"},
    5: {"nama": "hampir-mustahil", "metode": ["rantai"], "default": "rantai"},
}
AFFINE_A = [1, 3, 5, 7, 9, 11, 15, 17, 19, 21, 23, 25]


class GagalError(Exception):
    pass


def _geser_huruf(teks: str, fungsi) -> str:
    out = []
    for i, ch in enumerate(teks):
        if ch.isascii() and ch.isalpha():
            basis = 65 if ch.isupper() else 97
            out.append(chr(basis + fungsi(ord(ch) - basis, i) % 26))
        else:
            out.append(ch)
    return "".join(out)


def caesar(teks: str, geser: int) -> str:
    return _geser_huruf(teks, lambda p, _: p + geser)


def vigenere(teks: str, kunci: str, balik: bool = False) -> str:
    deret = [ABJAD.index(c) for c in kunci.lower() if c in ABJAD]
    if not deret:
        raise GagalError("kunci vigenere harus mengandung huruf")
    tanda = -1 if balik else 1
    out, j = [], 0
    for ch in teks:
        if ch.isascii() and ch.isalpha():
            basis = 65 if ch.isupper() else 97
            out.append(chr(basis + (ord(ch) - basis + tanda * deret[j % len(deret)]) % 26))
            j += 1
        else:
            out.append(ch)
    return "".join(out)


def atbash(teks: str) -> str:
    return _geser_huruf(teks, lambda p, _: 25 - p)


def railfence(teks: str, rel: int, balik: bool = False) -> str:
    if rel < 2:
        raise GagalError("jumlah rel minimal 2")
    siklus = 2 * (rel - 1)
    urutan = sorted(range(len(teks)), key=lambda i: (min(i % siklus, siklus - i % siklus), i))
    if not balik:
        return "".join(teks[i] for i in urutan)
    hasil = [""] * len(teks)
    for k, i in enumerate(urutan):
        hasil[i] = teks[k]
    return "".join(hasil)


def affine(teks: str, a: int, b: int, balik: bool = False) -> str:
    if a not in AFFINE_A:
        raise GagalError(f"a={a} tidak koprima dengan 26")
    inv = pow(a, -1, 26)
    if balik:
        return _geser_huruf(teks, lambda p, _: inv * (p - b))
    return _geser_huruf(teks, lambda p, _: a * p + b)


def skor_bahasa(teks: str) -> float:
    if not teks:
        return 0.0
    cetak = sum(c.isprintable() or c in "\n\t" for c in teks) / len(teks)
    if cetak < 0.95:
        return 0.0
    huruf = sum(c.lower() in FREKUENSI_UMUM for c in teks) / len(teks)
    return round(0.5 * cetak + 0.5 * huruf, 4)


def _teks(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GagalError("input bukan UTF-8; untuk data biner pakai metode base64/aes/rantai") from exc


def _kdf(sandi: str, garam: bytes) -> bytes:
    if PBKDF2HMAC is None:
        raise GagalError("paket cryptography belum terpasang: pip install cryptography")
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=garam, iterations=ITERASI_PBKDF2).derive(sandi.encode("utf-8"))


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(teks: str) -> bytes:
    try:
        return base64.b64decode(teks, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise GagalError("base64 tidak valid") from exc


def enc_aes(data: bytes, sandi: str) -> bytes:
    if AESGCM is None:
        raise GagalError("paket cryptography belum terpasang: pip install cryptography")
    garam, nonce = os.urandom(16), os.urandom(12)
    ct = AESGCM(_kdf(sandi, garam)).encrypt(nonce, data, None)
    env = {"v": 1, "alg": "AES-256-GCM", "kdf": "PBKDF2-SHA256", "iter": ITERASI_PBKDF2,
           "salt": _b64(garam), "nonce": _b64(nonce), "ct": _b64(ct)}
    return json.dumps(env, ensure_ascii=False).encode("utf-8")


def dec_aes(data: bytes, sandi: str) -> bytes:
    env = _muat_envelope(data)
    try:
        return AESGCM(_kdf(sandi, _unb64(env["salt"]))).decrypt(_unb64(env["nonce"]), _unb64(env["ct"]), None)
    except KeyError as exc:
        raise GagalError("envelope AES tidak lengkap") from exc
    except Exception as exc:
        raise GagalError("sandi salah atau data rusak (autentikasi GCM gagal)") from exc


def enc_rantai(data: bytes) -> tuple[bytes, dict]:
    if AESGCM is None:
        raise GagalError("paket cryptography belum terpasang: pip install cryptography")
    k1, k2 = secrets.token_bytes(32), secrets.token_bytes(32)
    n1, n2 = os.urandom(12), os.urandom(12)
    lapis1 = ChaCha20Poly1305(k1).encrypt(n1, data, b"lapis-1")
    lapis2 = AESGCM(k2).encrypt(n2, lapis1, b"lapis-2")
    env = {"v": 1, "alg": "AES-256-GCM(ChaCha20-Poly1305)", "n1": _b64(n1), "n2": _b64(n2), "ct": _b64(lapis2)}
    return json.dumps(env, ensure_ascii=False).encode("utf-8"), {"k1": k1.hex(), "k2": k2.hex()}


def dec_rantai(data: bytes, kunci: dict) -> bytes:
    env = _muat_envelope(data)
    try:
        k1, k2 = bytes.fromhex(kunci["k1"]), bytes.fromhex(kunci["k2"])
        lapis1 = AESGCM(k2).decrypt(_unb64(env["n2"]), _unb64(env["ct"]), b"lapis-2")
        return ChaCha20Poly1305(k1).decrypt(_unb64(env["n1"]), lapis1, b"lapis-1")
    except KeyError as exc:
        raise GagalError("kunci atau envelope tidak lengkap") from exc
    except Exception as exc:
        raise GagalError("kunci salah atau data rusak (autentikasi gagal)") from exc


def _muat_envelope(data: bytes) -> dict:
    try:
        env = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GagalError("envelope harus JSON hasil enc level 4/5") from exc
    if not isinstance(env, dict):
        raise GagalError("envelope harus objek JSON")
    return env


def _muat_kunci(nilai: str) -> dict:
    if os.path.isfile(nilai):
        nilai = Path(nilai).read_text(encoding="utf-8")
    try:
        kunci = json.loads(nilai)
    except json.JSONDecodeError as exc:
        raise GagalError("kunci level 5 harus JSON {k1,k2} atau path berkas JSON") from exc
    return kunci


def jalankan(mode: str, level: int, metode: str | None, data: bytes, key: str | None, kunci_out: str | None = None) -> tuple[bytes, dict]:
    if level not in LEVEL:
        raise GagalError("level harus 1-5 (lihat: codec.py levels)")
    metode = metode or LEVEL[level]["default"]
    if metode not in LEVEL[level]["metode"]:
        raise GagalError(f"level {level} hanya mendukung: {', '.join(LEVEL[level]['metode'])}")
    meta: dict = {"level": level, "metode": metode}

    if level == 1:
        if metode == "base64":
            return (_b64(data).encode() if mode == "enc" else _unb64(_teks(data))), meta
        if metode == "hex":
            return (data.hex().encode() if mode == "enc" else bytes.fromhex(_teks(data))), meta
        geser = int(key) if key not in (None, "") else 3
        if metode == "rot13":
            geser = 13
        if mode == "dec":
            geser = -geser
        return caesar(_teks(data), geser).encode("utf-8"), meta

    if level == 2:
        teks = _teks(data)
        if metode == "atbash":
            return atbash(teks).encode("utf-8"), meta
        if metode == "vigenere":
            if not key:
                raise GagalError("vigenere butuh --key (kata kunci)")
            return vigenere(teks, key, balik=(mode == "dec")).encode("utf-8"), meta
        rel = int(key) if key else 3
        return railfence(teks, rel, balik=(mode == "dec")).encode("utf-8"), meta

    if level == 3:
        if metode == "affine":
            if not key or "," not in key:
                raise GagalError("affine butuh --key 'a,b' (a koprima dengan 26)")
            a, b = (int(x) for x in key.split(",", 1))
            return affine(_teks(data), a, b, balik=(mode == "dec")).encode("utf-8"), meta
        if not key:
            raise GagalError("xor butuh --key")
        kb = key.encode("utf-8")
        if mode == "enc":
            return bytes(x ^ kb[i % len(kb)] for i, x in enumerate(data)).hex().encode(), meta
        biner = bytes.fromhex(_teks(data))
        return bytes(x ^ kb[i % len(kb)] for i, x in enumerate(biner)), meta

    if level == 4:
        if not key:
            raise GagalError("aes butuh --key (frasa sandi)")
        if mode == "enc":
            return enc_aes(data, key), meta
        return dec_aes(data, key), meta

    if mode == "enc":
        env, kunci = enc_rantai(data)
        tujuan = Path(kunci_out or f"kunci-l5-{int(time.time())}.json")
        tujuan.write_text(json.dumps(kunci), encoding="utf-8")
        os.chmod(tujuan, 0o600)
        meta["kunci_out"] = str(tujuan)
        return env, meta
    if not key:
        raise GagalError("dekripsi level 5 butuh --key (JSON {k1,k2} atau path berkasnya)")
    return dec_rantai(data, _muat_kunci(key)), meta


def auto(data: bytes) -> list[dict]:
    teks = _teks(data)
    kandidat = []

    def coba(nama, fn):
        try:
            hasil = fn()
        except Exception:
            return
        skor = skor_bahasa(hasil)
        if skor >= 0.6:
            kandidat.append({"skor": skor, "metode": nama, "hasil": hasil[:300]})

    coba("base64", lambda: _unb64(teks).decode("utf-8"))
    coba("hex", lambda: bytes.fromhex(teks).decode("utf-8"))
    coba("rot13", lambda: caesar(teks, 13))
    coba("atbash", lambda: atbash(teks))
    for g in range(1, 26):
        coba(f"caesar geser-{g}", lambda g=g: caesar(teks, -g))
    for a in AFFINE_A:
        for b in range(26):
            coba(f"affine a={a} b={b}", lambda a=a, b=b: affine(teks, a, b, balik=True))
    return sorted(kandidat, key=lambda k: k["skor"], reverse=True)[:10]


def _ambil_input(args) -> bytes:
    if args.text is not None:
        return args.text.encode("utf-8")
    if args.in_file and args.in_file != "-":
        return Path(args.in_file).read_bytes()
    return sys.stdin.buffer.read()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="codec", description="enkripsi/dekripsi level 1-5")
    sub = p.add_subparsers(dest="perintah", required=True)
    sub.add_parser("levels")
    for nama in ("enc", "dec"):
        s = sub.add_parser(nama)
        s.add_argument("--level", type=int, required=True)
        s.add_argument("--metode")
        s.add_argument("--key")
        s.add_argument("--kunci-out")
        s.add_argument("--in", dest="in_file")
        s.add_argument("--text")
        s.add_argument("--out")
    a = sub.add_parser("auto")
    a.add_argument("--in", dest="in_file")
    a.add_argument("--text")
    args = p.parse_args(argv)

    try:
        if args.perintah == "levels":
            print(json.dumps({str(k): v for k, v in LEVEL.items()}, ensure_ascii=False, indent=2))
            return 0
        data = _ambil_input(args)
        if args.perintah == "auto":
            print(json.dumps({"kandidat": auto(data)}, ensure_ascii=False, indent=2))
            return 0
        keluaran, meta = jalankan(args.perintah, args.level, args.metode, data, args.key, args.kunci_out)
        if args.out:
            Path(args.out).write_bytes(keluaran)
            print(json.dumps({"ok": True, "out": args.out, **meta}, ensure_ascii=False))
        else:
            sys.stdout.buffer.write(keluaran)
            sys.stdout.buffer.write(b"\n")
            print(json.dumps(meta, ensure_ascii=False), file=sys.stderr)
        return 0
    except GagalError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
