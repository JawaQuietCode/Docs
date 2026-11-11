"""HTTP and WebSocket routes for chat-scoped AFK status cards."""
from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from agent.config import CONFIG
from agent.afk import AfkStore, create_read_token, verify_read_token


_CLIENTS: dict[str, set[WebSocket]] = {}


def _store() -> AfkStore:
    root = Path(CONFIG.get("root") or Path(__file__).resolve().parent.parent)
    if not root.is_absolute():
        root = Path(__file__).resolve().parent.parent / root
    return AfkStore(root / "data" / "memory" / "kwee.db")


def _secret() -> str:
    return str(os.environ.get("KWEE_AFK_WS_SECRET") or os.environ.get("ACTION_TOKEN")
               or CONFIG.get("agent", {}).get("authToken") or "").strip()


def _public_urls() -> tuple[str, str]:
    cfg = CONFIG.get("afk") or {}
    base = str(os.environ.get("KWEE_AFK_PUBLIC_URL") or cfg.get("publicBaseUrl") or "").strip().rstrip("/")
    ws = str(os.environ.get("KWEE_AFK_WS_URL") or cfg.get("websocketUrl") or "").strip()
    if not ws and base:
        if base.lower().startswith("https://"):
            ws = "wss://" + base[8:] + "/ws/afk"
        elif base.lower().startswith("http://"):
            ws = "ws://" + base[7:] + "/ws/afk"
    return base, ws


async def _broadcast(chat_id: str) -> None:
    clients = list(_CLIENTS.get(chat_id, set()))
    if not clients:
        return
    payload = {"type": "snapshot", "items": _store().list_active(chat_id), "server_time": time.time()}
    stale = []
    for ws in clients:
        try:
            await ws.send_json(payload)
        except Exception:
            stale.append(ws)
    for ws in stale:
        _CLIENTS.get(chat_id, set()).discard(ws)


class AfkIn(BaseModel):
    action: str = Field(default="start", max_length=16)
    chat_id: str = Field(min_length=1, max_length=256)
    sender_id: str = Field(min_length=1, max_length=256)
    sender_name: str = Field(default="", max_length=128)
    reason: str = Field(default="", max_length=240)


def create_router(auth_guard) -> APIRouter:
    router = APIRouter()
    auth = [Depends(auth_guard)]

    @router.get("/afk", dependencies=auth)
    def afk_list(chat_id: str = ""):
        if not chat_id or len(chat_id) > 256:
            raise HTTPException(status_code=400, detail="chat_id tidak valid")
        items = _store().list_active(chat_id)
        base, ws_url = _public_urls()
        secret = _secret()
        ttl = int((CONFIG.get("afk") or {}).get("wsTokenTtlSeconds", 43200))
        token = create_read_token(chat_id, secret, ttl=ttl) if secret and ws_url.lower().startswith("wss://") else ""
        return {"ok": True, "chat_id": chat_id, "items": items,
                "public_url": base, "ws_url": ws_url if token else "", "ws_token": token}

    @router.post("/afk", dependencies=auth)
    async def afk_action(payload: AfkIn):
        action = str(payload.action or "start").strip().lower()
        store = _store()
        if action in ("start", "on", "afk"):
            session = store.start(payload.chat_id, payload.sender_id, payload.sender_name, payload.reason or "AFK")
        elif action in ("stop", "off", "back", "end"):
            session = store.stop(payload.chat_id, payload.sender_id)
            if not session:
                return {"ok": False, "error": "tidak ada sesi AFK aktif"}
        elif action == "list":
            session = None
        else:
            raise HTTPException(status_code=400, detail="action AFK tidak dikenal")
        items = store.list_active(payload.chat_id)
        await _broadcast(payload.chat_id)
        return {"ok": True, "action": action, "session": session, "items": items}

    @router.websocket("/ws/afk")
    async def afk_websocket(websocket: WebSocket, chat_id: str = "", token: str = ""):
        if not chat_id or len(chat_id) > 256 or not verify_read_token(chat_id, token, _secret()):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        bucket = _CLIENTS.setdefault(chat_id, set())
        bucket.add(websocket)
        try:
            await websocket.send_json({"type": "snapshot", "items": _store().list_active(chat_id), "server_time": time.time()})
            while True:
                message = await websocket.receive_text()
                if message == "ping":
                    await websocket.send_json({"type": "ping"})
        except WebSocketDisconnect:
            pass
        finally:
            bucket.discard(websocket)
            if not bucket:
                _CLIENTS.pop(chat_id, None)

    return router
