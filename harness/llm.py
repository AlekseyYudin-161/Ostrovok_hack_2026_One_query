"""LLM-клиент: один OpenAI-совместимый интерфейс для роутера (kodikrouter, запасной routerai), Ollama/vLLM и фейка.

Переключение — только переменными окружения (код агента не меняется):
    LLM_PROVIDER=fake | openai_compat
    LLM_BASE_URL=https://api.kodikrouter.ru/v1   (запасной routerai: https://routerai.ru/api/v1;
                                                  Ollama: http://localhost:11434/v1, vLLM: http://host:8000/v1)
    LLM_API_KEY=...                              (Ollama: любое непустое значение)
    LLM_MODEL=qwen/qwen3.6-35b-a3b
    LLM_EXTRA_BODY='{"reasoning": {"enabled": false}}'  (для Qwen3.6 обязательно, проба 24.10:
        с reasoning — ×10 выходных токенов, 5–6 с и вырожденный JSON по строгой схеме;
        `reasoning.exclude` и `chat_template_kwargs.enable_thinking` на kodikrouter не выключают)

Структурированный вывод: сначала response_format=json_schema (strict), если провайдер/модель
не поддерживает — откат на json_object; результат всегда валидируется Pydantic с одним повтором.
"""

from __future__ import annotations

import json
import logging
import os
from contextvars import ContextVar
from dataclasses import dataclass

import httpx
from openai import AsyncOpenAI, BadRequestError
from pydantic import BaseModel, ValidationError

from harness.contracts import LLMClient, LLMResult
from harness.fakes import FakeLLM

log = logging.getLogger(__name__)

# Цена модели, ₽ за 1 млн токенов (прайс kodikrouter для qwen/qwen3.6-35b-a3b на 27.10). Меняется через env.
# Оценка: kodikrouter списывает цену провайдера в $ × курс × 1.1, поэтому расходится с прайсом на 1–3%.
PRICE_IN_RUB_PER_M = 9.19
PRICE_OUT_RUB_PER_M = 87.29


@dataclass
class Usage:
    """Токены и стоимость вызовов модели за один запрос пользователя (один ход диалога)."""
    tokens_in: int = 0
    tokens_out: int = 0
    calls: int = 0

    def add(self, tokens_in: int, tokens_out: int) -> None:
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.calls += 1

    @property
    def cost_rub(self) -> float:
        p_in = float(os.getenv("LLM_PRICE_IN_RUB_PER_M", PRICE_IN_RUB_PER_M))
        p_out = float(os.getenv("LLM_PRICE_OUT_RUB_PER_M", PRICE_OUT_RUB_PER_M))
        return round((self.tokens_in * p_in + self.tokens_out * p_out) / 1_000_000, 4)

    def as_dict(self) -> dict:
        return {"tokens_in": self.tokens_in, "tokens_out": self.tokens_out, "llm_calls": self.calls,
                "cost_rub": self.cost_rub}


# Счётчик текущего хода. Хранится в контексте задачи asyncio: параллельные запросы API не смешиваются.
_USAGE: ContextVar[Usage | None] = ContextVar("llm_usage", default=None)


def start_usage() -> Usage:
    """Начать подсчёт для нового хода; все вызовы `complete()` в этом контексте добавятся в возвращённый объект."""
    usage = Usage()
    _USAGE.set(usage)
    return usage


def _make_openai(base_url: str, api_key: str, http_client: httpx.AsyncClient | None) -> AsyncOpenAI:
    """Если заданы ключи Langfuse и пакет установлен — клиент с трассировкой, иначе обычный."""
    if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_TRACING_ENABLED", "true") != "false":
        try:
            from langfuse.openai import AsyncOpenAI as TracedAsyncOpenAI  # type: ignore[import-not-found]

            return TracedAsyncOpenAI(base_url=base_url, api_key=api_key, http_client=http_client)
        except ImportError:
            log.warning("langfuse не установлен — работаем без трассировки LLM")
    return AsyncOpenAI(base_url=base_url, api_key=api_key, http_client=http_client)


