"""Страница «Качество агента» (D6): те же метрики, что в отчёте `make eval`, на языке пользователя.

Источник — evals/reports/eval_report.json (путь можно задать переменной EVAL_REPORT). Код метрик и подписи —
общие с отчётом (evals/metrics.py), поэтому цифры на странице и в README совпадают.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.metrics import LABELS, fmt

REPORT = Path(os.getenv("EVAL_REPORT", str(ROOT / "evals" / "reports" / "eval_report.json")))
# Главные цифры — метрики оргов: что чувствует пользователь
HEADLINE = ("completion_rate", "clarifications_avg", "ttfr_p50_ms", "connection_error_rate")
QUALITY = ("mode_accuracy", "hard_pass_macro", "constraint_retention", "groundedness", "injection_asr",
           "edge_violations")
FILE_NAMES = {"dev": "Обычные запросы", "connections": "Ловушки дороги", "security": "Атаки на агента",
              "seed_from_chat": "Запросы из чата трека"}
MODE_RU = {"plan": "план", "confirm_action": "план + «оформить?»", "clarify": "вопрос", "no_solution": "нет вариантов",
           "purchased": "оплачено (демо)", "error": "ошибка"}

st.set_page_config(page_title="Качество агента", page_icon="📊", layout="wide")
st.title("Качество агента")

if not REPORT.exists():
    st.info("Отчёта пока нет. Запустите `make eval` (или `make eval-offline` без модели) — он появится здесь.")
    st.stop()

report = json.loads(REPORT.read_text(encoding="utf-8"))
meta, summary = report["meta"], report["summary"]
st.caption(f"Прогон {meta['finished_at'][:16].replace('T', ' ')} UTC · кейсов: {summary['cases']} · "
           f"модель: {meta.get('model') or meta['llm']} · все данные — демо")
if summary.get("cost_rub"):
    st.caption(f"Стоимость прогона: {summary['cost_rub']:.2f} ₽ · на один запрос: "
               f"{fmt('cost_per_case_rub', summary.get('cost_per_case_rub'))} ₽ · обращений к модели на запрос: "
               f"{fmt('llm_calls_avg', summary.get('llm_calls_avg'))}")


def tiles(keys: tuple[str, ...], data: dict) -> None:
    for col, key in zip(st.columns(len(keys)), keys, strict=True):
        col.metric(LABELS[key][0], fmt(key, data.get(key)))


st.subheader("Что видит путешественник")
tiles(HEADLINE, summary)
st.subheader("Почему агенту можно доверять")
tiles(QUALITY[:3], summary)
tiles(QUALITY[3:], summary)

st.subheader("По наборам запросов")
rows = [{"Набор": FILE_NAMES.get(name, name), "Запросов": v["cases"],
         "До брони": fmt("completion_rate", v["completion_rate"]),
         "Верный шаг": fmt("mode_accuracy", v["mode_accuracy"]),
         "Условия": fmt("hard_pass_macro", v["hard_pass_macro"]),
         "Ошибки стыковки": fmt("connection_error_rate", v["connection_error_rate"]),
         "Атак сработало": fmt("injection_asr", v["injection_asr"])}
        for name, v in report["by_file"].items()]
st.dataframe(rows, hide_index=True)

with st.expander("По срезам (дети, животные, пересборка, вне локации…)"):
    st.dataframe([{"Срез": name, "Запросов": v["cases"], "До брони": fmt("completion_rate", v["completion_rate"]),
                   "Верный шаг": fmt("mode_accuracy", v["mode_accuracy"]),
                   "Условия": fmt("hard_pass_macro", v["hard_pass_macro"])}
                  for name, v in report["by_slice"].items()], hide_index=True)

failed = [c for c in report["cases"] if c["reasons"]]
st.subheader(f"Что не прошло: {len(failed)} из {len(report['cases'])}")
for case in failed:
    with st.expander(f"{case['case_id']} — {case['query'][:80]}"):
        for reason in case["reasons"]:
            st.write(f"- {reason}")
        if case.get("trace_url"):
            st.markdown(f"[Открыть трейс в Langfuse]({case['trace_url']})")
        for turn in case["turns"]:
            st.caption(f"{turn['kind']}: «{turn['text'][:120]}» → {MODE_RU.get(turn['mode'], turn['mode'])}, "
                       f"{turn['ms']} мс")
