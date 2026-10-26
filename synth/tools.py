"""Инструменты агента поверх synth.pricing.Catalog (протокол harness.contracts.Tool, формат — как у фейков).

Каждый инструмент принимает уже разобранные поля (не текст запроса), вызывает Catalog и возвращает ToolResult:
  ok=True, data=[...]        — результат (пустой список — честное «вариантов нет»);
  ok=False, error_class=...  — validation (плохие аргументы) | unknown_entity (не знаем район/город/место).
Элемент data — модель контракта (HotelOffer, TransportOption, TransferOption, DestinationOption) в виде dict
плюс ключ "facts" с данными для сравнения и объяснения; Model(**item) лишний ключ игнорирует.

    tools = build_tools()
    res = await tools["search_hotels"](SearchHotelsArgs(area="Сириус", checkin=..., checkout=..., pets=True))
"""
from __future__ import annotations

import time
import uuid
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from harness.contracts import ToolResult, Trust
from synth.pricing import Catalog
from synth.refdata import CATALOG_FROM, CATALOG_TO

SideEffect = Literal["none", "reversible", "irreversible"]
Meal = Literal["none", "breakfast", "half_board", "full_board", "all_inclusive"]


@lru_cache(maxsize=1)
def get_catalog() -> Catalog:
    return Catalog()


class ToolError(Exception):
    def __init__(self, error_class: str, message: str, data: dict | None = None) -> None:
        super().__init__(message)
        self.error_class, self.data = error_class, {"message": message, **(data or {})}


class _CatalogTool:
    """Общая обвязка: call_id, замер времени, ошибки → ToolResult(ok=False)."""

    name: str
    side_effect: SideEffect = "none"
    args_model: type[BaseModel]

    def __init__(self, catalog: Catalog | None = None) -> None:
        self.cat = catalog or get_catalog()

    async def __call__(self, args: BaseModel) -> ToolResult:
        call_id = uuid.uuid4().hex[:8]
        started = time.perf_counter()
        try:
            a = self.args_model.model_validate(args.model_dump() if isinstance(args, BaseModel) else args)
            data, ok, err = self.run(a, call_id), True, None
        except ToolError as e:
            data, ok, err = e.data, False, e.error_class
        except ValueError as e:                        # pydantic ValidationError тоже ValueError
            data, ok, err = {"message": str(e)[:500]}, False, "validation"
        return ToolResult(tool=self.name, call_id=call_id, ok=ok, data=data, error_class=err, trust=Trust.UNTRUSTED,
                          latency_ms=int((time.perf_counter() - started) * 1000))

    def run(self, args, call_id: str) -> list[dict]:
        raise NotImplementedError

    def _areas(self, names: list[str]) -> list[str]:
        out = []
        for n in names:
            area = self.cat.resolve_area(n)
            if area is None:
                raise ToolError("unknown_entity", f"неизвестный район: {n}",
                                {"known": [a.name for a in self.cat.areas.values()]})
            out.append(area)
        return out

    def _dates(self, *days: date) -> None:
        lo, hi = CATALOG_FROM, CATALOG_TO
        for d in days:
            if not lo <= d <= hi:
                raise ToolError("validation", f"дата {d} вне периода каталога {lo}…{hi}")


# ---------------------------------------------------------------- отели
class SearchHotelsArgs(BaseModel):
    checkin: date
    checkout: date
    area: str | None = Field(None, description="район: id, название или синоним («Сириус»); None — везде")
    areas: list[str] = Field([], description="несколько районов (вместо area)")
    adults: int = Field(2, ge=1, le=8)
    children_ages: list[int] = Field([], description="возраст каждого ребёнка")
    pets: bool = False
    max_total: float | None = Field(None, description="бюджет на проживание за весь срок на всех, ₽")
    meal: Meal | None = None
    refundable: bool | None = Field(None, description="True — только с бесплатной отменой")
    min_stars: int | None = Field(None, ge=1, le=5)
    amenities: list[str] = Field([], description="обязательные удобства: pool, spa, kids_club, private_beach…")
    limit: int = Field(20, ge=1, le=50)

    @model_validator(mode="after")
    def _check(self):
        if self.checkout <= self.checkin:
            raise ValueError("checkout должен быть позже checkin")
        if (self.checkout - self.checkin).days > 30:
            raise ValueError("больше 30 ночей")
        return self


