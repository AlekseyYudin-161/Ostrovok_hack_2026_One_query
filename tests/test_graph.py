"""Бейзлайн-граф (шаги 1–3): офлайн на правилах, путь через LLM по схеме, пауза перед покупкой."""

import json
from datetime import date

import httpx
import pytest

from app.main import app
from harness.agent_graph import GraphAgent, Understood, plural, rub, yes_no
from harness.areas import load_areas, suggest_area
from harness.dates import next_weekend, parse_dates

TODAY = date(2026, 10, 25)  # воскресенье
from harness.contracts import Agent
from harness.fakes import FakeLLM
from harness.llm import OpenAICompatLLM


def _completion(content: str) -> dict:
    return {"id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


def test_protocol() -> None:
    assert isinstance(GraphAgent(FakeLLM()), Agent)


def test_text_helpers() -> None:
    assert [plural(n, "вариант", "варианта", "вариантов") for n in (1, 3, 5, 11, 21)] == \
        ["вариант", "варианта", "вариантов", "вариантов", "вариант"]
    assert rub(42000.0) == "42 000 ₽"
    assert yes_no("Да, оформляй!") is True and yes_no("не надо") is False
    assert yes_no("Адлер, дальше") is None and yes_no("давай в Хосту до 30 тыс на неделю") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("text", "mode", "n"), [
    ("Адлер на неделю, до 50 тыс", "confirm_action", 1),   # 42000 ≤ 50000 < 61000
    ("Хочу куда-нибудь на выходные", "clarify", 0),
    ("Красная Поляна, горы, до 30 тыс", "no_solution", 0),
])
async def test_offline_modes(text: str, mode: str, n: int) -> None:
    run = await GraphAgent(FakeLLM()).run(text, "s-offline")
    assert run.answer.mode.value == mode and len(run.answer.candidates) == n


@pytest.mark.asyncio
async def test_confirm_yes_buys() -> None:
    agent = GraphAgent(FakeLLM())
    first = await agent.run("Адлер на неделю, до 50 тыс", "s-yes")
    assert first.answer.mode.value == "confirm_action" and "42 000 ₽" in first.answer.text
    assert "1 вариант " in first.answer.text
    second = await agent.run("да", "s-yes")
    assert second.answer.mode.value == "purchased" and second.answer.plan["booking_ref"].startswith("DEMO-")
    assert second.request.destination == "Адлер"


@pytest.mark.asyncio
async def test_confirm_no_cancels_and_new_request_works() -> None:
    agent = GraphAgent(FakeLLM())
    await agent.run("Адлер до 50 тыс", "s-no")
    cancelled = await agent.run("нет", "s-no")
    assert cancelled.answer.mode.value == "plan" and "не оформляю" in cancelled.answer.text
    again = await agent.run("Хоста до 70 тыс", "s-no")
    assert again.answer.mode.value == "confirm_action" and again.request.destination == "Хоста"


@pytest.mark.asyncio
async def test_new_request_instead_of_answer() -> None:
    agent = GraphAgent(FakeLLM())
    await agent.run("Адлер до 50 тыс", "s-switch")
    run = await agent.run("Красная Поляна, горы, до 30 тыс", "s-switch")   # не «да/нет» → новый запрос
    assert run.answer.mode.value == "no_solution" and run.request.destination == "Красная Поляна"


@pytest.mark.asyncio
async def test_kids_and_pets_become_visible_assumptions() -> None:
    run = await GraphAgent(FakeLLM()).run("Адлер на неделю, до 50 тыс, с ребенком и собакой", "s-pets")
    fields = {a.field for a in run.answer.assumptions}
    assert {"children", "pets"} <= fields and run.request.pets is True
    assert "животн" in run.answer.text and "ребёнк" in run.answer.text


