"""Сборка поездки целиком на каталоге ML (L8): отель + дорога туда и обратно + трансферы + проверка состыковок.

Источник данных — `synth.pricing.Catalog` (демо-каталог «Большой Сочи», офферы детерминированы).
Три варианта, как в DesignDoc §5:
  * cheapest          — самый дешёвый отель и самая дешёвая дорога без ошибок состыковки;
  * balanced          — отель с рейтингом не ниже медианы, прямой рейс/поезд в удобное время;
  * most_comfortable  — лучший рейтинг, багаж включён, без пересадок, такси.
Бюджет пользователя — только проживание за весь срок на всех гостей (ADR-021); дорога и трансферы — отдельно.
Всё, что додумали (даты, число взрослых, город вылета), возвращается в notes и показывается пользователю.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from statistics import median

from harness.checks import AFTER_ARRIVAL_MIN, BEFORE_DEPARTURE_MIN, check_connections
from harness.contracts import (
    ConnectionCheck,
    DestinationOption,
    HotelOffer,
    Itinerary,
    Tradeoff,
    TransferOption,
    TransportOption,
    TripRequest,
)

log = logging.getLogger(__name__)

DEFAULT_NIGHTS = 7
HOUR_COST = 500      # «оптимально»: час в дороге ≈ 500 ₽ — двое суток в поезде не выигрывают у рейса ради экономии
LONG_HOURS = 8       # «комфортно»: дольше — это поезд на сутки и больше
KIDS_PUBLIC_MAX_MIN = 60   # с ребёнком автобус/электричка дольше часа — не предлагаем, если есть такси/трансфер
KIDS_MINUTE_COST = 50      # «оптимально» с ребёнком: минута в трансфере дороже — быстрее такси, а не долгий шаттл
NIGHT_END_HOUR = 6   # прибытие в отель / выезд из отеля раньше 06:00 — только если других вариантов нет
DEFAULT_ADULTS = 2
ASSUMED_KID_AGE = 7  # возраст не назвали: цена билетов и мест зависит от возраста
DEFAULT_ORIGIN = "Москва"
# «из Казани», «из Питера» → город вылета из справочника ML (основа слова → город)
ORIGIN_STEMS = {"москв": "Москва", "питер": "Санкт-Петербург", "петербург": "Санкт-Петербург", "спб": "Санкт-Петербург",
                "екатеринбург": "Екатеринбург", "казан": "Казань", "новосибирск": "Новосибирск", "самар": "Самара",
                "нижн": "Нижний Новгород", "перм": "Пермь", "уф": "Уфа", "воронеж": "Воронеж",
                "ростов": "Ростов-на-Дону", "краснодар": "Краснодар"}
MEAL_RU = {"breakfast": "завтрак", "half_board": "полупансион", "full_board": "полный пансион",
           "all_inclusive": "всё включено"}
MEAL_AT_LEAST = {"breakfast": ["breakfast", "half_board", "full_board", "all_inclusive"],
                 "half_board": ["half_board", "full_board", "all_inclusive"],
                 "full_board": ["full_board", "all_inclusive"], "all_inclusive": ["all_inclusive"]}
LABEL_RU = {"cheapest": "Дешевле всего", "balanced": "Оптимально", "most_comfortable": "Комфортно"}
KIND_RU = {"flight": "рейс", "train": "поезд", "local_train": "«Ласточка»", "bus": "автобус",
           "taxi": "такси", "shuttle": "шаттл", "public": "общественный транспорт"}


@dataclass
class TripPlan:
    itineraries: list[Itinerary] = field(default_factory=list)
    hotels: list[HotelOffer] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)       # допущения, которые показываем пользователю
    checkin: date | None = None
    checkout: date | None = None
    relax: list[dict] = field(default_factory=list)       # L7: что поменять, если вариантов нет (patch для агента)


def constraint(req: TripRequest, key: str):
    """Значение жёсткого условия пользователя («от 4★», «первая линия») или None."""
    return next((c.value for c in req.hard_constraints if c.key == key), None)


def origin_from_text(text: str) -> str | None:
    low = text.lower()
    for stem, city in ORIGIN_STEMS.items():
        if re.search(r"\bиз\s+" + stem, low):
            return city
    return None


def assumed_checkin(today: date, month: int | None) -> date:
    """Дат нет: ближайшая пятница не раньше чем через 3 дня; если назван месяц — первая пятница месяца."""
    start = today + timedelta(days=3)
    if month:
        year = today.year if month >= today.month else today.year + 1
        start = max(start, date(year, month, 1))
    return start + timedelta(days=(4 - start.weekday()) % 7)


class TripPlanner:
    def __init__(self, catalog) -> None:
        self.cat = catalog
        self._point_by_name = {p.name: pid for pid, p in catalog.points.items()}

    # ---------- справочники ----------
    def area_id(self, name: str | None) -> str | None:
        if not name:
            return None
        low = name.lower()
        for aid, a in self.cat.areas.items():
            if a.name.lower() == low or aid == low:
                return aid
        found = next((aid for aid, a in self.cat.areas.items() if low in a.name.lower() or a.name.lower() in low), None)
        words = set(re.findall(r"\w+", low))                 # «Сочи-центр» = «Центр Сочи»
        return found or next((aid for aid, a in self.cat.areas.items()
                              if words and words == set(re.findall(r"\w+", a.name.lower()))), None)

    def _point(self, place_name: str) -> str | None:
        return self._point_by_name.get(place_name)

    # ---------- сборка ----------
    def plan(self, req: TripRequest, today: date) -> TripPlan:
        out = TripPlan()
        aid = self.area_id(req.destination)
        if aid is None:
            log.warning("trip: район %r не найден в каталоге", req.destination)
            return out
        nights = req.nights or (((req.checkout - req.checkin).days) if req.checkin and req.checkout else None)
        checkin = req.checkin
        if checkin is None:
            checkin = assumed_checkin(today, req.month)
            n = nights or DEFAULT_NIGHTS
            out.notes.append(f"Даты не назвали — считаю заезд в пятницу {checkin:%d.%m} на {n} "
                             f"{_plural(n, 'ночь', 'ночи', 'ночей')}; напишите свои даты — пересчитаю.")
        elif not nights:
            out.notes.append(f"Число ночей не назвали — считаю {DEFAULT_NIGHTS}; напишите дату выезда, если иначе.")
        nights = nights or DEFAULT_NIGHTS
        checkout = req.checkout if req.checkin and req.checkout else checkin + timedelta(days=nights)
        out.checkin, out.checkout = checkin, checkout

        adults = req.guests_adults or DEFAULT_ADULTS
        if not req.guests_adults:
            out.notes.append(f"Считаю на {adults} взрослых — поправьте, если иначе.")
        kids = kids_ages(req)
        if kids and not req.children_ages:
            out.notes.append(("Возраст детей не назвали — считаю по 7 лет" if len(kids) > 1 else
                              "Возраст ребёнка не назвали — считаю 7 лет") + " (цена билетов и мест зависит от возраста).")

        origin = origin_from_text(req.raw_text) or req.origin_city or DEFAULT_ORIGIN
        if origin not in self.cat.origins:
            out.notes.append(f"Из города «{origin}» расписаний нет в демо-данных — считаю дорогу из Москвы.")
            origin = DEFAULT_ORIGIN
        elif not origin_from_text(req.raw_text):
            out.notes.append(f"Город отправления — {origin} (по умолчанию); напишите, если едете из другого.")

        stars, line, meal = constraint(req, "min_stars"), constraint(req, "beach_line"), constraint(req, "meal")
        if meal:
            out.notes.append(f"Учёл: питание — {MEAL_RU.get(meal, meal)}.")
        if stars:
            out.notes.append(f"Учёл: отели от {stars}★.")
        if line:
            out.notes.append("Учёл: только первая линия у моря.")
        ctx = {"checkin": checkin, "checkout": checkout, "adults": adults, "kids": kids, "pets": req.pets, "meal": meal}
        out.hotels = self._hotels(ctx, aid, req.budget_total, stars, line)
        log.info("trip: %s %s–%s, %d взр., дети %s, животные %s, от %s★, линия %s → %d отелей", aid, checkin,
                 checkout, adults, kids, req.pets, stars, line, len(out.hotels))
        if not out.hotels:
            out.relax = self._relax(ctx, aid, req.budget_total, stars, line)
            return out

        kinds = None if "any" in req.transport else [k for k in req.transport if k != "any"]
        outs = [o for d in range(3) for o in self.cat.transport_options(origin, checkin - timedelta(days=d),
                                                                       "outbound", adults, kids, kinds)
                if o.arrive.date() == checkin]          # поезд мог выехать накануне — важно прибыть в день заезда
        backs = self.cat.transport_options(origin, checkout, "return", adults, kids, kinds)
        if not outs or not backs:
            out.notes.append("Дорогу на эти даты в демо-расписании не нашёл — показываю только проживание.")

        picks = self._pick_hotels(req, out.hotels, today, kids, meal)
        seen: set[tuple] = set()
        for label in ("balanced", "cheapest", "most_comfortable"):
            it = self._variant(label, picks.get(label, out.hotels), outs, backs, checkin, checkout, adults, kids,
                               req.pets, aid)
            key = (it.hotel.offer_id if it.hotel else None, it.outbound.option_id if it.outbound else None,
                   it.inbound.option_id if it.inbound else None, tuple(t.kind for t in it.transfers))
            if key not in seen:
                seen.add(key)
                out.itineraries.append(it)
        explain(out.itineraries)
        return out

    def _pick_hotels(self, req: TripRequest, hotels: list[HotelOffer], today: date, kids: list[int],
                     meal: str | None) -> dict[str, list[HotelOffer]]:
        """M5: отель каждого варианта — скоринг ML по приоритетам (synth.scoring); без него — прежние правила."""
        try:
            from synth.scoring import ScoreContext, pick_variants
        except ImportError:
            return {}
        ctx = ScoreContext(today=today, checkin=req.checkin, children_ages=kids, pets=req.pets,
                           wishes=list(req.preferences), meal_required=meal)
        return {label: [s.offer] for label, s in pick_variants(self.cat, hotels, ctx).items()}

    def why(self, its: list[Itinerary], req: TripRequest, today: date) -> dict[str, list[str]]:
        """M5 explain: почему выбран отель варианта — плюсы и минусы отеля, которых нет в tradeoffs."""
        try:
            from synth.scoring import ScoreContext, score_hotels
        except ImportError:
            return {}
        kids = kids_ages(req)
        ctx = ScoreContext(today=today, checkin=req.checkin, children_ages=kids, pets=req.pets,
                           wishes=list(req.preferences), meal_required=constraint(req, "meal"))
        scored = {s.offer.offer_id: s for s in score_hotels(self.cat, [it.hotel for it in its if it.hotel], ctx)}
        return {it.label: scored[it.hotel.offer_id].why_extra() for it in its
                if it.hotel and it.hotel.offer_id in scored}

    def rank_areas(self, req: TripRequest, today: date, limit: int = 3) -> list[DestinationOption]:
        """L8 inspire: районы на месяц поездки по пожеланиям — инструмент ML RankAreas (погода, дорога, оценка цены)."""
        try:
            from synth.tools import RankAreas, RankAreasArgs, ToolError
        except ImportError:
            return []
        month = req.month or (req.checkin.month if req.checkin else assumed_checkin(today, None).month)
        kids = kids_ages(req)
        origin = origin_from_text(req.raw_text) or req.origin_city or DEFAULT_ORIGIN
        if origin not in self.cat.origins:
            origin = DEFAULT_ORIGIN
        args = RankAreasArgs(month=month, origin_city=origin, adults=min(req.guests_adults or DEFAULT_ADULTS, 8),
                             children_ages=kids, nights=min(req.nights or DEFAULT_NIGHTS, 30),
                             wishes=list(req.preferences), limit=limit)   # бюджет у RankAreas — на всю поездку, у нас — отель
        try:
            rows = RankAreas(self.cat).run(args, "inspire")
        except (ToolError, ValueError):
            log.warning("inspire: RankAreas не отработал", exc_info=True)
            return []
        log.info("inspire: месяц %s, пожелания %s → %s", month, req.preferences, [r["name"] for r in rows])
        return [DestinationOption(**{k: v for k, v in r.items() if k != "facts"}) for r in rows]

    def addon(self, hotel_id: str, kind: str) -> tuple[int | None, str | None]:
        """L14: цена раннего заезда / позднего выезда и стандартное время; (None, …) — услуга не предоставляется."""
        h = self.cat.hotels.get(hotel_id)
        if h is None:
            return None, None
        if kind == "early_checkin":
            return h.early_checkin_fee, h.check_in_time
        return h.late_checkout_fee, h.check_out_time

    def stars(self, hotel_id: str) -> int | None:
        h = self.cat.hotels.get(hotel_id)
        return h.stars if h else None

    def _hotels(self, ctx: dict, aid: str, budget: float | None, stars: int | None, line: int | None,
                limit: int = 50) -> list[HotelOffer]:
        best: dict[str, HotelOffer] = {}
        for meal in MEAL_AT_LEAST.get(ctx.get("meal"), [None]):     # «с завтраком» — подходит и полупансион
            for o in self.cat.search_hotels(ctx["checkin"], ctx["checkout"], ctx["adults"], ctx["kids"], ctx["pets"],
                                            area_ids=[aid], max_total=budget, min_stars=stars, meal=meal, limit=limit):
                if o.hotel.hotel_id not in best or o.price_total < best[o.hotel.hotel_id].price_total:
                    best[o.hotel.hotel_id] = o
        offers = sorted(best.values(), key=lambda o: o.price_total)
        if line:
            offers = [o for o in offers if (self.cat.hotels[o.hotel.hotel_id].beach_line or 99) <= line]
        return offers[:limit]

    def _relax(self, ctx: dict, aid: str, budget: float | None, stars: int | None, line: int | None) -> list[dict]:
        """L7: вариантов нет — считаем по каталогу, что конкретно поменять (цена — настоящая, не заготовка)."""
        out: list[dict] = []
        if budget:
            cheapest = self._hotels(ctx, aid, None, stars, line, limit=1)
            if cheapest:
                price = cheapest[0].price_total                  # в тексте — только цены из каталога (Groundedness)
                out.append({"title": "Поднять бюджет", "result": f"от {_rub(price)}",
                            "text": "Появятся варианты в этом районе, остальное без изменений.",
                            "patch": {"budget_total": math.ceil(price)},
                            "hotel": cheapest[0].hotel.model_dump(mode="json")})
        if stars or line or ctx.get("meal"):
            plain = self._hotels({**ctx, "meal": None}, aid, budget, None, None, limit=1)
            if plain:
                what = ", ".join(x for x in (f"{stars}★" if stars else "", "первую линию" if line else "",
                                             MEAL_RU.get(ctx["meal"], "") if ctx.get("meal") else "") if x)
                out.append({"title": f"Не требовать {what}", "result": f"от {_rub(plain[0].price_total)}",
                            "text": "Тот же район, даты и бюджет.", "patch": {"hard_constraints": []},
                            "hotel": plain[0].hotel.model_dump(mode="json")})
        other_best: tuple[str, HotelOffer] | None = None
        for other in self.cat.areas:
            if other == aid:
                continue
            found = self._hotels(ctx, other, budget, stars, line, limit=1)
            if found and (other_best is None or found[0].price_total < other_best[1].price_total):
                other_best = (other, found[0])
        if other_best:
            oid, offer = other_best
            out.append({"title": f"Другой район: {self.cat.areas[oid].name}", "result": f"от {_rub(offer.price_total)}",
                        "text": "Те же даты и условия.", "patch": {"destination_id": oid},
                        "hotel": offer.hotel.model_dump(mode="json")})
        log.info("relax: %s", [r["title"] for r in out])
        return out

    def _variant(self, label: str, hotels: list[HotelOffer], outs: list[TransportOption], backs: list[TransportOption],
                 checkin: date, checkout: date, adults: int, kids: list[int], pets: bool, aid: str) -> Itinerary:
        hotel = self._pick_hotel(label, hotels)
        hid = hotel.hotel.hotel_id
        it = Itinerary(destination_id=aid, checkin=checkin, checkout=checkout, hotel=hotel, label=label)

        def leg_in(o: TransportOption) -> tuple[Itinerary, TransferOption | None]:
            t = self._transfer(label, self._point(o.to_city), hid,
                               o.arrive + timedelta(minutes=AFTER_ARRIVAL_MIN[o.kind]), adults, kids, pets)
            return Itinerary(destination_id=aid, checkin=checkin, checkout=checkout, hotel=hotel, outbound=o,
                             transfers=[t] if t else []), t

        def leg_out(b: TransportOption) -> tuple[Itinerary, TransferOption | None]:
            leave = b.depart - timedelta(minutes=BEFORE_DEPARTURE_MIN[b.kind] + 60)
            t = self._transfer(label, hid, self._point(b.from_city), leave, adults, kids, pets)
            stub = TransferOption(option_id="-", from_place="-", to_place="-", minutes=0, price_total=0)
            return Itinerary(destination_id=aid, checkin=checkin, checkout=checkout, hotel=hotel, inbound=b,
                             transfers=[stub, t] if t else []), t

        it.outbound, t_in = _choose(sorted(outs, key=_transport_key(label, "outbound")), leg_in)
        it.inbound, t_out = _choose(sorted(backs, key=_transport_key(label, "return")), leg_out)
        # transfers[0] — в отель, [1] — из отеля (так их читает check_connections)
        it.transfers = [t for t in ([t_in] if it.outbound else []) + ([t_out] if it.inbound else []) if t]
        it.total_price = round(hotel.price_total + sum(x.price_total for x in (it.outbound, it.inbound) if x)
                               + sum(t.price_total for t in it.transfers))
        it.checks = [self._priced_fixes(c, hid) for c in check_connections(it)]
        for leg, word in ((it.outbound, "туда"), (it.inbound, "обратно")):
            if leg is None and (outs if word == "туда" else backs):
                it.checks.append(ConnectionCheck(rule="no_transport", ok=False, severity="warning",
                                                 message=f"Дорогу {word} без проблем со стыковкой на эти даты не нашёл.",
                                                 fixes=["сдвинуть даты", "другой вид транспорта"]))
        return it

    def _priced_fixes(self, c: ConnectionCheck, hotel_id: str) -> ConnectionCheck:
        """Подсказки с ценой из каталога: «ранний заезд — 1 200 ₽» вместо «запросить ранний заезд» (L9)."""
        h = self.cat.hotels.get(hotel_id)
        if h is None:
            return c
        fees = {"запросить ранний заезд": ("ранний заезд", h.early_checkin_fee),
                "запросить поздний выезд": ("поздний выезд", h.late_checkout_fee)}
        fixes = []
        for f in c.fixes:
            if f in fees:
                what, fee = fees[f]
                f = f"{what} — {_rub(fee)}" if fee is not None else f"{what} в этом отеле не предоставляется"
            fixes.append(f)
        return c.model_copy(update={"fixes": fixes})

    @staticmethod
    def _pick_hotel(label: str, hotels: list[HotelOffer]) -> HotelOffer:
        if label == "cheapest":
            return hotels[0]
        if label == "most_comfortable":
            return max(hotels, key=lambda h: (h.hotel.rating or 0, -h.price_total))
        rated = [h for h in hotels if h.hotel.rating is not None]
        if not rated:
            return hotels[len(hotels) // 2]
        mid = median(h.hotel.rating for h in rated)
        return min((h for h in rated if h.hotel.rating >= mid), key=lambda h: h.price_total)

    def _transfer(self, label: str, frm: str | None, to: str | None, when: datetime, adults: int, kids: list[int],
                  pets: bool) -> TransferOption | None:
        if not frm or not to:
            return None
        opts = self.cat.transfer_options(frm, to, when, adults, kids, pets)
        def unfit(t: TransferOption) -> bool:      # с животным — без автобусов и электричек; с ребёнком — без долгих
            return t.kind == "public" and (pets or (bool(kids) and t.minutes > KIDS_PUBLIC_MAX_MIN))
        opts = [t for t in opts if not unfit(t)] or opts
        if not opts:
            return None
        if label == "most_comfortable":
            taxi = [t for t in opts if t.kind == "taxi"]
            return min(taxi or opts, key=lambda t: (t.minutes, t.price_total))
        if label == "balanced":
            minute = KIDS_MINUTE_COST if kids else 20                          # минута в пути ≈ 20 ₽, с ребёнком — 50 ₽
            return min(opts, key=lambda t: t.price_total + minute * t.minutes)
        return opts[0]                                                         # отсортированы по цене


def kids_ages(req: TripRequest) -> list[int]:
    """Возрасты детей для поиска: названные; если не названы — по 7 лет на каждого (число — если названо, иначе 1)."""
    if req.children_ages:
        return list(req.children_ages)
    n = req.children_count or (1 if "с детьми" in req.preferences else 0)
    return [ASSUMED_KID_AGE] * n


def _night(it: Itinerary) -> bool:
    """Неудобно для живого человека, хоть и не ошибка: в отеле с 00:00 до 06:00 или выезд из отеля раньше 06:00."""
    if it.outbound:
        t = it.transfers[0].minutes if it.transfers else 0
        ready = it.outbound.arrive + timedelta(minutes=AFTER_ARRIVAL_MIN[it.outbound.kind] + t)
        return ready.hour < NIGHT_END_HOUR
    if it.inbound:
        t = it.transfers[1].minutes if len(it.transfers) > 1 else 0
        leave = it.inbound.depart - timedelta(minutes=BEFORE_DEPARTURE_MIN[it.inbound.kind] + t)
        return leave.hour < NIGHT_END_HOUR
    return False


def _choose(options: list[TransportOption], probe: Callable[[TransportOption], tuple[Itinerary, TransferOption | None]]
            ) -> tuple[TransportOption | None, TransferOption | None]:
    """Первый по порядку вариант без ошибки состыковки и без ночного прибытия/выезда; если таких нет —
    первый без ошибки (ночное время тогда останется видно в предупреждениях)."""
    fallback: tuple[TransportOption | None, TransferOption | None] = (None, None)
    for o in options:
        it, t = probe(o)
        if any(c.severity == "error" for c in check_connections(it)):
            continue
        if not _night(it):
            return o, t
        if fallback[0] is None:
            fallback = (o, t)
    return fallback


def _transport_key(label: str, direction: str) -> Callable[[TransportOption], tuple]:
    """Порядок перебора вариантов дороги: первый без ошибки состыковки и берём."""
    def hour(o: TransportOption) -> int:
        return (o.arrive if direction == "outbound" else o.depart).hour

    def convenient(o: TransportOption) -> bool:
        return 8 <= hour(o) <= 16 if direction == "outbound" else hour(o) >= 13

    def changes(o: TransportOption) -> int:          # у поездов stops — станции в Сочи, это не пересадки
        return o.stops if o.kind == "flight" else 0

    def basic(o: TransportOption) -> bool:          # без багажа / плацкарт / сидячий
        c = o.carrier or ""
        return "без багажа" in c or "плацкарт" in c or "сидячий" in c

    if label == "cheapest":
        return lambda o: (o.price_total,)
    if label == "most_comfortable":
        return lambda o: (changes(o), o.duration_hours > LONG_HOURS, not convenient(o), basic(o), o.duration_hours,
                          o.price_total)
    # «оптимально»: сутки в поезде не выигрывают у рейса, если рейс есть (поезд берём, только когда других нет)
    return lambda o: (changes(o), o.duration_hours > LONG_HOURS, not convenient(o),
                      o.price_total + HOUR_COST * o.duration_hours)


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def _rub(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ") + " ₽"


def explain(its: list[Itinerary]) -> None:
    """L9 explain: чем каждый вариант хуже лучшего по признаку (Itinerary.tradeoffs, интерфейс их показывает)."""
    if len(its) < 2:
        return

    def road_h(it: Itinerary) -> float:
        return sum(t.duration_hours for t in (it.outbound, it.inbound) if t)

    min_total = min(it.total_price for it in its)
    min_road = min(road_h(it) for it in its)
    max_rating = max((it.hotel.hotel.rating or 0) for it in its)
    for it in its:
        t: list[Tradeoff] = []
        if it.total_price > min_total:
            t.append(Tradeoff(criterion="price", this_value=it.total_price, best_value=min_total,
                              note=f"дороже самого дешёвого варианта на {round(100 * (it.total_price / min_total - 1))}%"))
        if road_h(it) - min_road >= 3:
            t.append(Tradeoff(criterion="travel_time", this_value=road_h(it), best_value=min_road,
                              note=f"в дороге дольше на ≈{round(road_h(it) - min_road)} ч"))
        rating = it.hotel.hotel.rating or 0
        if max_rating - rating >= 0.3:
            t.append(Tradeoff(criterion="rating", this_value=rating, best_value=max_rating,
                              note=f"рейтинг отеля {rating} против {max_rating}"))
        if any("без багажа" in (x.carrier or "") for x in (it.outbound, it.inbound) if x):
            t.append(Tradeoff(criterion="baggage", this_value=False, best_value=True, note="перелёт без багажа"))
        if not it.hotel.refundable:
            t.append(Tradeoff(criterion="cancellation", this_value=False, best_value=True,
                              note="без бесплатной отмены"))
        it.tradeoffs = t


def describe(it: Itinerary, n: int, stars: int | None = None, why: list[str] | None = None) -> list[str]:
    """Строки варианта для ответа пользователю: что, когда, сколько, почему (M5) и какие риски."""
    h = it.hotel
    rating = (f", {stars}★" if stars else "") + (f", рейтинг {h.hotel.rating}" if h and h.hotel.rating else "")
    lines = [f"{n}) {LABEL_RU[it.label]}: «{h.hotel.name}»{rating} — {_rub(h.price_total)} за проживание."]
    if why:
        lines.append("   Почему: " + "; ".join(why) + ".")
    road = 0.0
    for t, word in ((it.outbound, "Туда"), (it.inbound, "Обратно")):
        if t:
            road += t.price_total
            lines.append(f"   {word}: {KIND_RU[t.kind]} {t.carrier}, {t.depart:%d.%m %H:%M} → {t.arrive:%d.%m %H:%M}, "
                         f"{_rub(t.price_total)}.")
    if it.transfers:
        tr = it.transfers[0]
        road += sum(t.price_total for t in it.transfers)
        lines.append(f"   Трансфер: {KIND_RU[tr.kind]}, ≈{tr.minutes} мин, туда-обратно "
                     f"{_rub(sum(t.price_total for t in it.transfers))}.")
    lines.append(f"   Итого поездка: {_rub(it.total_price)} (проживание {_rub(h.price_total)} + дорога {_rub(road)}).")
    if it.tradeoffs:
        lines.append("   Минусы: " + "; ".join(t.note for t in it.tradeoffs) + ".")
    lines += [f"   ⚠ {c.message}" + (f" Можно: {', '.join(c.fixes)}." if c.fixes else "")
              for c in it.checks if c.severity in ("warning", "error")]
    return lines


def build_planner() -> TripPlanner | None:
    """Каталог ML, если он есть в репо (папка synth/), иначе None — агент работает на фейках."""
    try:
        from synth.pricing import Catalog
    except ImportError:
        log.info("synth/ не найден — сборка поездки выключена, поиск на фейках")
        return None
    return TripPlanner(Catalog())
