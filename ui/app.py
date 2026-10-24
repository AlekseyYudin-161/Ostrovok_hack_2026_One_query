"""Минимальный Streamlit-чат поверх ui/client.py (владелец — дизайнер, это лишь заглушка)."""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

import streamlit as st

# Streamlit кладёт в sys.path папку скрипта (ui/), а не корень проекта — добавляем корень,
# чтобы работали импорты ui.* и harness.* при любом способе запуска.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.client import stream

st.set_page_config(page_title="О!дин запрос", page_icon="🧳")
st.title("О!дин запрос")
st.session_state.setdefault("session_id", uuid.uuid4().hex[:12])
st.session_state.setdefault("history", [])

for role, text in st.session_state.history:
    st.chat_message(role).write(text)


async def run(prompt: str) -> str:
    final = "…"
    with st.status("Агент работает", expanded=False) as status:
        async for ev in stream(prompt, st.session_state.session_id):
            status.write(f"{ev.type} · {ev.node or ''}")
            if ev.type == "answer":
                ans = ev.payload["answer"]
                lines = [ans["text"]] + [f"- {h['name']} — {h['price_total']} ₽" for h in ans.get("candidates", [])]
                final = "\n".join(lines + ans.get("questions", []))
        status.update(label="Готово", state="complete")
    return final


if prompt := st.chat_input("Куда хотите поехать?"):
    st.chat_message("user").write(prompt)
    answer = asyncio.run(run(prompt))
    st.chat_message("assistant").write(answer)
    st.session_state.history += [("user", prompt), ("assistant", answer)]