@pytest.mark.asyncio
async def test_llm_structured_path() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_completion(json.dumps(
            {"area": "Сириус", "other_destination": None, "wishes": ["с детьми"], "budget_total": 70000,
             "checkin": "2026-11-02", "checkout": "2026-11-05", "nights": 3, "adults": 2, "with_kids": True,
             "children_ages": [6], "pets": False, "min_stars": None, "beach_line": False, "meal": None, "children_count": None, "month": 11}, ensure_ascii=False)))

    llm = OpenAICompatLLM("http://router/v1", "k", "m", extra_body={"reasoning": {"enabled": False}},
                          http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    agent = GraphAgent(llm, today=TODAY)
    run = await agent.run("Двое взрослых и ребёнок 6 лет, в Сириус со 2 по 5 ноября, до 70 тыс", "s-llm")
    assert run.request.destination == "Сириус" and run.request.nights == 3 and run.request.month == 11
    assert str(run.request.checkin) == "2026-11-02" and run.request.children_ages == [6]
    assert "Сегодня 2026-10-25" in seen[0]["messages"][1]["content"]
    assert run.answer.mode.value == "confirm_action" and len(run.answer.candidates) == 2
    assert seen[0]["response_format"]["type"] == "json_schema" and seen[0]["reasoning"] == {"enabled": False}
    props = seen[0]["response_format"]["json_schema"]["schema"]["properties"]
    assert "Красная Поляна" in props["area"]["anyOf"][0]["enum"] and {"pets", "checkin", "other_destination"} <= set(props)


@pytest.mark.asyncio
async def test_bad_llm_falls_back_to_rules() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion("не JSON"))

    llm = OpenAICompatLLM("http://router/v1", "k", "m",
                          http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    run = await GraphAgent(llm).run("Хоста до 50 тыс", "s-bad")
    assert run.request.destination == "Хоста"


def test_schema_is_strict_friendly() -> None:
    schema = Understood.model_json_schema()
    assert set(schema["required"]) == set(schema["properties"])  # strict требует все поля обязательными


@pytest.mark.asyncio
async def test_chat_sse_two_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    app.state.agent = GraphAgent()
    transport = httpx.ASGITransport(app=app)

    async def turn(c: httpx.AsyncClient, text: str) -> list[dict]:
        r = await c.post("/chat", json={"text": text, "session_id": "s-sse"})
        return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]

    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        first = await turn(c, "Адлер до 50 тыс")
        second = await turn(c, "да")
    assert first[0]["type"] == "plan" and "cart" in [e["type"] for e in first]
    assert first[-1]["type"] == "answer" and first[-1]["payload"]["answer"]["mode"] == "confirm_action"
    assert "purchase" in [e["type"] for e in second]
    assert second[-1]["payload"]["answer"]["mode"] == "purchased"


# ---------- шаг 3: вне локации, даты, гости, бюджет ----------
def test_dates_rules() -> None:
    assert next_weekend(TODAY) == (date(2026, 10, 30), date(2026, 11, 1))          # вс → следующие пт–вс
    assert next_weekend(date(2026, 10, 27)) == (date(2026, 10, 30), date(2026, 11, 1))  # вт → эти пт–вс
    assert parse_dates("с 1 ноября на неделю", TODAY)[:2] == (date(2026, 11, 1), None)
    assert parse_dates("с 3 по 10 января", TODAY)[:2] == (date(2027, 1, 3), date(2027, 1, 10))  # прошло → след. год
    assert parse_dates("хочу на выходные", TODAY)[2].startswith("«На выходные»")


@pytest.mark.asyncio
async def test_out_of_location_suggest_yes() -> None:
    agent = GraphAgent(FakeLLM(), today=TODAY)
    first = await agent.run("Хочу в Турцию на море с детьми, до 60 тыс", "s-oos")
    assert first.answer.mode.value == "clarify" and "только по Большому Сочи" in first.answer.text
    suggested = first.answer.pending_action["suggested_area"]
    second = await agent.run("да", "s-oos")
    assert second.answer.mode.value == "confirm_action" and second.request.destination == suggested
    assert any("Вместо «Турция»" in a.reason for a in second.request.assumptions)


