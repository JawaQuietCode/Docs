import unittest
from unittest.mock import patch

from agent import canvas_engine


class CanvasHealthTests(unittest.TestCase):
    def test_health_marks_200_authorized(self):
        with patch.object(canvas_engine, "_req", return_value=({"items": []}, None)):
            self.assertEqual(canvas_engine._health({}), {"reachable": True, "authorized": True, "status": 200})

    def test_health_does_not_treat_401_as_authorized(self):
        with patch.object(canvas_engine, "_req", return_value=(None, {"status": 401, "detail": "Unauthorized"})):
            health = canvas_engine._health({})
        self.assertTrue(health["reachable"])
        self.assertFalse(health["authorized"])
        self.assertEqual(health["status"], 401)

    def test_ping_remains_liveness_check_for_401(self):
        with patch.object(canvas_engine, "_health", return_value={"reachable": True, "authorized": False, "status": 401}):
            self.assertTrue(canvas_engine.ping({}))


if __name__ == "__main__":
    unittest.main()