class SearchHotels(_CatalogTool):
    """Отели, где есть свободный номер под состав гостей на все ночи; лучший тариф каждого, по возрастанию цены."""

    name = "search_hotels"
    args_model = SearchHotelsArgs

    def run(self, a: SearchHotelsArgs, call_id: str) -> list[dict]:
        self._dates(a.checkin, a.checkout)
        area_ids = self._areas(([a.area] if a.area else []) + a.areas) or None
        quotes = self.cat.search_quotes(a.checkin, a.checkout, a.adults, a.children_ages, a.pets, area_ids,
                                        a.max_total, a.meal, a.refundable, a.min_stars, a.amenities or None, a.limit)
        out = []
        for q in quotes:
            h = self.cat.hotels[q["hotel_id"]]
            offer = self.cat.to_offer(q, call_id).model_copy(update={"hotel": self.cat.candidate(
                h.hotel_id, q, call_id, self.name)})
            out.append(offer.model_dump(mode="json") | {"facts": {
                "area_id": h.area_id, "area": self.cat.areas[h.area_id].name, "property_type": h.property_type,
                "stars": h.stars, "reviews_count": h.reviews_count, "beach_line": h.beach_line,
                "distance_to_sea_m": h.distance_to_sea_m, "distance_to_lift_m": h.distance_to_lift_m,
                "tags": h.tags, "pets_allowed": h.pets_allowed, "pet_max_kg": h.pet_max_kg,
                "min_child_age": h.min_child_age, "nights": q["nights"], "price_per_night_avg": q["price_per_night_avg"],
                "breakdown": q["breakdown"], "cancellation": q["rate"].cancellation,
                "early_checkin_fee": q["early_checkin_fee"], "late_checkout_fee": q["late_checkout_fee"],
                "review_aspects": h.review_aspects}})
        return out


# ---------------------------------------------------------------- транспорт
class SearchTransportArgs(BaseModel):
    origin_city: str = Field(description="город пользователя: «Москва», «из Питера», «СПб»…")
    travel_date: date = Field(description="дата отправления (туда — из города, обратно — из Сочи)")
    direction: Literal["outbound", "return"] = "outbound"
    kinds: list[Literal["flight", "train", "local_train", "bus"]] = Field(
        [], description="виды транспорта; пусто — все")
    adults: int = Field(2, ge=1, le=8)
    children_ages: list[int] = []
    direct_only: bool = False
    limit: int = Field(30, ge=1, le=100)


class SearchTransport(_CatalogTool):
    """Рейсы, поезда, «Ласточки» и автобусы между городом пользователя и Сочи на дату — по времени отправления."""

    name = "search_transport"
    args_model = SearchTransportArgs

    def run(self, a: SearchTransportArgs, call_id: str) -> list[dict]:
        self._dates(a.travel_date)
        city = self.cat.resolve_city(a.origin_city)
        if city is None:
            raise ToolError("unknown_entity", f"нет сообщения с городом: {a.origin_city}",
                            {"known": list(self.cat.origins)})
        opts = self.cat.transport_options(city, a.travel_date, a.direction, a.adults, a.children_ages,
                                          a.kinds or None)
        if a.direct_only:
            opts = [o for o in opts if o.kind != "flight" or o.stops == 0]
        out = []
        for o in opts[:a.limit]:
            facts: dict = {"origin_city": city, "direction": a.direction}
            if o.kind != "flight":                     # все станции поезда в Сочи — выйти ближе к отелю
                facts["stations_min_from_departure"] = {
                    self.cat.points[p].name: m for p, m in self.cat.train_stops(o.option_id).items()}
            out.append(o.model_dump(mode="json") | {"facts": facts})
        return out


# ---------------------------------------------------------------- трансферы
class SearchTransfersArgs(BaseModel):
    from_place: str = Field(description="откуда: «аэропорт», «вокзал Адлер», point_id, hotel_id или район")
    to_place: str = Field(description="куда: то же")
    when: datetime = Field(description="время подачи (после прилёта — с запасом на выход из аэропорта)")
    adults: int = Field(2, ge=1, le=8)
    children_ages: list[int] = []
    pets: bool = False
    luggage: int | None = Field(None, description="мест багажа; None — по числу пассажиров")


