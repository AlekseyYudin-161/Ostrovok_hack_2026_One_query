"""Быстрая проверка разбора запросов (задача L5): прогон EvalCase через GraphAgent, сверка режима ответа.

Запуск (из корня репо):
    uv run --env-file .env python scripts/understand_eval.py                     # все evals/golden/*.jsonl
    uv run --env-file .env python scripts/understand_eval.py evals/golden/dev.jsonl
Без .env (или с LLM_PROVIDER=fake) разбор идёт по правилам — так видно, что даёт именно модель.

Считает Mode Accuracy (ожидаемый режим = полученный) и печатает разобранные поля для глазной проверки.
«plan» в кейсе засчитывается и для «confirm_action»: с шага 2 агент после плана спрашивает «оформить?».
Это не полный eval (его делает DA): без уточнений, пересборки и симулятора пользователя.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # корень репо: импорты harness.*

from harness.agent_graph import GraphAgent
from harness.contracts import EvalCase

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
SAME = {"plan": {"plan", "confirm_action"}}


def load_cases(paths: list[str]) -> list[EvalCase]:
    files = [Path(p) for p in paths] or sorted(Path("evals/golden").glob("*.jsonl"))
    cases = []
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                cases.append(EvalCase.model_validate_json(line))
    return cases


async def main() -> None:
    cases = load_cases(sys.argv[1:])
    if not cases:
        sys.exit("Нет кейсов: положите *.jsonl в evals/golden/ или передайте путь.")
    agent = GraphAgent()
    print(f"LLM: {type(agent.llm).__name__}, кейсов: {len(cases)}\n")
    ok = 0
    for i, case in enumerate(cases):
        t0 = time.perf_counter()
        run = await agent.run(case.query, f"eval-{i}-{case.case_id}")
        sec = time.perf_counter() - t0
        got, want = run.answer.mode.value, case.expected_mode.value
        hit = got in SAME.get(want, {want})
        ok += hit
        r = run.request
        print(f"{'✓' if hit else '✗'} {case.case_id:24} ждали={want:13} получили={got:15} {sec:4.1f}s | "
              f"район={r.destination} бюджет={r.budget_total} ночей={r.nights} месяц={r.month} "
              f"дети={'с детьми' in r.preferences} животные={r.pets}")
    print(f"\nMode Accuracy: {ok}/{len(cases)} = {ok / len(cases):.0%}")


if __name__ == "__main__":
    asyncio.run(main())
