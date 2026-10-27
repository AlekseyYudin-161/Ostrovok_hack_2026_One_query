"""HTML-компоненты экранов. Чистые функции без Streamlit: принимают dict-ы из contracts.py
(AgentAnswer.model_dump и т.п.), возвращают строку HTML. Благодаря этому их проверяют обычными тестами.
"""
from __future__ import annotations

import html
import re
from typing import Any

from ui.theme import photo_uri

TICK = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7"/></svg>'
def hero_icon(path: str, color: str) -> str:
    """Большая линейная иконка для финальных экранов (одинаковая для «оформлено» и «решения нет»)."""
    return (f'<svg width="72" height="72" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.3" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{path}</svg>')


HERO_OK = '<path d="M3.5 12.5l6.5 6.5L21 5.5"/>'
HERO_NONE = '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M20 20l-4.9-4.9M8 8l5 5M13 8l-5 5"/>'
STEPS = ["Запрос", "Район", "Варианты", "Корзина"]
LABELS = {"balanced": "Рекомендуем · сбалансированный", "cheapest": "Самый дешёвый",
          "most_comfortable": "Самый комфортный", "custom": "Ваш вариант"}
MEALS = {"none": "без питания", "breakfast": "завтрак", "half_board": "полупансион",
         "full_board": "полный пансион", "all_inclusive": "всё включено"}
CANCEL = {"free_until_3d": "бесплатная отмена за 3 дня", "non_refundable": "без возврата"}
# какое фото к какому месту; остальное — по хэшу названия
AREA_PHOTO = {"дагомыс": "sea-cliff", "адлер": "resort", "сочи": "sunset-rock", "центр": "sunset-rock",
              "красная поляна": "meadow", "горы": "mountains"}
HOTEL_PHOTOS = ["sea-cliff", "resort", "sunset-rock"]


def _h(s: str) -> str:
    """Склеивает HTML в одну строку: Streamlit-markdown ломается на пустых строках и отступах."""
    return re.sub(r"\n\s*", "", s).strip()


def esc(x: Any) -> str:
    return html.escape(str(x), quote=True)


def rub(n: float | int | None) -> str:
    return "—" if n is None else f"{round(n):,}".replace(",", " ") + " ₽"


MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def day(iso: str) -> str:
    """'2026-09-12T09:40' -> '12 сентября'."""
    try:
        return f"{int(iso[8:10])} {MONTHS[int(iso[5:7]) - 1]}"
    except (ValueError, IndexError):
        return iso[:10]


def photo_for(name: str, kind: str = "hotel") -> str:
    low = (name or "").lower()
    for key, photo in AREA_PHOTO.items():
        if key in low:
            return photo
    return HOTEL_PHOTOS[sum(map(ord, low)) % len(HOTEL_PHOTOS)]


def cover(photo: str, alt: str, badge: str = "", score: str = "", green: bool = False, height: int = 170) -> str:
    b = f'<span class="od-badge{" green" if green else ""}">{esc(badge)}</span>' if badge else ""
    s = f'<span class="od-badge right">{esc(score)}</span>' if score else ""
    return _h(f'<div class="od-cover" style="height:{height}px"><img src="{photo_uri(photo)}" alt="{esc(alt)}">{b}{s}</div>')


def chip(text: str, kind: str = "") -> str:
    return f'<span class="od-chip {kind}">{esc(text)}</span>'


def chips(items: list[str], kind: str = "") -> str:
    return "".join(chip(t, kind) for t in items)


def head() -> str:
    return _h('<div class="od-head"><span class="od-logo"><b>О!</b>дин запрос</span>'
              '<span class="od-chip" style="margin:0">Демо-данные</span></div>')


def stepper(active: int) -> str:
    out = []
    for i, name in enumerate(STEPS):
        cls, mark = ("done", TICK) if i < active else (("now", str(i + 1)) if i == active else ("", str(i + 1)))
        out.append(f'<div class="od-step {cls}"><i>{mark}</i><span>{name}</span></div>')
        if i < len(STEPS) - 1:
            out.append('<span class="od-line"></span>')
    return f'<div class="od-steps">{"".join(out)}</div>'


def query_card(text: str) -> str:
    return _h(f'<div class="od-glass" style="padding:18px 24px;border-radius:24px;font-size:16px;line-height:1.45">🔎&nbsp; {esc(text)}</div>')


