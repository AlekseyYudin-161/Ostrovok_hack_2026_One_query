from __future__ import annotations

import json

import httpx
import pytest

from app.main import app, build_agent
from harness.constraints import check
from harness.contracts import Agent, AgentEvent, Constraint, HotelCandidate, LLMClient, MemoryStore, Ranker
from harness.fakes import FakeAgent, FakeLLM, FakeRanker, InMemoryStore


def test_fakes_match_protocols() -> None:
    assert isinstance(FakeAgent(), Agent)
    assert isinstance(FakeLLM(), LLMClient)
    assert isinstance(FakeRanker(), Ranker)
    assert isinstance(InMemoryStore(), MemoryStore)


def test_constraint_check() -> None:
    h = HotelCandidate(hotel_id="x", name="x", price_total=5000, amenities=["WiFi"],
                       source_tool="t", source_call_id="c")
    assert check(Constraint(key="budget_total", op="<=", value=8000, source="user"), h) is True
    assert check(Constraint(key="has_amenity", op="contains", value="wifi", source="user"), h) is True
    assert check(Constraint(key="stars", op="==", value=4, source="user"), h) is None


@pytest.mark.asyncio
async def test_fake_agent_modes() -> None:
    run = await FakeAgent().run("Хочу в Сочи на неделю", "s1")
    assert run.answer.mode.value == "plan" and run.answer.candidates
    run = await FakeAgent().run("Хочу куда-нибудь", "s1")
    assert run.answer.mode.value == "clarify"


@pytest.mark.asyncio
async def test_chat_sse() -> None:
    app.state.agent = build_agent()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/health")).json()["status"] == "ok"
        events = []
        async with client.stream("POST", "/chat", json={"text": "Питер на выходные", "session_id": "s1"}) as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            async for line in r.aiter_lines():
                if line.startswith("data: "):
                    events.append(AgentEvent(**json.loads(line[6:])))
        assert events[0].type == "plan" and events[-1].type == "answer"
