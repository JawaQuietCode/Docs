import io
import tempfile
import unittest
from pathlib import Path

from agent.progres import Layar, Progres, bilah, hitung_persen
from agent.cli import cmd_progress_html
from argparse import Namespace
import json


class ProgresTest(unittest.TestCase):
    def test_persen_naik_monoton_dan_berhenti_sebelum_100(self):
        nilai = [hitung_persen(n) for n in range(0, 40)]
        self.assertEqual(nilai, sorted(nilai))
        self.assertEqual(nilai[-1], 92)
        self.assertEqual(hitung_persen(3, selesai=True), 100)

    def test_bilah_panjang_tetap(self):
        self.assertEqual(len(bilah(42, 30)), 30)
        self.assertEqual(bilah(0, 10), '░' * 10)
        self.assertEqual(bilah(100, 10), '█' * 10)

    def test_state_dan_terminal_sinkron(self):
        p = Progres()
        p.catat('bash', 'ls -la', kind='shell')
        p.catat('baca_berkas', 'agent/progres.py')
        d = p.ke_dict()
        self.assertEqual(d['persen'], p.persen)
        self.assertEqual(d['shell'], 'ls -la')
        self.assertEqual(d['tool'], 'baca_berkas')
        self.assertIn(f"{p.persen:>3}%", p.terminal(berwarna=False))

    def test_jawaban_ikut_di_state_setelah_tutup(self):
        p = Progres().tutup('Halo')
        self.assertTrue(p.ke_dict()['selesai'])
        self.assertEqual(p.ke_dict()['jawaban'], 'Halo')
        self.assertEqual(p.ke_dict()['persen'], 100)

    def test_html_mandiri_bukan_widget_dan_mengamankan_teks(self):
        p = Progres(judul='Kwee <script>alert(1)</script>')
        p.set_fase('tool', '2 skills aktif')
        p.catat('bash', 'ls -la')
        doc = p.html()
        self.assertIn('<!doctype html>', doc.lower())
        self.assertIn('window.kweeProgress', doc)
        self.assertIn('prefers-reduced-motion', doc)
        self.assertNotIn('<title>Kwee <script>', doc)
        self.assertIn('textContent', doc)
        self.assertNotIn('web2ui', doc.lower())

    def test_cli_export_snapshot_ke_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "state.json"
            target = Path(tmp) / "progress.html"
            source.write_text(json.dumps({"judul": "Run Kwee", "persen": 48, "fase": "tool", "langkah": []}), encoding="utf-8")
            self.assertEqual(cmd_progress_html(Namespace(input=str(source), output=str(target))), 0)
            self.assertIn("<!doctype html>", target.read_text(encoding="utf-8").lower())

    def test_simpan_html_menolak_ekstensi_non_html(self):
        p = Progres()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'progress.txt'
            with self.assertRaises(ValueError):
                p.simpan_html(str(target))

    def test_layar_non_tty_tidak_memakai_escape(self):
        buf = io.StringIO()
        layar = Layar(out=buf)
        p = Progres()
        p.catat('berpikir')
        layar.tampil(p)
        self.assertNotIn('\x1b[', buf.getvalue())


if __name__ == '__main__':
    unittest.main()