def row(label: str, value: str, big: bool = False) -> str:
    return f'<div class="od-row{" big" if big else ""}"><span>{esc(label)}</span><span>{esc(value)}</span></div>'


def bullets(items: list[tuple[str, str]]) -> str:
    """items: (цвет точки, html-текст)."""
    return "".join(f'<div class="od-li"><span class="od-dot" style="background:{c}"></span><span>{t}</span></div>' for c, t in items)


# ---------- адаптер: ответ агента → карточки отелей ----------
def options_from_answer(answer: dict, budget: float | None = None) -> list[dict]:
    """Единый вид варианта для карточки. Берёт itineraries (если есть), иначе candidates."""
    opts: list[dict] = []
    for it in answer.get("itineraries") or []:
        offer = it.get("hotel") or {}
        hotel = offer.get("hotel") or {}
        opts.append({
            "name": hotel.get("name", "Отель"), "area": hotel.get("city") or "", "rating": hotel.get("rating"),
            "price": it.get("total_price") or offer.get("price_total"), "label": it.get("label", "custom"),
            "meal": MEALS.get(offer.get("meal", "none"), ""),
            "cancel": "бесплатная отмена" if offer.get("refundable", True) else "без возврата",
            "tradeoffs": [t.get("note", "") for t in it.get("tradeoffs", [])],
            "checks": it.get("checks", []), "raw": it,
        })
    if not opts:
        for i, c in enumerate(answer.get("candidates") or []):
            opts.append({
                "name": c.get("name", "Отель"), "area": c.get("city") or "", "rating": c.get("rating"),
                "price": c.get("price_total"), "label": "balanced" if i == 0 else "custom",
                "meal": MEALS.get(c.get("meal") or "", c.get("meal") or ""),
                "cancel": CANCEL.get(c.get("cancellation") or "", c.get("cancellation") or ""),
                "tradeoffs": [], "checks": [], "raw": c,
            })
    for o in opts:
        o["over"] = bool(budget and o["price"] and o["price"] > budget)
        o["photo"] = photo_for(o["name"])
    return opts


def hotel_card(o: dict, rec: bool = False, budget: float | None = None) -> str:
    badge = LABELS.get(o["label"], "") if o["label"] != "custom" else ""
    sub = " · ".join(x for x in [o["area"], f'{o["rating"]}' if o.get("rating") else "", o.get("meal", ""), o.get("cancel", "")] if x)
    warn = ""
    if o.get("over") and budget:
        warn = f'<div style="margin-top:8px">{chip(f"дороже бюджета на {rub(o["price"] - budget)}", "red")}</div>'
    trade = ""
    if o.get("tradeoffs"):
        li = "".join(f"<li>{esc(t)}</li>" for t in o["tradeoffs"])
        trade = f'<div style="margin-top:12px"><b style="font-size:13px">Чем пожертвовали</b><ul class="od-muted" style="margin:4px 0 0;padding-left:18px;font-size:14px">{li}</ul></div>'
    return _h(f'''<div class="od-glass tight {"rec" if rec else ""}">
{cover(o["photo"], o["name"], badge, green=rec, height=190)}
<div style="padding:16px 12px 8px"><div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start">
<div><div class="od-h3">{esc(o["name"])}</div><div class="od-small" style="margin-top:4px">{esc(sub)}</div></div>
<div class="od-price">{rub(o["price"])}</div></div>{warn}{trade}</div></div>''')


def area_card(d: dict, rec: bool = False) -> str:
    """DestinationOption → карточка района."""
    score = f'{round((d.get("score") or 0) * 100)}%' if d.get("score") is not None else ""
    why = "".join(f"<li>{esc(w)}</li>" for w in d.get("why", [])[:3])
    air = f'{round(d["air_temp_c"])}°' if d.get("air_temp_c") is not None else "—"
    sea = f'{round(d["sea_temp_c"])}°' if d.get("sea_temp_c") is not None else "—"
    hrs = f'{str(d["travel_hours"]).replace(".", ",")} ч' if d.get("travel_hours") is not None else "—"
    direct = {True: "прямой", False: "с пересадкой", None: "—"}[d.get("direct")]
    return _h(f'''<div class="od-glass tight {"rec" if rec else ""}">
{cover(photo_for(d["name"]), d["name"], "Лучше всего подходит" if rec else "", score, green=rec)}
<div style="padding:16px 10px 8px">{chips(d.get("best_for", []))}
<div class="od-h3" style="font-size:22px;margin:4px 0 8px">{esc(d["name"])}</div>
<ul class="od-muted" style="padding-left:18px;font-size:14px;line-height:1.45;margin-bottom:12px">{why}</ul>
<div class="od-stats"><div><small>Воздух</small><b>{air}</b></div><div><small>Море</small><b>{sea}</b></div>
<div><small>В пути</small><b>{hrs}</b></div><div><small>Дорога</small><b>{direct}</b></div></div>
<div style="padding-top:12px"><div class="od-small">поездка целиком</div><div class="od-price">≈ {rub(d.get("est_total_price"))}</div></div></div></div>''')


