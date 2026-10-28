"""LLM-клиент без сети: OpenAI-совместимый сервер подменён httpx.MockTransport."""
import json

import httpx
import pytest
from pydantic import BaseModel

from harness.llm import OpenAICompatLLM, structured


class Parsed(BaseModel):
    city: str | None
    nights: int | None


def _completion(content: str) -> dict:
    return {"id": "x", "object": "chat.completion", "created": 0, "model": "test-model",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}}


@pytest.mark.asyncio
async def test_structured_with_json_schema() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body["response_format"]["type"])
        return httpx.Response(200, json=_completion('{"city": "Сочи", "nights": 7}'))

    llm = OpenAICompatLLM("http://router/v1", "k", "test-model",
                          http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out = await structured(llm, [{"role": "user", "content": "Сочи на неделю"}], Parsed)
    assert out == Parsed(city="Сочи", nights=7) and seen == ["json_schema"]


@pytest.mark.asyncio
async def test_fallback_to_json_object_when_schema_rejected() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        rf = json.loads(request.content)["response_format"]["type"]
        seen.append(rf)
        if rf == "json_schema":
            return httpx.Response(400, json={"error": {"message": "json_schema not supported"}})
        return httpx.Response(200, json=_completion('{"city": "Адлер", "nights": 3}'))

    llm = OpenAICompatLLM("http://router/v1", "k", "test-model",
                          http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out = await structured(llm, [{"role": "user", "content": "Адлер 3 ночи"}], Parsed)
    assert out.city == "Адлер" and seen == ["json_schema", "json_object"]


@pytest.mark.asyncio
async def test_extra_body_is_sent() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_completion('{"city": "Сочи", "nights": 2}'))

    llm = OpenAICompatLLM("http://router/v1", "k", "m", extra_body={"provider": {"country": "ru"}},
                          http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await structured(llm, [{"role": "user", "content": "Сочи"}], Parsed)
    assert seen["provider"] == {"country": "ru"}
