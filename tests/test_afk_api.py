import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import agent.main as main
from agent import afk_api


class AfkApiTests(unittest.TestCase):
    def test_authenticated_api_and_scoped_websocket_broadcasts(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "ACTION_TOKEN": "test-secret",
            "KWEE_AFK_PUBLIC_URL": "https://bot.example",
        }, clear=False), patch.dict(main.CONFIG, {"root": tmp}), patch.object(main, "_AGENT_TOKEN", "test-secret"):
            client = TestClient(main.app)
            self.assertEqual(client.get("/afk", params={"chat_id": "group-1"}).status_code, 401)
            headers = {"X-Agent-Token": "test-secret"}
            started = client.post("/afk", headers=headers, json={
                "action": "start", "chat_id": "group-1", "sender_id": "user-a",
                "sender_name": "Alice", "reason": "tidur",
            })
            self.assertEqual(started.status_code, 200, started.text)
            listing = client.get("/afk", headers=headers, params={"chat_id": "group-1"}).json()
            self.assertEqual(listing["ws_url"], "wss://bot.example/ws/afk")
            self.assertEqual(len(listing["items"]), 1)

            with client.websocket_connect(
                "/ws/afk?chat_id=group-1&token=" + listing["ws_token"]
            ) as ws:
                self.assertEqual(len(ws.receive_json()["items"]), 1)
                client.post("/afk", headers=headers, json={
                    "action": "start", "chat_id": "group-1", "sender_id": "user-b",
                    "sender_name": "Bob", "reason": "makan",
                })
                self.assertEqual(len(ws.receive_json()["items"]), 2)
                client.post("/afk", headers=headers, json={
                    "action": "stop", "chat_id": "group-1", "sender_id": "user-a",
                })
                self.assertEqual(len(ws.receive_json()["items"]), 1)


if __name__ == "__main__":
    unittest.main()

class ActivityTraceTests(unittest.TestCase):
    def test_activity_keeps_phase_and_tool_events_for_polling(self):
        chat_id = 'activity-trace-test'
        main._clear_activity(chat_id)
        try:
            main._set_activity(chat_id, stage='skills', step='memuat skill', tool='', detail='memindai', event_kind='phase', started=1)
            main._set_activity(chat_id, stage='think', step='berpikir', tool='', detail='', event_kind='phase')
            main._set_activity(chat_id, stage='tool', step='menjalankan tool', tool='bash', detail='ls -1 skills/external | nl', event_kind='tool_start', event_id='call-1')
            main._set_activity(chat_id, stage='tool', step='menjalankan tool', tool='bash', detail='ls -1 skills/external | nl', event_kind='tool', event_id='call-1', ok=True)
            state = main._ACTIVITY[chat_id]
            self.assertEqual([e['step'] for e in state['events']], ['memuat skill', 'berpikir', 'menjalankan tool', 'menjalankan tool'])
            self.assertEqual([e['seq'] for e in state['events']], [1, 2, 3, 4])
            self.assertEqual(state['events'][2]['detail'], 'ls -1 skills/external | nl')
            self.assertEqual(state['events'][3]['kind'], 'tool')
        finally:
            main._clear_activity(chat_id)