def trip_card(t: dict) -> str:
    """Карточка «готового путешествия» на главной."""
    return _h(f'''<div class="od-glass tight">{cover(t["photo"], t["title"], "демо")}
<div style="padding:14px 10px 6px">{chips(t["tags"])}<div class="od-h3">{esc(t["title"])}</div>
<p class="od-muted" style="font-size:14px;margin:4px 0 8px">{esc(t["place"])}</p></div></div>''')


STEP_TITLES = {"plan": "Составил план", "node_start": "Разбираю запрос", "tool_call": "Ищу отели и тарифы",
               "tool_result": "Получил результаты поиска", "verify": "Сверяю стыковки по времени",
               "replan": "Пересобираю с новыми условиями", "budget": "Считаю бюджет",
               "clarify": "Нужны уточнения", "cart": "Готовлю корзину", "purchase": "Оформляю",
               "answer": "Собираю варианты"}


def timeline(events: list[dict], finished: bool) -> str:
    """events: [{type, node, payload}] из SSE. Последний шаг — в работе, пока finished=False."""
    rows, seen = [], []
    for ev in events:
        title = STEP_TITLES.get(ev["type"], ev["type"])
        detail = ev.get("node") or ""
        if ev["type"] == "tool_result" and ev.get("payload", {}).get("n") is not None:
            detail = f'нашёл вариантов: {ev["payload"]["n"]}'
        if ev["type"] == "replan" and ev.get("payload", {}).get("changes"):
            detail = "; ".join(map(str, ev["payload"]["changes"]))[:120]
        if seen and seen[-1][0] == title:
            seen[-1] = (title, detail or seen[-1][1])
        else:
            seen.append((title, detail))
    for i, (title, detail) in enumerate(seen):
        last = i == len(seen) - 1 and not finished
        ring = '<span class="od-ring run"></span>' if last else f'<span class="od-ring ok">{TICK}</span>'
        rows.append(f'<div class="od-tl">{ring}<div><div class="t">{esc(title)}</div><div class="s">{esc(detail)}</div></div></div>')
    pct = 100 if finished else min(90, 15 + 18 * len(seen))
    return _h(f'''<div class="od-glass" style="padding:26px 28px"><div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:14px">
<span class="od-h3" style="font-size:22px">{"Готово" if finished else "Агент работает"}</span><span class="od-small">{"" if finished else "обычно 10–15 секунд"}</span></div>
<div class="od-bar" style="margin-bottom:22px"><div style="width:{pct}%"></div></div>{"".join(rows)}</div>''')


def skeleton() -> str:
    def bar(w: str) -> str:
        return f'<div style="width:{w};height:14px;border-radius:8px;background:#E9E9EE;margin-top:10px"></div>'

    return _h(f'<div class="od-glass tight"><div style="height:130px;border-radius:18px;background:#E5E5EA"></div><div style="padding:6px 8px 12px">{bar("60%")}{bar("40%")}{bar("80%")}</div></div>')


def agent_bubble(text: str) -> str:
    return _h(f'''<div class="od-glass" style="border-radius:24px 24px 24px 6px;max-width:680px;padding:20px 24px">
<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px"><span style="width:30px;height:30px;border-radius:50%;background:#CDF4B0;color:#1E4A12;font-weight:700;font-size:13px;display:flex;align-items:center;justify-content:center">О!</span>
<b style="font-size:15px;color:#3A3A3C">Агент</b></div><div style="font-size:16px;line-height:1.5">{esc(text)}</div></div>''')


def user_bubble(text: str) -> str:
    return f'<div class="od-bubble-user">{esc(text)}</div>'