class SearchTransfers(_CatalogTool):
    """Такси, шаттл и общественный транспорт между аэропортом/вокзалом и отелем/районом — по цене."""

    name = "search_transfers"
    args_model = SearchTransfersArgs

    def run(self, a: SearchTransfersArgs, call_id: str) -> list[dict]:
        refs = []
        for place in (a.from_place, a.to_place):
            ref = self.cat.resolve_place(place)
            if ref is None:
                raise ToolError("unknown_entity", f"неизвестное место: {place}",
                                {"known": [p.name for p in self.cat.points.values()]})
            refs.append(ref)
        rt = self.cat.route(*refs)
        opts = self.cat.transfer_options(refs[0], refs[1], a.when, a.adults, a.children_ages, a.pets, a.luggage)
        return [o.model_dump(mode="json") | {"facts": {
            "from_ref": refs[0], "to_ref": refs[1], "km": rt["km"],
            "arrive_at": (a.when + timedelta(minutes=o.minutes)).isoformat()}} for o in opts]


# ---------------------------------------------------------------- районы
WISH_TO_FLAG = {"море": "swim_ok", "горы/лыжи": "ski_open"}


class RankAreasArgs(BaseModel):
    month: int = Field(ge=1, le=12)
    origin_city: str = "Москва"
    adults: int = Field(2, ge=1, le=8)
    children_ages: list[int] = []
    nights: int = Field(7, ge=1, le=30)
    wishes: list[str] = Field([], description="из словаря best_for: море, горы/лыжи, с детьми, тихо, бюджетно, "
                                              "тусовка, оздоровление, экскурсии, активный отдых, романтика, люкс")
    max_total: float | None = Field(None, description="бюджет поездки целиком, ₽")
    limit: int = Field(5, ge=1, le=10)


class RankAreas(_CatalogTool):
    """Районы на месяц: погода, дорога, оценка стоимости поездки целиком; по соответствию пожеланиям.

    Простой прозрачный скоринг (не M5): +2 за каждое совпадение пожелания с best_for, +1 если сезон
    подходит (море — купальный, горы — лыжный), +1 «с детьми» при детях, −3 если дороже бюджета.
    """

    name = "rank_areas"
    args_model = RankAreasArgs

    def run(self, a: RankAreasArgs, call_id: str) -> list[dict]:
        city = self.cat.resolve_city(a.origin_city)
        if city is None:
            raise ToolError("unknown_entity", f"нет сообщения с городом: {a.origin_city}",
                            {"known": list(self.cat.origins)})
        wishes = [w.lower() for w in a.wishes]
        scored = []
        for d in self.cat.destination_options(a.month, city, a.adults, a.children_ages, a.nights):
            cl = self.cat.climate_for(d.destination_id, a.month)
            score, reasons = 0.0, []
            for w in (w for w in wishes if w in d.best_for):
                score += 2
                reasons.append(f"подходит: {w}")
                flag = WISH_TO_FLAG.get(w)
                if flag and getattr(cl, flag):
                    score += 1
                    reasons.append("купальный сезон" if flag == "swim_ok" else "лыжный сезон открыт")
                elif flag:
                    reasons.append("не купальный сезон" if flag == "swim_ok" else "лыжный сезон закрыт")
            if a.children_ages and "с детьми" in d.best_for and "с детьми" not in wishes:
                score += 1
                reasons.append("подходит для детей")
            over = a.max_total is not None and d.est_total_price is not None and d.est_total_price > a.max_total
            if over:
                score -= 3
                reasons.append(f"оценка {d.est_total_price:.0f} ₽ выше бюджета")
            item = d.model_copy(update={"score": score, "why": reasons + d.why})
            scored.append((score, -(d.est_total_price or 0), item, over))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [item.model_dump(mode="json") | {"facts": {"over_budget": over,
                                                          "poi": [p.name for p in self.cat.poi_for(item.destination_id,
                                                                                                    a.month)][:3]}}
                for _, _, item, over in scored[:a.limit]]


TOOL_CLASSES = (SearchHotels, SearchTransport, SearchTransfers, RankAreas)


def build_tools(catalog: Catalog | None = None) -> dict[str, _CatalogTool]:
    """Все инструменты по имени, на одном каталоге."""
    cat = catalog or get_catalog()
    return {cls.name: cls(cat) for cls in TOOL_CLASSES}


def tool_specs() -> list[dict]:
    """Описания в формате OpenAI tools — если агенту понадобится, чтобы инструменты выбирала LLM."""
    return [{"type": "function", "function": {"name": cls.name, "description": (cls.__doc__ or "").strip(),
                                              "parameters": cls.args_model.model_json_schema()}}
            for cls in TOOL_CLASSES]
