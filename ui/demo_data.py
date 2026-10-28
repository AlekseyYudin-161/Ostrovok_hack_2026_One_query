"""Демо-данные в форме контракта (harness/contracts.py), чтобы показывать любой экран без бэкенда.

Открыть экран: `make ui`, затем в адресе `?screen=areas` (start|working|clarify|areas|results|diff|cart|paid|none).
Все цифры вымышленные.
"""
from __future__ import annotations

DEMO_QUERY = "Сочи на 7 дней в сентябре, отель 4★ на первой линии с завтраком, до 50 000 ₽. Еду один."

EXAMPLES = ["Сочи на неделю в сентябре, до 50 000 ₽", "Семья с ребёнком, море и всё включено",
            "Горы и лыжи на выходные", "Тихий отдых у моря"]

TRIPS = [
    {"title": "Море и тишина", "place": "Дагомыс · 7 ночей", "tags": ["У моря", "Тихо"], "photo": "sea-cliff",
     "query": "Тихий отдых у моря в Дагомысе на 7 ночей"},
    {"title": "Горы и лыжи", "place": "Красная Поляна · 5 ночей", "tags": ["Горы", "Активный отдых"], "photo": "mountains",
     "query": "Красная Поляна, 5 ночей, горы и активный отдых"},
    {"title": "Семейный отпуск", "place": "Адлер · 10 ночей", "tags": ["С детьми", "Всё включено"], "photo": "resort",
     "query": "Адлер на 10 ночей с ребёнком, всё включено"},
    {"title": "Выходные в центре", "place": "Сочи-центр · 3 ночи", "tags": ["Город", "Выходные"], "photo": "sunset-rock",
     "query": "Выходные в центре Сочи, 3 ночи"},
]

DESTINATIONS = [
    {"destination_id": "dagomys", "name": "Дагомыс", "best_for": ["Море", "Тихо"], "why": ["Галечный пляж в 5 минутах", "Нет шумных клубов", "Рядом вокзал"],
     "air_temp_c": 24, "sea_temp_c": 22, "travel_hours": 3.4, "direct": True, "est_total_price": 38400, "score": 0.92},
    {"destination_id": "adler", "name": "Адлер", "best_for": ["С детьми", "Бюджетно"], "why": ["Песчано-галечный пляж", "Рядом аэропорт, быстрый трансфер", "Много недорогих отелей"],
     "air_temp_c": 25, "sea_temp_c": 23, "travel_hours": 3.2, "direct": True, "est_total_price": 34900, "score": 0.84},
    {"destination_id": "center", "name": "Сочи-центр", "best_for": ["Город", "Рестораны"], "why": ["Набережная и вечерняя жизнь", "Всё рядом пешком", "Дороже и шумнее"],
     "air_temp_c": 24, "sea_temp_c": 22, "travel_hours": 3.3, "direct": True, "est_total_price": 52700, "score": 0.78},
    {"destination_id": "polyana", "name": "Красная Поляна", "best_for": ["Горы", "Активный отдых"], "why": ["Прогулки и канатные дороги", "Прохладнее, чем у моря", "Моря рядом нет"],
     "air_temp_c": 19, "sea_temp_c": None, "travel_hours": 4.3, "direct": False, "est_total_price": 41200, "score": 0.54},
]


def _itinerary(name, city, rating, hotel_price, label, tradeoffs, over=0, why=None):
    total = hotel_price + 14200 + 3200
    return {
        "destination_id": {"Дагомыс": "dagomys", "Адлер": "adler", "Сочи-центр": "center"}.get(city, city.lower()), "checkin": "2026-09-12", "checkout": "2026-09-19", "label": label, "total_price": total,
        "outbound": {"kind": "flight", "from_city": "Москва", "to_city": "Сочи", "depart": "2026-09-12T09:40", "arrive": "2026-09-12T12:25", "price_total": 7100},
        "inbound": {"kind": "flight", "from_city": "Сочи", "to_city": "Москва", "depart": "2026-09-19T17:10", "arrive": "2026-09-19T19:55", "price_total": 7100},
        "transfers": [{"from_place": "Аэропорт", "to_place": city, "minutes": 55, "price_total": 1600, "kind": "taxi"},
                      {"from_place": city, "to_place": "Аэропорт", "minutes": 55, "price_total": 1600, "kind": "taxi"}],
        "hotel": {"hotel": {"name": name, "city": city, "rating": rating}, "meal": "breakfast", "refundable": True, "price_total": hotel_price},
        "checks": [{"rule": "arrival_vs_checkin", "ok": True, "severity": "info", "message": "Прилёт в 12:25, заселение с 14:00: успеваете"},
                   {"rule": "checkout_vs_departure", "ok": True, "severity": "warning", "message": "После выезда в 12:00 до отъезда в аэропорт ≈ 3 ч.",
                    "fixes": ["запросить поздний выезд — 1 400 ₽", "оставить багаж в отеле"]},
                   {"rule": "dates_match", "ok": True, "severity": "info", "message": "Даты брони и рейсов совпадают"}],
        "why": why or [],
        "tradeoffs": [{"criterion": "x", "this_value": 0, "best_value": 0, "note": t} for t in tradeoffs],
    }


