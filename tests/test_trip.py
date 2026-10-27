"""L8: сборка поездки на каталоге ML (synth/). Без папки synth/ тесты пропускаются."""

from datetime import date, timedelta

import pytest

pytest.importorskip("synth.pricing")

from harness.agent_graph import GraphAgent
from harness.contracts import TripRequest
from harness.fakes import FakeLLM
from harness.trip import TripPlanner, assumed_checkin, build_planner, origin_from_text

TODAY = date(2026, 10, 26)  # понедельник


@pytest.fixture(scope="module")
def planner() -> TripPlanner:
    p = build_planner()
    assert p is not None
    return p


def _req(**kw) -> TripRequest:
    return TripRequest(raw_text=kw.pop("raw_text", ""), **kw)


def test_helpers(planner: TripPlanner) -> None:
    assert assumed_checkin(TODAY, None) == date(2026, 10, 30)          # пятница, не раньше чем через 3 дня
    assert assumed_checkin(TODAY, 12) == date(2026, 12, 4)             # первая пятница декабря
    assert origin_from_text("едем из Казани в Адлер") == "Казань" and origin_from_text("Адлер") is None
    assert planner.area_id("Сириус") == "imeretinka" and planner.area_id("Сочи-центр") == "sochi_center"
    assert planner.area_id("Красная Поляна") == "krasnaya_polyana" and planner.area_id("Атлантида") is None


def test_three_variants_with_assumptions(planner: TripPlanner) -> None:
    plan = planner.plan(_req(raw_text="Адлер на неделю до 50 тыс", destination="Адлер", nights=7,
                             budget_total=50000), TODAY)
    assert (plan.checkin, plan.checkout) == (date(2026, 10, 30), date(2026, 11, 6))
    assert any("Даты не назвали" in n for n in plan.notes) and any("2 взрослых" in n for n in plan.notes)
    assert [it.label for it in plan.itineraries] == ["balanced", "cheapest", "most_comfortable"]
    for it in plan.itineraries:
        assert it.hotel.price_total <= 50000                       # бюджет — только проживание
        assert it.outbound and it.inbound and len(it.transfers) == 2
        assert it.outbound.arrive.date() == it.checkin and it.inbound.depart.date() == it.checkout
        assert not any(c.severity == "error" for c in it.checks)   # ошибок состыковки нет
        parts = it.hotel.price_total + it.outbound.price_total + it.inbound.price_total
        assert it.total_price == round(parts + sum(t.price_total for t in it.transfers))
    cheapest = next(it for it in plan.itineraries if it.label == "cheapest")
    assert cheapest.total_price == min(it.total_price for it in plan.itineraries)


def test_origin_kids_pets_and_train(planner: TripPlanner) -> None:
    plan = planner.plan(_req(raw_text="Красная Поляна из Казани, ребёнок 6 лет, с собакой",
                             destination="Красная Поляна", checkin=date(2026, 11, 6), nights=5, guests_adults=2,
                             children_ages=[6], pets=True), TODAY)
    assert plan.itineraries and not plan.notes                     # всё названо — додумывать нечего
    assert all("Казань" in it.outbound.from_city for it in plan.itineraries)
    assert all(planner.cat.hotels[it.hotel.hotel.hotel_id].pets_allowed for it in plan.itineraries)
    trains = planner.plan(_req(raw_text="Сириус поездом", destination="Сириус", checkin=date(2026, 11, 1),
                               checkout=date(2026, 11, 3), transport=["train"]), TODAY)
    assert trains.itineraries and all(it.outbound.kind == "train" == it.inbound.kind for it in trains.itineraries)
    assert all(it.outbound.depart.date() < it.checkin for it in trains.itineraries)   # поезд выезжает накануне


def test_no_hotels_in_budget(planner: TripPlanner) -> None:
    plan = planner.plan(_req(destination="Адлер", nights=7, checkin=date(2026, 11, 2), budget_total=1000), TODAY)
    assert plan.hotels == [] and plan.itineraries == []