def leg(kind: str, title: str, sub: str, price: float | None) -> str:
    icons = {"flight": "✈", "train": "🚆", "local_train": "🚆", "bus": "🚌", "taxi": "🚕", "hotel": "🏨"}
    ic = f'<span style="width:44px;height:44px;border-radius:14px;background:#F2F2F7;display:flex;align-items:center;justify-content:center;font-size:20px;flex:0 0 auto">{icons.get(kind, "•")}</span>'
    return _h(f'<div class="od-leg">{ic}<div><div class="t">{esc(title)}</div><div class="s">{esc(sub)}</div></div><div class="p">{rub(price)}</div></div>')


def checks_list(checks: list[dict]) -> str:
    out = []
    for c in checks:
        ok = c.get("ok", True)
        col = "#CDF4B0" if ok else ("#D93025" if c.get("severity") == "error" else "#F5A623")
        out.append(f'<div class="od-li" style="font-size:14px;margin:0 0 8px"><span class="od-dot" style="background:{col};margin-top:5px"></span><span>{esc(c.get("message", ""))}</span></div>')
    return "".join(out)


def cart_summary(items: list[tuple[str, float]], total: float, budget: float | None = None) -> str:
    rows = "".join(row(k, rub(v)) for k, v in items)
    left = ""
    if budget:
        diff = budget - total
        left = f'<p class="od-small" style="margin:10px 0 0">{"В рамках бюджета, остаётся " + rub(diff) if diff >= 0 else "Выше бюджета на " + rub(-diff)}</p>'
    return _h(f'''<div class="od-glass"><div class="od-h3" style="margin-bottom:12px">Итого</div>
<div style="display:flex;flex-direction:column;gap:10px">{rows}</div>
<div style="height:1px;background:#E5E5EA;margin:14px 0"></div>{row("К оплате", rub(total), True)}{left}
<p class="od-small" style="margin:14px 0 0;font-size:12px;line-height:1.45">Оплата демонстрационная: деньги не списываются, бронь не создаётся.</p></div>''')


def diff_changes(changes: list[str]) -> str:
    if not changes:
        return _h('<div class="od-glass"><div class="od-h3" style="font-size:22px">Ничего не изменилось</div></div>')
    items = bullets([("#0D41D2" if i == 0 else "#CDF4B0", esc(c)) for i, c in enumerate(changes)])
    return _h(f'<div class="od-glass" style="padding:24px 28px"><div class="od-h3" style="font-size:22px;margin-bottom:14px">Что изменилось</div>{items}</div>')


def paid_hero(ref: str) -> str:
    icon = hero_icon(HERO_OK, "#1E4A12")
    return _h(f'''<div style="text-align:center;padding:24px 0 8px"><div style="display:inline-flex">{icon}</div>
<h1 class="od-h1" style="font-size:40px;margin:14px 0 8px">Поездка оформлена</h1><p class="od-muted" style="font-size:17px">Это демо: бронь не создавалась и деньги не списывались.</p>
<span style="display:inline-block;margin-top:14px;background:#fff;border:1px solid #E5E5EA;border-radius:16px;padding:12px 22px;font-size:15px;color:#6C6C70">Номер брони <b style="color:#14161F;letter-spacing:1px;margin-left:6px">{esc(ref)}</b></span></div>''')


def no_solution_hero(violations: list[str]) -> str:
    items = bullets([("#D93025", esc(v)) for v in violations])
    icon = hero_icon(HERO_NONE, "#86868B")
    return _h(f'''<div style="text-align:center;padding:24px 0 8px"><div style="display:inline-flex">{icon}</div>
<h1 class="od-h1" style="font-size:40px;margin:14px 0 8px">Под все условия вариантов нет</h1>
<p class="od-muted" style="font-size:17px">Я проверил районы и отели, и вместе эти условия не сходятся.</p></div>
<div class="od-glass" style="margin-top:16px"><div class="od-h3" style="font-size:17px;margin-bottom:12px">Что не сошлось</div>{items}</div>''')


def relax_card(r: dict) -> str:
    """Предложение агента из answer.plan.relax: {title, text, result, patch, hotel?}."""
    hotel = f'<div class="od-small" style="margin-top:6px">{esc(r["hotel"])}</div>' if isinstance(r.get("hotel"), str) else ""
    return _h(f'''<div class="od-glass" style="min-height:190px"><div class="od-h3">{esc(r.get("title", ""))}</div>
<p class="od-muted" style="font-size:14px;margin:8px 0 12px">{esc(r.get("text", ""))}</p><b>{esc(r.get("result", ""))}</b>{hotel}</div>''')
