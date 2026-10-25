"""Бейзлайн-агент на LangGraph, шаги 1–3 + пересборка (DesignDoc §5, L11, L21).

Граф:  understand → clarify
                  → out_of_scope → suggest_confirm (interrupt) → search | END      (вне Сочи, ADR-019)
                  → search → offer → confirm (interrupt) → purchase
                           └→ no_solution
Шаг 1: разбор запроса LLM по JSON-схеме (`structured()`), уточнение, поиск, ответ.
Шаг 2: пауза перед покупкой (interrupt) и ответ «да/нет» тем же `/chat` с тем же session_id;
       демо-оплата; дети и животные распознаются и показываются как допущения, пока поиск их не фильтрует.
Шаг 3: запрос вне Большого Сочи → «работаем только по Сочи» + похожий район; даты (в т.ч. «на выходные»,
       «с 1 ноября» — от сегодняшней даты), взрослые, дети с возрастом; бюджет = проживание за весь срок на всех.
Пересборка: в той же сессии «а если на 2 дня позже / дешевле / с нами собака / а в Хосте?» меняет только названное,
       остальное сохраняется; «было → стало» — в ответе (changes). Правка из панели — `patch` (контракт 1.2), без LLM.
Районы — из данных ML (`synth/data/areas.json`), пока их нет — встроенные 5 (`harness/areas.py`).
Пока фейковое: поиск (FakeHotelSearch по fixtures).

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
from datetime import UTC, date, datetime, timedelta
from typing import Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

from harness.areas import WISHES, Area, find_area, find_by_name, load_areas, suggest_area
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
from harness.dates import WEEKDAYS_RU, month_from_text, parse_dates, parse_shift
from harness.fakes import FakeHotelSearch, FakeLLM, FakeRanker, HotelSearchArgs
from harness.llm import build_llm, structured

log = logging.getLogger(__name__)

# Районы Большого Сочи (ADR-011): из данных ML или встроенные 5 (harness/areas.py)
AREA_LIST: list[Area] = load_areas()
AREAS = tuple(a.name for a in AREA_LIST)
FIXTURE_CITY = "Сочи"  # в fixtures/hotels.json все отели Большого Сочи записаны городом «Сочи»
PET_WORDS = ("собак", "кошк", "кот ", "котом", "питом", "животн", "пёс", "пес ")
KID_WORDS = ("ребён", "ребен", "дет", "сын", "дочь", "дочк")
YES_WORDS = {"да", "ага", "давай", "ок", "окей", "yes", "конечно", "согласен", "согласна"}
YES_PREFIXES = ("оформ", "подтвер", "бронир", "покупа", "оплач")
NO_WORDS = {"нет", "no", "стоп", "отбой"}
NO_PREFIXES = ("отмен",)
MAX_REPLY_WORDS = 4  # «да, оформляй» — ответ; длинная фраза — уже новый запрос
# Места вне Большого Сочи для офлайн-правил (LLM распознаёт любые); «из Питера» — это город вылета, не цель
OTHER_PLACES = {"турц": "Турция", "египет": "Египет", "дубай": "Дубай", "оаэ": "ОАЭ", "таиланд": "Таиланд",
                "крым": "Крым", "анап": "Анапа", "геленджик": "Геленджик", "калининград": "Калининград",
                "питер": "Санкт-Петербург", "петербург": "Санкт-Петербург", "абхаз": "Абхазия", "грузи": "Грузия"}
WISH_WORDS = {"море": ("мор", "пляж"), "горы/лыжи": ("гор", "лыж"), "с детьми": KID_WORDS,
              "тихо": ("тих", "спокойн"), "бюджетно": ("недорог", "бюджет", "дешев"), "тусовка": ("тусов", "клуб"),
              "оздоровление": ("оздоров", "санатор", "спа"), "экскурсии": ("экскурс", "достопр"),
              "активный отдых": ("актив", "поход"), "романтика": ("романт", "медов"), "люкс": ("люкс", "роскош")}

class Understood(BaseModel):
    """Что LLM обязана вернуть из текста пользователя. Только факты из текста, без догадок."""

    area: Literal[AREAS] | None = Field(description="район Большого Сочи, если назван или однозначно следует из текста")
    other_destination: str | None = Field(description="место ВНЕ Большого Сочи, если пользователь хочет туда "
                                                      "(«Турция», «Питер»); город вылета сюда не писать; иначе null")
    wishes: list[Literal[WISHES]] = Field(description="чего хочет от отдыха — только из списка; пусто, если не сказано")
    budget_total: int | None = Field(description="бюджет на проживание за весь срок на всех гостей, в рублях; "
                                                 "«до 60 тыс» = 60000; null, если не назван")
    checkin: str | None = Field(description="дата заезда YYYY-MM-DD, если названа или следует из текста; иначе null")
    checkout: str | None = Field(description="дата выезда YYYY-MM-DD, если названа или следует из текста; иначе null")
    nights: int | None = Field(description="число ночей; «неделя» = 7")
    adults: int | None = Field(description="число взрослых, только если названо явно («двое взрослых», «вдвоём»); иначе null")
    with_kids: bool
    children_ages: list[int] = Field(description="возрасты детей, только если названы; иначе пустой список")
    pets: bool = Field(description="едут с животным (собака, кошка и т.п.)")
    month: int | None = Field(description="номер месяца поездки 1–12")


def system_prompt(today: date) -> str:
    return (f"Сегодня {today:%Y-%m-%d}, {WEEKDAYS_RU[today.weekday()]}. Ты разбираешь запрос путешественника. "
            "Мы работаем только по Большому Сочи. Заполни поля только тем, что прямо сказано в тексте; чего нет — null. "
            "Район — строго один из: " + ", ".join(AREAS) + ". Если человек хочет в другое место — "
            "запиши его в other_destination, а area оставь null. Относительные даты переводи в YYYY-MM-DD от сегодняшней "
            "даты; «на выходные» — ближайшие пятница (заезд) и воскресенье (выезд). Бюджет — на проживание "
            "за весь срок на всех гостей.")


# ---------- утилиты текста ----------
def plural(n: int, one: str, few: str, many: str) -> str:
    """plural(1,'вариант','варианта','вариантов') → 'вариант'; 3 → 'варианта'; 5, 11, 12 → 'вариантов'."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def trip_summary(req: TripRequest) -> str | None:
    """Что агент понял о поездке одной строкой: «01.11–08.11, 7 ночей · 2 взрослых + ребёнок (5 лет)»."""
    when = []
    if req.checkin and req.checkout:
        when.append(f"{req.checkin:%d.%m}–{req.checkout:%d.%m}")
    elif req.checkin:
        when.append(f"с {req.checkin:%d.%m}")
    if req.nights:
        when.append(f"{req.nights} {plural(req.nights, 'ночь', 'ночи', 'ночей')}")
    who = []
    if req.guests_adults:
        who.append(f"{req.guests_adults} {plural(req.guests_adults, 'взрослый', 'взрослых', 'взрослых')}")
    kids = len(req.children_ages)
    if kids or "с детьми" in req.preferences:
        ages = f" ({', '.join(str(a) for a in req.children_ages)} лет)" if kids else ""
        who.append((f"{kids} {plural(kids, 'ребёнок', 'ребёнка', 'детей')}" if kids > 1 else "ребёнок") + ages)
    if req.pets:
        who.append("с животным")
    parts = [", ".join(when), " + ".join(who)]
    text = " · ".join(p for p in parts if p)
    return f"Поездка: {text}." if text else None


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
def detect_other_place(low: str) -> str | None:
    for stem, name in OTHER_PLACES.items():
        i = low.find(stem)
        if i >= 0 and not low[max(0, i - 3):i].endswith("из "):
            return name
    return None


