"""Проверки каталога: внутренняя согласованность и покрытие сценариев ТЗ.

Каждая проверка возвращает список проблем; пустой список по всем — данные годны.
"""
from __future__ import annotations

import itertools
from collections import Counter, defaultdict
from datetime import date, timedelta

from harness.checks import check_connections
from harness.contracts import Itinerary
from synth.pricing import Catalog


def consistency(cat: Catalog) -> list[str]:
    bad = []
    # трансфер: время монотонно растёт с км; цена = формула тарифа
    # (сравниваем внутри типа дороги: горная трасса быстрее побережья в пробках)
    for zone_filter in (lambda z: z != "mountains", lambda z: z == "mountains"):
        rows = sorted((cat.route("aer", a)["km"], cat.route("aer", a)["drive_min"], a)
                      for a, ar in cat.areas.items() if zone_filter(ar.zone))
        for (k1, m1, a1), (k2, m2, a2) in itertools.pairwise(rows):
            if k2 > k1 + 1 and m2 < m1:
                bad.append(f"время до {a2} ({k2} км, {m2} мин) меньше, чем до {a1} ({k1} км, {m1} мин)")
    # поезд дольше самолёта из того же города
    for city in cat.origins:
        fl = [f.duration_minutes for f in cat.flights if f.origin_city == city and f.stops == 0]
        tr = [t.duration_minutes for t in cat.trains if t.origin_city == city and t.kind == "train"]
        if fl and tr and min(tr) <= max(fl):
            bad.append(f"{city}: поезд не дольше самолёта")
    # рейс: дальше — дольше и дороже
    direct = sorted({(cat.origins[f.origin_city].air_km, f.duration_minutes) for f in cat.flights if f.stops == 0})
    for (k1, d1), (k2, d2) in itertools.pairwise(direct):
        if k2 > k1 and d2 < d1:
            bad.append(f"рейс {k2} км короче рейса {k1} км")
    # ЖД: классы по цене плацкарт < купе < СВ
    for t in cat.trains:
        c = t.classes
        if {"platzkart", "coupe", "sv"} <= set(c) and not c["platzkart"] < c["coupe"] < c["sv"]:
            bad.append(f"{t.schedule_id}: классы не по возрастанию цены")
    # отели: звёзды ↔ цена в среднем растут; рейтинги в диапазоне; удобства без противоречий
    by_stars = defaultdict(list)
    for h in cat.hotels.values():
        if h.property_type == "hotel":
            by_stars[h.stars].append(h.base_price_per_night / {"low": .8, "medium": 1, "high": 1.3,
                                                                 "premium": 1.7}[cat.areas[h.area_id].price_level])
        if cat.areas[h.area_id].zone == "mountains" and ("private_beach" in h.amenities or h.distance_to_sea_m):
            bad.append(f"{h.hotel_id}: пляж в горах")
        if "ski_in_out" in h.amenities and (h.distance_to_lift_m or 9999) > 150:
            bad.append(f"{h.hotel_id}: ski_in_out далеко от подъёмника")
        if not h.children_allowed and any(r.capacity_children for r in h.room_types):
            bad.append(f"{h.hotel_id}: adults-only, но есть детские места")
        if not h.rate_plans:
            bad.append(f"{h.hotel_id}: нет тарифов")
    avg = {s: sum(v) / len(v) for s, v in by_stars.items()}
    stars = sorted(avg)
    for s1, s2 in itertools.pairwise(stars):
        if avg[s2] <= avg[s1]:
            bad.append(f"средняя цена {s2}* не выше {s1}*")
    return bad


