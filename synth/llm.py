"""GLM-клиент для синтетики: JSON-ответы, дисковый кэш, ретраи с валидацией.

Ключ: SYNTH_LLM_API_KEY / SYNTH_LLM_BASE_URL, иначе — из ~/.config/opencode/opencode.json (провайдер sglang).
Повторный прогон ноутбука читает ответы из synth/raw/cache и не ходит в сеть.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

log = logging.getLogger(__name__)
CACHE = Path(__file__).resolve().parent / "raw" / "cache"


def _credentials() -> tuple[str, str]:
    key, url = os.getenv("SYNTH_LLM_API_KEY"), os.getenv("SYNTH_LLM_BASE_URL")
    if key and url:
        return url, key
    cfg = Path("~/.config/opencode/opencode.json").expanduser()
    opts = json.loads(cfg.read_text())["provider"]["sglang"]["options"]
    return url or opts["baseURL"], key or opts["apiKey"]


def _parse(text: str) -> Any:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1)
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=0)
    return json.loads(text[start:])


class GLM:
    def __init__(self, model: str = "glm-5.3-no-collect", concurrency: int = 8, reasoning_effort: str = "low",
                 offline: bool = False) -> None:
        self.model, self.reasoning_effort, self.offline = model, reasoning_effort, offline
        self.sem = asyncio.Semaphore(concurrency)
        self._client: AsyncOpenAI | None = None
        self.stats = {"calls": 0, "cache_hits": 0, "retries": 0, "tokens_out": 0}
        CACHE.mkdir(parents=True, exist_ok=True)

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            url, key = _credentials()
            self._client = AsyncOpenAI(base_url=url, api_key=key, timeout=180)
        return self._client

    async def json(self, system: str, user: str, *, tag: str, temperature: float = 0.9,
                   max_tokens: int = 12000, validate: Callable[[Any], Any] | None = None,
                   retries: int = 3) -> Any:
        """Вернуть распарсенный JSON (и прошедший validate). Кэш — по (tag, промпт, попытка)."""
        last_err: Exception | None = None
        for attempt in range(retries):
            key = hashlib.sha256(json.dumps([self.model, system, user, temperature, attempt],
                                            ensure_ascii=False).encode()).hexdigest()[:16]
            path = CACHE / f"{tag}_{key}.json"
            if path.exists():
                self.stats["cache_hits"] += 1
                text = json.loads(path.read_text(encoding="utf-8"))["response"]
            elif self.offline:
                raise RuntimeError(f"offline: нет кэша для {tag}")
            else:
                text = await self._call(system, user, temperature, max_tokens)
                path.write_text(json.dumps({"tag": tag, "system": system, "user": user, "response": text},
                                           ensure_ascii=False, indent=1), encoding="utf-8")
            try:
                data = _parse(text)
                return validate(data) if validate else data
            except Exception as e:  # noqa: BLE001 — любая ошибка формата → следующая попытка
                last_err = e
                self.stats["retries"] += 1
                log.warning("%s: попытка %d не прошла: %s", tag, attempt, str(e)[:200])
        raise RuntimeError(f"{tag}: не удалось получить валидный JSON") from last_err

    async def _call(self, system: str, user: str, temperature: float, max_tokens: int) -> str:
        async with self.sem:
            for backoff in (2, 5, 15, 30):
                try:
                    r = await self.client.chat.completions.create(
                        model=self.model, temperature=temperature, max_tokens=max_tokens,
                        reasoning_effort=self.reasoning_effort, response_format={"type": "json_object"},
                        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
                    self.stats["calls"] += 1
                    self.stats["tokens_out"] += r.usage.completion_tokens if r.usage else 0
                    return r.choices[0].message.content or ""
                except Exception as e:  # noqa: BLE001 — сеть/rate limit
                    log.warning("GLM ошибка (%s), повтор через %ss", str(e)[:120], backoff)
                    await asyncio.sleep(backoff)
            raise RuntimeError("GLM недоступен")
