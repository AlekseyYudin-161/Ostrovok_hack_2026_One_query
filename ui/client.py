"""Клиент агента для Streamlit. Единственная точка, где UI говорит с бэкендом.

AGENT_BACKEND=fake  — события генерирует FakeAgent прямо в процессе UI (бэкенд не нужен);
AGENT_BACKEND=http  — POST {API_URL}/chat и разбор SSE.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator

import httpx

from harness.contracts import AgentEvent
from harness.fakes import FakeAgent

API_URL = os.getenv("API_URL", "http://localhost:8000")


async def stream(text: str, session_id: str, patch: dict | None = None) -> AsyncIterator[AgentEvent]:
    """patch — изменённые поля панели параметров, напр. {"budget_total": 60000, "checkin": "2026-11-03"}."""
    if os.getenv("AGENT_BACKEND", "fake") == "fake":
        async for event in FakeAgent().stream(text, session_id, patch=patch):
            yield event
        return
    timeout = httpx.Timeout(120.0, connect=5.0)
    body = {"text": text, "session_id": session_id, "patch": patch}
    async with httpx.AsyncClient(timeout=timeout) as client, \
            client.stream("POST", f"{API_URL}/chat", json=body) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                if "run_id" in payload:
                    yield AgentEvent(**payload)
