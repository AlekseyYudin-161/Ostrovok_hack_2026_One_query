"""M5: скоринг вариантов по приоритетам пользователя (TripState.priorities) — без LLM, детерминированно, объяснимо.

Каждый критерий — полезность u ∈ [0, 1] с понятным смыслом (не min-max по выдаче: один кандидат или одинаковые
цены не ломают шкалу). Итог — взвешенное среднее по применимым критериям (неприменимый → None, его вес
перераспределяется). Каждый критерий отдаёт причину «за» или «против» — из них строится объяснение выбора.

    ctx = ScoreContext(today=date.today(), checkin=..., children_ages=[7], wishes=["море", "тихо"])
    ranked = score_hotels(cat, offers, ctx)            # по убыванию score
    picks = pick_variants(cat, offers, ctx)            # {"cheapest": ..., "balanced": ..., "most_comfortable": ...}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from harness.contracts import HotelCandidate, HotelOffer, TripRequest
from synth.pricing import MSK, Catalog
from synth.refdata import PRICING
from synth.schema import Hotel

# Веса по умолчанию; ключи TripState.priorities (price, travel_time, location, cancellation, meal) перекрывают их.
# quality (рейтинг с поправкой на число отзывов), fit (совпадение с пожеланиями) и level (класс размещения:
# звёзды, тип, удобства) в контракте пока нет.
DEFAULT_WEIGHTS = {"price": .30, "quality": .20, "location": .15, "cancellation": .10, "meal": .05,
                   "travel_time": .05, "fit": .15, "level": .05}
COMFORT_WEIGHTS = {"price": .10, "quality": .20, "location": .15, "cancellation": .05, "meal": .05,
                   "travel_time": .05, "fit": .10, "level": .30}
CRITERIA = tuple(DEFAULT_WEIGHTS)

# Отмена: чем ПОЗЖЕ можно отменить бесплатно, тем гибче («за 1 день» лучше, чем «за 14 дней»).
CANCEL_U = {"free_until_1d": 1.0, "free_until_3d": .9, "free_until_7d": .75, "free_until_14d": .6, "non_refundable": 0.0}
MEAL_U = {"none": .3, "breakfast": .6, "half_board": .8, "full_board": .9, "all_inclusive": 1.0}
MEAL_RU = {"none": "без питания", "breakfast": "завтрак", "half_board": "полупансион", "full_board": "полный пансион",
           "all_inclusive": "всё включено"}
RATING_PRIOR, RATING_PRIOR_N = 7.9, 30       # байесовская поправка: 9.8 при 5 отзывах ≈ 8.2, при 500 ≈ 9.7
SEA_SCALE_M, LIFT_SCALE_M, TRANSFER_SCALE_MIN = 400, 600, 45
KIDS_AMENITIES = {"kids_club", "playground", "kids_pool", "aquapark", "babysitting"}
LEVEL_BY_TYPE = {"guest_house": .30, "apartments": .45, "glamping": .55, "sanatorium": .50, "resort": .85}
LEVEL_BY_STARS = {1: .25, 2: .35, 3: .50, 4: .75, 5: .95}
LEVEL_AMENITIES = {"pool", "heated_pool", "indoor_pool", "spa", "restaurant", "room_service", "gym", "private_beach"}
TYPE_RU = {"hotel": "отель", "resort": "курорт", "sanatorium": "санаторий", "apartments": "апартаменты",
           "guest_house": "гостевой дом", "glamping": "глэмпинг"}
DISTINCT_WITHIN = .95                        # «комфортно» берёт другой отель, если он не хуже 95 % лучшего
COMFORT_MAX_RATIO = 3.0                      # «комфортно» — не дороже ×3 от «оптимально» (если есть такие)
EXPLICIT_FIT_BONUS = .15                     # +вес fit, если пользователь сам назвал пожелание про отель
LEVEL_STEP = .2                              # «комфортно» — классом выше: гостевой дом → 3★, 3★ → 4★
# Минусы, которых нет в Itinerary.tradeoffs (там уже цена, рейтинг, отмена, багаж, время в дороге)
EXTRA_CON_CRITERIA = ("location", "quality", "fit", "travel_time")


@dataclass
class ScoreContext:
    today: date
    checkin: date | None = None
    children_ages: list[int] = field(default_factory=list)
    pets: bool = False
    wishes: list[str] = field(default_factory=list)      # TripRequest.preferences: «море», «тихо», «с детьми»…
    meal_required: str | None = None                      # питание уже отфильтровано — критерий не учитываем
    arrival_point: str = "aer"                            # откуда трансфер до отеля
    priorities: dict[str, float] | None = None            # TripState.priorities

    @classmethod
    def from_request(cls, req: TripRequest, today: date, priorities: dict[str, float] | None = None,
                     arrival_point: str = "aer") -> ScoreContext:
        meal = next((c.value for c in req.hard_constraints if c.key == "meal"), None)
        return cls(today=today, checkin=req.checkin, children_ages=list(req.children_ages), pets=req.pets,
                   wishes=list(req.preferences), meal_required=meal, arrival_point=arrival_point,
                   priorities=priorities)


@dataclass
class Scored:
    offer: HotelOffer
    score: float
    parts: dict[str, float]                  # критерий → u (только применимые)
    weights: dict[str, float]                # итоговые веса после нормировки
    pros: list[str]
    cons: list[str]
    cons_by: list[tuple[str, str]] = field(default_factory=list)   # (критерий, минус) — по убыванию потерь

    @property
    def hotel_id(self) -> str:
        return self.offer.hotel.hotel_id

    def why(self, n: int = 3) -> list[str]:
        """Короткое объяснение: главные плюсы (по вкладу в балл), затем главный минус."""
        return self.pros[:n] + self.cons[:1]

    def why_extra(self, n: int = 3) -> list[str]:
        """Для ответа рядом с tradeoffs: плюсы + минусы отеля, которых tradeoffs не показывают (шум, далеко…)."""
        return self.pros[:n] + [t for c, t in self.cons_by if c in EXTRA_CON_CRITERIA][:2]


def resolve_weights(priorities: dict[str, float] | None, base: dict[str, float] = DEFAULT_WEIGHTS) -> dict[str, float]:
    """Веса из TripState.priorities поверх базовых: неизвестные ключи игнорируются, отрицательные → 0, сумма → 1."""
    w = dict(base)
    for k, v in (priorities or {}).items():
        if k in w and isinstance(v, int | float):
            w[k] = max(float(v), 0.0)
    total = sum(w.values())
    if total <= 0:                                  # всё обнулили — базовые веса
        w, total = dict(base), sum(base.values())
    return {k: v / total for k, v in w.items()}


# ---------------------------------------------------------------- критерии: u ∈ [0, 1] или None (неприменимо)
def _price(o: HotelOffer, p_min: float) -> tuple[float, str | None, str | None]:
    if o.price_total <= p_min:
        return 1.0, "самый дешёвый среди подходящих", None
    over = o.price_total / p_min - 1
    note = f"на {round(over * 100)}% дороже самого дешёвого"
    return p_min / o.price_total, None, (note if over >= .25 else None)


def _quality(h: Hotel) -> tuple[float, str | None, str | None]:
    n = h.reviews_count or 0
    r = (n * h.rating + RATING_PRIOR_N * RATING_PRIOR) / (n + RATING_PRIOR_N)
    u = min(max((r - 6.5) / 3.0, 0.0), 1.0)
    if h.stars:
        u = min(max(u + .04 * (h.stars - 3), 0.0), 1.0)
    pro = con = None
    if h.rating >= 8.7 and n >= 50:
        pro = f"рейтинг {h.rating} ({n} отзывов)"
    elif n < 20:
        con = f"мало отзывов ({n}) — рейтинг {h.rating} ненадёжен"
    elif h.rating < 7.3:
        con = f"невысокий рейтинг {h.rating}"
    if h.review_aspects.get("cleanliness", 0) <= -.3:
        u, con = max(u - .15, 0.0), "в отзывах жалуются на чистоту"
    return u, pro, con


def _level(h: Hotel) -> tuple[float, str | None, str | None]:
    """Класс размещения: звёзды или тип + ключевые удобства (бассейн, спа, ресторан…)."""
    u = LEVEL_BY_STARS.get(h.stars or 0, LEVEL_BY_TYPE.get(h.property_type, .4))
    if h.property_type == "resort":
        u = max(u, LEVEL_BY_TYPE["resort"])
    u = min(u + .03 * len(LEVEL_AMENITIES & set(h.amenities)), 1.0)
    pro = None
    if u >= .8:
        extras = [x for x in ("pool", "spa", "private_beach") if x in h.amenities]
        ru = {"pool": "бассейн", "spa": "спа", "private_beach": "свой пляж"}
        pro = (f"{h.stars}★ " if h.stars else "") + TYPE_RU[h.property_type] + (
            f" ({', '.join(ru[x] for x in extras)})" if extras else "")
    return u, pro, None


def _location(h: Hotel, zone: str) -> tuple[float | None, str | None, str | None]:
    if zone == "mountains":
        d = h.distance_to_lift_m
        if d is None:
            return None, None, None
        u = 1 / (1 + d / LIFT_SCALE_M)
        return u, (f"{d} м до подъёмника" if d <= 300 else None), (f"до подъёмника {d / 1000:.1f} км" if d >= 2000 else None)
    d = h.distance_to_sea_m
    if d is None:
        return None, None, None
    u = 1 / (1 + d / SEA_SCALE_M)
    pro = "первая линия" if h.beach_line == 1 else (f"{d} м до моря" if d <= 300 else None)
    return u, pro, (f"до моря {d} м" if d >= 800 else None)


def _cancellation(o: HotelOffer, rate: str | None, today: date) -> tuple[float, str | None, str | None]:
    if o.free_cancel_until is None:
        return 0.0, None, "невозвратный тариф"
    deadline = o.free_cancel_until
    now_end = datetime.combine(today, time(23, 59), MSK)
    if deadline < now_end:                          # горящая поездка: срок бесплатной отмены уже прошёл
        return 0.0, None, "бесплатная отмена уже недоступна (поздно)"
    u = CANCEL_U.get(rate or "", .8)
    if deadline.date() <= today + timedelta(days=1):   # отменить бесплатно можно только сегодня-завтра
        return .3 * u, None, f"бесплатная отмена только до {deadline:%d.%m} — решать надо сразу"
    return u, f"бесплатная отмена до {deadline:%d.%m}", None


def _meal(o: HotelOffer, ctx: ScoreContext) -> tuple[float | None, str | None, str | None]:
    if ctx.meal_required:
        return None, None, None                     # уже отфильтровано по требованию пользователя
    u = MEAL_U.get(o.meal, .3)
    if ctx.children_ages and o.meal in ("half_board", "full_board", "all_inclusive"):
        u = min(u + .1, 1.0)                        # с детьми включённое питание ценнее
    pro = f"питание: {MEAL_RU[o.meal]}" if o.meal in ("half_board", "full_board", "all_inclusive") else None
    return u, pro, None


def _travel(cat: Catalog, h: Hotel, ctx: ScoreContext) -> tuple[float | None, str | None, str | None]:
    if ctx.arrival_point not in cat.points:
        return None, None, None
    minutes = cat.route(ctx.arrival_point, h.hotel_id)["drive_min"]
    u = 1 / (1 + minutes / TRANSFER_SCALE_MIN)
    where = "аэропорта" if ctx.arrival_point == "aer" else "вокзала"
    return u, (f"{minutes} мин от {where}" if minutes <= 25 else None), (
        f"до отеля ≈{minutes} мин от {where}" if minutes >= 75 else None)


def _wish_u(h: Hotel, wish: str, zone: str) -> tuple[float | None, str | None, str | None]:
    """Совпадение одного пожелания с фактами отеля; None — пожелание не про отель (решается районом/ценой)."""
    tags, am, asp = set(h.tags), set(h.amenities), h.review_aspects
    w = wish.lower()
    if w in ("море", "у моря", "первая линия") and zone != "mountains":
        u = {1: 1.0, 2: .6, 3: .25}.get(h.beach_line or 0, .4)
        if "private_beach" in am:
            u = 1.0
        return u, ("свой пляж" if "private_beach" in am else None), None
    if w in ("горы/лыжи", "горы", "лыжи") and zone == "mountains":
        u = 1.0 if "ski_in_out" in am else (1 / (1 + (h.distance_to_lift_m or 3000) / LIFT_SCALE_M))
        u = min(u + (.1 if "ski_storage" in am else 0), 1.0)
        return u, ("ski-in/ski-out" if "ski_in_out" in am else None), None
    if w in ("с детьми", "дети"):
        kids = KIDS_AMENITIES & am
        u = min(.3 + .25 * len(kids) + (.2 if "для семей с детьми" in tags else 0) + .2 * asp.get("kids", 0), 1.0)
        pro = ("для детей: " + ", ".join(sorted(kids)).replace("_", " ")) if kids else None
        return max(u, 0.0), pro, ("для детей почти ничего нет" if not kids else None)
    if w == "тихо":
        noise = asp.get("noise", 0)
        u = .5 + (.3 if tags & {"тихо", "уединённо"} else 0) - (.3 if "тусовка рядом" in tags else 0) + .3 * noise
        con = "в отзывах жалуются на шум" if noise <= -.3 else ("рядом тусовочные места" if "тусовка рядом" in tags else None)
        return min(max(u, 0.0), 1.0), ("тихое место" if "тихо" in tags and noise >= 0 else None), con
    if w == "тусовка":
        u = .3 + (.5 if "тусовка рядом" in tags else 0) + (.2 if "bar" in am else 0)
        return min(u, 1.0), ("рядом бары и клубы" if "тусовка рядом" in tags else None), None
    if w == "оздоровление":
        hit = am & {"spa", "medical_treatment", "mineral_baths", "sauna"}
        u = min(.2 + .3 * len(hit) + (.3 if h.property_type == "sanatorium" else 0), 1.0)
        return u, ("лечебная база" if "medical_treatment" in am else ("спа" if "spa" in am else None)), None
    if w == "романтика":
        u = .3 + (.3 if "для пар" in tags else 0) + (.2 if am & {"sea_view", "mountain_view"} else 0) + (
            .2 if not h.children_allowed else 0)
        return min(u, 1.0), ("для пар" if "для пар" in tags else None), None
    if w == "люкс":
        u = .2 + .15 * max((h.stars or 3) - 3, 0) + (.4 if "люкс" in tags else 0)
        return min(u, 1.0), None, None
    if w == "активный отдых":
        u = .3 + (.3 if "для активных" in tags else 0) + (.2 if am & {"gym", "ski_rental"} else 0)
        return min(u, 1.0), None, None
    if w in ("удалёнка", "для удалёнки", "wifi"):
        u = .4 + (.3 if "coworking" in am else 0) + .3 * asp.get("wifi", 0)
        return min(max(u, 0.0), 1.0), ("есть коворкинг" if "coworking" in am else None), (
            "в отзывах жалуются на wifi" if asp.get("wifi", 0) <= -.3 else None)
    return None, None, None                          # «бюджетно», «экскурсии» и др. — не про конкретный отель


def _fit(h: Hotel, ctx: ScoreContext, zone: str) -> tuple[float | None, list[str], list[str]]:
    wishes = list(dict.fromkeys(ctx.wishes + (["с детьми"] if ctx.children_ages else [])))
    us, pros, cons = [], [], []
    for w in wishes:
        u, pro, con = _wish_u(h, w, zone)
        if u is None:
            continue
        us.append(u)
        pros += [pro] if pro else []
        cons += [con] if con else []
    return (sum(us) / len(us) if us else None), pros, cons


# ---------------------------------------------------------------- итог
def score_offer(cat: Catalog, o: HotelOffer, ctx: ScoreContext, p_min: float,
                base: dict[str, float] = DEFAULT_WEIGHTS) -> Scored:
    h = cat.hotels[o.hotel.hotel_id]
    zone = cat.areas[h.area_id].zone
    rate = o.hotel.cancellation
    wishes = {w.lower() for w in ctx.wishes}
    weights = resolve_weights(ctx.priorities, base)
    if "бюджетно" in wishes:                        # «недорого» — цена важнее, остальное пропорционально
        weights = resolve_weights({**weights, "price": weights["price"] + .2}, weights)
    if any(_wish_u(h, w, zone)[0] is not None for w in wishes):   # пожелание названо явно — оно важнее
        weights = resolve_weights({**weights, "fit": weights["fit"] + EXPLICIT_FIT_BONUS}, weights)

    parts: dict[str, float] = {}
    pros: list[tuple[float, str]] = []
    cons: list[tuple[float, str, str]] = []

    def add(name: str, u: float | None, pro: str | list[str] | None, con: str | list[str] | None) -> None:
        if u is None:
            return
        parts[name] = u
        for p in ([pro] if isinstance(pro, str) else pro or []):
            pros.append((weights[name] * u, p))
        for c in ([con] if isinstance(con, str) else con or []):
            cons.append((weights[name] * (1 - u), c, name))

    add("price", *_price(o, p_min))
    add("quality", *_quality(h))
    add("location", *_location(h, zone))
    add("cancellation", *_cancellation(o, rate, ctx.today))
    add("meal", *_meal(o, ctx))
    add("travel_time", *_travel(cat, h, ctx))
    add("fit", *_fit(h, ctx, zone))
    add("level", *_level(h))

    w_used = {k: weights[k] for k in parts}
    total_w = sum(w_used.values()) or 1.0
    w_norm = {k: v / total_w for k, v in w_used.items()}
    score = round(sum(w_norm[k] * parts[k] for k in parts), 4)
    return Scored(offer=o, score=score, parts={k: round(v, 3) for k, v in parts.items()}, weights=w_norm,
                  pros=[t for _, t in sorted(pros, key=lambda x: -x[0])],
                  cons=[t for _, t, _ in sorted(cons, key=lambda x: -x[0])],
                  cons_by=[(c, t) for _, t, c in sorted(cons, key=lambda x: -x[0])])


def score_hotels(cat: Catalog, offers: list[HotelOffer], ctx: ScoreContext,
                 base: dict[str, float] = DEFAULT_WEIGHTS, p_min: float | None = None) -> list[Scored]:
    """Офферы по убыванию балла; ничья — дешевле, затем hotel_id (детерминированно).
    p_min — самая низкая цена всей выдачи, если оцениваем её часть (иначе «самый дешёвый» соврёт)."""
    if not offers:
        return []
    p_min = p_min or min(o.price_total for o in offers)
    scored = [score_offer(cat, o, ctx, p_min, base) for o in offers]
    return sorted(scored, key=lambda s: (-s.score, s.offer.price_total, s.hotel_id))


def pick_variants(cat: Catalog, offers: list[HotelOffer], ctx: ScoreContext) -> dict[str, Scored]:
    """Отели для трёх вариантов поездки.

    cheapest — честно самый дешёвый; balanced — лучший по приоритетам пользователя;
    most_comfortable — лучший при «комфортных» весах среди отелей классом выше balanced (level ≥ +0.2: гостевой
    дом → отель 3★+, 3★ → 4★…) и не дороже ×3 от него — без «люкса в 8 раз дороже». Таких нет — лучший при
    комфортных весах в пределах ×3, по возможности другой отель (не хуже 95 % лучшего), чтобы было из чего выбрать.
    """
    if not offers:
        return {}
    balanced = score_hotels(cat, offers, ctx)
    best = balanced[0]
    p_min = min(o.price_total for o in offers)
    cap = COMFORT_MAX_RATIO * best.offer.price_total
    affordable = [o for o in offers if o.price_total <= cap] or offers
    level = {s.offer.offer_id: s.parts["level"] for s in balanced}
    upper = [o for o in affordable if level[o.offer_id] >= level[best.offer.offer_id] + LEVEL_STEP]
    cheapest = min(balanced, key=lambda s: (s.offer.price_total, -s.score, s.hotel_id))
    if upper:
        return {"cheapest": cheapest, "balanced": best,
                "most_comfortable": score_hotels(cat, upper, ctx, COMFORT_WEIGHTS, p_min)[0]}
    comfort = score_hotels(cat, affordable, ctx, COMFORT_WEIGHTS, p_min)
    alt = next((s for s in comfort if s.hotel_id not in (best.hotel_id, cheapest.hotel_id)
                and s.score >= DISTINCT_WITHIN * comfort[0].score), None)
    return {"cheapest": cheapest, "balanced": best, "most_comfortable": alt or comfort[0]}


def door_to_door_hours(cat: Catalog, origin: str, month: int, adults: int = 2,
                       children_ages: list[int] | None = None) -> dict[str, float | None]:
    """Часы от отправления из города до района: лучший из рейсов (→ аэропорт) и поездов/автобусов (→ любая их
    станция в Сочи) + буфер на выход + машина до района. Берётся неделя с 10-го числа месяца (разные дни рейсов)."""
    year = 2026 if month >= 11 else 2027
    buf = PRICING["buffers_minutes"]
    best: dict[str, float] = {}
    for day in range(10, 17):
        for o in cat.transport_options(origin, date(year, month, day), "outbound", adults, children_ages or []):
            legs = ([("aer", o.duration_hours * 60 + buf["after_flight"])] if o.kind == "flight" else
                    [(pid, m + buf["after_train"]) for pid, m in cat.train_stops(o.option_id).items()])
            for pid, m in legs:
                best[pid] = min(best.get(pid, m), m)
    out: dict[str, float | None] = {}
    for aid in cat.areas:
        mins = [m + cat.route(pid, aid)["drive_min"] for pid, m in best.items()]
        out[aid] = round(min(mins) / 60, 1) if mins else None
    return out


class CatalogRanker:
    """Ranker из контракта: HotelCandidate → по убыванию балла (факты отеля берутся из каталога)."""

    def __init__(self, cat: Catalog, today: date | None = None) -> None:
        self.cat, self.today = cat, today

    def rank(self, request: TripRequest, candidates: list[HotelCandidate]) -> list[HotelCandidate]:
        known = [c for c in candidates if c.hotel_id in self.cat.hotels and c.price_total]
        rest = [c for c in candidates if c not in known]
        ctx = ScoreContext.from_request(request, self.today or date.today())
        offers = [HotelOffer(offer_id=c.hotel_id, hotel=c, meal=c.meal or "none", price_total=c.price_total,
                             refundable=c.cancellation != "non_refundable",
                             free_cancel_until=self._deadline(c, request)) for c in known
                  if (c.meal or "none") in MEAL_U]
        ranked = [s.offer.hotel for s in score_hotels(self.cat, offers, ctx)]
        return ranked + [c for c in known if c not in ranked] + rest

    def _deadline(self, c: HotelCandidate, req: TripRequest) -> datetime | None:
        if not c.cancellation or c.cancellation == "non_refundable" or not req.checkin:
            return None if c.cancellation == "non_refundable" else datetime(2100, 1, 1, tzinfo=MSK)
        days = int(c.cancellation.split("_")[2][:-1])
        return datetime.combine(req.checkin - timedelta(days=days), time(23, 59), MSK)
