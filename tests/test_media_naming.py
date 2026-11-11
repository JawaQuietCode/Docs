import tempfile
import unittest
from pathlib import Path

from tools.builtin.media_naming import bersihkan, format_tanggal, nama_media, rapikan_berkas, unik


class MediaNamingTest(unittest.TestCase):
    def test_bersihkan_buang_karakter_terlarang_dan_spasi_ganda(self):
        self.assertEqual(bersihkan('  Judul:  "Baru"?/ya  '), 'Judul Baru ya')

    def test_bersihkan_buang_zero_width(self):
        self.assertEqual(bersihkan('Lagu\u200b\ufeff Baru'), 'Lagu Baru')

    def test_nama_reserved_windows_diberi_akhiran(self):
        self.assertEqual(bersihkan('CON'), 'CON_')

    def test_batas_byte_utf8(self):
        hasil = bersihkan('ä' * 300, 50)
        self.assertLessEqual(len(hasil.encode('utf-8')), 50)

    def test_format_tanggal_yt_dlp(self):
        self.assertEqual(format_tanggal('20261102'), '2026-11-02')
        self.assertEqual(format_tanggal(''), '')

    def test_nama_media_lengkap(self):
        nama = nama_media('Tutorial Python', kanal='Kwee TV', tanggal='20261102', kualitas=1080, ext='mp4')
        self.assertEqual(nama, 'Tutorial Python - Kwee TV (2026-11-02, 1080p).mp4')

    def test_nama_media_tanpa_metadata(self):
        self.assertEqual(nama_media('Judul saja', ext='mp3'), 'Judul saja.mp3')

    def test_unik_menambah_nomor(self):
        with tempfile.TemporaryDirectory() as d:
            induk = Path(d)
            (induk / 'a.mp4').write_bytes(b'x')
            self.assertEqual(unik(induk / 'a.mp4').name, 'a (2).mp4')

    def test_rapikan_berkas_ganti_nama_dan_hindari_tabrakan(self):
        with tempfile.TemporaryDirectory() as d:
            induk = Path(d)
            asal = induk / 'abc123.mp4'
            asal.write_bytes(b'video')
            (induk / 'Judul - Kanal (2026-11-02, 720p).mp4').write_bytes(b'lama')
            baru = rapikan_berkas(asal, judul='Judul', kanal='Kanal', tanggal='20261102', kualitas='720')
            self.assertEqual(baru.name, 'Judul - Kanal (2026-11-02, 720p) (2).mp4')
            self.assertFalse(asal.exists())

    def test_rapikan_berkas_nama_sudah_benar_tidak_diubah(self):
        with tempfile.TemporaryDirectory() as d:
            berkas = Path(d) / 'Judul.mp3'
            berkas.write_bytes(b'x')
            self.assertEqual(rapikan_berkas(berkas, judul='Judul'), berkas)


if __name__ == '__main__':
    unittest.main()
