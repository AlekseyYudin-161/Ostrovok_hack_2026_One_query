"""Экраны интерфейса. Каждая функция рисует один экран по st.session_state, навигация — go().

Состояние (session_state): screen, query, session_id, answer, prev_answer, request, events,
chosen (индекс варианта), chosen_area, booking_ref.
"""
from __future__ import annotations

import asyncio
import uuid

import streamlit as st

from ui import components as c
from ui import demo_data as demo
from ui.client import stream

DEFAULT_REQUEST = {"raw_text": demo.DEMO_QUERY, "destination": "Сочи", "budget_total": 50000, "nights": 7, "guests_adults": 1}


def md(html: str) -> None:
    st.markdown(html, unsafe_allow_html=True)


def go(screen: str) -> None:
    st.session_state.screen = screen
    st.rerun()


def S(key: str, default=None):
    return st.session_state.get(key, default)


def budget() -> float | None:
    return (S("request") or DEFAULT_REQUEST).get("budget_total")


# ---------- запуск агента ----------
async def _consume(text: str, patch: dict | None, placeholder) -> dict:
    events: list[dict] = []
    result: dict = {"answer": None, "request": None, "booking_ref": None}
    async for ev in stream(text, S("session_id"), patch):
        events.append({"type": ev.type, "node": ev.node, "payload": ev.payload})
        placeholder.markdown(c.timeline(events, False), unsafe_allow_html=True)
        if ev.type == "answer":
            result["answer"], result["request"] = ev.payload.get("answer"), ev.payload.get("request")
        elif ev.type == "purchase":
            result["booking_ref"] = ev.payload.get("booking_ref")
    placeholder.markdown(c.timeline(events, True), unsafe_allow_html=True)
    st.session_state.events = events
    return result


def run_agent(text: str, patch: dict | None = None) -> None:
    """Показывает экран «Агент работает», гонит SSE-события и переключает на нужный экран."""
    md(c.head())
    md(c.stepper(0))
    md(c.query_card(text))
    ph = st.empty()
    prev = S("answer")
    res = asyncio.run(_consume(text, patch, ph))
    ans = res["answer"] or {"mode": "no_solution", "text": "Агент не вернул ответ", "violations": []}
    st.session_state.prev_answer = prev                     # для «было → стало» после правки панелью или текстом
    st.session_state.answer, st.session_state.request = ans, res["request"] or S("request")
    st.session_state.chosen = None
    if res["booking_ref"]:
        st.session_state.booking_ref = res["booking_ref"]
    mode = ans.get("mode")
    if mode == "clarify":
        go("clarify")
    elif mode == "no_solution":
        go("none")
    elif mode == "purchased":
        go("paid")
    elif ans.get("changes") and prev:
        go("diff")
    elif mode == "confirm_action" and ans.get("itineraries"):  # агент собрал варианты и ждёт «да» — сначала показываем их
        go("results")
    elif mode == "confirm_action":
        go("cart")
    elif ans.get("changes"):
        go("diff")
    elif ans.get("destinations") and not S("chosen_area"):
        go("areas")
    else:
        go("results")


def submit(text: str, patch: dict | None = None) -> None:
    st.session_state.query = text
    st.session_state.screen = "working"
    st.session_state.pending = (text, patch)
    st.rerun()


def new_session() -> None:
    for k in ("answer", "prev_answer", "request", "events", "chosen", "chosen_area", "booking_ref", "query"):
        st.session_state.pop(k, None)
    st.session_state.session_id = uuid.uuid4().hex[:12]
    go("start")


def top(active: int) -> None:
    md(c.head())
    if st.button("← Новый запрос", key=f"new-{active}"):
        new_session()
    md(c.stepper(active))


