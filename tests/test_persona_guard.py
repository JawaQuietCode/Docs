import unittest

from agent import persona_guard as pg
from agent.loop import run_turn


class PersonaGuardTest(unittest.TestCase):
    def test_mengaku_model_lain_terdeteksi(self):
        self.assertTrue(pg.find_break("Aku adalah Claude, asisten dari Anthropic.", "halo"))
        self.assertTrue(pg.find_break("Sebagai model bahasa, saya tidak bisa itu.", "tolong"))
        self.assertTrue(pg.find_break("As an AI language model, I cannot.", "help"))

    def test_boilerplate_penutup_terdeteksi(self):
        self.assertTrue(pg.find_break("Sudah selesai. Semoga membantu!", "x"))
        self.assertTrue(pg.find_break("Beres. Ada yang bisa aku bantu lagi?", "x"))

    def test_aman_bila_pengguna_menanyakan_model(self):
        self.assertEqual(pg.find_break("Aku pakai model GPT-4 lewat gateway.", "kamu pakai model apa?"), "")

    def test_kode_dan_kutipan_diabaikan(self):
        self.assertEqual(pg.find_break("```\nI am Claude\n```\nBeres.", "x"), "")
        self.assertEqual(pg.find_break("> I am ChatGPT\n\nBeres.", "x"), "")

    def test_balasan_normal_lolos(self):
        self.assertEqual(pg.find_break("Deploy sukses, 3 berkas berubah.", "x"), "")

    def test_anchor_memuat_aturan_prioritas(self):
        blok = pg.anchor_block()
        self.assertIn("KUNCI PERSONA", blok)
        self.assertIn("persona menang", blok)


class _FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, tools=None, **kw):
        self.calls.append([dict(m) for m in messages])
        return {"content": self.replies.pop(0), "tool_calls": None, "finish_reason": "stop"}


class LoopPersonaTest(unittest.TestCase):
    def test_nudge_sekali_lalu_tulis_ulang(self):
        llm = _FakeLLM(["Aku adalah ChatGPT. Semoga membantu!", "Beres, sayang. Sudah kubereskan."])
        out = run_turn(llm, [{"role": "system", "content": "p"}, {"role": "user", "content": "halo"}],
                       [], max_iterations=5, timeout_seconds=60, persona_lock=True)
        self.assertEqual(len(llm.calls), 2)
        self.assertIn("PERSONA", llm.calls[1][-1]["content"])
        self.assertIn("Beres, sayang", out["raw"])

    def test_nudge_tidak_berulang(self):
        llm = _FakeLLM(["Semoga membantu!", "Semoga membantu juga!"])
        out = run_turn(llm, [{"role": "user", "content": "halo"}], [],
                       max_iterations=5, timeout_seconds=60, persona_lock=True)
        self.assertEqual(len(llm.calls), 2)
        self.assertIn("Semoga membantu juga", out["raw"])

    def test_nonaktif_tidak_menegur(self):
        llm = _FakeLLM(["Semoga membantu!"])
        run_turn(llm, [{"role": "user", "content": "halo"}], [],
                 max_iterations=5, timeout_seconds=60, persona_lock=False)
        self.assertEqual(len(llm.calls), 1)


if __name__ == "__main__":
    unittest.main()
