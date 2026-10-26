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
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from statistics import median

from harness.checks import AFTER_ARRIVAL_MIN, BEFORE_DEPARTURE_MIN, check_connections
from harness.contracts import (
    ConnectionCheck,
    HotelOffer,
    Itinerary,
    TransferOption,
    TransportOption,
    TripRequest,
)

log = logging.getLogger(__name__)

DEFAULT_NIGHTS = 7
HOUR_COST = 500      # «оптимально»: час в дороге ≈ 500 ₽ — двое суток в поезде не выигрывают у рейса ради экономии
LONG_HOURS = 8       # «комфортно»: дольше — это поезд на сутки и больше
DEFAULT_ADULTS = 2
DEFAULT_ORIGIN = "Москва"
# «из Казани», «из Питера» → город вылета из справочника ML (основа слова → город)
ORIGIN_STEMS = {"москв": "Москва", "питер": "Санкт-Петербург", "петербург": "Санкт-Петербург", "спб": "Санкт-Петербург",
                "екатеринбург": "Екатеринбург", "казан": "Казань", "новосибирск": "Новосибирск", "самар": "Самара",
                "нижн": "Нижний Новгород", "перм": "Пермь", "уф": "Уфа", "воронеж": "Воронеж",
                "ростов": "Ростов-на-Дону", "краснодар": "Краснодар"}
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
        kids = req.children_ages or ([7] if "с детьми" in req.preferences else [])
        if "с детьми" in req.preferences and not req.children_ages:
            out.notes.append("Возраст ребёнка не назвали — считаю 7 лет (цена билетов и мест зависит от возраста).")

        origin = origin_from_text(req.raw_text) or req.origin_city or DEFAULT_ORIGIN
        if origin not in self.cat.origins:
            out.notes.append(f"Из города «{origin}» расписаний нет в демо-данных — считаю дорогу из Москвы.")
            origin = DEFAULT_ORIGIN
        elif not origin_from_text(req.raw_text):
            out.notes.append(f"Город отправления — {origin} (по умолчанию); напишите, если едете из другого.")

        out.hotels = self.cat.search_hotels(checkin, checkout, adults, kids, req.pets, area_ids=[aid],
                                            max_total=req.budget_total, limit=50)
        log.info("trip: %s %s–%s, %d взр., дети %s, животные %s → %d отелей", aid, checkin, checkout, adults, kids,
                 req.pets, len(out.hotels))
        if not out.hotels:
            return out

        kinds = None if "any" in req.transport else [k for k in req.transport if k != "any"]
        outs = [o for d in range(3) for o in self.cat.transport_options(origin, checkin - timedelta(days=d),
                                                                       "outbound", adults, kids, kinds)
                if o.arrive.date() == checkin]          # поезд мог выехать накануне — важно прибыть в день заезда
        backs = self.cat.transport_options(origin, checkout, "return", adults, kids, kinds)
        if not outs or not backs:
            out.notes.append("Дорогу на эти даты в демо-расписании не нашёл — показываю только проживание.")

        seen: set[tuple] = set()
        for label in ("balanced", "cheapest", "most_comfortable"):
            it = self._variant(label, out.hotels, outs, backs, checkin, checkout, adults, kids, req.pets, aid)
            key = (it.hotel.offer_id if it.hotel else None, it.outbound.option_id if it.outbound else None,
                   it.inbound.option_id if it.inbound else None, tuple(t.kind for t in it.transfers))
            if key not in seen:
                seen.add(key)
                out.itineraries.append(it)
        return out

    def _variant(self, label: str, hotels: list[HotelOffer], outs: list[TransportOption], backs: list[TransportOption],
                 checkin: date, checkout: date, adults: int, kids: list[int], pets: bool, aid: str) -> Itinerary:
        hotel = self._pick_hotel(label, hotels)
        it = Itinerary(destination_id=aid, checkin=checkin, checkout=checkout, hotel=hotel, label=label)
        t_in = t_out = None
        for o in sorted(outs, key=_transport_key(label, "outbound")):
            t_in = self._transfer(label, self._point(o.to_city), hotel.hotel.hotel_id,
                                  o.arrive + timedelta(minutes=AFTER_ARRIVAL_MIN[o.kind]), adults, kids, pets)
            probe = Itinerary(destination_id=aid, checkin=checkin, checkout=checkout, hotel=hotel, outbound=o,
                              transfers=[t_in] if t_in else [])
            if not any(c.severity == "error" for c in check_connections(probe)):
                it.outbound = o
                break
        for b in sorted(backs, key=_transport_key(label, "return")):
            leave = b.depart - timedelta(minutes=BEFORE_DEPARTURE_MIN[b.kind] + 60)
            t_out = self._transfer(label, hotel.hotel.hotel_id, self._point(b.from_city), leave, adults, kids, pets)
            probe = Itinerary(destination_id=aid, checkin=checkin, checkout=checkout, hotel=hotel, inbound=b,
                              transfers=[TransferOption(option_id="-", from_place="-", to_place="-", minutes=0,
                                                        price_total=0), t_out] if t_out else [])
            if not any(c.severity == "error" for c in check_connections(probe)):
                it.inbound = b
                break
        # transfers[0] — в отель, [1] — из отеля (так их читает check_connections)
        it.transfers = [t for t in ([t_in] if it.outbound else []) + ([t_out] if it.inbound else []) if t]
        it.total_price = round(hotel.price_total + sum(x.price_total for x in (it.outbound, it.inbound) if x)
                               + sum(t.price_total for t in it.transfers))
        it.checks = check_connections(it)
        for leg, word in ((it.outbound, "туда"), (it.inbound, "обратно")):
            if leg is None and (outs if word == "туда" else backs):
                it.checks.append(ConnectionCheck(rule="no_transport", ok=False, severity="warning",
                                                 message=f"Дорогу {word} без проблем со стыковкой на эти даты не нашёл.",
                                                 fixes=["сдвинуть даты", "другой вид транспорта"]))
        return it

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
        if not opts:
            return None
        if label == "most_comfortable":
            taxi = [t for t in opts if t.kind == "taxi"]
            return min(taxi or opts, key=lambda t: (t.minutes, t.price_total))
        if label == "balanced":
            return min(opts, key=lambda t: t.price_total + 20 * t.minutes)     # минута в пути ≈ 20 ₽
        return opts[0]                                                         # отсортированы по цене


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
    return lambda o: (changes(o), not convenient(o), o.price_total + HOUR_COST * o.duration_hours)


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def _rub(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ") + " ₽"


def describe(it: Itinerary, n: int) -> list[str]:
    """Строки варианта для ответа пользователю: что, когда, сколько и какие риски."""
    h = it.hotel
    rating = f", рейтинг {h.hotel.rating}" if h and h.hotel.rating else ""
    lines = [f"{n}) {LABEL_RU[it.label]}: «{h.hotel.name}»{rating} — {_rub(h.price_total)} за проживание."]
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