def rules_parse(text: str, today: date) -> Understood:
    """Офлайн-разбор для LLM_PROVIDER=fake и запасной путь, если LLM не ответила по схеме."""
    low = text.lower()
    named = find_by_name(AREA_LIST, low)
    other = None if named else detect_other_place(low)
    area = named or (None if other else find_area(AREA_LIST, low))
    m = re.search(r"(?:до|бюджет)\s*(\d+)\s*(тыс|к)?", low)
    budget = int(m.group(1)) * (1000 if m.group(2) else 1) if m else None
    checkin, checkout, _ = parse_dates(text, today)
    ages = [int(x) for x in re.findall(r"(\d{1,2})\s*(?:лет|год)", low)]
    adults = 2 if re.search(r"\b(вдвоём|вдвоем|двое взрослых|с женой|с мужем)\b", low) else None
    return Understood(area=area.name if area else None, other_destination=other,
                      wishes=[w for w, keys in WISH_WORDS.items() if any(k in low for k in keys)],
                      budget_total=budget, checkin=checkin.isoformat() if checkin else None,
                      checkout=checkout.isoformat() if checkout else None,
                      nights=7 if "недел" in low else None, adults=adults,
                      with_kids=bool(ages) or any(k in low for k in KID_WORDS), children_ages=ages,
                      pets=any(k in f"{low} " for k in PET_WORDS), month=month_from_text(text))


