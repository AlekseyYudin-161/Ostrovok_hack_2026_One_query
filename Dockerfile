# syntax=docker/dockerfile:1
# Один образ на оба сервиса (API и интерфейс), команда запуска задаётся в docker-compose.yml.
# Multi-stage: в рантайме нет uv, dev-зависимостей и кэша сборки; процесс работает не от root.

FROM python:3.12-slim AS build
RUN pip install --no-cache-dir uv==0.11.32
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY . .
RUN uv sync --locked --no-dev

FROM python:3.12-slim AS runtime
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=build --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
EXPOSE 8080 8000
CMD ["streamlit", "run", "ui/app.py", "--server.port=8080", "--server.address=0.0.0.0", "--server.headless=true", "--browser.gatherUsageStats=false"]