def coverage(cat: Catalog) -> list[str]:
    bad = []
    hotels = list(cat.hotels.values())
    for a in cat.areas:
        hs = [h for h in hotels if h.area_id == a]
        if sum(h.pets_allowed for h in hs) < 2:
            bad.append(f"{a}: <2 pet-friendly")
        if not any(r.capacity_adults >= 2 and r.capacity_children >= 2 for h in hs for r in h.room_types):
            bad.append(f"{a}: нет номера 2+2")
        prices = sorted(h.base_price_per_night for h in hs)
        if prices[-1] < 2 * prices[0]:
            bad.append(f"{a}: нет разброса цен")
    meals = Counter(m for h in hotels for m in h.meal_options)
    for m in ("none", "breakfast", "half_board", "full_board", "all_inclusive"):
        if not meals[m]:
            bad.append(f"нет питания {m}")
    pols = Counter(p.cancellation for h in hotels for p in h.rate_plans)
    if not pols["non_refundable"] or len(pols) < 3:
        bad.append(f"мало политик отмены: {dict(pols)}")
    types = Counter(h.property_type for h in hotels)
    if len(types) < 6:
        bad.append(f"мало типов размещения: {dict(types)}")
    if not any(not h.children_allowed for h in hotels):
        bad.append("нет adults-only")
    msk = [f for f in cat.flights if f.origin_city == "Москва"]
    if not any(f.departure_time < "02:00" for f in msk):
        bad.append("нет ночного рейса из Москвы")
    if not any(t.origin_city == "Москва" and t.kind == "train" for t in cat.trains):
        bad.append("нет поезда из Москвы")
    if not any(f.stops for f in cat.flights):
        bad.append("нет рейсов с пересадкой")
    if not any(t.kind == "bus" for t in cat.trains) or not any(t.kind == "local_train" for t in cat.trains):
        bad.append("нет автобуса/Ласточки дальнего пригорода")
    # сезонность: Поляна зимой дороже лета, побережье — наоборот
    for area, hi, lo in (("rosa_khutor", 2, 9), ("adler", 7, 11)):
        h = next(x for x in hotels if x.area_id == area)
        if cat.night_coefficient(h, date(2027, hi, 3)) <= cat.night_coefficient(h, date(2027 if lo < 11 else 2026, lo, 3)):
            bad.append(f"{area}: сезонность не работает")
    if not any(rv.is_injection for rvs in cat.reviews.values() for rv in rvs):
        bad.append("нет отзывов-инъекций")
    if len(cat.reviews) < len(hotels) * 0.95:
        bad.append(f"отзывы есть только у {len(cat.reviews)} из {len(hotels)} отелей")
    return bad


def example_itinerary(cat: Catalog, origin: str = "Москва", area: str = "adler", checkin: date = date(2026, 11, 10),
                      nights: int = 7, adults: int = 2, children: list[int] | None = None,
                      pets: bool = True) -> tuple[Itinerary | None, dict]:
    """Собрать «сбалансированную» поездку и прогнать её через harness.checks — как будет делать агент."""
    children = [7] if children is None else children
    checkout = checkin + timedelta(days=nights)
    offers = cat.search_hotels(checkin, checkout, adults, children, pets, area_ids=[area])
    if not offers:
        return None, {"error": "нет отелей"}
    hotel = offers[len(offers) // 2]
    h = cat.hotels[hotel.hotel.hotel_id]
    outs = [o for o in cat.transport_options(origin, checkin, "outbound", adults, children, ["flight"]) if o.stops == 0]
    backs = [o for o in cat.transport_options(origin, checkout, "return", adults, children, ["flight"]) if o.stops == 0]
    out = min((o for o in outs if 7 <= o.arrive.hour <= 15), key=lambda o: o.price_total, default=outs[0])
    back = min((o for o in backs if o.depart.hour >= 15), key=lambda o: o.price_total, default=backs[-1])
    t_in = min(cat.transfer_options("aer", h.hotel_id, out.arrive + timedelta(minutes=45), adults, children, pets),
               key=lambda t: t.price_total)
    leave = back.depart - timedelta(hours=2)
    t_out_all = cat.transfer_options(h.hotel_id, "aer", leave, adults, children, pets)
    t_out = min(t_out_all, key=lambda t: t.price_total)
    it = Itinerary(destination_id=area, checkin=checkin, checkout=checkout, outbound=out, inbound=back,
                   transfers=[t_in, t_out], hotel=hotel)
    it.total_price = round(hotel.price_total + out.price_total + back.price_total + t_in.price_total + t_out.price_total)
    it.checks = check_connections(it)
    return it, {"hotel": h.name, "hotel_total": hotel.price_total, "out": f"{out.carrier} {out.depart:%H:%M}→{out.arrive:%H:%M}",
                "back": f"{back.carrier} {back.depart:%H:%M}", "transfer_in": (t_in.kind, t_in.minutes, t_in.price_total),
                "total": it.total_price, "checks": [(c.rule, c.severity, c.message) for c in it.checks]}


def determinism(cat: Catalog) -> list[str]:
    bad = []
    hid = next(iter(cat.hotels))
    q1 = cat.quote_hotel(hid, date(2026, 11, 10), date(2026, 11, 17))
    q2 = cat.quote_hotel(hid, date(2026, 11, 10), date(2026, 11, 17))
    if [q["total"] for q in q1] != [q["total"] for q in q2]:
        bad.append("цены отеля недетерминированы")
    t1 = cat.transport_options("Москва", date(2026, 11, 10))
    t2 = cat.transport_options("Москва", date(2026, 11, 10))
    if [o.price_total for o in t1] != [o.price_total for o in t2]:
        bad.append("цены транспорта недетерминированы")
    return bad


def run_all(cat: Catalog) -> dict[str, list[str]]:
    it, info = example_itinerary(cat)
    errs = [c.message for c in (it.checks if it else []) if c.severity == "error"]
    return {"consistency": consistency(cat), "coverage": coverage(cat), "determinism": determinism(cat),
            "example_itinerary": (["не собралась: " + str(info)] if it is None else errs)}
