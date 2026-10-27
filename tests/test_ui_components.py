"""Чистые HTML-компоненты UI: Streamlit не нужен."""
from ui import components as c
from ui import demo_data as d


def test_rub_uses_nbsp() -> None:
    assert c.rub(48900) == "48 900 ₽"
    assert c.rub(None) == "—"


def test_html_has_no_blank_lines() -> None:
    """Пустая строка внутри HTML ломает разбор markdown в Streamlit."""
    for html in (c.head(), c.stepper(1), c.area_card(d.DESTINATIONS[0], rec=True), c.trip_card(d.TRIPS[0]),
                 c.timeline([{"type": "plan", "node": "p"}], False), c.paid_hero("ODN-1"), c.no_solution_hero(["x"])):
        assert "\n\n" not in html


def test_options_from_itineraries_and_budget() -> None:
    opts = c.options_from_answer(d.ANSWER_PLAN, budget=50000)
    assert [o["name"] for o in opts] == ["Морской бриз", "Приморский", "Белый парус"]
    assert [o["over"] for o in opts] == [False, False, True]
    assert "дороже бюджета на 11 300 ₽" in c.hotel_card(opts[2], budget=50000)


def test_options_from_candidates_fallback() -> None:
    ans = {"candidates": [{"name": "Отель", "city": "Казань", "price_total": 1000, "rating": 8.0, "meal": "breakfast", "cancellation": "non_refundable"}]}
    (o,) = c.options_from_answer(ans)
    assert o["meal"] == "завтрак" and o["cancel"] == "без возврата" and o["label"] == "balanced"


def test_timeline_finishes() -> None:
    evs = [{"type": "plan"}, {"type": "tool_result", "node": "search", "payload": {"n": 3}}]
    assert "od-ring run" in c.timeline(evs, False)
    done = c.timeline(evs, True)
    assert "od-ring run" not in done and "нашёл вариантов: 3" in done


def test_day_format() -> None:
    assert c.day("2026-09-12T09:40") == "12 сентября"


def test_theme_buttons_are_blue_only() -> None:
    from ui import theme
    assert "ACEF81" not in theme.CSS and "blur(" not in theme.CSS  # старый салатовый и «стекло» убраны
    assert "st-key-green" not in theme.CSS
    for state in (":hover", ":active", ":focus-visible", ":disabled"):
        assert f".stButton > button{state}" in theme.CSS


def test_trip_card_has_no_invented_price() -> None:
    assert "₽" not in c.trip_card(d.TRIPS[0])


def test_relax_card_renders_agent_fields() -> None:
    html = c.relax_card({"title": "Поднять бюджет", "text": "До 61 300 ₽", "result": "+ 11 300 ₽", "patch": {"budget_total": 61300}})
    assert "Поднять бюджет" in html and "+ 11" in html


def test_no_italian_landmarks_in_photos() -> None:
    from ui.theme import ASSETS
    names = {p.stem for p in ASSETS.glob("*.jpg")}
    assert not names & {"city", "colorful-coast", "white-town"}  # Флоренция, Чинкве-Терре, Амальфи


def test_confirm_card_without_options():
    from ui import components as c

    html = c.confirm_card("Ранний заезд — 2 500 ₽.\nДобавить?", "addon_mock")
    assert "Ранний заезд" in html and "Добавить?" in html
    assert "\n\n" not in html
    assert c.options_from_answer({"mode": "confirm_action", "candidates": [], "itineraries": []}) == []
