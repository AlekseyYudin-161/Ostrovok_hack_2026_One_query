from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta

from harness.contracts import TripRequest
from synth.pricing import MSK, Catalog
from synth.scoring import (
    COMFORT_MAX_RATIO,
    DEFAULT_WEIGHTS,
    CatalogRanker,
    ScoreContext,
    _cancellation,
    _quality,
    _wish_u,
    pick_variants,
    resolve_weights,
    score_hotels,
)
from synth.tools import RankAreas, RankAreasArgs

CAT = Catalog()
TODAY = date(2026, 10, 27)
IN, OUT = date(2026, 11, 10), date(2026, 11, 17)


def _offers(area: str, kids: list[int] | None = None, pets: bool = False, checkin: date = IN, checkout: date = OUT):
    return CAT.search_hotels(checkin, checkout, 2, kids or [], pets, area_ids=[area], limit=50)


def test_resolve_weights_edge_cases() -> None:
    w = resolve_weights({"price": 0.9, "unknown": 5, "meal": -1})
    assert math.isclose(sum(w.values()), 1) and w["meal"] == 0 and "unknown" not in w
    assert w["price"] > resolve_weights(None)["price"]
    zero = resolve_weights(dict.fromkeys(DEFAULT_WEIGHTS, 0))
    assert math.isclose(sum(zero.values()), 1)                     # всё обнулили — базовые веса, без деления на 0


def test_single_offer_and_equal_prices() -> None:
    one = _offers("adler")[:1]
    picks = pick_variants(CAT, one, ScoreContext(today=TODAY))
    assert {p.hotel_id for p in picks.values()} == {one[0].hotel.hotel_id}
    assert 0 <= picks["balanced"].score <= 1
    same = [o.model_copy(update={"price_total": 10000.0}) for o in _offers("adler")[:5]]
    assert all(s.parts["price"] == 1.0 for s in score_hotels(CAT, same, ScoreContext(today=TODAY)))
    assert pick_variants(CAT, [], ScoreContext(today=TODAY)) == {}


def test_scoring_is_deterministic() -> None:
    offers, ctx = _offers("sochi_center"), ScoreContext(today=TODAY, wishes=["тихо"])
    a = [(s.hotel_id, s.score) for s in score_hotels(CAT, offers, ctx)]
    assert a == [(s.hotel_id, s.score) for s in score_hotels(CAT, list(reversed(offers)), ctx)]


def test_quality_shrinks_few_reviews() -> None:
    h = next(iter(CAT.hotels.values()))
    few = h.model_copy(update={"rating": 9.8, "reviews_count": 5, "stars": None, "review_aspects": {}})
    many = h.model_copy(update={"rating": 9.3, "reviews_count": 800, "stars": None, "review_aspects": {}})
    assert _quality(many)[0] > _quality(few)[0]
    assert "мало отзывов" in (_quality(few)[2] or "")


def test_cancellation_flexibility_and_deadline() -> None:
    o = _offers("adler")[0]
    future = datetime.combine(IN - timedelta(days=3), time(23, 59), MSK)
    o = o.model_copy(update={"free_cancel_until": future})
    late, early = _cancellation(o, "free_until_1d", TODAY)[0], _cancellation(o, "free_until_14d", TODAY)[0]
    assert late > early > 0                                         # отменить за 1 день гибче, чем за 14
    assert _cancellation(o.model_copy(update={"free_cancel_until": None}), "non_refundable", TODAY)[0] == 0
    u, _, con = _cancellation(o, "free_until_3d", IN)              # горящая: срок уже прошёл
    assert u == 0 and "недоступна" in con
    u, _, con = _cancellation(o, "free_until_3d", future.date())   # срок — сегодня
    assert 0 < u < .5 and "решать надо сразу" in con


def test_wishes_use_hotel_facts() -> None:
    h = next(h for h in CAT.hotels.values() if CAT.areas[h.area_id].zone == "coast")
    quiet = h.model_copy(update={"tags": ["тихо"], "review_aspects": {"noise": 1.0}})
    noisy = h.model_copy(update={"tags": ["тусовка рядом"], "review_aspects": {"noise": -1.0}})
    assert _wish_u(quiet, "тихо", "coast")[0] > _wish_u(noisy, "тихо", "coast")[0]
    assert "шум" in (_wish_u(noisy, "тихо", "coast")[2] or "")
    assert _wish_u(h, "бюджетно", "coast")[0] is None              # не про отель — вес fit не трогает
    assert _wish_u(h, "горы/лыжи", "coast")[0] is None             # лыжи у моря неприменимы


