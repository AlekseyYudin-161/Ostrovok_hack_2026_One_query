"""Бейзлайн-агент на LangGraph, шаги 1–2 (DesignDoc §5).

Граф:  understand → clarify | search → offer → confirm (interrupt) → purchase
                                     └→ no_solution
Шаг 1: разбор запроса LLM по JSON-схеме (`structured()`), уточнение, поиск, ответ.
Шаг 2: пауза перед покупкой (interrupt) и ответ «да/нет» тем же `/chat` с тем же session_id;
       демо-оплата; дети и животные распознаются и показываются как допущения, пока поиск их не фильтрует.
Пока фейковое: каталог районов (AREAS — до `data/areas.yaml` от ML) и поиск (FakeHotelSearch по fixtures).

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
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

from harness.contracts import (
    AgentAnswer,
    AgentEvent,
    AgentMode,
    AgentRun,
    Assumption,
    HotelCandidate,
    LLMClient,
    TripRequest,
)
from harness.fakes import FakeHotelSearch, FakeLLM, FakeRanker, HotelSearchArgs
from harness.llm import build_llm, structured

log = logging.getLogger(__name__)

# Районы Большого Сочи (ADR-011). TODO(ml): брать из data/areas.yaml, когда он появится в dev.
AREAS = ("Сочи-центр", "Адлер", "Хоста", "Сириус", "Красная Поляна")
AREA_KEYWORDS = {"Сочи-центр": ["сочи"], "Адлер": ["адлер"], "Красная Поляна": ["поляна", "горы", "лыж"],
                 "Сириус": ["сириус"], "Хоста": ["хоста"]}
# в fixtures/hotels.json все отели Большого Сочи записаны городом «Сочи»
FIXTURE_CITY = "Сочи"
PET_WORDS = ("собак", "кошк", "кот ", "котом", "питом", "животн", "пёс", "пес ")
KID_WORDS = ("ребён", "ребен", "дет", "сын", "дочь", "дочк")
YES_WORDS = {"да", "ага", "давай", "ок", "окей", "yes", "конечно", "согласен", "согласна"}
YES_PREFIXES = ("оформ", "подтвер", "бронир", "покупа", "оплач")
NO_WORDS = {"нет", "no", "стоп", "отбой"}
NO_PREFIXES = ("отмен",)
MAX_REPLY_WORDS = 4                 # «да, оформляй» — ответ; длинная фраза — уже новый запрос

class Understood(BaseModel):
    """Что LLM обязана вернуть из текста пользователя. Только факты из текста, без догадок."""

    area: Literal[AREAS] | None = Field(description="район, если назван или однозначно следует из текста")
    budget_total: int | None = Field(description="бюджет на всю поездку в рублях")
    nights: int | None = Field(description="число ночей; «неделя» = 7")
    adults: int | None = Field(description="число взрослых, только если названо явно («двое взрослых», «вдвоём»); иначе null")
    with_kids: bool
    pets: bool = Field(description="едут с животным (собака, кошка и т.п.)")
    month: int | None = Field(description="номер месяца поездки 1–12")


SYSTEM = ("Ты разбираешь запрос путешественника про Большой Сочи. Заполни поля только тем, что прямо сказано "
          "в тексте; чего нет — null. «до 60 тыс» = 60000. Район — строго один из: " + ", ".join(AREAS) + ".")


# ---------- утилиты текста ----------
def plural(n: int, one: str, few: str, many: str) -> str:
    """plural(1,'вариант','варианта','вариантов') → 'вариант'; 3 → 'варианта'; 5, 11, 12 → 'вариантов'."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def rub(x: float) -> str:
    """42000.0 → '42 000 ₽'."""
    return f"{round(x):,}".replace(",", " ") + " ₽"


def yes_no(text: str) -> bool | None:
    """Короткий ответ на «Оформить?»: True / False; None — не похоже на ответ (значит, это новый запрос)."""
    words = re.sub(r"[^\w\s]", " ", text.lower()).split()
    if not words or len(words) > MAX_REPLY_WORDS:
        return None
    joined = " ".join(words)
    if "не надо" in joined or "не нужно" in joined or any(w in NO_WORDS or w.startswith(NO_PREFIXES) for w in words):
        return False
    if any(w in YES_WORDS or w.startswith(YES_PREFIXES) for w in words):
        return True
    return None


# ---------- разбор запроса ----------
def rules_parse(text: str) -> Understood:
    """Офлайн-разбор для LLM_PROVIDER=fake и запасной путь, если LLM не ответила по схеме."""
    low = text.lower()
    area = next((a for a, keys in AREA_KEYWORDS.items() if any(k in low for k in keys)), None)
    m = re.search(r"до\s*(\d+)\s*(тыс|к)?", low)
    budget = int(m.group(1)) * (1000 if m.group(2) else 1) if m else None
    nights = 7 if "недел" in low else None
    return Understood(area=area, budget_total=budget, nights=nights, adults=None,
                      with_kids=any(k in low for k in KID_WORDS),
                      pets=any(k in f"{low} " for k in PET_WORDS), month=None)


