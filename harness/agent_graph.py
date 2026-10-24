"""Бейзлайн-агент на LangGraph, шаг 1: understand → clarify_gate → search → answer.

Что уже настоящее: разбор запроса LLM по JSON-схеме (`structured()`), граф с состоянием по session_id,
поток AgentEvent в том же формате, что у FakeAgent (UI и eval не меняются).
Что пока фейковое: каталог районов (AREAS — до `data/areas.yaml` от ML) и поиск (FakeHotelSearch по fixtures).
Следующие узлы по DesignDoc §5: inspire, compare, assemble, check_connections, explain, confirm (interrupt), purchase.

Включение: AGENT_BACKEND=graph. Модель — по переменным LLM_* (`harness/llm.py`); при LLM_PROVIDER=fake
разбор идёт по правилам, без сети и без денег.
"""
from __future__ import annotations

import logging
import os
import re
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from harness.contracts import (
    AgentAnswer,
    AgentEvent,
    AgentMode,
    AgentRun,
    HotelCandidate,
    LLMClient,
    TripRequest,
)
from harness.fakes import FakeHotelSearch, FakeLLM, FakeRanker, HotelSearchArgs
from harness.llm import build_llm, structured

log = logging.getLogger(__name__)

# Районы Большого Сочи (ADR-011). TODO(ml): брать из data/areas.yaml, когда он появится в dev.
AREAS = ("Сочи-центр", "Адлер", "Хоста", "Сириус", "Красная Поляна")
AREA_KEYWORDS = {"Сочи-центр": ["центр", "сочи"], "Адлер": ["адлер"], "Красная Поляна": ["поляна", "горы", "лыж"],
                 "Сириус": ["сириус"], "Хоста": ["хоста"]}
FIXTURE_CITY = "Сочи"  # в fixtures/hotels.json все отели Большого Сочи записаны городом «Сочи»


class Understood(BaseModel):
    """Что LLM обязана вернуть из текста пользователя. Только факты из текста, без догадок."""

    area: Literal[AREAS] | None = Field(description="район, если назван или однозначно следует из текста")
    budget_total: int | None = Field(description="бюджет на всю поездку в рублях")
    nights: int | None = Field(description="число ночей; «неделя» = 7")
    adults: int | None
    with_kids: bool
    month: int | None = Field(description="номер месяца поездки 1–12")


SYSTEM = ("Ты разбираешь запрос путешественника про Большой Сочи. Заполни поля только тем, что прямо сказано "
          "в тексте; чего нет — null. «до 60 тыс» = 60000. Район — строго один из: " + ", ".join(AREAS) + ".")


def rules_parse(text: str) -> Understood:
    """Офлайн-разбор для LLM_PROVIDER=fake и как запасной путь, если LLM не ответила по схеме."""
    low = text.lower()
    area = next((a for a, keys in AREA_KEYWORDS.items() if any(k in low for k in keys)), None)
    m = re.search(r"до\s*(\d+)\s*(тыс|к)?", low)
    budget = int(m.group(1)) * (1000 if m.group(2) else 1) if m else None
    nights = 7 if "недел" in low else None
    return Understood(area=area, budget_total=budget, nights=nights, adults=None,
                      with_kids=any(k in low for k in ("ребён", "дет")), month=None)


async def understand_text(llm: LLMClient, text: str) -> Understood:
    if isinstance(llm, FakeLLM):
        return rules_parse(text)
    try:
        return await structured(llm, [{"role": "system", "content": SYSTEM},
                                      {"role": "user", "content": text}], Understood)
    except Exception:
        log.warning("LLM не разобрала запрос — откат на правила", exc_info=True)
        return rules_parse(text)


def to_trip_request(text: str, u: Understood) -> TripRequest:
    return TripRequest(raw_text=text, destination=u.area, budget_total=u.budget_total, nights=u.nights,
                       guests_adults=u.adults, month=u.month, children_ages=[],
                       preferences=["с детьми"] if u.with_kids else [],
                       missing_fields=[] if u.area else ["destination"])


class State(TypedDict, total=False):
    text: str
    request: dict
    hotels: list[dict]
    answer: dict