def test_deterministic(planner: TripPlanner) -> None:
    r = _req(destination="Хоста", checkin=date(2026, 11, 9), nights=4, guests_adults=2)
    assert [it.total_price for it in planner.plan(r, TODAY).itineraries] == \
        [it.total_price for it in planner.plan(r, TODAY).itineraries]


@pytest.mark.asyncio
async def test_graph_trip_buy_and_replan(planner: TripPlanner) -> None:
    agent = GraphAgent(FakeLLM(), today=TODAY, planner=planner)
    run = await agent.run("Адлер на неделю до 50 тыс", "s-trip")
    assert run.answer.mode.value == "confirm_action" and len(run.answer.itineraries) == 3
    assert "Итого поездка" in run.answer.text and "Даты не назвали" in run.answer.text
    assert run.request.checkin == date(2026, 10, 30)                 # допущение по датам записано в запрос
    total = run.answer.itineraries[0].total_price
    assert run.answer.pending_action["price_total"] == total
    shifted = await agent.run("а если на 2 дня позже?", "s-trip")      # пересборка на допущенных датах
    assert shifted.request.checkin == date(2026, 10, 30) + timedelta(days=2) and shifted.answer.itineraries
    bought = await agent.run("да", "s-trip")
    assert bought.answer.mode.value == "purchased" and "поездка" in bought.answer.text
    assert bought.answer.itineraries[0].total_price == shifted.answer.itineraries[0].total_price


@pytest.mark.asyncio
async def test_kids_and_pets_notes_match_catalog(planner: TripPlanner) -> None:
    run = await GraphAgent(FakeLLM(), today=TODAY, planner=planner).run(
        "Сириус с ребёнком 7 лет и собакой на неделю", "s-kids")
    assert "только отели, где можно с детьми" in run.answer.text and "можно с животными" in run.answer.text
    assert "не поддерживается" not in run.answer.text


CASES = [
    {"destination": "Центр Сочи", "checkin": date(2026, 11, 6), "nights": 5, "guests_adults": 2, "budget_total": 60000},
    {"destination": "Адлер", "nights": 7, "budget_total": 50000},
    {"destination": "Сириус", "checkin": date(2026, 11, 1), "checkout": date(2026, 11, 3), "transport": ["train"]},
    {"destination": "Красная Поляна", "checkin": date(2026, 11, 6), "nights": 3, "pets": True},
]


@pytest.mark.parametrize("kw", CASES)
def test_l9_no_night_legs_and_priced_fixes(planner: TripPlanner, kw: dict) -> None:
    from harness.trip import _night
    plan = planner.plan(_req(**kw), TODAY)
    assert plan.itineraries
    for it in plan.itineraries:
        there = it.model_copy(update={"inbound": None, "transfers": it.transfers[:1]})
        back = it.model_copy(update={"outbound": None})
        assert not _night(there) and not _night(back)       # в отеле не ночью, из отеля не раньше 06:00
        fixes = [f for c in it.checks for f in c.fixes]
        assert not any(f.startswith("запросить") for f in fixes)          # вместо «запросить» — цена из каталога
        assert all("₽" in f or "не предоставляется" in f for f in fixes if f.startswith(("ранний", "поздний")))
        if kw.get("pets"):
            assert all(t.kind != "public" for t in it.transfers)       # с животным — такси


@pytest.mark.asyncio
async def test_choose_variant_two(planner: TripPlanner) -> None:
    agent = GraphAgent(FakeLLM(), today=TODAY, planner=planner)
    first = await agent.run("Адлер с 6 ноября на 5 ночей, вдвоём", "s-choice")
    its = first.answer.itineraries
    assert len(its) >= 2 and "«да, вариант 2»" in first.answer.text
    bought = await agent.run("да, вариант 2", "s-choice")
    assert bought.answer.mode.value == "purchased"
    assert bought.answer.itineraries[0].label == its[1].label
    assert bought.answer.itineraries[0].total_price == its[1].total_price