# ---------- 1. старт ----------
def start() -> None:
    md(c.head())
    md('<div style="text-align:center;padding:44px 0 8px"><span class="od-chip green" style="margin:0">AI-агент для путешествий</span>'
       '<h1 class="od-h1" style="margin-top:18px">Один запрос,<br>и вся поездка <span class="od-mark">готова</span></h1>'
       '<p class="od-muted" style="font-size:18px;max-width:620px;margin:18px auto 0">Напишите, куда и когда хотите поехать. Агент подберёт район, отель, дорогу и трансфер и проверит, что всё стыкуется по времени.</p></div>')
    cols = st.columns(len(demo.EXAMPLES))
    for col, ex in zip(cols, demo.EXAMPLES, strict=True):
        if col.button(ex, key=f"ex-{ex}", use_container_width=True):
            submit(ex)
    md('<div class="od-h2" style="margin-top:28px">Готовые <span class="od-blue">путешествия</span></div>'
       '<p class="od-muted">Нажмите на любое, и агент соберёт его под ваши даты и бюджет.</p>')
    cols = st.columns(2)
    for i, trip in enumerate(demo.TRIPS):
        with cols[i % 2]:
            md(c.trip_card(trip))
            if st.button("Собрать поездку →", key=f"trip-{i}", use_container_width=True):
                submit(trip["query"])
    md('<div class="od-h2" style="margin-top:28px">Как это <span class="od-blue">работает</span></div>')
    cols = st.columns(3)
    for col, (n, t, d) in zip(cols, [("01", "Напишите, как вам удобно", "Свободным текстом: куда, на сколько, с кем и сколько готовы потратить."),
                                      ("02", "Агент уточнит или додумает", "Если чего-то не хватает, задаст вопрос или сделает допущение, которое вы поправите одним кликом."),
                                      ("03", "Получите поездку целиком", "Район, отель, дорога и трансфер в одном варианте. Стыковки по времени проверены.")], strict=True):
        col.markdown(c._h(f'<div class="od-glass" style="height:100%"><div style="font-size:48px;font-weight:700;letter-spacing:-2px;color:#D1D1D6">{n}</div>'
                          f'<div class="od-h3" style="margin:8px 0">{t}</div><p class="od-muted">{d}</p></div>'), unsafe_allow_html=True)
    md('<p class="od-small" style="text-align:center;margin-top:24px">Все данные в демо вымышленные: отели, цены и расписания не настоящие.</p>')


# ---------- 2. уточнение ----------
def clarify() -> None:
    top(0)
    ans = S("answer") or demo.ANSWER_CLARIFY
    md(c.user_bubble(S("query") or "Хочу на море, недорого, на неделю"))
    md(c.agent_bubble(ans.get("text") or "Нужно уточнить пару деталей."))
    suggested = (ans.get("pending_action") or {}).get("suggested_area")
    if suggested:                                           # «работаем только по Сочи» + похожий район (ADR-019)
        cols = st.columns(3)
        with cols[0].container(key="main-suggest-yes"):
            if st.button(f"Да, показать {suggested}", key="suggest-yes", use_container_width=True):
                submit("да")
        if cols[1].button("Нет", key="suggest-no", use_container_width=True):
            submit("нет")
    for q in ans.get("questions", []):
        md(f'<div style="font-size:17px;font-weight:600;margin-top:8px">{c.esc(q)}</div>')
        options = demo.QUICK_REPLIES.get(q, [])
        if options:
            cols = st.columns(len(options) + 2)
            for col, opt in zip(cols, options, strict=False):
                if col.button(opt, key=f"qr-{q}-{opt}"):
                    submit(f"{S('query') or ''}. {q} {opt}")
    if ans.get("assumptions"):
        chips = "".join(c.chip(f'{a["field"]}: {a["value"]}', "" if a.get("confirmed") else "dash") for a in ans["assumptions"])
        md(f'<div class="od-glass" style="margin-top:12px"><div class="od-h3" style="font-size:17px;margin-bottom:12px">Пока предположил</div>{chips}'
           '<p class="od-small">Пунктир — допущение агента. Подтверждённое станет сплошным.</p></div>')