class GraphAgent:
    """Реализует Protocol Agent (harness/contracts.py)."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or build_llm()
        self.search_tool = FakeHotelSearch()
        self.ranker = FakeRanker()
        self.graph = self._build()

    # ---------- узлы ----------
    async def _understand(self, state: State) -> State:
        u = await understand_text(self.llm, state["text"])
        log.info("understand: %s", u.model_dump())
        return {"request": to_trip_request(state["text"], u).model_dump(mode="json")}

    @staticmethod
    def _route(state: State) -> str:
        return "search" if state["request"].get("destination") else "clarify"

    async def _clarify(self, state: State) -> State:
        answer = AgentAnswer(mode=AgentMode.CLARIFY,
                             text="Что ближе: море (Адлер, Сочи-центр), горы (Красная Поляна), "
                                  "парки для детей (Сириус) или тихо и недорого (Хоста)?",
                             questions=["Какой район или формат отдыха?"])
        return {"answer": answer.model_dump(mode="json")}

    async def _search(self, state: State) -> State:
        req = TripRequest(**state["request"])
        result = await self.search_tool(HotelSearchArgs(city=FIXTURE_CITY, max_price_total=req.budget_total))
        hotels = self.ranker.rank(req, [HotelCandidate(**h) for h in result.data or []])
        log.info("search: %d отелей (бюджет %s)", len(hotels), req.budget_total)
        return {"hotels": [h.model_dump(mode="json") for h in hotels]}

    async def _answer(self, state: State) -> State:
        req = TripRequest(**state["request"])
        hotels = [HotelCandidate(**h) for h in state.get("hotels", [])]
        if hotels:
            answer = AgentAnswer(mode=AgentMode.PLAN, candidates=hotels[:3],
                                 text=f"{req.destination}: нашёл {len(hotels)} вариантов (демо-данные).")
        else:
            limit = f" до {int(req.budget_total)} ₽" if req.budget_total else ""
            answer = AgentAnswer(mode=AgentMode.NO_SOLUTION,
                                 text=f"{req.destination}: нет вариантов{limit}. "
                                      "Поднять бюджет или посмотреть другой район?")
        return {"answer": answer.model_dump(mode="json")}

    def _build(self):
        b = StateGraph(State)
        for name, fn in [("understand", self._understand), ("clarify", self._clarify),
                         ("search", self._search), ("answer", self._answer)]:
            b.add_node(name, fn)
        b.add_edge(START, "understand")
        b.add_conditional_edges("understand", self._route, {"search": "search", "clarify": "clarify"})
        b.add_edge("search", "answer")
        b.add_edge("clarify", END)
        b.add_edge("answer", END)
        return b.compile(checkpointer=InMemorySaver())

    # ---------- Protocol Agent ----------
    def _config(self, session_id: str) -> dict:
        cfg: dict = {"configurable": {"thread_id": session_id}}
        if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_TRACING_ENABLED", "true") != "false":
            from langfuse.langchain import CallbackHandler  # импорт здесь: без ключей Langfuse не нужен

            cfg["callbacks"] = [CallbackHandler()]
        return cfg

    async def stream(self, text: str, session_id: str) -> AsyncIterator[AgentEvent]:
        run_id = uuid.uuid4().hex[:12]

        def ev(type_: str, node: str | None = None, **payload) -> AgentEvent:
            return AgentEvent(run_id=run_id, ts=datetime.now(UTC), type=type_, node=node, payload=payload)

        yield ev("plan", "graph", steps=["understand", "clarify|search", "answer"])
        request: dict | None = None
        async for update in self.graph.astream({"text": text}, self._config(session_id), stream_mode="updates"):
            for node, out in update.items():
                out = out or {}
                if "request" in out:
                    request = out["request"]
                    yield ev("node_start", node, request=request)
                if "hotels" in out:
                    yield ev("tool_result", node, tool=self.search_tool.name, ok=True, n=len(out["hotels"]))
                if "answer" in out:
                    if out["answer"]["mode"] == AgentMode.CLARIFY.value:
                        yield ev("clarify", node, questions=out["answer"]["questions"])
                    yield ev("answer", node, answer=out["answer"], request=request)

    async def run(self, text: str, session_id: str) -> AgentRun:
        started = time.perf_counter()
        last: AgentEvent | None = None
        async for last in self.stream(text, session_id):
            log.debug("event %s %s", last.type, last.node)
        assert last is not None and last.type == "answer"
        return AgentRun(run_id=last.run_id, session_id=session_id,
                        request=TripRequest(**last.payload["request"]),
                        answer=AgentAnswer(**last.payload["answer"]),
                        tokens_in=0, tokens_out=0, cost=0.0,  # TODO(lead): из LLMResult/Langfuse
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        pressure_max=0.0, replans=0)
