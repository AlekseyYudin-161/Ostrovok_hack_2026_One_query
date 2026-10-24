"""Бейзлайн-граф: офлайн (правила) и путь через LLM по схеме (сервер подменён MockTransport)."""
import json

import httpx
import pytest

from app.main import app
from harness.agent_graph import GraphAgent, Understood
from harness.contracts import Agent
from harness.fakes import FakeLLM
from harness.llm import OpenAICompatLLM


def test_protocol() -> None:
    assert isinstance(GraphAgent(FakeLLM()), Agent)


@pytest.mark.asyncio
@pytest.mark.parametrize(("text", "mode", "n"), [
    ("Адлер на неделю, до 50 тыс", "plan", 1),        # 42000 ≤ 50000 < 61000
    ("Хочу куда-нибудь на выходные", "clarify", 0),
    ("Красная Поляна, горы, до 30 тыс", "no_solution", 0),
])
async def test_offline_modes(text: str, mode: str, n: int) -> None:
    run = await GraphAgent(FakeLLM()).run(text, "s-offline")
    assert run.answer.mode.value == mode and len(run.answer.candidates) == n


@pytest.mark.asyncio
async def test_llm_structured_path() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        content = json.dumps({"area": "Сириус", "budget_total": 70000, "nights": 3, "adults": 2,
                              "with_kids": True, "month": 11}, ensure_ascii=False)
        return httpx.Response(200, json={"id": "x", "object": "chat.completion", "created": 0, "model": "m",
                                         "choices": [{"index": 0, "finish_reason": "stop",
                                                      "message": {"role": "assistant", "content": content}}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    llm = OpenAICompatLLM("http://router/v1", "k", "m", extra_body={"reasoning": {"enabled": False}},
                          http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    run = await GraphAgent(llm).run("Двое взрослых и ребёнок, в Сириус на 3 ночи в ноябре, до 70 тыс", "s-llm")
    assert run.request.destination == "Сириус" and run.request.nights == 3 and run.request.month == 11
    assert run.answer.mode.value == "plan" and len(run.answer.candidates) == 2
    assert seen[0]["response_format"]["type"] == "json_schema" and seen[0]["reasoning"] == {"enabled": False}
    enum = seen[0]["response_format"]["json_schema"]["schema"]["properties"]["area"]["anyOf"][0]["enum"]
    assert "Красная Поляна" in enum


@pytest.mark.asyncio
async def test_bad_llm_falls_back_to_rules() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "x", "object": "chat.completion", "created": 0, "model": "m",
                                         "choices": [{"index": 0, "finish_reason": "stop",
                                                      "message": {"role": "assistant", "content": "не JSON"}}]})

    llm = OpenAICompatLLM("http://router/v1", "k", "m",
                          http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    run = await GraphAgent(llm).run("Хоста до 50 тыс", "s-bad")
    assert run.request.destination == "Хоста"


def test_schema_is_strict_friendly() -> None:
    schema = Understood.model_json_schema()
    assert set(schema["required"]) == set(schema["properties"])  # strict требует все поля обязательными


@pytest.mark.asyncio
async def test_chat_sse_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    app.state.agent = GraphAgent()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/chat", json={"text": "Адлер до 50 тыс", "session_id": "s1"})
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    assert events[0]["type"] == "plan" and events[-1]["type"] == "answer"
    assert events[-1]["payload"]["answer"]["mode"] == "plan"
