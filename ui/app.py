"""Streamlit-интерфейс «О!дин запрос» (владелец — дизайнер). Макеты: docs/ui/README.md.

Запуск: `make ui` (фейковый агент) или `make ui-api` (через FastAPI).
Любой экран можно открыть с демо-данными: `?screen=areas` (см. ui/demo_data.py).
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import streamlit as st

# Streamlit кладёт в sys.path папку скрипта (ui/), а не корень проекта — добавляем корень,
# чтобы работали импорты ui.* и harness.* при любом способе запуска.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui import demo_data, screens, theme

st.set_page_config(page_title="О!дин запрос", page_icon="🧳", layout="centered")
theme.inject()

ss = st.session_state
ss.setdefault("session_id", uuid.uuid4().hex[:12])
ss.setdefault("screen", "start")

# ?screen=<имя> — открыть экран с демо-данными (один раз за сессию)
demo_screen = st.query_params.get("screen")
if demo_screen in demo_data.DEMO_STATES and ss.get("_demo") != demo_screen:
    ss.update(demo_data.DEMO_STATES[demo_screen])
    ss.screen = "working" if demo_screen == "working" else demo_screen
    ss._demo = demo_screen

if ss.screen == "working":
    if "pending" in ss:
        text, patch = ss.pop("pending")
        screens.run_agent(text, patch)
    else:  # демо экрана «Агент работает»
        from ui import components as c
        screens.md(c.head())
        screens.md(c.stepper(0))
        screens.md(c.query_card(demo_data.DEMO_QUERY))
        screens.md(c.timeline(ss.get("events", []), False))
else:
    screens.SCREENS[ss.screen]()

if ss.screen in screens.CHAT_SCREENS and (prompt := st.chat_input("Куда хотите поехать?")):
    screens.submit(prompt)
