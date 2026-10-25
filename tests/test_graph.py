"""Бейзлайн-граф (шаги 1–2): офлайн на правилах, путь через LLM по схеме, пауза перед покупкой."""

import json

import httpx
import pytest

from app.main import app
from harness.agent_graph import GraphAgent, Understood, plural, rub, yes_no
from harness.contracts import Agent
from harness.fakes import FakeLLM
from harness.llm import OpenAICompatLLM


def _completion(content: str) -> dict:
    return {"id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


def test_protocol() -> None:
    assert isinstance(GraphAgent(FakeLLM()), Agent)


def test_text_helpers() -> None:
    assert [plural(n, "вариант", "варианта", "вариантов") for n in (1, 3, 5, 11, 21)] == \
        ["вариант", "варианта", "вариантов", "вариантов", "вариант"]
    assert rub(42000.0) == "42 000 ₽"
    assert yes_no("Да, оформляй!") is True and yes_no("не надо") is False
    assert yes_no("Адлер, дальше") is None and yes_no("давай в Хосту до 30 тыс на неделю") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("text", "mode", "n"), [
    ("Адлер на неделю, до 50 тыс", "confirm_action", 1),   # 42000 ≤ 50000 < 61000
    ("Хочу куда-нибудь на выходные", "clarify", 0),
    ("Красная Поляна, горы, до 30 тыс", "no_solution", 0),
])
async def test_offline_modes(text: str, mode: str, n: int) -> None:
    run = await GraphAgent(FakeLLM()).run(text, "s-offline")
    assert run.answer.mode.value == mode and len(run.answer.candidates) == n


@pytest.mark.asyncio
async def test_confirm_yes_buys() -> None:
    agent = GraphAgent(FakeLLM())
    first = await agent.run("Адлер на неделю, до 50 тыс", "s-yes")
    assert first.answer.mode.value == "confirm_action" and "42 000 ₽" in first.answer.text
    assert "1 вариант " in first.answer.text
    second = await agent.run("да", "s-yes")
    assert second.answer.mode.value == "purchased" and second.answer.plan["booking_ref"].startswith("DEMO-")
    assert second.request.destination == "Адлер"


@pytest.mark.asyncio
async def test_confirm_no_cancels_and_new_request_works() -> None:
    agent = GraphAgent(FakeLLM())
    await agent.run("Адлер до 50 тыс", "s-no")
    cancelled = await agent.run("нет", "s-no")
    assert cancelled.answer.mode.value == "plan" and "не оформляю" in cancelled.answer.text
    again = await agent.run("Хоста до 70 тыс", "s-no")
    assert again.answer.mode.value == "confirm_action" and again.request.destination == "Хоста"


@pytest.mark.asyncio
async def test_new_request_instead_of_answer() -> None:
    agent = GraphAgent(FakeLLM())
    await agent.run("Адлер до 50 тыс", "s-switch")
    run = await agent.run("Красная Поляна, горы, до 30 тыс", "s-switch")   # не «да/нет» → новый запрос
    assert run.answer.mode.value == "no_solution" and run.request.destination == "Красная Поляна"


@pytest.mark.asyncio
async def test_kids_and_pets_become_visible_assumptions() -> None:
    run = await GraphAgent(FakeLLM()).run("Адлер на неделю, до 50 тыс, с ребенком и собакой", "s-pets")
    fields = {a.field for a in run.answer.assumptions}
    assert fields == {"children", "pets"} and run.request.pets is True
    assert "животн" in run.answer.text and "ребёнк" in run.answer.text


@pytest.mark.asyncio
async def test_llm_structured_path() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_completion(json.dumps(
            {"area": "Сириус", "budget_total": 70000, "nights": 3, "adults": 2, "with_kids": True,
             "pets": False, "month": 11}, ensure_ascii=False)))

    llm = OpenAICompatLLM("http://router/v1", "k", "m", extra_body={"reasoning": {"enabled": False}},
                          http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    run = await GraphAgent(llm).run("Двое взрослых и ребёнок, в Сириус на 3 ночи в ноябре, до 70 тыс", "s-llm")
    assert run.request.destination == "Сириус" and run.request.nights == 3 and run.request.month == 11
    assert run.answer.mode.value == "confirm_action" and len(run.answer.candidates) == 2
    assert seen[0]["response_format"]["type"] == "json_schema" and seen[0]["reasoning"] == {"enabled": False}
    props = seen[0]["response_format"]["json_schema"]["schema"]["properties"]
    assert "Красная Поляна" in props["area"]["anyOf"][0]["enum"] and "pets" in props


@pytest.mark.asyncio
async def test_bad_llm_falls_back_to_rules() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion("не JSON"))

    llm = OpenAICompatLLM("http://router/v1", "k", "m",
                          http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    run = await GraphAgent(llm).run("Хоста до 50 тыс", "s-bad")
    assert run.request.destination == "Хоста"


def test_schema_is_strict_friendly() -> None:
    schema = Understood.model_json_schema()
    assert set(schema["required"]) == set(schema["properties"])  # strict требует все поля обязательными


@pytest.mark.asyncio
async def test_chat_sse_two_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    app.state.agent = GraphAgent()
    transport = httpx.ASGITransport(app=app)

    async def turn(c: httpx.AsyncClient, text: str) -> list[dict]:
        r = await c.post("/chat", json={"text": text, "session_id": "s-sse"})
        return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]

    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        first = await turn(c, "Адлер до 50 тыс")
        second = await turn(c, "да")
    assert first[0]["type"] == "plan" and "cart" in [e["type"] for e in first]
    assert first[-1]["type"] == "answer" and first[-1]["payload"]["answer"]["mode"] == "confirm_action"
    assert "purchase" in [e["type"] for e in second]
    assert second[-1]["payload"]["answer"]["mode"] == "purchased"