def test_mountains_use_lift_distance() -> None:
    for s in score_hotels(CAT, _offers("rosa_khutor"), ScoreContext(today=TODAY)):
        h = CAT.hotels[s.hotel_id]
        assert ("location" in s.parts) == (h.distance_to_lift_m is not None)
        assert not any("моря" in p for p in s.pros + s.cons)


def test_pick_variants_rules() -> None:
    for area, kids in (("adler", [7]), ("sochi_center", []), ("rosa_khutor", []), ("imeretinka", [4, 9])):
        offers = _offers(area, kids)
        ctx = ScoreContext(today=TODAY, children_ages=kids)
        p = pick_variants(CAT, offers, ctx)
        assert p["cheapest"].offer.price_total == min(o.price_total for o in offers)
        assert p["balanced"].hotel_id == score_hotels(CAT, offers, ctx)[0].hotel_id
        comfy, best = p["most_comfortable"], p["balanced"]
        assert comfy.offer.price_total <= COMFORT_MAX_RATIO * best.offer.price_total + 1
        assert comfy.parts["level"] >= best.parts["level"]
        assert all("самый дешёвый" not in x for x in comfy.pros) or comfy.offer.price_total == p["cheapest"].offer.price_total


def test_explicit_wish_changes_choice() -> None:
    offers = _offers("matsesta", checkout=date(2026, 11, 20))
    plain = pick_variants(CAT, offers, ScoreContext(today=TODAY))["balanced"]
    health = pick_variants(CAT, offers, ScoreContext(today=TODAY, wishes=["оздоровление"]))["balanced"]
    h = CAT.hotels[health.hotel_id]
    assert _wish_u(h, "оздоровление", "coast")[0] >= .5             # спа / лечебная база / санаторий
    assert health.parts["fit"] > plain.parts.get("fit", 0)


def test_rank_areas_tie_breaks_on_travel_time() -> None:
    rows = RankAreas(CAT).run(RankAreasArgs(month=7, wishes=["море", "с детьми"], children_ages=[7]), "t")
    top_points = math.floor(rows[0]["score"])
    tied = [r for r in rows if math.floor(r["score"]) == top_points]
    assert len(tied) > 1 and rows[0]["destination_id"] != "lazarevskoe"
    assert rows[0]["travel_hours"] <= min(r["travel_hours"] for r in tied) + 0.5
    assert all(r["score"] - math.floor(r["score"]) < 1 for r in rows)   # целые очки не меняются (порог «лучший − 1»)
    krd = RankAreas(CAT).run(RankAreasArgs(month=7, wishes=["море"], origin_city="Краснодар"), "t")
    assert krd[0]["destination_id"] == "lazarevskoe"                     # поездом — ближе всех северное побережье


def test_catalog_ranker_protocol() -> None:
    offers = _offers("adler", [7])
    cands = [o.hotel for o in offers] + [offers[0].hotel.model_copy(update={"hotel_id": "unknown"})]
    req = TripRequest(raw_text="тихо у моря", checkin=IN, checkout=OUT, children_ages=[7], preferences=["тихо"])
    ranked = CatalogRanker(CAT, TODAY).rank(req, cands)
    assert sorted(c.hotel_id for c in ranked) == sorted(c.hotel_id for c in cands)
    assert ranked[-1].hotel_id == "unknown"


def test_trip_planner_uses_scoring() -> None:
    from harness.trip import TripPlanner, describe
    planner = TripPlanner(CAT)
    req = TripRequest(raw_text="Адлер с ребёнком, тихо", destination="Адлер", checkin=IN, checkout=OUT, nights=7,
                      guests_adults=2, children_ages=[7], preferences=["тихо", "море"])
    plan = planner.plan(req, TODAY)
    by = {it.label: it for it in plan.itineraries}
    assert by["cheapest"].hotel.price_total == min(h.price_total for h in plan.hotels)
    why = planner.why(plan.itineraries, req, TODAY)
    assert why and all(why.values())
    text = "\n".join(describe(plan.itineraries[0], 1, None, why.get(plan.itineraries[0].label)))
    assert "Почему:" in text
