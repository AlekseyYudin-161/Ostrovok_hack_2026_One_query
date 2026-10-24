"""Контракт данных и интерфейсы модулей. Источник истины — этот файл.

Изменения — только PR с меткой `contract` и новой CONTRACT_VERSION (см. docs, документ 03 §3).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime
from enum import Enum
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

CONTRACT_VERSION = "1.1.0"   # 1.1 (24.10): одна локация — районы, электрички; 1.0: модели поездки; 0.2: trace_id


# ---------- Запрос и разбор ----------
class Constraint(BaseModel):
    """Машинно проверяемое ограничение (используется и агентом, и eval-ом)."""
    key: str                                 # "max_price_per_night", "has_amenity", …
    op: Literal["<=", ">=", "==", "in", "contains"]
    value: Any
    source: Literal["user", "commonsense"]



class Assumption(BaseModel):
    """Допущение агента вместо вопроса пользователю; показывается в UI и правится одним кликом."""
    field: str
    value: Any
    reason: str
    confirmed: bool = False


class TripRequest(BaseModel):
    """Структурированный разбор пользовательского запроса (выход NLP-шага)."""
    raw_text: str
    destination: str | None = None          # город или регион, как сказал пользователь
    destination_id: str | None = None        # ID в системе поставщика   # TBD
    checkin: date | None = None
    checkout: date | None = None
    guests_adults: int | None = None
    children_ages: list[int] = []
    budget_total: float | None = None
    currency: str = "RUB"                    # TBD
    preferences: list[str] = []              # «у моря», «с завтраком», …
    origin_city: str | None = "Москва"       # откуда едем (допущение по умолчанию)
    month: int | None = None                 # «в ноябре» без точных дат
    nights: int | None = None
    flexible_days: int = 0                   # ± дней гибкости дат
    pets: bool = False
    max_travel_hours: float | None = None    # «без долгих перелётов»
    transport: list[Literal["flight", "train", "any"]] = ["any"]
    assumptions: list[Assumption] = []     # что агент додумал и показал пользователю
    hard_constraints: list[Constraint] = []
    missing_fields: list[str] = []           # что нужно уточнить


# ---------- Инструменты ----------
class Trust(str, Enum):
    TRUSTED = "trusted"        # наш собственный код и константы
    UNTRUSTED = "untrusted"    # всё, что пришло извне (API, отзывы, описания)


class ToolResult(BaseModel):
    tool: str
    call_id: str
    ok: bool
    data: Any | None = None                  # нормализованная выдача (модель на каждый инструмент)
    error_class: str | None = None           # rate_limit | validation | unknown_entity | missing_info | policy
    trust: Trust = Trust.UNTRUSTED
    cached: bool = False
    latency_ms: int
    cost_units: float = 0.0


class HotelCandidate(BaseModel):
    """Нормализованная карточка — единый формат для агента, UI и eval."""
    hotel_id: str
    name: str
    city: str | None = None
    price_total: float | None = None
    currency: str | None = None
    rating: float | None = None
    amenities: list[str] = []
    meal: str | None = None
    cancellation: str | None = None
    source_tool: str                          # из какого вызова (для groundedness)
    source_call_id: str
    # TBD: поля под API оргов


# ---------- Поездка (v1.0) ----------
class DestinationOption(BaseModel):
    """Карточка направления (в режиме одной локации — района: Сочи-центр, Адлер, Красная Поляна…).

    Все факты — из каталога/инструментов, не из LLM.
    """
    destination_id: str
    name: str
    country: str = "Россия"
    location: str = "Большой Сочи"
    kind: Literal["area", "city"] = "area"
    best_for: list[str] = []                 # «море», «горы/лыжи», «с детьми», «тихо», «бюджетно»
    why: list[str] = []                      # причины, собранные кодом из фактов
    air_temp_c: float | None = None
    sea_temp_c: float | None = None
    travel_hours: float | None = None
    direct: bool | None = None
    est_total_price: float | None = None     # оценка поездки целиком
    visa: str | None = None                  # справка: "не нужна" / "e-visa" / …
    is_demo_data: bool = True
    score: float | None = None


class HotelOffer(BaseModel):
    """Тариф конкретного отеля (отель + питание + отмена + цена за весь срок)."""
    offer_id: str
    hotel: HotelCandidate
    room: str = "standard"
    meal: Literal["none", "breakfast", "half_board", "full_board", "all_inclusive"] = "none"
    refundable: bool = True
    free_cancel_until: datetime | None = None
    price_total: float
    currency: str = "RUB"
    checkin_time: str = "14:00"
    checkout_time: str = "12:00"
    is_demo_data: bool = True


class TransportOption(BaseModel):
    option_id: str
    kind: Literal["flight", "train", "local_train", "bus"]
    carrier: str | None = None
    from_city: str
    to_city: str
    depart: datetime
    arrive: datetime
    duration_hours: float
    stops: int = 0
    price_total: float
    currency: str = "RUB"
    is_demo_data: bool = True


class TransferOption(BaseModel):
    option_id: str
    from_place: str
    to_place: str
    minutes: int
    price_total: float
    kind: Literal["taxi", "shuttle", "public"] = "taxi"
    is_demo_data: bool = True


class ConnectionCheck(BaseModel):
    """Результат детерминированной проверки состыковки."""
    rule: str                                # "arrival_vs_checkin", "checkout_vs_departure", "dates_match"
    ok: bool
    severity: Literal["info", "warning", "error"] = "info"
    message: str
    fixes: list[str] = []                    # «ранний заезд», «ночь накануне», …


class Tradeoff(BaseModel):
    """Чем пожертвовали относительно лучшего по признаку варианта."""
    criterion: str                           # "price", "cancellation", "meal", "location", "travel_time"
    this_value: Any
    best_value: Any
    note: str


class Itinerary(BaseModel):
    destination_id: str
    checkin: date
    checkout: date
    outbound: TransportOption | None = None
    inbound: TransportOption | None = None
    transfers: list[TransferOption] = []
    hotel: HotelOffer | None = None
    total_price: float = 0.0
    checks: list[ConnectionCheck] = []
    tradeoffs: list[Tradeoff] = []
    label: Literal["balanced", "cheapest", "most_comfortable", "custom"] = "balanced"


class Cart(BaseModel):
    items: list[Itinerary] = []
    total_price: float = 0.0
    status: Literal["draft", "awaiting_confirmation", "paid_mock"] = "draft"
    booking_ref: str | None = None


# ---------- Результат агента ----------
class AgentMode(str, Enum):
    PLAN = "plan"
    CLARIFY = "clarify"
    NO_SOLUTION = "no_solution"
    CONFIRM_ACTION = "confirm_action"
    PURCHASED = "purchased"


class AgentAnswer(BaseModel):
    mode: AgentMode
    text: str                                 # ответ пользователю
    candidates: list[HotelCandidate] = []
    plan: dict | None = None                  # TBD: структура плана поездки
    questions: list[str] = []                 # для CLARIFY
    pending_action: dict | None = None        # для CONFIRM_ACTION: tool + args + сверка
    violations: list[str] = []                # что не прошло детерминированную проверку
    destinations: list[DestinationOption] = []
    itineraries: list[Itinerary] = []
    assumptions: list[Assumption] = []
    changes: list[str] = []                   # «было → стало» после пересборки


class AgentRun(BaseModel):
    run_id: str
    session_id: str
    request: TripRequest
    answer: AgentAnswer
    tokens_in: int
    tokens_out: int
    cost: float
    latency_ms: int
    pressure_max: float
    replans: int
    trace_id: str | None = None              # id трейса Langfuse (None, если трассировка выключена)


class TripState(BaseModel):
    """Состояние сессии в чекпоинтере LangGraph (thread_id = session_id)."""
    session_id: str
    version: int = 0
    request: TripRequest
    priorities: dict[str, float] = {"price": 0.4, "travel_time": 0.2, "location": 0.15,
                                    "cancellation": 0.15, "meal": 0.1}
    destination_options: list[DestinationOption] = []
    chosen_destination_id: str | None = None
    itineraries: list[Itinerary] = []
    cart: Cart = Cart()
    history: list[dict] = []                 # снимки для «было → стало»
    mode: AgentMode = AgentMode.PLAN


# ---------- События (SSE и Tracer) ----------
class AgentEvent(BaseModel):
    run_id: str
    ts: datetime
    type: Literal["plan", "node_start", "tool_call", "tool_result",
                  "verify", "replan", "budget", "answer", "error",
                  "clarify", "options", "cart", "purchase"]
    node: str | None = None
    payload: dict = {}


# ---------- Eval ----------
class EvalCase(BaseModel):
    case_id: str
    split: Literal["dev", "heldout"]
    slice: list[str]                          # ["3x2", "clarify", "injection", …]
    query: str
    context: dict = {}                        # история диалога, профиль
    hard_constraints: list[Constraint] = []
    commonsense_rules: list[str] = []         # ID правил из реестра
    expected_mode: AgentMode
    forbidden_actions: list[str] = []         # например, "book без подтверждения"
    injected_payload: str | None = None       # для среза injection
    followups: list[str] = []                 # реплики пользователя после первого ответа (пересборка)
    simulated_answers: dict[str, str] = {}    # ответы симулятора на уточняющие вопросы по ключу поля
    notes: str = ""


class EvalResult(BaseModel):
    case_id: str
    run_id: str
    delivered: bool
    mode_correct: bool
    hard_passed: int
    hard_total: int
    commonsense_passed: int
    commonsense_total: int
    grounded_ratio: float
    injection_success: bool | None = None
    judge_score: float | None = None
    latency_ms: int
    cost: float
    trace_url: str | None = None             # ссылка на трейс в Langfuse для разбора ошибки кликом
    completed: bool = False                   # доведён до покупки / корректного «решения нет»
    clarifications: int = 0
    ttfr_ms: int | None = None                # время до первой рекомендации
    connection_errors: int = 0
    constraint_retention: float | None = None # после пересборки


# ---------- LLM и память (данные) ----------
class LLMResult(BaseModel):
    text: str | None = None
    tool_calls: list[dict] = []
    parsed: dict | None = None               # structured output
    tokens_in: int = 0                       # из метаданных SDK
    tokens_out: int = 0
    model: str = ""


class MemoryItem(BaseModel):
    key: str
    text: str
    kind: Literal["episodic", "semantic"]
    score: float | None = None
    meta: dict = {}


# =====================================================================
# ИНТЕРФЕЙСЫ МОДУЛЕЙ. Каждый модуль реализует свой Protocol;
# для каждого Protocol в harness/fakes.py есть фейк — им пользуются
# все остальные, пока настоящая реализация не влита в main.
# =====================================================================
@runtime_checkable
class LLMClient(Protocol):                    # владелец: M1 (тимлид)
    async def complete(self, messages: list[dict], tools: list[dict] | None = None,
                       schema: dict | None = None) -> LLMResult: ...


@runtime_checkable
class Tool(Protocol):                         # владелец: M2 (ML)
    name: str
    side_effect: Literal["none", "reversible", "irreversible"]
    args_model: type[BaseModel]
    async def __call__(self, args: BaseModel) -> ToolResult: ...


@runtime_checkable
class Ranker(Protocol):                       # владелец: M2 (ML)
    def rank(self, request: TripRequest, candidates: list[HotelCandidate]) -> list[HotelCandidate]: ...


@runtime_checkable
class MemoryStore(Protocol):                  # владелец: M2 (ML)
    async def put(self, session_id: str, item: MemoryItem) -> None: ...
    async def search(self, session_id: str, query: str, k: int = 5) -> list[MemoryItem]: ...


@runtime_checkable
class Agent(Protocol):                        # владелец: M1 (тимлид)
    def stream(self, text: str, session_id: str) -> AsyncIterator[AgentEvent]: ...
    async def run(self, text: str, session_id: str) -> AgentRun: ...
