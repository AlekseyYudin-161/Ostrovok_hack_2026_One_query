#!/usr/bin/env bash
# Проверка окружения участника: uv, Python 3.12, зависимости, тесты, API.
set -euo pipefail
echo "1) uv:";        uv --version
echo "2) python:";    uv run python -c "import sys; print(sys.version.split()[0]); assert sys.version_info[:2]==(3,12)"
echo "3) sync:";      uv sync --locked
echo "4) tests:";     uv run pytest -q
echo "5) imports:";   uv run python -c "import fastapi, streamlit, openai, httpx, pydantic; print('ok')"
echo "Окружение в порядке."
