"""Генерация каталога: Python задаёт «слоты» (структура, квоты, числа), GLM дописывает тексты.

Так распределения и покрытие сценариев контролируются кодом, а разнообразие названий, описаний,
номеров и отзывов даёт LLM.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from datetime import date

from synth import geo
from synth.llm import GLM
from synth.refdata import (
    AREAS,
    CATALOG_FROM,
    CATALOG_TO,
    COAST_COEF,
    COAST_SEASON,
    FLIGHT,
    INJECTION_REVIEWS,
    LOCAL_LINES,
    MOUNTAIN_COEF,
    MOUNTAIN_SEASON,
    ORIGINS,
    PRICING,
    REFERENCE_POINTS,
    SEED,
    TRAIN,
)
from synth.schema import (
    AMENITIES,
    AREA_BEST_FOR,
    HOTEL_TAGS,
    POI,
    REVIEW_ASPECTS,
    Area,
    Carriers,
    FlightSchedule,
    Hotel,
    RatePlan,
    Review,
    RoomType,
    TrainSchedule,
)

SYSTEM = ("Ты генерируешь синтетический каталог для демо travel-агента по курорту «Большой Сочи». "
          "Все отели, люди и компании вымышленные, но звучат естественно и разнообразно (без шаблонных "
          "«Лазурный берег» у каждого второго). Пиши по-русски. Отвечай строго одним JSON-объектом без markdown.")


def rng_for(*parts) -> random.Random:
    h = hashlib.sha256(json.dumps([SEED, *parts], ensure_ascii=False).encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def slug(text: str) -> str:
    tr = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                  ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s",
                   "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))
    s = "".join(tr.get(c, c) for c in text.lower())
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")[:40] or "x"


# ---------------------------------------------------------------- районы
async def gen_areas(glm: GLM) -> list[Area]:
    skel = [{k: a[k] for k in ("area_id", "name", "zone", "price_level", "vacation_types")} for a in AREAS]
    user = (f"Районы курорта (скелет): {json.dumps(skel, ensure_ascii=False)}\n"
            f"Для каждого района верни: area_id, description (3–4 предложения: характер, кому подходит, "
            f"минусы), vibe (одна фраза), best_for (2–4 из {AREA_BEST_FOR}), tags (3–6 коротких тегов).\n"
            'Формат: {"areas": [{"area_id": ..., "description": ..., "vibe": ..., "best_for": [...], "tags": [...]}]}')

    def check(d):
        got = {x["area_id"]: x for x in d["areas"]}
        assert set(got) == {a["area_id"] for a in AREAS}, "не все районы"
        return got

    texts = await glm.json(SYSTEM, user, tag="areas", validate=check)
    out = []
    for a in AREAS:
        t = texts[a["area_id"]]
        mountain = a["zone"] == "mountains"
        out.append(Area(**{k: v for k, v in a.items() if k != "n_hotels"},
                        season_calendar=MOUNTAIN_SEASON if mountain else COAST_SEASON,
                        season_coefficients=MOUNTAIN_COEF if mountain else COAST_COEF,
                        description=t["description"], vibe=t["vibe"],
                        best_for=[b for b in t["best_for"] if b in AREA_BEST_FOR], tags=t["tags"]))
    return out


async def gen_carriers(glm: GLM) -> Carriers:
    user = ("Придумай вымышленных перевозчиков: 6 авиакомпаний (лоукост, национальная, региональные), "
            "3 ЖД-перевозчика дальнего следования, 2 местных перевозчика (пригородные поезда, автобусы). "
            'Только названия строками, без кавычек-ёлочек. Формат: {"airlines": ["..."], "rail": ["..."], "local": ["..."]}')

    def check(d):
        names = {k: [(x["name"] if isinstance(x, dict) else x).strip("«»\" ") for x in d[k]]
                 for k in ("airlines", "rail", "local")}
        assert len(names["airlines"]) >= 5 and names["rail"] and len(names["local"]) >= 2
        return Carriers(**names)

    return await glm.json(SYSTEM, user, tag="carriers", validate=check)


# ---------------------------------------------------------------- отели: слоты
TYPE_MIX = {
    "coast": {"hotel": .40, "guest_house": .28, "apartments": .14, "sanatorium": .10, "resort": .08},
    "imeretinka": {"hotel": .50, "apartments": .25, "resort": .15, "guest_house": .10},
    "mountains": {"hotel": .45, "apartments": .20, "guest_house": .15, "glamping": .10, "resort": .10},
}
STARS_BY_LEVEL = {"low": [2, 3, 3, 3, 4], "medium": [3, 3, 4, 4, 5], "high": [3, 4, 4, 5, 5],
                  "premium": [4, 4, 5, 5, 5]}
BASE_PRICE = {("hotel", 2): 2800, ("hotel", 3): 4200, ("hotel", 4): 7500, ("hotel", 5): 15000,
              ("guest_house", None): 2600, ("apartments", None): 4000, ("sanatorium", 3): 5500,
              ("sanatorium", 4): 7500, ("resort", 4): 11000, ("resort", 5): 17000, ("glamping", None): 7000}
LEVEL_MULT = {"low": 0.8, "medium": 1.0, "high": 1.3, "premium": 1.7}
PET_PROB = {"guest_house": .45, "apartments": .35, "hotel": .25, "glamping": .6, "resort": .2, "sanatorium": .05}
REQUIRED_AMENITIES = {"resort": ["pool", "restaurant", "spa", "kids_club"], "sanatorium": ["medical_treatment",
                      "restaurant"], "hotel": ["wifi"], "apartments": ["kitchen", "wifi"], "guest_house": ["wifi"],
                      "glamping": ["bbq"]}


def _allocate(mix: dict[str, float], n: int) -> list[str]:
    raw = {k: v * n for k, v in mix.items()}
    counts = {k: max(1, int(v)) for k, v in raw.items()}
    while sum(counts.values()) < n:
        k = max(raw, key=lambda k: raw[k] - counts[k])
        counts[k] += 1
    while sum(counts.values()) > n:
        k = max(counts, key=lambda k: counts[k])
        counts[k] -= 1
    return [k for k, c in counts.items() for _ in range(c)]


def _meals(ptype: str, stars: int | None, r: random.Random) -> list[str]:
    if ptype == "guest_house":
        return ["none", "breakfast"] if r.random() < .5 else ["none"]
    if ptype == "apartments":
        return ["none", "breakfast"] if r.random() < .2 else ["none"]
    if ptype == "glamping":
        return ["breakfast"] + (["half_board"] if r.random() < .4 else [])
    if ptype == "sanatorium":
        return ["full_board"] + (["all_inclusive"] if r.random() < .35 else [])
    if ptype == "resort":
        return ["breakfast", "half_board", "all_inclusive"] + (["full_board"] if r.random() < .3 else [])
    if stars and stars >= 4:
        m = ["breakfast", "half_board"] + (["none"] if r.random() < .3 else [])
        return m + (["all_inclusive"] if stars == 5 and r.random() < .25 else [])
    return ["none", "breakfast"] + (["half_board"] if r.random() < .3 else [])


def build_slots(area: dict) -> list[dict]:
    r = rng_for("slots", area["area_id"])
    mix = dict(TYPE_MIX[area["zone"]])
    if area["area_id"] == "matsesta":
        mix["sanatorium"] = .35
    types = _allocate(mix, area["n_hotels"])
    r.shuffle(types)
    slots = []
    for i, ptype in enumerate(types):
        stars = None
        if ptype == "hotel":
            stars = r.choice(STARS_BY_LEVEL[area["price_level"]])
        elif ptype == "resort":
            stars = r.choice([4, 5, 5])
        elif ptype == "sanatorium":
            stars = r.choice([3, 4])
        coast = area["zone"] != "mountains"
        line = None
        if coast:
            w = [.5, .3, .2] if ptype in ("resort", "sanatorium") else [.22, .35, .43]
            line = r.choices([1, 2, 3], w)[0]
        lift = None
        if not coast:
            lift = r.randint(50, 1500) if area["area_id"] == "rosa_khutor" else r.randint(1500, 7000)
        adults_only = ptype in ("hotel", "guest_house") and r.random() < .08
        slots.append({
            "slot": f"{area['area_id']}_{i:02d}", "property_type": ptype, "stars": stars, "beach_line": line,
            "distance_to_lift_m": lift, "pets_allowed": r.random() < PET_PROB[ptype], "adults_only": adults_only,
            "family_room": not adults_only and r.random() < .7, "meal_options": _meals(ptype, stars, r),
            "price_level": area["price_level"]})
    pets = [s for s in slots if s["pets_allowed"]]
    for s in slots:                                   # ≥2 pet-friendly в каждом районе
        if len(pets) >= 2:
            break
        if not s["pets_allowed"] and s["property_type"] != "sanatorium":
            s["pets_allowed"] = True
            pets.append(s)
    if not any(s["family_room"] for s in slots):     # ≥1 номер на 2+2
        slots[0].update(family_room=True, adults_only=False)
    return slots


def _slot_brief(s: dict) -> dict:
    out = {"slot": s["slot"], "тип": s["property_type"], "звёзды": s["stars"], "питание": s["meal_options"],
           "с животными": s["pets_allowed"], "только взрослые": s["adults_only"],
           "нужен семейный номер 2+2": s["family_room"], "ценовой уровень района": s["price_level"]}
    if s["beach_line"]:
        out["линия от моря"] = s["beach_line"]
    if s["distance_to_lift_m"]:
        out["до подъёмника, м"] = s["distance_to_lift_m"]
    return out


async def gen_hotels(glm: GLM, area: Area, slots: list[dict], batch: int = 6) -> list[dict]:
    """Вернуть сырые LLM-карточки по слотам района (батчами)."""
    async def one(chunk: list[dict], k: int) -> list[dict]:
        user = (
            f"Район: {area.name} ({area.zone}). {area.description}\n"
            f"Слоты объектов размещения: {json.dumps([_slot_brief(s) for s in chunk], ensure_ascii=False)}\n"
            "Для каждого слота придумай объект, строго соблюдая параметры слота:\n"
            "- name: оригинальное название (отель, гостевой дом, апарт-комплекс, санаторий, курорт, глэмпинг — "
            "по типу; можно без слова «отель»);\n"
            "- address: вымышленная улица и номер дома;\n"
            "- description: 3–4 предложения, конкретно: что рядом, какой номерной фонд, атмосфера, "
            "одна честная особенность или минус;\n"
            f"- amenities: 5–12 из {AMENITIES};\n"
            f"- tags: 2–5 из {HOTEL_TAGS};\n"
            "- room_types: 2–4 типа: name, capacity_adults, capacity_children, area_m2, "
            "view (sea|mountain|city|garden|courtyard|none), beds. Если «нужен семейный номер 2+2» — хотя бы "
            "один номер на 2 взрослых + 2 детей; для «только взрослые» capacity_children=0.\n"
            'Формат: {"hotels": [{"slot": ..., "name": ..., "address": ..., "description": ..., '
            '"amenities": [...], "tags": [...], "room_types": [...]}]}')

        def check(d):
            got = {h["slot"]: h for h in d["hotels"]}
            assert {s["slot"] for s in chunk} <= set(got), "не все слоты"
            for h in got.values():
                assert h["room_types"] and h["name"] and h["description"]
            return [got[s["slot"]] for s in chunk]

        return await glm.json(SYSTEM, user, tag=f"hotels_{area.area_id}_{k}", validate=check)

    chunks = [slots[i:i + batch] for i in range(0, len(slots), batch)]
    res = await asyncio.gather(*(one(c, k) for k, c in enumerate(chunks)))
    return [h for part in res for h in part]


def _room_coef(rt: dict) -> float:
    cap = rt["capacity_adults"] + rt["capacity_children"] * 0.5
    c = 1 + 0.15 * (cap - 2) + (rt.get("area_m2", 22) - 22) / 120 + (0.15 if rt.get("view") == "sea" else 0)
    return max(0.8, min(c, 3.5))


def finalize_hotel(area: Area, slot: dict, card: dict, idx: int) -> Hotel:
    r = rng_for("hotel", slot["slot"])
    ptype, stars = slot["property_type"], slot["stars"]
    base = BASE_PRICE.get((ptype, stars)) or BASE_PRICE.get((ptype, None)) or 5000
    base = round(base * LEVEL_MULT[area.price_level] * r.uniform(.85, 1.2), -2)

    amen = [a for a in dict.fromkeys(card["amenities"] + REQUIRED_AMENITIES.get(ptype, [])) if a in AMENITIES]
    coast = area.zone != "mountains"
    drop = set()
    if not coast:
        drop |= {"private_beach", "beach_shuttle", "sea_view"}
    if slot["beach_line"] != 1:
        drop.add("private_beach")
    if not slot["distance_to_lift_m"] or slot["distance_to_lift_m"] > 150:
        drop.add("ski_in_out")
    if coast:
        drop |= {"ski_storage", "ski_in_out", "ski_rental"}
    if slot["adults_only"]:
        drop |= {"kids_club", "playground", "kids_pool", "babysitting"}
    amen = [a for a in amen if a not in drop]
    if slot["beach_line"] == 1 and ptype in ("resort", "sanatorium") and "private_beach" not in amen:
        amen.append("private_beach")

    rooms: list[RoomType] = []
    seen = set()
    for rt in card["room_types"]:
        rid = slug(rt["name"])
        while rid in seen:
            rid += "_2"
        seen.add(rid)
        kids = 0 if slot["adults_only"] else int(rt.get("capacity_children", 0))
        view = rt.get("view") if rt.get("view") in ("sea", "mountain", "city", "garden", "courtyard") else "none"
        if not coast and view == "sea":
            view = "mountain"
        rooms.append(RoomType(room_type_id=rid, name=rt["name"], capacity_adults=max(1, int(rt["capacity_adults"])),
                              capacity_children=kids, area_m2=int(rt.get("area_m2") or 20), view=view,
                              beds=str(rt.get("beds", "")), price_coefficient=_room_coef(rt),
                              inventory=r.randint(1, 3) if ptype == "guest_house" else r.randint(2, 12)))
    if slot["family_room"] and not any(x.capacity_adults >= 2 and x.capacity_children >= 2 for x in rooms):
        rooms.append(RoomType(room_type_id="family", name="Семейный", capacity_adults=2, capacity_children=2,
                              area_m2=32, view="none", beds="двуспальная + диван", price_coefficient=1.35,
                              inventory=r.randint(1, 4)))
    lo = min(x.price_coefficient for x in rooms)
    for x in rooms:
        x.price_coefficient = round(x.price_coefficient / lo, 2)

    refundable = {"sanatorium": "free_until_14d", "guest_house": r.choice(["free_until_1d", "free_until_3d"]),
                  "resort": r.choice(["free_until_7d", "free_until_14d"])}.get(
        ptype, r.choice(["free_until_1d", "free_until_3d", "free_until_7d"]))
    policies = [refundable] + (["non_refundable"] if ptype in ("hotel", "resort", "apartments") and r.random() < .6
                               else [])
    plans = [RatePlan(rate_id=f"{rt.room_type_id}__{meal}__{pol}", room_type_id=rt.room_type_id, meal=meal,
                      cancellation=pol, free_cancel_days=None if pol == "non_refundable" else int(pol.split("_")[2][:-1]),
                      price_coefficient=PRICING["cancellation_coefficient"][pol])
             for rt in rooms for meal in slot["meal_options"] for pol in policies]

    times = {"guest_house": r.choice([("13:00", "11:00"), ("12:00", "10:00"), ("14:00", "12:00")]),
             "apartments": ("15:00", "11:00"), "resort": ("15:00", "12:00"),
             "sanatorium": r.choice([("12:00", "10:00"), ("08:00", "08:00")]),
             "glamping": ("15:00", "12:00")}.get(ptype, r.choice([("14:00", "12:00"), ("15:00", "12:00")]))
    no_early = ptype == "guest_house" and r.random() < .5
    sea = None
    if slot["beach_line"]:
        sea = {1: r.randint(30, 200), 2: r.randint(200, 600), 3: r.randint(600, 1800)}[slot["beach_line"]]
    rating = round(min(9.8, max(6.2, r.gauss(7.9 + 0.2 * ((stars or 3) - 3), 0.6))), 1)
    tags = [t for t in card.get("tags", []) if t in HOTEL_TAGS]
    if slot["pets_allowed"] and "с животными" not in tags:
        tags.append("с животными")
    if slot["adults_only"]:
        tags = [t for t in tags if t != "для семей с детьми"]
    return Hotel(
        hotel_id=f"h_{area.area_id}_{idx:03d}", area_id=area.area_id, name=card["name"].strip(),
        property_type=ptype, stars=stars, address=card["address"], description=card["description"],
        rating=rating, reviews_count=int(r.lognormvariate(4.5, 1.0)) + 5,
        km_to_area_center=round(r.uniform(0.2, 3.5), 1), distance_to_sea_m=sea, beach_line=slot["beach_line"],
        distance_to_lift_m=slot["distance_to_lift_m"], amenities=amen, tags=tags,
        meal_options=slot["meal_options"], children_allowed=not slot["adults_only"],
        min_child_age=r.choice([12, 14, 16]) if slot["adults_only"] else 0,
        pets_allowed=slot["pets_allowed"],
        pet_fee_per_night=(r.choice([0, 500, 800, 1000, 1500, 2000]) if slot["pets_allowed"] else 0),
        pet_max_kg=(r.choice([5, 10, 15, 25, None]) if slot["pets_allowed"] else None),
        check_in_time=times[0], check_out_time=times[1],
        early_checkin_fee=None if no_early else int(round(base * .5, -2)),
        late_checkout_fee=None if no_early else int(round(base * .5, -2)),
        base_price_per_night=int(base), room_types=rooms, rate_plans=plans)


def dedupe_names(hotels: list[Hotel], areas: dict[str, Area]) -> None:
    cnt = Counter(h.name.lower() for h in hotels)
    for h in hotels:
        if cnt[h.name.lower()] > 1:
            h.name = f"{h.name} ({areas[h.area_id].name.split(' (')[0]})"


# ---------------------------------------------------------------- отзывы
def _text(x) -> str:
    if isinstance(x, list):
        return "; ".join(map(str, x))
    return str(x or "")


def _review_hints(h: Hotel) -> tuple[list[str], list[str]]:
    r = rng_for("hints", h.hotel_id)
    pros, cons = [], []
    if h.beach_line == 1:
        pros.append("море в двух шагах")
    if h.beach_line == 3:
        cons.append("до моря далеко, обратно в горку")
    if h.distance_to_lift_m and h.distance_to_lift_m < 300:
        pros.append("выходишь — и сразу на подъёмник")
    if h.distance_to_lift_m and h.distance_to_lift_m > 3000:
        cons.append("до подъёмников только на шаттле/такси")
    if "kids_club" in h.amenities or "playground" in h.amenities:
        pros.append("детям есть чем заняться")
    if h.property_type == "guest_house":
        cons.append(r.choice(["тонкие стены, слышно соседей", "скромный ремонт", "хозяйка строгая к шуму"]))
    if h.property_type == "sanatorium":
        cons.append(r.choice(["советский интерьер", "много пожилых, вечером скучно"]))
        pros.append("процедуры и питание включены")
    pool = ["шумно от дороги ночью", "медленный wifi", "в сезон людно у бассейна", "однообразные завтраки",
            "долго ждали заселения", "парковка платная и мало мест", "ремонт местами уставший",
            "кондиционер шумит", "персонал не всегда на ресепшене"]
    cons += r.sample(pool, 2 if h.rating < 8 else 1)
    pros += r.sample(["вежливый персонал", "чисто", "вкусная кухня", "удобные кровати", "красивый вид",
                      "тихо по ночам", "хорошее соотношение цена/качество"], 2)
    return pros, cons


async def gen_reviews(glm: GLM, h: Hotel, area: Area) -> list[Review]:
    pros, cons = _review_hints(h)
    types = ["family"] if h.children_allowed else []
    if h.pets_allowed:
        types.append("with_pet")
    brief = {"name": h.name, "type": h.property_type, "stars": h.stars, "район": area.name,
             "рейтинг": h.rating, "удобства": h.amenities, "питание": h.meal_options,
             "до моря, м": h.distance_to_sea_m, "до подъёмника, м": h.distance_to_lift_m,
             "дети": h.children_allowed, "животные": h.pets_allowed,
             "номера": [f"{r.name} ({r.capacity_adults}+{r.capacity_children})" for r in h.room_types]}
    user = (f"Объект: {json.dumps(brief, ensure_ascii=False)}\n"
            f"Сильные стороны: {pros}. Слабые стороны: {cons}.\n"
            "Напиши 6 разных отзывов гостей. Средняя оценка ≈ рейтингу; разброс оценок 1–10 (шкала 10). "
            "Отзывы живые, разной длины (1–5 предложений), разные стили; каждый слабый пункт упомяни хотя бы в "
            f"одном отзыве. Обязательно включи типы путешественников: {types or ['couple']}.\n"
            "Не упоминай удобства, которых нет в списке (нет pool — нет бассейна), и состав семьи, который не "
            "помещается в номера.\n"
            "Поля: author (имя и первая буква фамилии), traveler_type (family|couple|solo|friends|business|"
            "with_pet), stay_month (YYYY-MM, от 2025-05 до 2026-10), score, text, pros, cons, "
            f"aspects — словарь из 2–5 аспектов {REVIEW_ASPECTS} со значением -1/0/1.\n"
            'Формат: {"reviews": [...]}')

    def check(d):
        out = []
        for i, x in enumerate(d["reviews"]):
            asp = {k: int(v) for k, v in (x.get("aspects") or {}).items() if k in REVIEW_ASPECTS}
            tt = x.get("traveler_type") if x.get("traveler_type") in (
                "family", "couple", "solo", "friends", "business", "with_pet") else "couple"
            out.append(Review(review_id=f"{h.hotel_id}_r{i}", hotel_id=h.hotel_id, author=x["author"],
                              traveler_type=tt, stay_month=str(x["stay_month"])[:7],
                              score=float(min(10, max(1, float(x["score"])))), text=x["text"],
                              pros=_text(x.get("pros")), cons=_text(x.get("cons")), aspects=asp))
        assert len(out) >= 4
        return out

    return await glm.json(SYSTEM, user, tag=f"reviews_{h.hotel_id}", validate=check, temperature=1.0)


def inject_reviews(reviews: list[Review], hotels: list[Hotel]) -> list[Review]:
    r = rng_for("injections")
    targets = r.sample(hotels, len(INJECTION_REVIEWS))
    extra = [Review(review_id=f"{h.hotel_id}_inj{i}", hotel_id=h.hotel_id, author="Гость", traveler_type="couple",
                    stay_month="2026-09", score=10, text=txt, is_injection=True)
             for i, (h, txt) in enumerate(zip(targets, INJECTION_REVIEWS))]
    return reviews + extra


def aggregate_aspects(hotels: list[Hotel], reviews: list[Review]) -> None:
    acc: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for rv in reviews:
        if not rv.is_injection:
            for k, v in rv.aspects.items():
                acc[rv.hotel_id][k].append(v)
    for h in hotels:
        h.review_aspects = {k: round(sum(v) / len(v), 2) for k, v in sorted(acc[h.hotel_id].items())}


# ---------------------------------------------------------------- POI
async def gen_poi(glm: GLM, area: Area) -> list[POI]:
    n = 5 if area.zone == "mountains" or area.area_id in ("sochi_center", "imeretinka", "adler") else 3
    user = (f"Район: {area.name} ({area.zone}). {area.description}\n"
            f"Придумай {n} мест/активностей в районе (можно вдохновляться реальными, но названия вымышленные). "
            "Поля: name, type (beach|park|museum|attraction|ski|viewpoint|waterpark|promenade|nature|food|"
            "entertainment), description (1–2 предложения), months_open (список месяцев 1–12; горнолыжное — "
            "только зима, пляж — лето), kid_friendly, price_rub (0 если бесплатно), km_from_area_center.\n"
            'Формат: {"poi": [...]}')

    def check(d):
        return [POI(poi_id=f"poi_{area.area_id}_{i}", area_id=area.area_id, **p) for i, p in enumerate(d["poi"])]

    return await glm.json(SYSTEM, user, tag=f"poi_{area.area_id}", validate=check)


# ---------------------------------------------------------------- транспорт
MOSCOW_KM = {"Екатеринбург": 1420, "Новосибирск": 2810, "Пермь": 1150}
DEP_SLOTS = ["00:40", "01:30", "05:50", "06:30", "07:15", "08:40", "09:55", "11:20", "12:45", "14:10", "15:30",
             "16:50", "18:05", "19:40", "21:15", "22:50", "23:35"]
ALL_MONTHS = list(range(1, 13))
SUMMER = [5, 6, 7, 8, 9, 10]


def flight_minutes(km: float) -> int:
    return int(round((FLIGHT["overhead_min"] + km / FLIGHT["cruise_kmh"] * 60) / 5) * 5)


def flight_price(km: float) -> int:
    return int(round(FLIGHT["base_price"] + km * FLIGHT["price_per_km"], -2))


def build_flights(carriers: Carriers) -> list[FlightSchedule]:
    out: list[FlightSchedule] = []
    for o in ORIGINS:
        if not o.air_km:
            continue
        r = rng_for("flights", o.city)
        n_total = o.flights_per_day + o.summer_extra_flights
        times_out = sorted(r.sample(DEP_SLOTS[2:], n_total))
        times_back = sorted(r.sample(DEP_SLOTS[2:], n_total))
        if o.city == "Москва":                               # ночные рейсы для проверок состыковок
            times_out[0], times_back[-1] = "00:40", "23:35"
        dur = flight_minutes(o.air_km)
        for i in range(n_total):
            summer_only = i >= o.flights_per_day
            daily = o.flights_per_day >= 2 or r.random() < .5
            days = list(range(1, 8)) if daily else sorted(r.sample(range(1, 8), r.randint(3, 5)))
            carrier = r.choice(carriers.airlines)
            code = "".join(w[0] for w in carrier.split()[:2]).upper().ljust(2, "X")[:2]
            bag = r.random() < .65
            price = flight_price(o.air_km) + (FLIGHT["baggage_fee"] if bag else 0)
            for direction, t in (("outbound", times_out[i]), ("return", times_back[i])):
                num = r.randint(100, 999)
                out.append(FlightSchedule(
                    schedule_id=f"fl_{slug(o.city)}_{direction[0]}{i}", direction=direction, origin_city=o.city,
                    origin_airport=o.airports[i % len(o.airports)], point_id="aer", carrier=carrier,
                    route_number=f"{code}-{num}", departure_time=t, duration_minutes=dur, operating_days=days,
                    valid_months=SUMMER if summer_only else ALL_MONTHS, baggage_included=bag,
                    base_adult_price=int(price * (1.05 if direction == "return" else 1))))
        if o.via_moscow:                                     # стыковка через Москву
            leg1 = flight_minutes(MOSCOW_KM[o.city])
            for direction in ("outbound", "return"):
                lay = r.randint(*FLIGHT["layover_range"]) // 5 * 5
                carrier = r.choice(carriers.airlines)
                out.append(FlightSchedule(
                    schedule_id=f"fl_{slug(o.city)}_{direction[0]}via", direction=direction, origin_city=o.city,
                    origin_airport=o.airports[0], point_id="aer", carrier=carrier,
                    route_number=f"{carrier[:2].upper()}-{r.randint(1000, 1999)}",
                    departure_time=r.choice(DEP_SLOTS[3:12]), duration_minutes=leg1 + lay + flight_minutes(1360),
                    stops=1, via="Москва", layover_minutes=lay, operating_days=list(range(1, 8)),
                    valid_months=ALL_MONTHS, baggage_included=True,
                    base_adult_price=int(round((flight_price(MOSCOW_KM[o.city]) + flight_price(1360)) * .8, -2))))
    return out


def build_trains(carriers: Carriers, areas: dict[str, dict]) -> list[TrainSchedule]:
    """Поезда идут с севера по побережью: Лазаревская → Сочи → Адлер (→ Олимпийский парк)."""
    long_stops = [p for p in REFERENCE_POINTS if p.long_distance]
    pos = {p.point_id: areas[p.area_id]["coast_km"] for p in long_stops}
    out: list[TrainSchedule] = []
    for o in ORIGINS:
        if not o.rail_km:
            continue
        r = rng_for("trains", o.city)
        for i in range(o.trains_per_day):
            fast = o.fast_train and i == 0
            speed = TRAIN["fast_speed_kmh"] if fast else TRAIN["speed_kmh"]
            to_first = int(o.rail_km / speed * 60)          # до Лазаревской
            ends_olympic = r.random() < .4
            stop_ids = [p.point_id for p in long_stops if ends_olympic or p.point_id != "st_olympic_park"]
            coast_min = {pid: int((pos[pid] - pos["st_lazarevskaya"]) / 50 * 60) + 3 * k
                         for k, pid in enumerate(stop_ids)}
            classes = ({"seat": int(round(TRAIN["classes"]["seat"][0] + o.rail_km * TRAIN["classes"]["seat"][1], -1))}
                       if fast else {c: int(round(b + o.rail_km * k, -1)) for c, (b, k) in TRAIN["classes"].items()
                                     if c != "seat"})
            carrier = carriers.local[0] if fast else r.choice(carriers.rail)
            kind = "local_train" if fast and o.rail_km <= 350 else "train"
            dep_out = f"{r.randint(5, 23):02d}:{r.choice([0, 15, 30, 45]):02d}"
            dep_back = f"{r.randint(5, 23):02d}:{r.choice([0, 15, 30, 45]):02d}"
            num = r.randint(1, 199)
            summer_only = i >= 2 and o.city == "Москва"
            total = to_first + coast_min[stop_ids[-1]]
            out.append(TrainSchedule(
                schedule_id=f"tr_{slug(o.city)}_o{i}", kind=kind, direction="outbound", origin_city=o.city,
                carrier=carrier, route_number=f"{num:03d}С", departure_time=dep_out, duration_minutes=total,
                stops={pid: to_first + coast_min[pid] for pid in stop_ids}, operating_days=list(range(1, 8)),
                valid_months=SUMMER if summer_only else ALL_MONTHS, classes=classes))
            back = {pid: coast_min[stop_ids[-1]] - coast_min[pid] for pid in stop_ids}  # от последней станции
            out.append(TrainSchedule(
                schedule_id=f"tr_{slug(o.city)}_r{i}", kind=kind, direction="return", origin_city=o.city,
                carrier=carrier, route_number=f"{num + 1:03d}С", departure_time=dep_back, duration_minutes=total,
                stops=back, operating_days=list(range(1, 8)),
                valid_months=SUMMER if summer_only else ALL_MONTHS, classes=classes))
        if o.city in ("Краснодар", "Ростов-на-Дону"):           # междугородний автобус
            dur = int(o.rail_km * 1.1 / 50 * 60)
            price = int(round(300 + o.rail_km * 1.6, -1))
            for direction in ("outbound", "return"):
                out.append(TrainSchedule(
                    schedule_id=f"bus_{slug(o.city)}_{direction[0]}", kind="bus", direction=direction,
                    origin_city=o.city, carrier=carriers.local[-1], route_number=f"{r.randint(500, 599)}",
                    departure_time=r.choice(["07:00", "09:30", "22:00"]), duration_minutes=dur,
                    stops={"st_sochi": dur - 40, "st_adler": dur} if direction == "outbound"
                    else {"st_adler": 0, "st_sochi": 40},
                    operating_days=list(range(1, 8)), valid_months=ALL_MONTHS, classes={"seat": price}))
    return out


def area_distances(areas: list[Area]) -> list[dict]:
    """Таблица район × точка прибытия: км и минуты (выведены из geo, не заданы руками)."""
    ad = {a.area_id: a.model_dump() for a in areas}
    rows = []
    for a in areas:
        for p in REFERENCE_POINTS:
            rt = geo.route(ad[p.area_id], ad[a.area_id], p.offset_km, 0)
            rows.append({"area_id": a.area_id, "point_id": p.point_id, **rt})
    return rows


# ---------------------------------------------------------------- крайние случаи
def build_edge_cases(hotels: list[Hotel], flights: list[FlightSchedule], areas: list[Area]) -> dict:
    by_area = defaultdict(list)
    for h in hotels:
        by_area[h.area_id].append(h)
    best_adler = max(by_area["adler"], key=lambda h: h.rating)
    sold_out = [
        {"hotel_ids": [h.hotel_id for h in by_area["imeretinka"] if h.pets_allowed],
         "from": "2026-12-30", "to": "2027-01-03", "reason": "новогодний пик: pet-friendly в Сириусе раскуплены"},
        {"hotel_ids": [best_adler.hotel_id], "from": "2026-11-13", "to": "2026-11-16",
         "reason": "лучший по рейтингу отель Адлера занят на выходные"},
        {"hotel_ids": [h.hotel_id for h in by_area["rosa_khutor"] if h.property_type == "resort"],
         "from": "2027-02-20", "to": "2027-02-24", "reason": "курорты Розы Хутор раскуплены на 23 февраля"},
    ]
    closures = [{"hotel_ids": [h.hotel_id for a in ("lazarevskoe", "loo") for h in by_area[a]
                               if h.property_type == "guest_house"],
                 "months": [11, 12, 1, 2, 3, 4], "reason": "гостевые дома на северном побережье закрыты зимой"},
                {"hotel_ids": [h.hotel_id for h in by_area["krasnaya_polyana"] if h.property_type == "glamping"],
                 "months": [11, 12, 1, 2, 3], "reason": "глэмпинг работает только в тёплый сезон"}]
    msk = [f for f in flights if f.origin_city == "Москва" and f.stops == 0]
    cancelled = [{"schedule_id": msk[0].schedule_id, "dates": ["2026-11-20", "2026-11-21"], "reason": "погода"},
                 {"schedule_id": next(f for f in msk if f.direction == "return").schedule_id,
                  "dates": ["2026-11-27"], "reason": "техническая причина"}]
    no_seats = [{"schedule_id": f.schedule_id, "dates": ["2026-12-30", "2026-12-31"]}
                for f in msk if f.direction == "outbound"][:3]
    return {"sold_out": sold_out, "seasonal_closures": closures, "cancelled_departures": cancelled,
            "no_seats": no_seats}


def catalog_meta() -> dict:
    return {"dataset_name": "sochi_synth", "version": "2.0", "currency": "RUB", "timezone": "Europe/Moscow",
            "synthetic": True, "seed": SEED, "valid_from": str(CATALOG_FROM), "valid_to": str(CATALOG_TO),
            "generated_at": str(date.today()), "local_lines": [ln.line_id for ln in LOCAL_LINES]}
