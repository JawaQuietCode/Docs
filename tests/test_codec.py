import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "skills" / "external" / "encode-decode" / "scripts"
sys.path.insert(0, str(SKILL))

import codec  # noqa: E402


class CodecTest(unittest.TestCase):
    def roundtrip(self, level, metode, teks, key=None):
        enc, _ = codec.jalankan("enc", level, metode, teks.encode("utf-8"), key)
        dec, _ = codec.jalankan("dec", level, metode, enc, key)
        return dec.decode("utf-8")

    def test_level1(self):
        self.assertEqual(self.roundtrip(1, "base64", "Halo dunia ✦"), "Halo dunia ✦")
        self.assertEqual(self.roundtrip(1, "hex", "abc"), "abc")
        self.assertEqual(self.roundtrip(1, "caesar", "Kwee Agent", "5"), "Kwee Agent")
        self.assertEqual(self.roundtrip(1, "rot13", "Hello"), "Hello")

    def test_level2(self):
        self.assertEqual(self.roundtrip(2, "vigenere", "Attack at dawn", "lemon"), "Attack at dawn")
        self.assertEqual(self.roundtrip(2, "atbash", "Kwee"), "Kwee")
        self.assertEqual(self.roundtrip(2, "railfence", "WEAREDISCOVERED", "3"), "WEAREDISCOVERED")

    def test_vigenere_vektor_dikenal(self):
        enc, _ = codec.jalankan("enc", 2, "vigenere", b"ATTACKATDAWN", "LEMON")
        self.assertEqual(enc, b"LXFOPVEFRNHR")

    def test_level3(self):
        self.assertEqual(self.roundtrip(3, "affine", "Halo Dunia", "5,8"), "Halo Dunia")
        self.assertEqual(self.roundtrip(3, "xor", "pesan", "kunci"), "pesan")

    def test_affine_a_tidak_koprima_ditolak(self):
        with self.assertRaises(codec.GagalError):
            codec.jalankan("enc", 3, "affine", b"x", "2,3")

    def test_level4_aes_roundtrip_dan_sandi_salah(self):
        enc, _ = codec.jalankan("enc", 4, "aes", b"rahasia", "frasa-kuat")
        dec, _ = codec.jalankan("dec", 4, "aes", enc, "frasa-kuat")
        self.assertEqual(dec, b"rahasia")
        with self.assertRaises(codec.GagalError):
            codec.jalankan("dec", 4, "aes", enc, "salah")

    def test_level5_kunci_berkas_dan_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            kunci_path = os.path.join(d, "kunci.json")
            enc, meta = codec.jalankan("enc", 5, "rantai", bytes(range(256)), None, kunci_path)
            self.assertEqual(meta["kunci_out"], kunci_path)
            self.assertEqual(oct(os.stat(kunci_path).st_mode & 0o777), "0o600")
            dec, _ = codec.jalankan("dec", 5, "rantai", enc, kunci_path)
            self.assertEqual(dec, bytes(range(256)))

    def test_level5_tanpa_kunci_gagal(self):
        with tempfile.TemporaryDirectory() as d:
            enc, _ = codec.jalankan("enc", 5, "rantai", b"x", None, os.path.join(d, "k.json"))
            with self.assertRaises(codec.GagalError):
                codec.jalankan("dec", 5, "rantai", enc, None)

    def test_auto_menemukan_caesar(self):
        teks = "the quick brown fox jumps over the lazy dog and the cat"
        sandi = codec.caesar(teks, 7)
        hasil = codec.auto(sandi.encode("utf-8"))
        self.assertTrue(hasil)
        self.assertEqual(hasil[0]["hasil"], teks)

    def test_level_tidak_valid(self):
        with self.assertRaises(codec.GagalError):
            codec.jalankan("enc", 9, None, b"x", None)


if __name__ == "__main__":
    unittest.main()