@pytest.mark.asyncio
async def test_out_of_location_suggest_no() -> None:
    agent = GraphAgent(FakeLLM(), today=TODAY)
    await agent.run("Хочу в Египет", "s-oos-no")
    run = await agent.run("нет", "s-oos-no")
    assert run.answer.mode.value == "clarify" and run.request.destination is None


@pytest.mark.asyncio
async def test_origin_city_is_not_out_of_location() -> None:
    run = await GraphAgent(FakeLLM(), today=TODAY).run("Летим из Питера в Адлер до 50 тыс", "s-origin")
    assert run.request.destination == "Адлер" and run.answer.mode.value == "confirm_action"


@pytest.mark.asyncio
async def test_dates_guests_budget_in_request() -> None:
    run = await GraphAgent(FakeLLM(), today=TODAY).run(
        "Адлер с 1 ноября на неделю, вдвоём и ребёнок 5 лет, до 50 тыс", "s-dates")
    r = run.request
    assert (str(r.checkin), str(r.checkout), r.nights) == ("2026-11-01", "2026-11-08", 7)
    assert r.guests_adults == 2 and r.children_ages == [5]
    reasons = " ".join(a.reason for a in r.assumptions)
    assert "за весь срок для всех гостей" in reasons and "(5 лет)" in reasons
    assert "Поездка: 01.11–08.11, 7 ночей · 2 взрослых + ребёнок (5 лет)." in run.answer.text


@pytest.mark.asyncio
async def test_weekend_assumption_visible() -> None:
    run = await GraphAgent(FakeLLM(), today=TODAY).run("Адлер на выходные до 50 тыс", "s-wknd")
    assert str(run.request.checkin) == "2026-10-30" and run.request.nights == 2
    assert "Поездка: 30.10–01.11, 2 ночи." in run.answer.text
    assert "«На выходные» понял как пятница — воскресенье" in run.answer.text


def test_areas_from_ml_file(tmp_path) -> None:
    f = tmp_path / "areas.json"
    f.write_text(json.dumps([
        {"area_id": "imeretinka", "name": "Имеретинка", "best_for": ["с детьми", "море"]},
        {"area_id": "rosa_khutor", "name": "Роза Хутор", "best_for": ["горы/лыжи"], "aliases": ["Роза"]},
    ], ensure_ascii=False), encoding="utf-8")
    areas = load_areas(f)
    assert [a.name for a in areas] == ["Имеретинка", "Роза Хутор"]
    assert "сириус" in areas[0].aliases and areas[1].aliases == ["роза"]
    assert suggest_area(areas, ["горы/лыжи"]).name == "Роза Хутор"


def test_trip_summary_forms() -> None:
    from harness.agent_graph import trip_summary
    from harness.contracts import TripRequest
    assert trip_summary(TripRequest(raw_text="x")) is None
    r = TripRequest(raw_text="x", nights=1, guests_adults=1, children_ages=[3, 7], pets=True)
    assert trip_summary(r) == "Поездка: 1 ночь · 1 взрослый + 2 ребёнка (3, 7 лет) + с животным."


# ---------- пересборка (L11) и правка из панели (L21) ----------
BASE = "Адлер с 1 ноября на неделю, вдвоём и ребёнок 5 лет, с собакой, до 50 тыс"


@pytest.mark.asyncio
async def test_replan_shift_keeps_everything_else() -> None:
    agent = GraphAgent(FakeLLM(), today=TODAY)
    await agent.run(BASE, "s-shift")
    run = await agent.run("а если на два дня позже?", "s-shift")
    r = run.request
    assert (str(r.checkin), str(r.checkout), r.nights) == ("2026-11-03", "2026-11-10", 7)
    assert r.destination == "Адлер" and r.budget_total == 50000 and r.guests_adults == 2
    assert r.children_ages == [5] and r.pets is True                                 # Constraint Retention
    assert run.answer.changes == ["Даты: 01.11–08.11 → 03.11–10.11"]
    assert "Изменил: Даты: 01.11–08.11 → 03.11–10.11." in run.answer.text


