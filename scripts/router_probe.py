"""Проверка роутера на нашей задаче: какие варианты запроса работают, сколько стоят и сколько ждать.

Запуск (ключ — только в окружении, не в git):
    export LLM_BASE_URL=https://routerai.ru/api/v1 LLM_API_KEY=... LLM_MODEL=qwen/qwen3.6-35b-a3b
    uv run python scripts/router_probe.py            # все варианты по 1 разу
    uv run python scripts/router_probe.py --runs 3   # по 3 раза, медиана latency

Каждый вариант = один и тот же запрос разбора (NL → JSON) с разным extra_body.
Синтаксис provider/reasoning взят по аналогии с OpenRouter и ПРОВЕРЯЕТСЯ здесь же:
если роутер параметр не понял, это видно по ошибке, по полю provider в ответе или по reasoning-токенам.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError


class Parsed(BaseModel):
    area: str | None
    budget_rub: int | None
    nights: int | None
    with_kids: bool


AREAS = ["Сочи-центр", "Адлер", "Хоста", "Сириус", "Красная Поляна"]
SYSTEM = ("Разбери запрос путешественника про Большой Сочи. Верни только JSON. "
          f"area — строго одно из {AREAS} или null; budget_rub — рубли за весь срок или null.")
USER = "Хотим с ребёнком в Адлер на неделю в ноябре, до 60 тыс за всё"
SCHEMA = {"type": "json_schema", "json_schema": {"name": "parsed", "strict": True,
                                                 "schema": Parsed.model_json_schema()}}

VARIANTS: dict[str, dict] = {
    "default (роутер выбирает сам)": {},
    "AkashML fp8": {"provider": {"order": ["AkashML"], "allow_fallbacks": False}},
    "DeepInfra fp8": {"provider": {"order": ["DeepInfra"], "allow_fallbacks": False}},
    "Yandex (ru)": {"provider": {"order": ["Yandex"], "allow_fallbacks": False}},
    "country=ru": {"provider": {"country": "ru"}},
    "fp8 + require_parameters": {"provider": {"quantizations": ["fp8"], "require_parameters": True}},
    "reasoning off: enabled=false": {"reasoning": {"enabled": False}},
    "reasoning off: effort=none": {"reasoning": {"effort": "none"}},
    "reasoning exclude": {"reasoning": {"exclude": True}},
    "qwen: enable_thinking=false": {"chat_template_kwargs": {"enable_thinking": False}},
}


async def one(client: AsyncOpenAI, model: str, extra: dict, rf: dict) -> dict:
    t0 = time.perf_counter()
    raw = await client.chat.completions.with_raw_response.create(
        model=model, temperature=0, response_format=rf, extra_body=extra or None,
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER}])
    dt = time.perf_counter() - t0
    resp = raw.parse()
    body = json.loads(raw.text)
    msg = body["choices"][0]["message"]
    usage = body.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    content = msg.get("content") or ""
    try:
        valid = Parsed.model_validate_json(content).model_dump()
    except ValidationError:
        valid = None
    return {"sec": dt, "provider": body.get("provider"), "model": resp.model,
            "in": usage.get("prompt_tokens"), "out": usage.get("completion_tokens"),
            "reasoning_tokens": details.get("reasoning_tokens"),
            "has_reasoning_text": bool(msg.get("reasoning") or msg.get("reasoning_content")),
            "cost": usage.get("cost"), "valid": valid, "content": content[:120]}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--only", help="подстрока имени варианта")
    args = ap.parse_args()
    client = AsyncOpenAI(base_url=os.environ["LLM_BASE_URL"], api_key=os.environ["LLM_API_KEY"])
    model = os.environ.get("LLM_MODEL", "qwen/qwen3.6-35b-a3b")
    for name, extra in VARIANTS.items():
        if args.only and args.only not in name:
            continue
        for rf_name, rf in (("json_schema", SCHEMA), ("json_object", {"type": "json_object"})):
            secs, last = [], None
            try:
                for _ in range(args.runs):
                    last = await one(client, model, extra, rf)
                    secs.append(last["sec"])
            except Exception as e:  # noqa: BLE001 — нам нужен текст любой ошибки роутера
                print(f"✗ {name:32} {rf_name:11} ОШИБКА: {str(e)[:160]}")
                continue
            ok = "✓" if last["valid"] else "✗ JSON"
            print(f"{ok} {name:32} {rf_name:11} p50={statistics.median(secs):5.1f}s "
                  f"provider={last['provider']} in={last['in']} out={last['out']} "
                  f"reasoning_tok={last['reasoning_tokens']} reasoning_text={last['has_reasoning_text']} "
                  f"cost={last['cost']} → {last['valid'] or last['content']}")


if __name__ == "__main__":
    asyncio.run(main())