# ---------- 3. выбор района ----------
def areas() -> None:
    top(1)
    ans = S("answer")
    md(c.query_card(S("query") or demo.DEMO_QUERY))
    md('<div class="od-h2">Куда в Большом Сочи</div><p class="od-muted">Агент сравнил районы по вашему запросу. Проценты показывают, насколько район подходит. Все цифры демо.</p>')
    dests = ans.get("destinations", [])
    best = max(dests, key=lambda d: d.get("score") or 0, default=None)
    for i in range(0, len(dests), 2):
        cols = st.columns(2)
        for col, d in zip(cols, dests[i:i + 2], strict=False):
            with col:
                md(c.area_card(d, rec=d is best))
                with st.container(key=f"main-area-{d['destination_id']}" if d is best else f"area-{d['destination_id']}"):
                    if st.button("Выбрать район", key=f"pick-{d['destination_id']}", use_container_width=True):
                        st.session_state.chosen_area = d["destination_id"]
                        go("results")


# ---------- 4. результаты ----------
def params_panel() -> None:
    req = S("request") or DEFAULT_REQUEST
    with st.expander("Параметры поездки", expanded=False):
        cols = st.columns(3)
        b = cols[0].number_input("Бюджет, ₽", 5000, 1_000_000, int(req.get("budget_total") or 50000), 1000)
        n = cols[1].number_input("Ночей", 1, 30, int(req.get("nights") or 7))
        g = cols[2].number_input("Гостей", 1, 10, int(req.get("guests_adults") or 1))
        if st.button("Пересчитать", type="primary"):
            shown = {"budget_total": int(req.get("budget_total") or 50000), "nights": int(req.get("nights") or 7),
                     "guests_adults": int(req.get("guests_adults") or 1)}
            patch = {k: v for k, v in {"budget_total": b, "nights": n, "guests_adults": g}.items() if v != shown[k]}
            if patch:
                submit("Пересчитай с новыми параметрами", patch)


def results() -> None:
    top(2)
    ans = S("answer")
    md(c.query_card(S("query") or demo.DEMO_QUERY))
    opts = c.options_from_answer(ans, budget())
    md(f'<p class="od-small" style="margin-left:8px">✅ {c.esc(ans.get("text") or "Готово")}</p>')
    params_panel()
    if not opts:
        md('<div class="od-glass">Вариантов нет. Попробуйте изменить параметры.</div>')
        return
    md(f'<div class="od-h2">{len(opts)} {"вариант" if len(opts) == 1 else "варианта" if len(opts) < 5 else "вариантов"} поездки</div>')
    for i, o in enumerate(opts):
        md(c.hotel_card(o, rec=i == 0, budget=budget()))
        if o.get("checks"):
            md(f'<div class="od-glass" style="margin-top:-8px;padding:14px 22px">{c.checks_list(o["checks"])}</div>')
        with st.container(key=f"main-pick-{i}" if i == 0 else f"pick-{i}"):
            if st.button("Выбрать этот вариант", key=f"choose-{i}", use_container_width=True):
                st.session_state.chosen = i
                go("cart")


# ---------- 5. было → стало ----------
def diff() -> None:
    top(2)
    ans, prev = S("answer"), S("prev_answer") or demo.ANSWER_PLAN
    md(c.query_card(S("query") or demo.DEMO_QUERY))
    md(c.diff_changes(ans.get("changes", [])))
    cols = st.columns(2)
    for col, title, a in ((cols[0], "Было", prev), (cols[1], "Стало", ans)):
        with col:
            md(f'<div class="od-h3" style="margin:8px 0">{title}</div>')
            for i, o in enumerate(c.options_from_answer(a, budget())):
                md(c.hotel_card(o, rec=i == 0, budget=budget()))
    if st.button("Смотреть варианты", type="primary"):
        go("results")


