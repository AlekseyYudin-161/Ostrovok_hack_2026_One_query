"""Фейки для каждого интерфейса из contracts.py.

Правило команды: пока настоящая реализация не влита в main, все работают против этих фейков.
Фейки детерминированы — на них гоняются CI и eval-smoke.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel

from harness.contracts import (
    AgentAnswer,
    AgentEvent,
    AgentMode,
    AgentRun,
    HotelCandidate,
    LLMResult,
    MemoryItem,
    ToolResult,
    TripRequest,
)

log = logging.getLogger(__name__)
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _now() -> datetime:
    return datetime.now(UTC)


class FakeLLM:
    """Отвечает шаблоном; токены считает по длине, чтобы бюджет и трейсинг было на чём проверить."""

    async def complete(self, messages: list[dict], tools: list[dict] | None = None,
                       schema: dict | None = None) -> LLMResult:
        last = messages[-1]["content"] if messages else ""
        return LLMResult(text=f"[fake] {last[:80]}", tokens_in=len(str(messages)) // 4,
                         tokens_out=20, model="fake-llm")


class HotelSearchArgs(BaseModel):
    city: str | None = None
    max_price_total: float | None = None


class FakeHotelSearch:
    """Поиск по fixtures/hotels.json."""

    name = "fake_hotel_search"
    side_effect: Literal["none", "reversible", "irreversible"] = "none"
    args_model = HotelSearchArgs

    def __init__(self, path: Path = FIXTURES / "hotels.json") -> None:
        self._hotels = [HotelCandidate(**h) for h in json.loads(path.read_text(encoding="utf-8"))]

    async def __call__(self, args: BaseModel) -> ToolResult:
        a = HotelSearchArgs.model_validate(args.model_dump())
        call_id = uuid.uuid4().hex[:8]
        started = time.perf_counter()
        found = [
            h.model_copy(update={"source_call_id": call_id})
            for h in self._hotels
            if (a.city is None or (h.city or "").lower() == a.city.lower())
            and (a.max_price_total is None or (h.price_total or 0) <= a.max_price_total)
        ]
        return ToolResult(tool=self.name, call_id=call_id, ok=True,
                          data=[h.model_dump() for h in found],
                          latency_ms=int((time.perf_counter() - started) * 1000))


class FakeRanker:
    """Сортировка по цене (по возрастанию)."""

    def rank(self, request: TripRequest, candidates: list[HotelCandidate]) -> list[HotelCandidate]:
        return sorted(candidates, key=lambda h: h.price_total or float("inf"))


class InMemoryStore:
    def __init__(self) -> None:
        self._items: dict[str, list[MemoryItem]] = {}

    async def put(self, session_id: str, item: MemoryItem) -> None:
        self._items.setdefault(session_id, []).append(item)

    async def search(self, session_id: str, query: str, k: int = 5) -> list[MemoryItem]:
        q = set(query.lower().split())
        items = self._items.get(session_id, [])
        scored = [(len(q & set(i.text.lower().split())), i) for i in items]
        return [i for s, i in sorted(scored, key=lambda x: -x[0]) if s > 0][:k]


class FakeAgent:
    """Агент-заглушка: город ищется подстрокой, дальше — FakeHotelSearch + FakeRanker.

    Даёт тот же поток AgentEvent, что будет у настоящего агента, — на нём работают UI и eval.
    """

    CITIES: ClassVar[dict[str, str]] = {"сочи": "Сочи", "питер": "Санкт-Петербург", "петербург": "Санкт-Петербург", "казан": "Казань"}

    def __init__(self) -> None:
        self.search = FakeHotelSearch()
        self.ranker = FakeRanker()

    def _parse(self, text: str) -> TripRequest:
        low = text.lower()
        city = next((v for k, v in self.CITIES.items() if k in low), None)
        return TripRequest(raw_text=text, destination=city,
                           missing_fields=[] if city else ["destination"])

    async def stream(self, text: str, session_id: str) -> AsyncIterator[AgentEvent]:
        run_id = uuid.uuid4().hex[:12]

        def ev(type_: str, node: str | None = None, **payload) -> AgentEvent:
            return AgentEvent(run_id=run_id, ts=_now(), type=type_, node=node, payload=payload)

        request = self._parse(text)
        yield ev("plan", "planner", steps=["parse", "search", "rank", "answer"])
        if request.missing_fields:
            answer = AgentAnswer(mode=AgentMode.CLARIFY, text="Куда хотите поехать?",
                                 questions=["Город или регион?"])
            yield ev("answer", "answer", answer=answer.model_dump(mode="json"), request=request.model_dump(mode="json"))
            return
        yield ev("tool_call", "search", tool=self.search.name, args={"city": request.destination})
        result = await self.search(HotelSearchArgs(city=request.destination))
        yield ev("tool_result", "search", ok=result.ok, n=len(result.data or []))
        hotels = self.ranker.rank(request, [HotelCandidate(**h) for h in result.data or []])
        mode = AgentMode.PLAN if hotels else AgentMode.NO_SOLUTION
        answer = AgentAnswer(mode=mode, text=f"Нашёл {len(hotels)} вариантов в {request.destination}.",
                             candidates=hotels[:3])
        yield ev("answer", "answer", answer=answer.model_dump(mode="json"), request=request.model_dump(mode="json"))

    async def run(self, text: str, session_id: str) -> AgentRun:
        started = time.perf_counter()
        last: AgentEvent | None = None
        async for last in self.stream(text, session_id):
            log.debug("event %s %s", last.type, last.node)
        assert last is not None and last.type == "answer"
        return AgentRun(
            run_id=last.run_id, session_id=session_id,
            request=TripRequest(**last.payload["request"]),
            answer=AgentAnswer(**last.payload["answer"]),
            tokens_in=0, tokens_out=0, cost=0.0,
            latency_ms=int((time.perf_counter() - started) * 1000),
            pressure_max=0.0, replans=0,
        )
