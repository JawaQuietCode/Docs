"""progres.py — satu state progres untuk terminal dan modul HTML di transport.

Transport membaca `ke_dict()`; terminal memakai `terminal()`. Keduanya dari
objek yang sama, jadi persen dan langkah tidak pernah berbeda antar tampilan.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field

JUDUL_DEFAULT = "kwee sedang bekerja"
LANGKAH_TAMPIL = 6
PERSEN_MAKS_JALAN = 98

WARNA = {
    "reset": "\x1b[0m",
    "dim": "\x1b[2m",
    "pink": "\x1b[38;5;213m",
    "lilac": "\x1b[38;5;183m",
    "hijau": "\x1b[38;5;120m",
    "merah": "\x1b[38;5;203m",
}


def hitung_persen(jumlah_langkah: int, selesai: bool = False) -> int:
    """Estimasi konservatif bila telemetry fase/waktu tidak tersedia."""
    if selesai:
        return 100
    return min(92, max(0, int(jumlah_langkah)) * 4)


RENTANG_FASE = {
    "skills": (3, 8, 0.65, 1),
    "think": (10, 18, 0.45, 2),
    "tool": (28, 48, 0.50, 6),
    "respond": (76, 17, 0.70, 2),
    "asset": (93, 5, 0.40, 1),
}


def target_persen(fase: str, detik_fase: float, langkah_fase: int) -> int:
    awal, ruang, per_detik, per_langkah = RENTANG_FASE.get(fase, RENTANG_FASE["tool"])
    kenaikan = max(0.0, detik_fase) * per_detik + max(0, langkah_fase) * per_langkah
    return min(PERSEN_MAKS_JALAN, round(awal + min(ruang, kenaikan)))


def bilah(persen: int, lebar: int = 30) -> str:
    isi = round(lebar * max(0, min(100, persen)) / 100)
    return "█" * isi + "░" * (lebar - isi)


@dataclass
class Progres:
    judul: str = JUDUL_DEFAULT
    langkah: list = field(default_factory=list)
    tool: str = ""
    shell: str = ""
    mulai: float = field(default_factory=time.time)
    selesai: bool = False
    jawaban: str = ""
    fase: str = "think"
    fase_mulai: float = field(default_factory=time.time)
    langkah_fase: int = 0
    persen_terakhir: int = 0
    skill_info: str = ""

    def set_fase(self, fase: str, skill_info: str | None = None) -> None:
        fase = str(fase or "tool")
        if fase != self.fase:
            self.fase = fase
            self.fase_mulai = time.time()
            self.langkah_fase = 0
        if skill_info is not None:
            self.skill_info = str(skill_info)[:180]

    def _naikkan_persen(self) -> None:
        if self.selesai:
            self.persen_terakhir = 100
            return
        target = target_persen(self.fase, time.time() - self.fase_mulai, self.langkah_fase)
        self.persen_terakhir = min(target, self.persen_terakhir + 2)

    def catat(self, nama: str, detail: str = "", kind: str = "tool", ok: bool = True, fase: str | None = None) -> None:
        if fase:
            self.set_fase(fase)
        elif kind == "think":
            self.set_fase("think")
        elif kind in {"tool", "shell"}:
            self.set_fase("tool")
        self.langkah_fase += 1
        self.langkah.append({"kind": kind, "nama": nama, "detail": str(detail)[:160], "ok": bool(ok)})
        if kind == "shell":
            self.shell = str(detail)[:160]
        elif kind == "tool":
            self.tool = nama

    def tutup(self, jawaban: str = "") -> "Progres":
        self.selesai = True
        self.jawaban = jawaban
        self.persen_terakhir = 100
        self.fase = "done"
        return self

    @property
    def persen(self) -> int:
        return self.persen_terakhir

    @property
    def detik(self) -> float:
        return round(time.time() - self.mulai, 1)

    def ke_dict(self) -> dict:
        self._naikkan_persen()
        return {
            "judul": self.judul,
            "persen": self.persen,
            "langkah": self.langkah[-LANGKAH_TAMPIL:],
            "selesai": self.selesai,
            "fase": "done" if self.selesai else self.fase,
            "skill_info": self.skill_info,
            "tool": self.tool,
            "shell": self.shell,
            "detik": self.detik,
            "jawaban": self.jawaban,
        }

    def html(self) -> str:
        from agent.progres_html import render_html
        return render_html(self.ke_dict())

    def simpan_html(self, tujuan: str = "progress-kwee.html"):
        from agent.progres_html import simpan_html
        return simpan_html(self.ke_dict(), tujuan)

    def refresh(self) -> None:
        """Perbarui estimasi sekali per siklus polling/tampilan."""
        self._naikkan_persen()

    def terminal(self, berwarna: bool = True) -> str:
        def c(kunci: str, teks: str) -> str:
            return f"{WARNA[kunci]}{teks}{WARNA['reset']}" if berwarna else teks

        baris = [f"{c('pink', '✦')} {c('lilac', self.judul)}  {c('dim', f'{self.detik:.1f}s')}"]
        baris.append(f"  {c('pink', bilah(self.persen))} {self.persen:>3}%")
        for l in self.langkah[-LANGKAH_TAMPIL:]:
            ikon = c("merah", "✗") if not l["ok"] else (c("hijau", "✓") if self.selesai else c("pink", "…"))
            detail = f" — {l['detail']}" if l["detail"] else ""
            baris.append(f"  {ikon} {l['nama']}{c('dim', detail)}")
        if self.selesai:
            baris.append(f"  {c('hijau', '♡ selesai')}")
        return "\n".join(baris)


class Layar:
    """Gambar ulang blok progres di terminal tanpa mencetak baris baru terus-menerus."""

    def __init__(self, out=None):
        self.out = out or sys.stdout
        self.tinggi = 0

    def tampil(self, p: Progres) -> None:
        p.refresh()
        teks = p.terminal(berwarna=self.out.isatty())
        if self.out.isatty() and self.tinggi:
            self.out.write(f"\x1b[{self.tinggi}F\x1b[J")
        self.out.write(teks + "\n")
        self.out.flush()
        self.tinggi = teks.count("\n") + 1


def _demo() -> None:
    p = Progres()
    layar = Layar()
    rencana = [
        ("berpikir", "menyusun rencana", "think"),
        ("bash", "ls -la agent", "shell"),
        ("baca_berkas", "agent/progres.py", "tool"),
        ("tulis_berkas", "agent/progres.py", "tool"),
        ("tes", "unittest tests/test_progres.py", "shell"),
    ]
    for nama, detail, kind in rencana:
        time.sleep(0.6)
        p.catat(nama, detail, kind=kind)
        layar.tampil(p)
    time.sleep(0.6)
    layar.tampil(p.tutup("Selesai. Modul progres sudah berjalan."))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        print("pakai: python3 -m agent.progres demo", file=sys.stderr)