async def understand_text(llm: LLMClient, text: str, today: date) -> Understood:
    if isinstance(llm, FakeLLM):
        return rules_parse(text, today)
    try:
        return await structured(llm, [{"role": "system", "content": system_prompt(today)},
                                      {"role": "user", "content": text}], Understood)
    except Exception:
        log.warning("LLM не разобрала запрос — откат на правила", exc_info=True)
        return rules_parse(text, today)


def _iso(value: str | None, today: date) -> date | None:
    """Дата от LLM: только корректная и не в прошлом."""
    try:
        d = date.fromisoformat(value) if value else None
    except ValueError:
        log.warning("LLM вернула некорректную дату %r", value)
        return None
    return d if d is None or d >= today else None


def request_assumptions(req: TripRequest, notes: list[str | None] = ()) -> list[Assumption]:
    """Что агент понял и как трактует — показываем, а не молчим (итерация №1)."""
    out = []
    if req.budget_total:
        out.append(Assumption(field="budget_total", value=req.budget_total,
                              reason=f"Бюджет {rub(req.budget_total)} считаю на проживание за весь срок для всех гостей; "
                                     "дорогу покажу отдельно."))
    out += [Assumption(field="note", value=n, reason=n) for n in notes if n]
    if req.children_ages or "с детьми" in req.preferences:
        ages = f" ({', '.join(str(a) for a in req.children_ages)} лет)" if req.children_ages else ""
        out.append(Assumption(field="children", value=req.children_ages or True,
                              reason=f"Учёл: едете с ребёнком{ages}. Фильтр «подходит для детей» пока не поддерживается."))
    if req.pets:
        out.append(Assumption(field="pets", value=True,
                              reason="Учёл: едете с животным. Фильтр «можно с животными» пока не поддерживается — "
                                     "уточните у отеля перед бронью."))
    return out


def to_trip_request(text: str, u: Understood, today: date) -> TripRequest:
    checkin, checkout = _iso(u.checkin, today), _iso(u.checkout, today)
    rule_in, rule_out, weekend_note = parse_dates(text, today)
    if checkin is None and rule_in:                  # LLM дат не дала — берём правила
        checkin, checkout = rule_in, checkout or rule_out
    if checkin and checkout and checkout <= checkin:
        checkout = None
    nights = u.nights
    if checkin and checkout:
        nights = (checkout - checkin).days
    elif checkin and nights:
        checkout = checkin + timedelta(days=nights)
    weekend = weekend_note if (checkin, checkout) == (rule_in, rule_out) and weekend_note else None
    req = TripRequest(raw_text=text, destination=u.area, budget_total=u.budget_total, nights=nights,
                       checkin=checkin, checkout=checkout, guests_adults=u.adults,
                       month=u.month or (checkin.month if checkin else None), children_ages=u.children_ages,
                       pets=u.pets, preferences=[*u.wishes, *(["с детьми"] if u.with_kids and "с детьми" not in u.wishes else [])],
                       missing_fields=[] if u.area else ["destination"])
    req.assumptions = request_assumptions(req, [weekend])
    return req


