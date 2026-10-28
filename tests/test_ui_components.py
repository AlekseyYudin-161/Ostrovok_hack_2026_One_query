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
    opts = c.options_from_answer(d.ANSWER_PLAN, budget=40000)  # бюджет сравниваем с ценой отеля, не с итогом поездки
    assert [o["name"] for o in opts] == ["Морской бриз", "Приморский", "Белый парус"]
    assert [o["over"] for o in opts] == [False, False, True]
    assert f"отель дороже бюджета на {c.rub(3900)}" in c.hotel_card(opts[2], budget=40000)


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


def test_params_patch_sends_only_changed_fields():
    from ui import components as c

    req = {"budget_total": None, "nights": 7, "guests_adults": None, "children_ages": [4, 6]}
    assert c.params_patch(req, None, 7, 2, "4, 6") == ({}, "")  # ничего не меняли — patch пуст (кнопка неактивна)
    assert c.params_patch(req, 100000, 7, 2, "4, 6")[0] == {"budget_total": 100000}
    assert c.params_patch(req, None, 7, 3, "4, 6")[0] == {"guests_adults": 3}
    assert c.params_patch(req, None, 7, 2, "4")[0] == {"children_ages": [4]}
    patch, err = c.params_patch(req, None, 7, 2, "четыре")
    assert err and patch == {}


def test_parse_ages():
    from ui import components as c

    assert c.parse_ages("4, 6") == [4, 6] and c.parse_ages("") == [] and c.parse_ages("4 6") == [4, 6]
    assert c.parse_ages("20") is None and c.parse_ages("a") is None


def test_over_budget_compares_hotel_price_not_trip_total():
    from ui import components as c

    ans = {"itineraries": [{"total_price": 120000, "hotel": {"hotel": {"name": "Отель"}, "price_total": 80000}}]}
    o = c.options_from_answer(ans, 100000)[0]
    assert o["over"] is False and o["stay_price"] == 80000
    assert "дороже бюджета" not in c.hotel_card(o, budget=100000)
    o2 = c.options_from_answer(ans, 70000)[0]
    assert o2["over"] and f"отель дороже бюджета на {c.rub(10000)}" in c.hotel_card(o2, budget=70000)


def test_checks_warning_is_yellow_with_fixes():
    from ui import components as c

    html = c.checks_list([{"ok": True, "severity": "warning", "message": "Ждать 3 ч.", "fixes": ["запросить ранний заезд"]},
                          {"ok": True, "severity": "info", "message": "Всё хорошо"}])
    assert "#F5A623" in html and "запросить ранний заезд" in html and "#CDF4B0" in html
    assert html.count("Что можно сделать") == 1


def test_trip_header_only_trip_line_and_assumptions():
    from ui import components as c

    text = "Сочи: собрал 3 варианта.\nПоездка: 01.11–08.11, 7 ночей.\nОформить вариант 1?"
    html = c.trip_header(text, [{"reason": "Вылет из Москвы."}, {"reason": "Взрослых: 2."}])
    assert "Поездка: 01.11–08.11" in html and "собрал" not in html and "Оформить" not in html
    assert html.count("Вылет из Москвы.") == 1 and "Взрослых: 2." in html


def test_cart_summary_compares_budget_with_stay():
    from ui import components as c

    assert f"Проживание в рамках бюджета, остаётся {c.rub(20000)}" in c.cart_summary([("Отель", 80000)], 120000, 100000, 80000)
    assert f"Проживание выше бюджета на {c.rub(10000)}" in c.cart_summary([("Отель", 80000)], 120000, 70000, 80000)


def test_why_is_shown_above_tradeoffs():
    from ui import components as c

    ans = {"itineraries": [{"total_price": 1000, "why": ["первая линия", "бесплатная отмена до 29.10"],
                            "tradeoffs": [{"note": "Дальше от центра"}], "hotel": {"hotel": {"name": "Отель"}, "price_total": 900}}]}
    html = c.hotel_card(c.options_from_answer(ans)[0])
    assert "Почему этот вариант" in html and "первая линия" in html
    assert html.index("Почему этот вариант") < html.index("Чем пожертвовали")
    plain = c.hotel_card(c.options_from_answer({"itineraries": [{"total_price": 1, "hotel": {"hotel": {"name": "О"}}}]})[0])
    assert "Почему" not in plain


def test_agent_question_is_prominent_and_pending_variant_is_highlighted() -> None:
    ans = d.ANSWER_ASK
    html = c.agent_question(ans["questions"])
    assert "Вопрос агента" in html and "Оформить вариант 3" in html and "\n\n" not in html
    assert c.agent_question([]) == ""
    opts = c.options_from_answer(ans)
    assert c.pending_index(opts, ans["pending_action"]) == 2  # по label
    assert c.pending_index(opts, {"hotel_id": "нет такого"}) == 0
    assert c.pending_index(opts, None) == 0


def test_trip_header_shows_changes_above_assumptions() -> None:
    html = c.trip_header(d.ANSWER_PLAN["text"], d.ANSWER_PLAN["assumptions"], d.ANSWER_ASK["changes"])
    assert "Изменил: Даты: 30.10–01.11 → 01.11–03.11; Район: Адлер → Хоста" in html
    assert html.index("Поездка:") < html.index("Изменил:") < html.index("Вылет из Москвы")
    assert "Изменил" not in c.trip_header(d.ANSWER_PLAN["text"], [], [])


def test_route_line_in_card() -> None:
    (o, *_) = c.options_from_answer(d.ANSWER_PLAN)
    assert o["route"] == "✈️ 09:40→12:25 · обратно 17:10→19:55 · 🚕 такси 55 мин"
    assert o["route"] in c.hotel_card(o)
    assert c.route_line({}) == ""
    bus = {"transfers": [{"kind": "public", "minutes": 40}, {"kind": "shuttle", "minutes": 5}]}
    assert c.route_line(bus) == "🚌 автобус 40 мин"


def test_transfer_title_has_kind() -> None:
    t = {"from_place": "Аэропорт", "to_place": "Адлер", "kind": "shuttle"}
    assert c.transfer_title(t) == "Трансфер, шаттл: Аэропорт → Адлер"
    assert c.transfer_title({**t, "kind": "public"}).startswith("Трансфер, автобус")


def test_params_patch_pets_only_when_changed() -> None:
    req = {"budget_total": 50000, "nights": 7, "guests_adults": 2, "children_ages": [], "pets": False}
    assert c.params_patch(req, 50000, 7, 2, "", False)[0] == {}
    assert c.params_patch(req, 50000, 7, 2, "", True)[0] == {"pets": True}
    assert c.params_patch({**req, "pets": True}, 50000, 7, 2, "", False)[0] == {"pets": False}
    assert c.params_patch(req, 50000, 7, 2, "")[0] == {}  # переключатель не передан


def test_no_solution_keeps_only_trip_and_no_variants_lines() -> None:
    text = ("Поездка: 01.11–08.11, 7 ночей.\nИзменил: Район: Хоста → Дагомыс.\n"
            "Дагомыс: нет вариантов до 50 000 ₽. Поднять бюджет или посмотреть другой район?")
    assert c.no_solution_lines(text) == ["Поездка: 01.11–08.11, 7 ночей.", "Дагомыс: нет вариантов до 50 000 ₽"]
    assert c.no_solution_lines("") == [] and c.no_solution_lines(None) == []