class OpenAICompatLLM:
    def __init__(self, base_url: str, api_key: str, model: str, temperature: float = 0.0,
                 http_client: httpx.AsyncClient | None = None, extra_body: dict | None = None) -> None:
        self.model = model
        self.extra_body = extra_body  # напр. {"provider": {"country": "ru"}} — режим «данные в РФ» у routerai
        self.temperature = temperature
        self._client = _make_openai(base_url, api_key, http_client)
        self._json_schema_ok = True  # выключается после первого отказа провайдера

    async def complete(self, messages: list[dict], tools: list[dict] | None = None,
                       schema: dict | None = None) -> LLMResult:
        kwargs: dict = {"model": self.model, "messages": messages, "temperature": self.temperature}
        if tools:
            kwargs["tools"] = tools
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
        if schema is not None:
            if self._json_schema_ok:
                kwargs["response_format"] = {"type": "json_schema",
                                             "json_schema": {"name": "out", "schema": schema, "strict": True}}
            else:
                kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except BadRequestError as e:
            if schema is not None and self._json_schema_ok:
                log.warning("json_schema не поддержан моделью %s (%s) — откат на json_object", self.model, e)
                self._json_schema_ok = False
                return await self.complete(messages, tools, schema)
            raise
        msg = resp.choices[0].message
        parsed = None
        if schema is not None and msg.content:
            try:
                parsed = json.loads(msg.content)
            except json.JSONDecodeError:
                log.warning("модель вернула не-JSON: %.200s", msg.content)
        usage = resp.usage
        result = LLMResult(
            text=msg.content,
            tool_calls=[tc.model_dump() for tc in (msg.tool_calls or [])],
            parsed=parsed,
            tokens_in=(usage.prompt_tokens or 0) if usage else 0,
            tokens_out=(usage.completion_tokens or 0) if usage else 0,
            model=resp.model or self.model,
        )
        if (counter := _USAGE.get()) is not None:
            counter.add(result.tokens_in, result.tokens_out)
        return result


async def structured[T: BaseModel](llm: LLMClient, messages: list[dict], model_cls: type[T],
                                   retries: int = 1) -> T:
    """Вызов LLM со схемой Pydantic-модели, валидация и повтор с текстом ошибки."""
    schema = model_cls.model_json_schema()
    sys_msg = {"role": "system",
               "content": "Ответь только JSON-объектом по схеме: " + json.dumps(schema, ensure_ascii=False)}
    messages = [sys_msg, *messages]
    msgs = list(messages)
    for attempt in range(retries + 1):
        res = await llm.complete(msgs, schema=schema)
        raw = res.parsed if res.parsed is not None else res.text
        try:
            return model_cls.model_validate(raw) if isinstance(raw, dict) else model_cls.model_validate_json(raw or "")
        except ValidationError as e:
            log.warning("ответ не прошёл схему (попытка %d): %s", attempt + 1, e.errors()[:3])
            msgs = [*messages, {"role": "user",
                                "content": f"Ответ не соответствует JSON-схеме: {e.errors()[:3]}. Верни только JSON."}]
    raise ValueError(f"LLM не вернула валидный {model_cls.__name__}")


def build_llm() -> LLMClient:
    provider = os.getenv("LLM_PROVIDER", "fake")
    if provider == "fake":
        return FakeLLM()
    if provider == "openai_compat":
        return OpenAICompatLLM(
            base_url=os.environ["LLM_BASE_URL"],
            api_key=os.environ["LLM_API_KEY"],
            model=os.environ["LLM_MODEL"],
            extra_body=json.loads(os.environ["LLM_EXTRA_BODY"]) if os.getenv("LLM_EXTRA_BODY") else None,
        )
    raise ValueError(f"неизвестный LLM_PROVIDER={provider}")