ITINERARIES = [
    _itinerary("Морской бриз", "Дагомыс", 8.7, 31500, "balanced", ["Дальше от центра: 25 минут на такси"],
                why=["первая линия", "бесплатная отмена до 29.10"]),
    _itinerary("Приморский", "Адлер", 8.2, 22200, "cheapest", ["Отель 3★ вместо 4★", "Вторая линия"]),
    _itinerary("Белый парус", "Сочи-центр", 9.1, 43900, "most_comfortable", ["Дороже других вариантов"]),
]

DEMO_REQUEST = {"raw_text": DEMO_QUERY, "destination": "Сочи", "budget_total": 50000, "nights": 7, "guests_adults": None,
                "children_ages": []}

ANSWER_PLAN = {"mode": "plan", "text": "Сочи: собрал 3 варианта поездки.\nПоездка: 01.11–08.11, 7 ночей.\nОстальное — допущения ниже.", "itineraries": ITINERARIES, "destinations": DESTINATIONS,
               "assumptions": [{"field": "origin_city", "value": "Москва", "reason": "Вылет из Москвы — допущение по умолчанию.", "confirmed": False},
                               {"field": "guests_adults", "value": 1, "reason": "Взрослых: 1 — вы написали «еду один».", "confirmed": True}],
               "changes": [], "candidates": [], "questions": [], "violations": []}

ANSWER_CHANGED = {**ANSWER_PLAN, "changes": ["Бюджет: 50 000 → 60 000 ₽", "«Белый парус» теперь укладывается в бюджет (было +11 300 ₽)",
                                             "Рекомендация сменилась: «Морской бриз» → «Белый парус»", "Не изменилось: 4★, первая линия, завтрак, даты"],
                  "itineraries": [{**ITINERARIES[2], "label": "balanced"}, {**ITINERARIES[0], "label": "custom"}, ITINERARIES[1]]}

ANSWER_CLARIFY = {"mode": "clarify", "text": "Почти всё понятно. Осталось два уточнения, чтобы не угадывать.",
                  "questions": ["Когда поедете?", "Сколько человек?"], "candidates": [], "itineraries": [], "destinations": [],
                  "assumptions": ANSWER_PLAN["assumptions"], "changes": [], "violations": []}
QUICK_REPLIES = {"Когда поедете?": ["Сентябрь", "Октябрь", "Ноябрь"], "Сколько человек?": ["1", "2", "С детьми"]}

ANSWER_NONE = {"mode": "no_solution", "text": "", "candidates": [], "itineraries": [], "destinations": [], "assumptions": [], "changes": [], "questions": [],
               "plan": {"relax": None}, "violations": ["4★ на первой линии с завтраком в сентябре стоят от 61 300 ₽, вы готовы потратить 50 000 ₽",
                              "С бюджетом 50 000 ₽ на первой линии остаются только 3★ без завтрака"]}
RELAX = [  # ТОЛЬКО для демо-экрана: настоящие варианты приходят в answer.plan.relax

    {"title": "Поднять бюджет", "text": "До 61 300 ₽. Остаются 4★, первая линия и завтрак.", "result": "+ 11 300 ₽", "patch": {"budget_total": 61300}, "green": True},
    {"title": "Не требовать первую линию", "text": "Отель в 5–10 минутах от моря, остальное без изменений.", "result": "от 44 100 ₽", "patch": {"preferences": []}},
    {"title": "Сдвинуть даты", "text": "Уехать на неделю позже, когда цены ниже.", "result": "от 46 200 ₽", "patch": {"checkin": "2026-09-19", "checkout": "2026-09-26"}},
]

ANSWER_NONE["plan"]["relax"] = RELAX

ANSWER_ADDON = {"mode": "confirm_action", "text": "Ранний заезд в «Белый парус» с 10:00 — 2 500 ₽ (демо). Добавить к брони? Ответьте «да» или «нет».",
                "candidates": [], "itineraries": [], "destinations": [], "assumptions": [], "changes": [], "questions": [], "violations": [],
                "pending_action": {"tool": "addon_mock"}}

DEMO_STATES = {
    "start": {}, "working": {"events": [{"type": "plan", "node": "planner"}, {"type": "node_start", "node": "understand"}, {"type": "tool_call", "node": "search"}]},
    "clarify": {"answer": ANSWER_CLARIFY}, "areas": {"answer": ANSWER_PLAN}, "results": {"answer": ANSWER_PLAN, "request": DEMO_REQUEST, "chosen_area": "Дагомыс"},
    "diff": {"answer": ANSWER_CHANGED, "request": DEMO_REQUEST, "prev_answer": ANSWER_PLAN, "chosen_area": "Дагомыс"},
    "cart": {"answer": ANSWER_PLAN, "request": DEMO_REQUEST, "chosen_area": "Дагомыс", "chosen": 0}, "paid": {"answer": ANSWER_PLAN, "chosen": 0, "booking_ref": "ODN-7K2F9Q"},
    "none": {"answer": ANSWER_NONE}, "confirm": {"answer": ANSWER_ADDON},
}
