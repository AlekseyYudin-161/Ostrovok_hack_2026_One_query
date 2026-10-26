"""Офферы на даты из каталога synth/data — детерминированно (хэш от id и даты, без глобального random).

Выход — модели harness/contracts.py, поэтому функции можно оборачивать в инструменты агента как есть.
Один и тот же запрос всегда даёт ту же цену; сдвиг дат даёт новую, но согласованную цену.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, time, timedelta, timezone
from functools import cached_property
from pathlib import Path
from statistics import median

from harness.contracts import DestinationOption, HotelCandidate, HotelOffer, TransferOption, TransportOption
from synth import geo
from synth.schema import (
    POI,
    Area,
    ClimateRow,
    FlightSchedule,
    Hotel,
    LocalLine,
    OriginCity,
    RatePlan,
    ReferencePoint,
    Review,
    RoomType,
    TrainSchedule,
    TransferTariff,
)

DATA = Path(__file__).resolve().parent / "data"
MSK = timezone(timedelta(hours=3))
CLASS_RU = {"platzkart": "плацкарт", "coupe": "купе", "sv": "СВ", "seat": "сидячий"}


def _u(*parts) -> float:
    """Детерминированное «случайное» число [0, 1) от аргументов."""
    h = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return int(h[:12], 16) / 16 ** 12




def _hhmm(s: str) -> time:
    h, m = map(int, s.split(":"))
    return time(h, m)


def _nights(checkin: date, checkout: date) -> list[date]:
    return [checkin + timedelta(days=i) for i in range((checkout - checkin).days)]


def _child_count(children_ages: list[int], min_age: int = 0) -> int:
    return sum(1 for a in children_ages if a >= min_age)


class Catalog:
    def __init__(self, data_dir: Path = DATA) -> None:
        self.dir = data_dir
        _load = self._load
        self.areas = {a["area_id"]: Area(**a) for a in _load("areas.json")}
        self.hotels = {h["hotel_id"]: Hotel(**h) for h in _load("hotels.json")}
        self.points = {p["point_id"]: ReferencePoint(**p) for p in _load("reference_points.json")}
        self.climate = [ClimateRow(**c) for c in _load("climate.json")]
        tr = _load("transport_schedules.json")
        self.flights = [FlightSchedule(**f) for f in tr["flights"]]
        self.trains = [TrainSchedule(**t) for t in tr["trains"]]
        self.lines = [LocalLine(**ln) for ln in tr["local_lines"]]
        self.origins = {o["city"]: OriginCity(**o) for o in tr["origins"]}
        self.tariffs = [TransferTariff(**t) for t in _load("transfer_tariffs.json")]
        self.rules = _load("pricing_rules.json")
        self.edge = _load("edge_cases.json")
        self.poi = [POI(**p) for p in _load("poi.json")]

    def _load(self, name: str):
        return json.loads((self.dir / name).read_text(encoding="utf-8"))

    @cached_property
    def reviews(self) -> dict[str, list[Review]]:
        out: dict[str, list[Review]] = {}
        for line in (self.dir / "reviews.jsonl").read_text(encoding="utf-8").splitlines():
            rv = Review(**json.loads(line))
            out.setdefault(rv.hotel_id, []).append(rv)
        return out

    # ------------------------------------------------------------ сезон и цена ночи
    def season(self, area_id: str, d: date) -> str:
        return self.areas[area_id].season_calendar[d.month]

    def night_coefficient(self, hotel: Hotel, d: date) -> float:
        area = self.areas[hotel.area_id]
        c = area.season_coefficients[self.season(hotel.area_id, d)]
        if d.isoweekday() in self.rules["weekend_nights"]:
            c *= self.rules["weekend_coefficient"]
        for start, end, k in self.rules["holidays"]:
            if date.fromisoformat(start) <= d <= date.fromisoformat(end):
                c *= k
        lo, hi = self.rules["occupancy_coefficient_range"]
        return c * (lo + (hi - lo) * _u("occ", hotel.hotel_id, d))

    def _closed(self, hotel_id: str, d: date) -> str | None:
        for rule in self.edge["sold_out"]:
            if hotel_id in rule["hotel_ids"] and date.fromisoformat(rule["from"]) <= d < date.fromisoformat(rule["to"]):
                return rule["reason"]
        for rule in self.edge["seasonal_closures"]:
            if hotel_id in rule["hotel_ids"] and d.month in rule["months"]:
                return rule["reason"]
        return None

    def room_available(self, hotel: Hotel, room: RoomType, d: date) -> bool:
        if self._closed(hotel.hotel_id, d):
            return False
        return _u("avail", hotel.hotel_id, room.room_type_id, d) < self.rules["hotel_availability_probability"]

    # ------------------------------------------------------------ отели
    def _fits(self, hotel: Hotel, room: RoomType, adults: int, children_ages: list[int], pets: bool) -> bool:
        kids = len(children_ages)
        if kids and (not hotel.children_allowed or min(children_ages) < hotel.min_child_age):
            return False
        if pets and not hotel.pets_allowed:
            return False
        if adults > room.capacity_adults:
            return False
        free_adult_beds = room.capacity_adults - adults
        return kids <= room.capacity_children + free_adult_beds

    def quote_hotel(self, hotel_id: str, checkin: date, checkout: date, adults: int = 2,
                    children_ages: list[int] | None = None, pets: bool = False) -> list[dict]:
        """Все тарифы отеля на даты с разбивкой цены. Пусто — не подходит или нет мест."""
        children_ages = children_ages or []
        h = self.hotels[hotel_id]
        nights = _nights(checkin, checkout)
        if not nights:
            return []
        meal_pp = self.rules["meal_price_per_person_night"]
        meal_k = 1.4 if (h.stars or 3) >= 4 else 1.0
        out = []
        for room in h.room_types:
            if not self._fits(h, room, adults, children_ages, pets):
                continue
            if not all(self.room_available(h, room, d) for d in nights):
                continue
            room_nights = [round(h.base_price_per_night * room.price_coefficient * self.night_coefficient(h, d), -1)
                           for d in nights]
            extra_kids = max(0, len(children_ages) - room.capacity_children)
            for rp in (p for p in h.rate_plans if p.room_type_id == room.room_type_id):
                accommodation = sum(room_nights) * rp.price_coefficient
                persons = adults + len(children_ages) * self.rules["meal_child_coefficient"]
                meal = meal_pp[rp.meal] * meal_k * persons * len(nights)
                pet = h.pet_fee_per_night * len(nights) if pets else 0
                extra = extra_kids * self.rules["extra_bed_child_fee"] * len(nights)
                tax = (accommodation + extra) * self.rules["tourist_tax_rate"]
                total = round(accommodation + meal + pet + extra + tax)
                out.append({
                    "offer_id": f"{hotel_id}__{rp.rate_id}__{checkin}__{checkout}", "hotel_id": hotel_id,
                    "room_type_id": room.room_type_id, "room": room.name, "rate": rp, "meal": rp.meal,
                    "nights": len(nights), "price_per_night_avg": round(sum(room_nights) / len(nights)),
                    "breakdown": {"accommodation": round(accommodation), "meal": round(meal), "pet_fee": pet,
                                  "extra_bed": extra, "tourist_tax": round(tax)},
                    "total": total, "free_cancel_until": self._free_cancel(rp, checkin),
                    # не входят в total — платные опции; None — отель их не предоставляет
                    "early_checkin_fee": h.early_checkin_fee, "late_checkout_fee": h.late_checkout_fee,
                })
        return sorted(out, key=lambda x: x["total"])

    @staticmethod
    def _free_cancel(rp: RatePlan, checkin: date) -> datetime | None:
        if rp.free_cancel_days is None:
            return None
        return datetime.combine(checkin - timedelta(days=rp.free_cancel_days), time(23, 59), MSK)

    def candidate(self, hotel_id: str, quote: dict | None = None, call_id: str = "catalog",
                  tool: str = "synth_hotel_search") -> HotelCandidate:
        h = self.hotels[hotel_id]
        return HotelCandidate(
            hotel_id=h.hotel_id, name=h.name, city=self.areas[h.area_id].name,
            price_total=quote["total"] if quote else None, currency="RUB", rating=h.rating,
            amenities=h.amenities, meal=quote["meal"] if quote else None,
            cancellation=quote["rate"].cancellation if quote else None, source_tool=tool, source_call_id=call_id)

    def to_offer(self, q: dict, call_id: str = "catalog") -> HotelOffer:
        h = self.hotels[q["hotel_id"]]
        return HotelOffer(offer_id=q["offer_id"], hotel=self.candidate(h.hotel_id, q, call_id), room=q["room"],
                          meal=q["meal"], refundable=q["free_cancel_until"] is not None,
                          free_cancel_until=q["free_cancel_until"], price_total=q["total"],
                          checkin_time=h.check_in_time, checkout_time=h.check_out_time)

    def search_hotels(self, checkin: date, checkout: date, adults: int = 2, children_ages: list[int] | None = None,
                      pets: bool = False, area_ids: list[str] | None = None, max_total: float | None = None,
                      meal: str | None = None, refundable: bool | None = None, min_stars: int | None = None,
                      amenities: list[str] | None = None, limit: int = 20) -> list[HotelOffer]:
        """Лучший (самый дешёвый подходящий) тариф каждого отеля."""
        call_id = uuid.uuid4().hex[:8]
        out = []
        for h in self.hotels.values():
            if area_ids and h.area_id not in area_ids:
                continue
            if min_stars and (h.stars or 0) < min_stars:
                continue
            if amenities and not set(amenities) <= set(h.amenities):
                continue
            qs = [q for q in self.quote_hotel(h.hotel_id, checkin, checkout, adults, children_ages, pets)
                  if (meal is None or q["meal"] == meal)
                  and (refundable is None or (q["free_cancel_until"] is not None) == refundable)
                  and (max_total is None or q["total"] <= max_total)]
            if qs:
                out.append(self.to_offer(qs[0], call_id))
        return sorted(out, key=lambda o: o.price_total)[:limit]

    # ------------------------------------------------------------ транспорт
    def _tz(self, city: str) -> timezone:
        return timezone(timedelta(hours=self.origins[city].tz_offset))

    def _transport_ok(self, schedule_id: str, d: date) -> bool:
        ds = str(d)
        for rule in self.edge["cancelled_departures"] + self.edge["no_seats"]:
            if rule["schedule_id"] == schedule_id and ds in rule["dates"]:
                return False
        return _u("seats", schedule_id, d) < self.rules["transport_seats_probability"]

    def _transport_coef(self, schedule_id: str, d: date) -> float:
        season = self.areas["adler"].season_calendar[d.month]
        c = self.rules["transport_season_coefficient"][season]
        for start, end, k in self.rules["holidays"]:
            if date.fromisoformat(start) - timedelta(days=2) <= d <= date.fromisoformat(end):
                c *= k
        if d.isoweekday() in (5, 7):
            c *= 1.1
        lo, hi = self.rules["transport_jitter"]
        return c * (lo + (hi - lo) * _u("tprice", schedule_id, d))

    def _pax_price(self, adult: float, adults: int, children_ages: list[int], kind: str) -> float:
        cd = self.rules["child_discount"]
        total = adult * adults
        for a in children_ages:
            if kind == "flight":
                total += adult * (cd["infant_flight"] if a < 2 else cd["flight"] if a < 12 else 1)
            else:
                total += adult * (0 if a < 5 else cd["train"] if a < 10 else 1)
        return total

    def transport_options(self, origin_city: str, travel_date: date, direction: str = "outbound", adults: int = 2,
                          children_ages: list[int] | None = None, kinds: list[str] | None = None) -> list[TransportOption]:
        """Рейсы/поезда/автобусы в Сочи (outbound) или из Сочи (return), отправлением в travel_date."""
        children_ages = children_ages or []
        kinds = kinds or ["flight", "train", "local_train", "bus"]
        out: list[TransportOption] = []
        tz_city = self._tz(origin_city)
        if "flight" in kinds:
            for f in self.flights:
                if f.origin_city != origin_city or f.direction != direction:
                    continue
                if travel_date.month not in f.valid_months or travel_date.isoweekday() not in f.operating_days:
                    continue
                if not self._transport_ok(f.schedule_id, travel_date):
                    continue
                tz_dep = tz_city if direction == "outbound" else MSK
                tz_arr = MSK if direction == "outbound" else tz_city
                dep = datetime.combine(travel_date, _hhmm(f.departure_time), tz_dep)
                arr = (dep + timedelta(minutes=f.duration_minutes)).astimezone(tz_arr)
                adult = f.base_adult_price * self._transport_coef(f.schedule_id, travel_date)
                frm, to = ((f"{origin_city} ({f.origin_airport})", self.points[f.point_id].name)
                           if direction == "outbound" else (self.points[f.point_id].name,
                                                            f"{origin_city} ({f.origin_airport})"))
                carrier = f"{f.carrier} {f.route_number}" + ("" if f.baggage_included else ", без багажа") + (
                    f", пересадка {f.via} {f.layover_minutes // 60} ч {f.layover_minutes % 60} мин" if f.stops else "")
                out.append(TransportOption(
                    option_id=f"{f.schedule_id}__{travel_date}", kind="flight", carrier=carrier, from_city=frm,
                    to_city=to, depart=dep, arrive=arr, duration_hours=round(f.duration_minutes / 60, 2),
                    stops=f.stops, price_total=round(self._pax_price(adult, adults, children_ages, "flight"), -1)))
        for t in self.trains:
            if t.kind not in kinds or t.origin_city != origin_city or t.direction != direction:
                continue
            if travel_date.month not in t.valid_months or not self._transport_ok(t.schedule_id, travel_date):
                continue
            dep = datetime.combine(travel_date, _hhmm(t.departure_time), tz_city if direction == "outbound" else MSK)
            k = self._transport_coef(t.schedule_id, travel_date)
            if direction == "outbound":
                pid, mins = max(t.stops.items(), key=lambda x: x[1])      # конечная в Сочи
                frm, to = origin_city, self.points[pid].name
            else:
                pid, mins = min(t.stops.items(), key=lambda x: x[1])      # станция посадки
                frm, to = self.points[pid].name, origin_city
                mins = t.duration_minutes
            arr = (dep + timedelta(minutes=mins)).astimezone(MSK if direction == "outbound" else tz_city)
            for cls, price in t.classes.items():
                if _u("cls", t.schedule_id, cls, travel_date) > .85:
                    continue                                              # класс распродан
                out.append(TransportOption(
                    option_id=f"{t.schedule_id}__{cls}__{travel_date}", kind=t.kind,
                    carrier=f"{t.carrier} №{t.route_number}, {CLASS_RU[cls]}", from_city=frm, to_city=to,
                    depart=dep, arrive=arr, duration_hours=round(mins / 60, 2), stops=len(t.stops) - 1,
                    price_total=round(self._pax_price(price * k, adults, children_ages, "train"), -1)))
        return sorted(out, key=lambda o: o.depart)

    def train_stops(self, option_id: str) -> dict[str, int]:
        """Все станции поезда в Сочи (point_id -> мин от отправления) — чтобы выйти ближе к отелю."""
        sid = option_id.split("__")[0]
        return next(t.stops for t in self.trains if t.schedule_id == sid)

    # ------------------------------------------------------------ трансферы
    def _place(self, ref: str) -> tuple[dict, float, str]:
        if ref in self.points:
            p = self.points[ref]
            return self.areas[p.area_id].model_dump(), p.offset_km, p.name
        if ref in self.hotels:
            h = self.hotels[ref]
            return self.areas[h.area_id].model_dump(), h.km_to_area_center, h.name
        if ref in self.areas:
            return self.areas[ref].model_dump(), 0.0, self.areas[ref].name
        raise KeyError(ref)

    def route(self, from_ref: str, to_ref: str) -> dict:
        a, la, _ = self._place(from_ref)
        b, lb, _ = self._place(to_ref)
        return geo.route(a, b, la, lb)

    def transfer_options(self, from_ref: str, to_ref: str, when: datetime, adults: int = 2,
                         children_ages: list[int] | None = None, pets: bool = False,
                         luggage: int | None = None) -> list[TransferOption]:
        """from_ref/to_ref: point_id, hotel_id или area_id. Время в пути выведено из км."""
        children_ages = children_ages or []
        pax = adults + len(children_ages)
        luggage = pax if luggage is None else luggage
        a, _, a_name = self._place(from_ref)
        b, _, b_name = self._place(to_ref)
        rt = self.route(from_ref, to_ref)
        night = when.hour >= 23 or when.hour < 6
        out: list[TransferOption] = []
        for t in self.tariffs:
            if t.kind == "shuttle":
                if pets or "aer" not in (from_ref, to_ref):
                    continue
                price = (t.base_price + t.price_per_km * rt["km"]) * pax
                minutes = rt["drive_min"] + 20 + 15                      # сбор группы + заезды по отелям
            else:
                if pax > t.capacity or luggage > t.luggage or (pets and not t.pets_allowed):
                    continue
                price = (t.base_price + t.price_per_km * rt["km"]) * (t.night_coefficient if night else 1)
                price += t.child_seat_fee * sum(1 for x in children_ages if x < 12) + (t.pet_fee if pets else 0)
                minutes = rt["drive_min"]
            out.append(TransferOption(option_id=f"tf__{t.tariff_id}__{from_ref}__{to_ref}__{when:%Y%m%dT%H%M}",
                                      from_place=a_name, to_place=b_name, minutes=int(minutes),
                                      price_total=round(price, -1), kind=t.kind))
        pub = self._public(a, b, when, pax, rt)
        if pub:
            out.append(TransferOption(option_id=f"tf__public__{from_ref}__{to_ref}__{when:%Y%m%dT%H%M}",
                                      from_place=a_name, to_place=b_name, minutes=pub[0], price_total=pub[1],
                                      kind="public"))
        return sorted(out, key=lambda o: o.price_total)

    def _public(self, a: dict, b: dict, when: datetime, pax: int, rt: dict) -> tuple[int, float] | None:
        best = None
        for ln in self.lines:
            if a["area_id"] not in ln.stops or b["area_id"] not in ln.stops or a["area_id"] == b["area_id"]:
                continue
            first, last = _hhmm(ln.first_departure), _hhmm(ln.last_departure)
            if not first <= when.time() <= last:
                continue
            pos = geo.line_positions({k: v.model_dump() for k, v in self.areas.items()}, ln.stops)
            i, j = ln.stops.index(a["area_id"]), ln.stops.index(b["area_id"])
            speed = 50 if ln.kind == "local_train" else 35
            ride = geo.line_minutes(pos, i, j, speed)
            walk = int(rt["local_km"] / 4.5 * 60) if rt["local_km"] < 1.5 else 15   # пешком или маршрутка
            minutes = ride + walk + min(self.rules["public_wait_min"], ln.interval_minutes // 2)
            price = (ln.base_price + ln.price_per_km * abs(pos[j] - pos[i])) * pax
            if best is None or minutes < best[0]:
                best = (minutes, round(price, -1))
        return best

    # ------------------------------------------------------------ вдохновение
    def climate_for(self, area_id: str, month: int) -> ClimateRow:
        zone = self.areas[area_id].zone
        return next(c for c in self.climate if c.zone == zone and c.month == month)

    def destination_options(self, month: int, origin_city: str = "Москва", adults: int = 2,
                            children_ages: list[int] | None = None, nights: int = 7,
                            year: int | None = None) -> list[DestinationOption]:
        """Карточки районов на месяц: погода, дорога, оценка стоимости поездки целиком (медианный отель)."""
        children_ages = children_ages or []
        year = year or (2026 if month >= 11 else 2027)
        d = date(year, month, 10)
        flights = [o for o in self.transport_options(origin_city, d, "outbound", adults, children_ages, ["flight"])]
        backs = self.transport_options(origin_city, d + timedelta(days=nights), "return", adults, children_ages,
                                       ["flight"])
        trip = (median(o.price_total for o in flights) + median(o.price_total for o in backs)) if flights and backs else None
        fly_h = min((o.duration_hours for o in flights), default=None)
        out = []
        for a in self.areas.values():
            cl = self.climate_for(a.area_id, month)
            hotels = [h for h in self.hotels.values() if h.area_id == a.area_id and h.property_type != "guest_house"]
            stay = median(h.base_price_per_night * a.season_coefficients[a.season_calendar[month]] for h in hotels)
            rt = self.route("aer", a.area_id)
            transfer = 2 * (650 + 50 * rt["km"])
            total = (trip or 0) + stay * nights + transfer
            why = [f"воздух днём ≈{cl.air_day_c:.0f}°C" + (f", море ≈{cl.sea_c:.0f}°C" if cl.sea_c is not None else "")]
            why.append("купальный сезон" if cl.swim_ok else ("горнолыжный сезон открыт" if cl.ski_open else
                                                            "не купальный сезон"))
            why.append(f"от аэропорта {rt['drive_min']} мин ({rt['km']:.0f} км)")
            why.append(f"сезон: {a.season_calendar[month]}")
            out.append(DestinationOption(
                destination_id=a.area_id, name=a.name, best_for=a.best_for, why=why, air_temp_c=cl.air_day_c,
                sea_temp_c=cl.sea_c, travel_hours=round((fly_h or 0) + rt["drive_min"] / 60, 1) if fly_h else None,
                direct=any(o.stops == 0 for o in flights) if flights else None,
                est_total_price=round(total, -2), visa="не нужна"))
        return out

    def poi_for(self, area_id: str, month: int | None = None) -> list[POI]:
        return [p for p in self.poi if p.area_id == area_id and (month is None or month in p.months_open)]
