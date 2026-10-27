"""Чистые HTML-компоненты UI: Streamlit не нужен."""
from ui import components as c
from ui import demo_data as d


def test_rub_uses_nbsp() -> None:
    assert c.rub(48900) == "48 900 ₽"
    assert c.rub(None) == "—"


def test_html_has_no_blank_lines() -> None:
    """Пустая строка внутри HTML ломает разбор markdown в Streamlit."""
    for html in (c.head(), c.stepper(1), c.area_card(d.DESTINATIONS[0], rec=True), c.trip_card(d.TRIPS[0]),
                 c.timeline([{"type": "plan", "node": "p"}], False), c.paid_hero("ODN-1"), c.no_solution_hero(["x"])):
        assert "\n\n" not in html


def test_options_from_itineraries_and_budget() -> None:
    # бюджет — только проживание (ADR-018): отели 31 500 / 22 200 / 43 900 ₽ против 40 000 ₽
    opts = c.options_from_answer(d.ANSWER_PLAN, budget=40000)
    assert [o["name"] for o in opts] == ["Морской бриз", "Приморский", "Белый парус"]
    assert [o["over"] for o in opts] == [False, False, True]
    assert "проживание дороже бюджета на 3 900 ₽" in c.hotel_card(opts[2], budget=40000)
    assert not any(o["over"] for o in c.options_from_answer(d.ANSWER_PLAN, budget=50000))  # дорога в бюджет не входит


def test_cart_summary_compares_hotel_only() -> None:
    assert "Проживание в рамках бюджета, остаётся 18 500 ₽" in c.cart_summary([("Отель", 31500)], 48900, 50000, hotel=31500)
    assert "выше бюджета на 10 000 ₽" in c.cart_summary([("Отель", 1)], 60000, 50000)  # без цены отеля — по итогу


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