@pytest.mark.asyncio
async def test_relax_after_no_solution(planner: TripPlanner) -> None:
    agent = GraphAgent(FakeLLM(), today=TODAY, planner=planner)
    run = await agent.run("Адлер с 6 ноября на 5 ночей, вдвоём, до 3 тыс", "s-relax")
    assert run.answer.mode.value == "no_solution"
    relax = run.answer.plan["relax"]
    assert relax[0]["title"] == "Поднять бюджет" and relax[0]["patch"]["budget_total"] > 3000
    assert "Что можно поменять" in run.answer.text and "₽" in relax[0]["result"]
    fixed = await agent.run("да", "s-relax")                              # «да» применяет первое предложение
    assert fixed.answer.mode.value == "confirm_action"
    assert fixed.request.budget_total == relax[0]["patch"]["budget_total"]
    assert all(it.hotel.price_total <= fixed.request.budget_total for it in fixed.answer.itineraries)


@pytest.mark.asyncio
async def test_stars_and_first_line(planner: TripPlanner) -> None:
    agent = GraphAgent(FakeLLM(), today=TODAY, planner=planner)
    run = await agent.run("Адлер с 6 ноября на 5 ночей, вдвоём, отель 4 звезды на первой линии", "s-stars")
    keys = {c.key: c.value for c in run.request.hard_constraints}
    assert keys == {"min_stars": 4, "beach_line": 1}
    if run.answer.mode.value == "confirm_action":
        for it in run.answer.itineraries:
            h = planner.cat.hotels[it.hotel.hotel.hotel_id]
            assert (h.stars or 0) >= 4 and h.beach_line == 1
        assert "4★" in run.answer.text
    else:                                                              # таких нет — предложит снять условие
        assert any(r["patch"] == {"hard_constraints": []} for r in run.answer.plan["relax"])


def test_tradeoffs_explain(planner: TripPlanner) -> None:
    plan = planner.plan(_req(raw_text="", destination="Центр Сочи", checkin=date(2026, 11, 6), nights=5,
                             guests_adults=2), TODAY)
    cheapest = min(it.total_price for it in plan.itineraries)
    for it in plan.itineraries:
        notes = [t.note for t in it.tradeoffs]
        assert (it.total_price > cheapest) == any(n.startswith("дороже") for n in notes)


@pytest.mark.asyncio
async def test_choice_gives_single_answer(planner: TripPlanner) -> None:
    agent = GraphAgent(FakeLLM(), today=TODAY, planner=planner)
    await agent.run("Адлер с 6 ноября на 5 ночей, вдвоём", "s-one")
    answers = [e async for e in agent.stream("да, вариант 2", "s-one") if e.type == "answer"]
    assert len(answers) == 1 and answers[0].payload["answer"]["mode"] == "purchased"


@pytest.mark.asyncio
async def test_inspire_ranks_areas(planner: TripPlanner) -> None:
    """L8 inspire: район не назван — RankAreas ML выбирает лучший на месяц, альтернативы — в answer.destinations."""
    agent = GraphAgent(FakeLLM(), today=TODAY, planner=planner)
    run = await agent.run("Хочу на море с ребёнком 6 лет в июле на неделю", "s-inspire")
    assert run.answer.mode.value == "confirm_action" and run.request.destination
    names = [d.name for d in run.answer.destinations]
    assert names and names[0] == run.request.destination and "Район не назвали — подобрал район" in run.answer.text
    assert all("море" in " ".join(d.why) for d in run.answer.destinations)
    switched = await agent.run(f"а если {names[-1]}?", "s-inspire") if len(names) > 1 else run
    assert switched.request.destination == names[-1] and switched.request.month == 7


@pytest.mark.asyncio
async def test_inspire_skips_poor_alternatives(planner: TripPlanner) -> None:
    run = await GraphAgent(FakeLLM(), today=TODAY, planner=planner).run(
        "хочу в горы на лыжи в январе на 5 ночей, вдвоём", "s-ski")
    assert all("горы/лыжи" in d.best_for for d in run.answer.destinations)