@pytest.mark.asyncio
async def test_replan_cheaper_and_new_budget() -> None:
    agent = GraphAgent(FakeLLM(), today=TODAY)
    await agent.run(BASE, "s-cheap")
    run = await agent.run("а подешевле?", "s-cheap")
    assert run.request.budget_total == 40000 and "−20%" in run.answer.text
    run = await agent.run("бюджет 45 тыс", "s-cheap")
    assert run.request.budget_total == 45000 and run.request.checkin == date(2026, 11, 1)


@pytest.mark.asyncio
async def test_replan_add_pet_and_change_area() -> None:
    agent = GraphAgent(FakeLLM(), today=TODAY)
    await agent.run("Адлер с 1 ноября на неделю до 70 тыс", "s-pet")
    run = await agent.run("с нами едет собака", "s-pet")
    assert run.request.pets is True and "Животные: нет → да" in run.answer.changes
    run = await agent.run("а если в Хосте?", "s-pet")
    assert run.request.destination == "Хоста" and run.request.pets is True
    assert str(run.request.checkin) == "2026-11-01" and "Район: Адлер → Хоста" in run.answer.changes


@pytest.mark.asyncio
async def test_fresh_search_resets() -> None:
    agent = GraphAgent(FakeLLM(), today=TODAY)
    await agent.run(BASE, "s-fresh")
    run = await agent.run("хочу куда-нибудь на выходные", "s-fresh")
    assert run.answer.mode.value == "clarify" and run.request.pets is False and not run.answer.changes


@pytest.mark.asyncio
async def test_patch_from_panel() -> None:
    agent = GraphAgent(FakeLLM(), today=TODAY)
    await agent.run(BASE, "s-patch")
    run = await agent.run("", "s-patch", patch={"checkin": "2026-11-05", "budget_total": 70000, "pets": False,
                                                "hacker": "x", "destination": "Атлантида"})
    r = run.request
    assert (str(r.checkin), str(r.checkout), r.budget_total, r.pets) == ("2026-11-05", "2026-11-12", 70000, False)
    assert r.destination == "Адлер" and r.children_ages == [5]           # неизвестные поле и район — пропущены
    assert "Бюджет: 50 000 ₽ → 70 000 ₽" in run.answer.changes and run.answer.mode.value == "confirm_action"


@pytest.mark.asyncio
async def test_patch_via_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    app.state.agent = GraphAgent(today=TODAY)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        await c.post("/chat", json={"text": "Адлер до 50 тыс", "session_id": "s-api-patch"})
        r = await c.post("/chat", json={"session_id": "s-api-patch", "patch": {"destination": "Хоста"}})
        bad = await c.post("/chat", json={"session_id": "s-api-patch"})
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    assert "replan" in [e["type"] for e in events]
    assert events[-1]["payload"]["request"]["destination"] == "Хоста"
    assert bad.status_code == 422                                          # ни text, ни patch


def test_date_hint() -> None:
    from harness.dates import has_date_hint
    for t in ("с 1 ноября", "на выходные", "2-5 ноября", "в декабре", "02.11", "завтра", "в пятницу",
              "на следующей неделе", "на новый год", "через две недели"):
        assert has_date_hint(t), t
    for t in ("Адлер на неделю до 50 тыс", "Хоста, двое взрослых", "Сириус с детьми 5 лет", "в среднем до 40 тыс"):
        assert not has_date_hint(t), t