# ---------- пересборка (L11) и правка из панели (L21) ----------
FRESH_RE = re.compile(r"куда-нибудь|куда нибудь|друг(ую|ой|ое) поездк|нов(ую|ый) поиск|с нуля")
CHEAPER_RE = re.compile(r"дешевле|бюджет\s+меньше|меньше\s+бюджет|подешевле")
PATCH_FIELDS = {"destination", "checkin", "checkout", "nights", "guests_adults", "children_ages", "pets", "budget_total"}


def _dates_label(r: TripRequest) -> str:
    if r.checkin and r.checkout:
        return f"{r.checkin:%d.%m}–{r.checkout:%d.%m}"
    return f"{r.nights} ноч." if r.nights else "не заданы"


def describe_changes(old: TripRequest, new: TripRequest) -> list[str]:
    """«Было → стало» по полям, которые видит пользователь."""
    rows = [("Район", old.destination or "—", new.destination or "—"),
            ("Даты", _dates_label(old), _dates_label(new)),
            ("Бюджет", rub(old.budget_total) if old.budget_total else "—",
             rub(new.budget_total) if new.budget_total else "—"),
            ("Взрослых", str(old.guests_adults or "—"), str(new.guests_adults or "—")),
            ("Дети", ", ".join(map(str, old.children_ages)) or ("да" if "с детьми" in old.preferences else "нет"),
             ", ".join(map(str, new.children_ages)) or ("да" if "с детьми" in new.preferences else "нет")),
            ("Животные", "да" if old.pets else "нет", "да" if new.pets else "нет")]
    return [f"{name}: {a} → {b}" for name, a, b in rows if a != b]


def _finish(old: TripRequest, data: dict, notes: list[str | None]) -> tuple[TripRequest, list[str]]:
    """Пересчитать ночи/выезд, допущения и список изменений после правки."""
    new = TripRequest(**data)
    if new.checkin and new.checkout and new.checkout > new.checkin:
        new.nights = (new.checkout - new.checkin).days
    elif new.checkin and new.nights:
        new.checkout = new.checkin + timedelta(days=new.nights)
    new.missing_fields = [] if new.destination else ["destination"]
    new.assumptions = request_assumptions(new, notes)
    return new, describe_changes(old, new)


def merge_request(old: TripRequest, fresh: TripRequest, u: Understood, text: str) -> tuple[TripRequest, list[str]]:
    """Пересборка по тексту: меняем только то, что пользователь назвал; остальное сохраняем (Constraint Retention)."""
    data = old.model_dump()
    data["raw_text"] = f"{old.raw_text} | {text}"
    notes: list[str | None] = []
    if fresh.destination:
        data["destination"] = fresh.destination
    shift = parse_shift(text)
    if shift and old.checkin:
        data["checkin"] = old.checkin + timedelta(days=shift)
        data["checkout"] = old.checkout + timedelta(days=shift) if old.checkout else None
    elif fresh.checkin:
        data.update(checkin=fresh.checkin, checkout=fresh.checkout, nights=fresh.nights)
    elif fresh.nights:
        data.update(nights=fresh.nights, checkout=None)
    if u.budget_total:
        data["budget_total"] = u.budget_total
    elif CHEAPER_RE.search(text.lower()) and old.budget_total:
        data["budget_total"] = round(old.budget_total * 0.8 / 1000) * 1000
        notes.append(f"«Дешевле» понял как бюджет −20%: {rub(data['budget_total'])}.")
    if u.adults:
        data["guests_adults"] = u.adults
    if u.children_ages:
        data["children_ages"] = u.children_ages
    data["pets"] = old.pets or u.pets
    data["preferences"] = list(dict.fromkeys([*old.preferences, *fresh.preferences]))
    return _finish(old, data, notes)


def apply_patch(old: TripRequest, patch: dict) -> tuple[TripRequest, list[str]]:
    """Правка из панели параметров: только разрешённые поля; неизвестный район и мусор — игнорируем с логом."""
    data = old.model_dump()
    for key, value in patch.items():
        if key not in PATCH_FIELDS:
            log.warning("patch: поле %r не поддерживается — пропускаю", key)
            continue
        if key == "destination" and value not in AREAS:
            log.warning("patch: неизвестный район %r — пропускаю", value)
            continue
        data[key] = value
    if "checkin" in patch and "checkout" not in patch and "nights" not in patch and old.nights:
        data["checkout"] = None          # сдвинули заезд — выезд пересчитаем по числу ночей
    try:
        return _finish(old, data, [])
    except ValueError:
        log.warning("patch: некорректные значения %s — оставляю как было", patch)
        return old, []


