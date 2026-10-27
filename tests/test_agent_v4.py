"""agent-v4: выбор варианта по названию («давай Вертикаль») без покупки наугад; число детей без возрастов."""

import re
from datetime import date

import pytest

pytest.importorskip("synth.pricing")

from harness.agent_graph import GraphAgent, pick_variant, rules_parse
from harness.fakes import FakeLLM
from harness.trip import TripPlanner, build_planner, kids_ages

TODAY = date(2026, 10, 27)
ADLER = "адлер. на неделю вдвоем с двумя детьми, с 8 ноября. до 80 000"


@pytest.fixture(scope="module")
def planner() -> TripPlanner:
    p = build_planner()
    assert p is not None
    return p


def _its(*names: str) -> list[dict]:
    labels = ["balanced", "cheapest", "most_comfortable"]
    return [{"label": labels[i], "hotel": {"hotel": {"name": n}}} for i, n in enumerate(names)]


ITS = _its("Гостевой дом «Вера и Марина»", "Гостевой дом «У Тамары» (Адлер)", "Отель «Вертикаль»")


@pytest.mark.parametrize(("text", "choice"), [
    ("давай Вертикаль", 3), ("Отель Вертикаль", 3), ("да, вариант 2", 2), ("беру третий", 3),
    ("давай Веру и Марину", 1), ("да, самый дешёвый", 2), ("комфортный", 3), ("у Тамары", 2)])
def test_pick_variant(text: str, choice: int) -> None:
    assert pick_variant(text, ITS).choice == choice


def test_pick_variant_unclear() -> None:
    assert pick_variant("да", ITS).choice is None and pick_variant("да", ITS).unknown == []
    assert pick_variant("давай Ривьеру", ITS).unknown == ["ривьеру"]
    assert pick_variant("Адлер", ITS).choice is None                # район в скобках — не название
    same = _its("Гостевой дом «У Ашота»", "Гостевой дом «У Ашота»", "Отель «Ачишхо»")
    assert pick_variant("давай у Ашота", same).ambiguous == [1, 2]


def test_children_count_rules(planner: TripPlanner) -> None:
    u = rules_parse(ADLER, TODAY)
    assert u.children_count == 2 and u.children_ages == []
    assert rules_parse("дети 5 и 8 лет", TODAY).children_ages == [5, 8]
    assert rules_parse("Сириус, дети 4, 9 лет", TODAY).children_ages == [4, 9]


@pytest.mark.asyncio
async def test_two_kids_without_ages(planner: TripPlanner) -> None:
    agent = GraphAgent(FakeLLM(), today=TODAY, planner=planner)
    run = await agent.run(ADLER, "s-kids2")
    assert run.request.children_count == 2 and kids_ages(run.request) == [7, 7]
    assert "2 взрослых + 2 ребёнка." in run.answer.text and "с 2 детьми" in run.answer.text
    assert "Возраст детей не назвали — считаю по 7 лет" in run.answer.text
    ages = await agent.run("дети 5 и 8 лет", "s-kids2")
    assert ages.request.children_ages == [5, 8] and "Дети: 2 ребёнка → 5, 8" in ages.answer.text


async def _offer(planner: TripPlanner, sid: str):
    """Предложение по ADLER и вариант k ≥ 2 с отелем, которого нет в других вариантах, + слово из его названия.
    Какие отели попадут в варианты, решает скоринг ML, поэтому вариант и слово ищем, а не зашиваем."""
    agent = GraphAgent(FakeLLM(), today=TODAY, planner=planner)
    run = await agent.run(ADLER, sid)
    its = [it.model_dump(mode="json") for it in run.answer.itineraries]
    for k in range(len(its), 1, -1):
        name = its[k - 1]["hotel"]["hotel"]["name"]
        for word in re.findall(r"[А-Яа-яЁё]{4,}", name.split("«", 1)[-1]):
            if pick_variant(f"давай {word}", its).choice == k:
                return agent, run, word, k
    pytest.skip("в вариантах нет отеля с уникальным названием")


@pytest.mark.asyncio
async def test_yes_with_name_buys_named_variant(planner: TripPlanner) -> None:
    agent, run, word, k = await _offer(planner, "s-name")
    bought = await agent.run(f"давай {word}", "s-name")
    assert bought.answer.mode.value == "purchased"
    assert bought.answer.itineraries[0].total_price == run.answer.itineraries[k - 1].total_price


@pytest.mark.asyncio
async def test_unknown_name_is_not_bought(planner: TripPlanner) -> None:
    agent, run, _, _ = await _offer(planner, "s-unknown")
    ask = await agent.run("давай Ривьеру", "s-unknown")
    assert ask.answer.mode.value == "confirm_action" and ask.answer.text.startswith("Не понял, какой вариант")
    assert ask.answer.itineraries                                  # варианты остались на экране
    bought = await agent.run("да, вариант 2", "s-unknown")         # пауза сохранилась
    assert bought.answer.mode.value == "purchased"
    assert bought.answer.itineraries[0].total_price == run.answer.itineraries[1].total_price


@pytest.mark.asyncio
async def test_name_only_asks_again(planner: TripPlanner) -> None:
    agent, run, word, k = await _offer(planner, "s-only")
    ask = await agent.run(word, "s-only")                          # без «да» — только переспрашиваем
    assert ask.answer.mode.value == "confirm_action" and ask.answer.text.startswith(f"Оформить вариант {k}")
    assert ask.answer.pending_action["price_total"] == run.answer.itineraries[k - 1].total_price
    bought = await agent.run("да", "s-only")
    assert bought.answer.itineraries[0].total_price == run.answer.itineraries[k - 1].total_price


@pytest.mark.asyncio
async def test_name_after_purchase_offers_it(planner: TripPlanner) -> None:
    agent, _run, word, k = await _offer(planner, "s-after")
    await agent.run("да", "s-after")                               # купили вариант 1
    again = await agent.run(f"я же сказал {word} !", "s-after")
    assert again.answer.mode.value == "confirm_action"
    assert "оформление заменит её" in again.answer.text and f"Оформить вариант {k}" in again.answer.text
    changed = await agent.run("да", "s-after")
    assert changed.answer.text.startswith("Бронь изменена")
    assert changed.answer.itineraries[0].total_price == again.answer.itineraries[k - 1].total_price


@pytest.mark.asyncio
async def test_plain_yes_still_buys_first(planner: TripPlanner) -> None:
    agent, run, _, _ = await _offer(planner, "s-yes")
    bought = await agent.run("да", "s-yes")
    assert bought.answer.itineraries[0].total_price == run.answer.itineraries[0].total_price
