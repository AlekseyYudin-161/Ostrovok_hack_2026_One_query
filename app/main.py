"""FastAPI-сервис агента.

/health — проверка живости; /chat — поток событий агента в формате SSE (text/event-stream).
Какой агент отвечает, решает переменная окружения AGENT_BACKEND (fake | graph).
"""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from harness.contracts import CONTRACT_VERSION, Agent, HotelCandidate
from harness.fakes import FakeAgent, FakeHotelSearch, HotelSearchArgs

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("app")


def build_agent() -> Agent:
    backend = os.getenv("AGENT_BACKEND", "fake")
    if backend == "fake":
        return FakeAgent()
    if backend == "graph":
        from harness.agent_graph import GraphAgent  # импорт здесь: fake-режиму LangGraph не нужен

        return GraphAgent()
    raise RuntimeError(f"AGENT_BACKEND={backend}: допустимо fake | graph")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.agent = build_agent()
    log.info("agent=%s contract=%s", type(app.state.agent).__name__, CONTRACT_VERSION)
    yield
    # сюда позже: get_client().shutdown() для Langfuse
    log.info("shutdown")


app = FastAPI(title="О!дин запрос", lifespan=lifespan)


class ChatRequest(BaseModel):
    text: str = Field(default="", max_length=2000)
    session_id: str = Field(min_length=1, max_length=64)
    patch: dict | None = None  # правка полей из панели параметров (ADR-022), контракт 1.2

    @model_validator(mode="after")
    def text_or_patch(self) -> ChatRequest:
        if not self.text.strip() and not self.patch:
            raise ValueError("нужен text или patch")
        return self


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/hotels", response_model=list[HotelCandidate])
async def hotels(city: str | None = None,
                 max_price: float | None = Query(default=None, ge=0)) -> list[HotelCandidate]:
    """Отладочный поиск по фикстурам (пока нет API оргов)."""
    result = await FakeHotelSearch()(HotelSearchArgs(city=city, max_price_total=max_price))
    return [HotelCandidate(**h) for h in result.data or []]


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "contract": CONTRACT_VERSION}


@app.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    agent: Agent = app.state.agent

    async def sse() -> AsyncIterator[str]:
        try:
            async for event in agent.stream(req.text, req.session_id, patch=req.patch):
                yield f"event: {event.type}\ndata: {event.model_dump_json()}\n\n"
        except Exception:  # клиенту — событие error, в лог — трейс
            log.exception("agent failed session=%s", req.session_id)
            yield 'event: error\ndata: {"message": "internal error"}\n\n'

    return StreamingResponse(sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})