class State(TypedDict, total=False):
    text: str
    patch: dict | None
    changes: list[str]
    other_destination: str | None
    request: dict
    hotels: list[dict]
    answer: dict


class GraphAgent:
    """Реализует Protocol Agent (harness/contracts.py)."""

    def __init__(self, llm: LLMClient | None = None, today: date | None = None) -> None:
        self.llm = llm or build_llm()
        self._today = today  # для тестов; иначе — сегодняшняя дата на момент запроса
        self.search_tool = FakeHotelSearch()
        self.ranker = FakeRanker()
        self.graph = self._build()

    # ---------- узлы ----------
    @property
    def today(self) -> date:
        return self._today or date.today()

    async def _understand(self, state: State) -> State:
        prev = TripRequest(**state["request"]) if state.get("request") else None
        if state.get("patch") and prev:                          # правка из панели — без LLM
            req, changes = apply_patch(prev, state["patch"])
            log.info("patch: %s → %s", state["patch"], changes)
            return {"request": req.model_dump(mode="json"), "hotels": [], "changes": changes, "other_destination": None}
        text = state["text"]
        u = await understand_text(self.llm, text, self.today)
        log.info("understand: %s", u.model_dump())
        fresh = to_trip_request(text, u, self.today)
        if prev and prev.destination and not u.other_destination and not FRESH_RE.search(text.lower()):
            req, changes = merge_request(prev, fresh, u, text)     # пересборка, а не поиск с нуля
            log.info("replan: %s", changes)
        else:
            req, changes = fresh, []
        return {"request": req.model_dump(mode="json"), "hotels": [], "changes": changes,
                "other_destination": None if req.destination else u.other_destination}

    @staticmethod
    def _route_understand(state: State) -> str:
        if state["request"].get("destination"):
            return "search"
        return "out_of_scope" if state.get("other_destination") else "clarify"

    async def _out_of_scope(self, state: State) -> State:
        """ADR-019: честно говорим, что работаем только по Большому Сочи, и предлагаем похожий район."""
        req = TripRequest(**state["request"])
        wishes = [w for w in req.preferences if w in WISHES] or ["море"]
        area = suggest_area(AREA_LIST, wishes)
        why = ", ".join(w for w in area.best_for if w in wishes) or ", ".join(area.best_for[:2])
        text = (f"Я подбираю поездки только по Большому Сочи, «{state['other_destination']}» не покрываю. "
                f"Похоже по вашему запросу — {area.name} ({why}). Посмотреть варианты там? Ответьте «да» или «нет».")
        answer = AgentAnswer(mode=AgentMode.CLARIFY, text=text, assumptions=req.assumptions,
                             questions=[f"Посмотреть варианты: {area.name}?"],
                             pending_action={"suggested_area": area.name})
        return {"answer": answer.model_dump(mode="json")}

    async def _suggest_confirm(self, state: State) -> State:
        reply = interrupt(state["answer"]["pending_action"])
        if yes_no(str(reply)):
            req = dict(state["request"])
            area = state["answer"]["pending_action"]["suggested_area"]
            req.update(destination=area, missing_fields=[])
            req["assumptions"] = [*req.get("assumptions", []),
                                  {"field": "destination", "value": area, "confirmed": True,
                                   "reason": f"Вместо «{state['other_destination']}» смотрю {area}."}]
            return {"request": req, "other_destination": None}
        return await self._clarify(state)

    @staticmethod
    def _route_suggest(state: State) -> str:
        return "search" if state["request"].get("destination") else END

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
        changed = ("Изменил: " + "; ".join(state["changes"]) + ".\n") if state.get("changes") else ""
        changed += "".join(f"{a.reason}\n" for a in req.assumptions if a.field == "note")
        answer = AgentAnswer(mode=AgentMode.NO_SOLUTION, assumptions=req.assumptions, changes=state.get("changes", []),
                             text=(f"{trip_summary(req)}\n" if trip_summary(req) else "") + changed
                                  + f"{req.destination}: нет вариантов{limit}. "
                                  "Поднять бюджет или посмотреть другой район?")
        return {"answer": answer.model_dump(mode="json")}

    async def _offer(self, state: State) -> State:
        """План + вопрос «оформить?». Сам вопрос задаёт следующий узел через interrupt."""
        req = TripRequest(**state["request"])
        hotels = [HotelCandidate(**h) for h in state["hotels"]]
        best = hotels[0]
        n = len(hotels)
        lines = [f"{req.destination}: нашёл {n} {plural(n, 'вариант', 'варианта', 'вариантов')} (демо-данные)."]
        lines += [x for x in [trip_summary(req)] if x]
        if state.get("changes"):
            lines.append("Изменил: " + "; ".join(state["changes"]) + ".")
        lines += [a.reason for a in req.assumptions]
        lines.append(f"Оформить «{best.name}» за {rub(best.price_total or 0)}? Ответьте «да» или «нет» (демо-оплата).")
        pending = {"tool": "purchase_mock", "hotel_id": best.hotel_id, "name": best.name,
                   "price_total": best.price_total, "source_call_id": best.source_call_id}
        answer = AgentAnswer(mode=AgentMode.CONFIRM_ACTION, text="\n".join(lines), candidates=hotels[:3],
                             changes=state.get("changes", []),
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
                         ("purchase", self._purchase), ("out_of_scope", self._out_of_scope),
                         ("suggest_confirm", self._suggest_confirm)]:
            b.add_node(name, fn)
        b.add_edge(START, "understand")
        b.add_conditional_edges("understand", self._route_understand,
                                {"search": "search", "clarify": "clarify", "out_of_scope": "out_of_scope"})
        b.add_edge("out_of_scope", "suggest_confirm")
        b.add_conditional_edges("suggest_confirm", self._route_suggest, {"search": "search", END: END})
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

    async def _graph_input(self, text: str, cfg: dict, patch: dict | None) -> dict | Command:
        """Ответ «да/нет» на паузу — продолжаем граф; иначе — новый ход (текст или правка из панели)."""
        snapshot = await self.graph.aget_state(cfg)
        if not patch and snapshot.next in (("confirm",), ("suggest_confirm",)) and yes_no(text) is not None:
            return Command(resume=text)
        return {"text": text, "patch": patch}

    async def stream(self, text: str, session_id: str, patch: dict | None = None) -> AsyncIterator[AgentEvent]:
        run_id = uuid.uuid4().hex[:12]

        def ev(type_: str, node: str | None = None, **payload) -> AgentEvent:
            return AgentEvent(run_id=run_id, ts=datetime.now(UTC), type=type_, node=node, payload=payload)

        cfg = self._config(session_id)
        graph_input = await self._graph_input(text, cfg, patch)
        resumed = isinstance(graph_input, Command)
        yield ev("plan", "graph", resumed=resumed,
                 steps=["resume"] if resumed else ["understand", "clarify|out_of_scope|search", "offer", "confirm"])
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
                if out.get("changes"):
                    yield ev("replan", node, changes=out["changes"], request=request)
                if out.get("hotels"):
                    yield ev("tool_result", node, tool=self.search_tool.name, ok=True, n=len(out["hotels"]))
                if "answer" in out:
                    mode = out["answer"]["mode"]
                    if mode == AgentMode.CLARIFY.value:
                        yield ev("clarify", node, questions=out["answer"]["questions"])
                    if mode == AgentMode.PURCHASED.value:
                        yield ev("purchase", node, booking_ref=(out["answer"].get("plan") or {}).get("booking_ref"))
                    answer_ev = ev("answer", node, answer=out["answer"], request=request)
                    if node in ("offer", "out_of_scope"):
                        held = answer_ev
                    else:
                        yield answer_ev
        if held is not None:
            yield held

    async def run(self, text: str, session_id: str, patch: dict | None = None) -> AgentRun:
        started = time.perf_counter()
        last_answer: AgentEvent | None = None
        async for event in self.stream(text, session_id, patch):
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
