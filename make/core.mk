.PHONY: setup api ui ui-api test lint
ENV_FILE := $(if $(wildcard .env),--env-file .env,)

setup: ## установить зависимости (uv sync)
	uv sync

api: ## запустить FastAPI на :8000 (переменные из .env, если он есть)
	uv run uvicorn app.main:app --reload --port 8000 $(ENV_FILE)

ui: ## запустить Streamlit (AGENT_BACKEND=fake по умолчанию)
	uv run streamlit run ui/app.py

ui-api: ## Streamlit через настоящий API (сначала make api)
	AGENT_BACKEND=http API_URL=http://localhost:8000 uv run streamlit run ui/app.py

test: ## тесты
	uv run pytest -q

lint: ## линтер
	uv run ruff check .