@pytest.mark.asyncio
async def test_llm_invented_dates_dropped() -> None:
    """Модель вернула даты и «бюджетно», которых в тексте нет, — не берём, а просим дату заезда."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion(json.dumps(
            {"area": "Адлер", "other_destination": None, "wishes": ["бюджетно"], "budget_total": 50000,
             "checkin": "2026-10-30", "checkout": "2026-11-06", "nights": 7, "adults": None, "with_kids": False,
             "children_ages": [], "pets": False, "min_stars": None, "beach_line": False, "meal": None, "children_count": None, "month": 10}, ensure_ascii=False)))

    llm = OpenAICompatLLM("http://router/v1", "k", "m",
                          http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    run = await GraphAgent(llm, today=TODAY).run("Адлер на неделю до 50 тыс", "s-nodates")
    r = run.request
    assert r.checkin is None and r.checkout is None and r.month is None and r.nights == 7
    assert "бюджетно" not in r.preferences and "30.10" not in run.answer.text
    assert "Поездка: 7 ночей" in run.answer.text and "Точных дат нет — напишите дату заезда." in run.answer.text
    assert run.answer.text.count("7 ночей") == 1


@pytest.mark.asyncio
async def test_sochi_without_area_picks_one() -> None:
    """«Хочу в Сочи на море»: модель не дала район — подбираем по пожеланиям и говорим об этом, без лишнего вопроса."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion(json.dumps(
            {"area": None, "other_destination": None, "wishes": ["море"], "budget_total": 60000,
             "checkin": "2026-11-06", "checkout": "2026-11-11", "nights": 5, "adults": 2, "with_kids": False,
             "children_ages": [], "pets": False, "min_stars": None, "beach_line": False, "meal": None, "children_count": None, "month": 11}, ensure_ascii=False)))

    llm = OpenAICompatLLM("http://router/v1", "k", "m",
                          http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    run = await GraphAgent(llm, today=TODAY).run("Хочу в Сочи на море с 6 ноября на 5 ночей, вдвоём, до 60 тыс", "s-sochi")
    expected = suggest_area(load_areas("builtin"), ["море"]).name
    assert run.request.destination == expected and str(run.request.checkin) == "2026-11-06" and run.request.nights == 5
    assert "Район не назвали — подобрал" in run.answer.text


@pytest.mark.asyncio
async def test_answer_to_clarify_keeps_dates() -> None:
    agent = GraphAgent(FakeLLM(), today=TODAY)
    first = await agent.run("Хочу куда-нибудь с 6 ноября на 5 ночей, вдвоём", "s-fill")
    assert first.answer.mode.value == "clarify" and "Большом Сочи" in first.answer.text
    run = await agent.run("а если в Хосте?", "s-fill")
    r = run.request
    assert r.destination == "Хоста" and str(r.checkin) == "2026-11-06" and r.nights == 5 and r.guests_adults == 2
    assert run.answer.changes == []


@pytest.mark.asyncio
async def test_llm_area_without_text_support_dropped() -> None:
    """Модель выбрала «Адлер», хотя в тексте только «Сочи», — район подбираем сами и пишем об этом."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion(json.dumps(
            {"area": "Адлер", "other_destination": None, "wishes": ["море"], "budget_total": 60000,
             "checkin": "2026-11-06", "checkout": "2026-11-11", "nights": 5, "adults": 2, "with_kids": False,
             "children_ages": [], "pets": False, "min_stars": None, "beach_line": False, "meal": None, "children_count": None, "month": 11}, ensure_ascii=False)))

    llm = OpenAICompatLLM("http://router/v1", "k", "m",
                          http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    run = await GraphAgent(llm, today=TODAY).run("Хочу в Сочи на море с 6 ноября на 5 ночей, вдвоём", "s-guess")
    assert run.request.destination == "Сочи-центр" and "Район не назвали — подобрал район «Сочи-центр»" in run.answer.text


def test_month_forms_and_weekend_in_month() -> None:
    from harness.dates import has_date_hint, month_from_text
    assert month_from_text("на 10 дней в мае") == 5 and has_date_hint("в мае")
    start, end, note = parse_dates("на 2 ночи в выходные в январе", TODAY)
    assert (start, end) == (date(2027, 1, 1), date(2027, 1, 3)) and "первые выходные" in note


def test_rules_budget_with_spaces_and_adults() -> None:
    from harness.agent_graph import rules_parse
    u = rules_parse("Центр Сочи до 30 000 ₽, четверо взрослых, 4★ первая линия", TODAY)
    assert u.budget_total == 30000 and u.adults == 4 and u.min_stars == 4 and u.beach_line
    assert rules_parse("Адлер до 50 тыс, еду один", TODAY).adults == 1