async def understand_text(llm: LLMClient, text: str) -> Understood:
    if isinstance(llm, FakeLLM):
        return rules_parse(text)
    try:
        return await structured(llm, [{"role": "system", "content": SYSTEM},
                                      {"role": "user", "content": text}], Understood)
    except Exception:
        log.warning("LLM не разобрала запрос — откат на правила", exc_info=True)
        return rules_parse(text)


def assumptions_for(u: Understood) -> list[Assumption]:
    """Что агент понял, но пока не может учесть в поиске, — показываем, а не молчим (итерация №1)."""
    out = []
    if u.with_kids:
        out.append(Assumption(field="children", value=True,
                              reason="Учёл: едете с ребёнком. Фильтр «подходит для детей» пока не поддерживается."))
    if u.pets:
        out.append(Assumption(field="pets", value=True,
                              reason="Учёл: едете с животным. Фильтр «можно с животными» пока не поддерживается — "
                                     "уточните у отеля перед бронью."))
    return out


def to_trip_request(text: str, u: Understood) -> TripRequest:
    return TripRequest(raw_text=text, destination=u.area, budget_total=u.budget_total, nights=u.nights,
                       guests_adults=u.adults, month=u.month, children_ages=[], pets=u.pets,
                       preferences=["с детьми"] if u.with_kids else [],
                       assumptions=assumptions_for(u),
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
        return {"request": to_trip_request(state["text"], u).model_dump(mode="json"), "hotels": []}

    @staticmethod
    def _route_understand(state: State) -> str:
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

    @staticmethod
    def _route_search(state: State) -> str:
        return "offer" if state.get("hotels") else "no_solution"

    async def _no_solution(self, state: State) -> State:
        req = TripRequest(**state["request"])
        limit = f" до {rub(req.budget_total)}" if req.budget_total else ""
        answer = AgentAnswer(mode=AgentMode.NO_SOLUTION, assumptions=req.assumptions,
                             text=f"{req.destination}: нет вариантов{limit}. "
                                  "Поднять бюджет или посмотреть другой район?")
        return {"answer": answer.model_dump(mode="json")}

    async def _offer(self, state: State) -> State:
        """План + вопрос «оформить?». Сам вопрос задаёт следующий узел через interrupt."""
        req = TripRequest(**state["request"])
        hotels = [HotelCandidate(**h) for h in state["hotels"]]
        best = hotels[0]
        n = len(hotels)
        lines = [f"{req.destination}: нашёл {n} {plural(n, 'вариант', 'варианта', 'вариантов')} (демо-данные)."]
        lines += [a.reason for a in req.assumptions]
        lines.append(f"Оформить «{best.name}» за {rub(best.price_total or 0)}? Ответьте «да» или «нет» (демо-оплата).")
        pending = {"tool": "purchase_mock", "hotel_id": best.hotel_id, "name": best.name,
                   "price_total": best.price_total, "source_call_id": best.source_call_id}
        answer = AgentAnswer(mode=AgentMode.CONFIRM_ACTION, text="\n".join(lines), candidates=hotels[:3],
                             assumptions=req.assumptions, pending_action=pending)
        return {"answer": answer.model_dump(mode="json")}

    async def _confirm(self, state: State) -> State:
        """Пауза до ответа пользователя. Покупку вызывает только код и только после явного «да» (DesignDoc §10)."""
        reply = interrupt(state["answer"]["pending_action"])
        decision = yes_no(str(reply))
        log.info("confirm: ответ %r → %s", reply, decision)
        if decision:
            return {}
        answer = AgentAnswer(mode=AgentMode.PLAN, candidates=[HotelCandidate(**h) for h in state["hotels"][:3]],
                             text="Хорошо, не оформляю. Можно изменить район, бюджет или даты.")
        return {"answer": answer.model_dump(mode="json")}

    @staticmethod
    def _route_confirm(state: State) -> str:
        return "purchase" if state["answer"]["mode"] == AgentMode.CONFIRM_ACTION.value else END

    async def _purchase(self, state: State) -> State:
        """Демо-оплата: сверяем то, что подтвердил пользователь, с тем, что предлагали (никаких новых цен)."""
        pending = state["answer"]["pending_action"]
        offered = {h["hotel_id"]: h for h in state["hotels"]}
        hotel = offered.get(pending["hotel_id"])
        if hotel is None or hotel["price_total"] != pending["price_total"]:
            answer = AgentAnswer(mode=AgentMode.NO_SOLUTION, text="Предложение изменилось — давайте подберу заново.",
                                 violations=["pending_action не совпал с выдачей"])
            return {"answer": answer.model_dump(mode="json")}
        ref = "DEMO-" + uuid.uuid4().hex[:8].upper()
        answer = AgentAnswer(mode=AgentMode.PURCHASED, candidates=[HotelCandidate(**hotel)],
                             text=f"Готово (демо): «{hotel['name']}» за {rub(hotel['price_total'])}. "
                                  f"Номер брони {ref}. Реальной оплаты не было.",
                             plan={"booking_ref": ref})
        log.info("purchase: %s %s", ref, hotel["hotel_id"])
        return {"answer": answer.model_dump(mode="json")}

    def _build(self):
        b = StateGraph(State)
        for name, fn in [("understand", self._understand), ("clarify", self._clarify), ("search", self._search),
                         ("no_solution", self._no_solution), ("offer", self._offer), ("confirm", self._confirm),
                         ("purchase", self._purchase)]:
            b.add_node(name, fn)
        b.add_edge(START, "understand")
        b.add_conditional_edges("understand", self._route_understand, {"search": "search", "clarify": "clarify"})
        b.add_conditional_edges("search", self._route_search, {"offer": "offer", "no_solution": "no_solution"})
        b.add_edge("offer", "confirm")
        b.add_conditional_edges("confirm", self._route_confirm, {"purchase": "purchase", END: END})
        for last in ("clarify", "no_solution", "purchase"):
            b.add_edge(last, END)
        return b.compile(checkpointer=InMemorySaver())

    # ---------- Protocol Agent ----------
    def _config(self, session_id: str) -> dict:
        cfg: dict = {"configurable": {"thread_id": session_id}}
        if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_TRACING_ENABLED", "true") != "false":
            from langfuse.langchain import CallbackHandler  # импорт здесь: без ключей Langfuse не нужен

            cfg["callbacks"] = [CallbackHandler()]
        return cfg

    async def _graph_input(self, text: str, cfg: dict) -> dict | Command:
        """Если граф ждёт «да/нет» и пользователь ответил да/нет — продолжаем паузу, иначе — новый запрос."""
        snapshot = await self.graph.aget_state(cfg)
        if snapshot.next == ("confirm",) and yes_no(text) is not None:
            return Command(resume=text)
        return {"text": text}

    async def stream(self, text: str, session_id: str) -> AsyncIterator[AgentEvent]:
        run_id = uuid.uuid4().hex[:12]

        def ev(type_: str, node: str | None = None, **payload) -> AgentEvent:
            return AgentEvent(run_id=run_id, ts=datetime.now(UTC), type=type_, node=node, payload=payload)

        cfg = self._config(session_id)
        graph_input = await self._graph_input(text, cfg)
        resumed = isinstance(graph_input, Command)
        yield ev("plan", "graph", resumed=resumed,
                 steps=["confirm", "purchase"] if resumed else ["understand", "clarify|search", "offer", "confirm"])
        request: dict | None = None
        if resumed:
            request = (await self.graph.aget_state(cfg)).values.get("request")
        held: AgentEvent | None = None  # ответ «оформить?» отдаём после события cart — ответ всегда последний
        async for update in self.graph.astream(graph_input, cfg, stream_mode="updates"):
            for node, out in update.items():
                if node == "__interrupt__":
                    yield ev("cart", "confirm", pending_action=out[0].value)
                    if held is not None:
                        yield held
                        held = None
                    continue
                out = out or {}
                if "request" in out:
                    request = out["request"]
                    yield ev("node_start", node, request=request)
                if out.get("hotels"):
                    yield ev("tool_result", node, tool=self.search_tool.name, ok=True, n=len(out["hotels"]))
                if "answer" in out:
                    mode = out["answer"]["mode"]
                    if mode == AgentMode.CLARIFY.value:
                        yield ev("clarify", node, questions=out["answer"]["questions"])
                    if mode == AgentMode.PURCHASED.value:
                        yield ev("purchase", node, booking_ref=(out["answer"].get("plan") or {}).get("booking_ref"))
                    answer_ev = ev("answer", node, answer=out["answer"], request=request)
                    if mode == AgentMode.CONFIRM_ACTION.value and node == "offer":
                        held = answer_ev
                    else:
                        yield answer_ev
        if held is not None:
            yield held

    async def run(self, text: str, session_id: str) -> AgentRun:
        started = time.perf_counter()
        last_answer: AgentEvent | None = None
        async for event in self.stream(text, session_id):
            log.debug("event %s %s", event.type, event.node)
            if event.type == "answer":
                last_answer = event
        assert last_answer is not None
        return AgentRun(run_id=last_answer.run_id, session_id=session_id,
                        request=TripRequest(**last_answer.payload["request"]),
                        answer=AgentAnswer(**last_answer.payload["answer"]),
                        tokens_in=0, tokens_out=0, cost=0.0,  # TODO(lead): из LLMResult/Langfuse
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        pressure_max=0.0, replans=0)