# ---------- 6. корзина ----------
def cart() -> None:
    top(3)
    ans = S("answer")
    opts = c.options_from_answer(ans, budget())
    idx = S("chosen") or 0
    o = opts[min(idx, len(opts) - 1)] if opts else None
    if not o:
        go("results")
        return
    it = o["raw"] if "hotel" in o["raw"] else {}
    legs, items = [], []
    if it:
        out, inb = it.get("outbound"), it.get("inbound")
        tr = it.get("transfers", [])
        if out:
            legs.append(c.leg(out["kind"], f'{out["from_city"]} → {out["to_city"]}', f'{c.day(out["depart"])} · {out["depart"][11:16]} → {out["arrive"][11:16]}', out["price_total"]))
        if tr:
            legs.append(c.leg("taxi", f'Трансфер: {tr[0]["from_place"]} → {tr[0]["to_place"]}', f'{tr[0]["minutes"]} минут', tr[0]["price_total"]))
        legs.append(c.leg("hotel", f'«{o["name"]}» · {o["area"]}', f'{o.get("meal", "")} · {o.get("cancel", "")}', it["hotel"]["price_total"]))
        if len(tr) > 1:
            legs.append(c.leg("taxi", f'Трансфер: {tr[1]["from_place"]} → {tr[1]["to_place"]}', f'{tr[1]["minutes"]} минут', tr[1]["price_total"]))
        if inb:
            legs.append(c.leg(inb["kind"], f'{inb["from_city"]} → {inb["to_city"]}', f'{c.day(inb["depart"])} · {inb["depart"][11:16]} → {inb["arrive"][11:16]}', inb["price_total"]))
        items = [("Перелёты", sum(x["price_total"] for x in (out, inb) if x)), ("Отель", it["hotel"]["price_total"]), ("Трансферы", sum(t["price_total"] for t in tr))]
    else:
        legs.append(c.leg("hotel", f'«{o["name"]}» · {o["area"]}', f'{o.get("meal", "")} · {o.get("cancel", "")}', o["price"]))
        items = [("Отель", o["price"])]
    left, right = st.columns([3, 2])
    with left:
        md(c._h(f'<div class="od-glass" style="padding:26px 28px"><div style="display:flex;justify-content:space-between;align-items:center"><div class="od-h3" style="font-size:24px">Ваша поездка</div>{c.chip("Ждёт подтверждения", "blue")}</div>{"".join(legs)}<div style="margin-top:16px">{c.checks_list(o["checks"])}</div></div>'))
    with right:
        md(c.cart_summary(items, o["price"], budget(), hotel=o.get("hotel_price")))
        with st.container(key="main-pay"):
            if st.button("Подтвердить и оплатить", use_container_width=True):
                if ans.get("mode") == "confirm_action":     # агент ждёт явного «да» — покупку делает только он
                    submit("да")
                st.session_state.booking_ref = "ODN-" + uuid.uuid4().hex[:6].upper()  # демо-экраны без агента
                go("paid")
        if ans.get("mode") == "confirm_action" and idx:
            md('<p class="od-small">Демо-оплата агента пока оформляет рекомендованный вариант (первый).</p>')
        if st.button("Вернуться к вариантам", use_container_width=True):
            go("results")


# ---------- 7. оплачено ----------
def paid() -> None:
    md(c.head())
    md(c.paid_hero(S("booking_ref") or "ODN-DEMO00"))
    opts = c.options_from_answer(S("answer") or {}, budget())
    if opts:
        o = opts[min(S("chosen") or 0, len(opts) - 1)]
        md(c.hotel_card(o, rec=True, budget=None))
    if st.button("Начать новый запрос", type="primary"):
        new_session()


# ---------- 8. нет решения ----------
def none() -> None:
    md(c.head())
    ans = S("answer") or demo.ANSWER_NONE
    md(c.no_solution_hero(ans.get("violations") or ([ans["text"]] if ans.get("text") else [])))
    md('<div class="od-h2" style="font-size:24px;margin-top:8px">Что можно поменять</div>')
    cols = st.columns(len(demo.RELAX))
    for col, r in zip(cols, demo.RELAX, strict=True):
        with col:
            md(c._h(f'<div class="od-glass" style="min-height:190px"><div class="od-h3">{r["title"]}</div><p class="od-muted" style="font-size:14px;margin:8px 0 12px">{r["text"]}</p><b>{r["result"]}</b></div>'))
            with st.container(key=f"main-relax-{r['title']}" if r.get("green") else f"relax-{r['title']}"):
                if st.button("Применить", key=f"relax-btn-{r['title']}", use_container_width=True):
                    submit("Подбери с изменёнными условиями: " + r["title"].lower(), r["patch"])


SCREENS = {"start": start, "clarify": clarify, "areas": areas, "results": results, "diff": diff, "cart": cart, "paid": paid, "none": none}
CHAT_SCREENS = {"start", "clarify", "areas", "results", "diff", "none"}
