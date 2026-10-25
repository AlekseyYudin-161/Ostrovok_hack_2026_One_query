# О!дин запрос — AI-агент для путешествий (Ostrovok Tech Hackathon 2026)

> Каркас (PR №0): контракт `harness/contracts.py`, общая проверка ограничений `harness/constraints.py`,
> фейки всех модулей `harness/fakes.py`, FastAPI `/health` + `/chat` (SSE), Streamlit-заглушка, CI.
> Бейзлайн-агент на LangGraph: `harness/agent_graph.py` (`AGENT_BACKEND=graph`), шаг 1 — understand → clarify | search → answer.
> По умолчанию всё работает на фейках: ключи LLM и сеть не нужны.

## Быстрый старт

Требования: [uv](https://docs.astral.sh/uv/) (Python 3.12 он поставит сам).

### Через Makefile
```bash
make setup      # uv sync
make test       # pytest
make api        # FastAPI на :8000 (читает .env)
make ui         # Streamlit сам по себе (AGENT_BACKEND=fake)
make ui-api     # Streamlit через API (в соседнем окне — make api)
```

### Вручную
```bash
uv sync
uv run pytest -q
uv run uvicorn app.main:app --reload --port 8000 --env-file .env   # без .env — убрать флаг
uv run streamlit run ui/app.py
# UI через настоящий API:
AGENT_BACKEND=http API_URL=http://localhost:8000 uv run streamlit run ui/app.py
```

Проверка окружения одной командой: `bash scripts/check_env.sh`.


## Режимы LLM (переменные в `.env`, см. `.env.example`)
| Режим | Как включить | Когда |
|---|---|---|
| Офлайн, без ключа | `LLM_PROVIDER=fake` | проверить путь целиком без сети |
| Облако (kodikrouter; запасной routerai / любой OpenAI-совместимый) | `LLM_PROVIDER=openai_compat`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL=qwen/qwen3.6-35b-a3b`, `LLM_EXTRA_BODY={"reasoning": {"enabled": false}}` | разработка и демо |
| Локально (Ollama) | `ollama pull <модель>`, затем `LLM_BASE_URL=http://localhost:11434/v1`, `LLM_API_KEY=ollama`, `LLM_MODEL=<модель>` | без внешних сервисов |

Данные: **все данные — демо** (синтетический каталог `synth/`: отели, тарифы, расписания, трансферы и отзывы вымышленные; районы Большого Сочи и климатические нормы — условно реальные).


## Правила
- Ветки `feature/<задача>` от `dev`; в `dev` — только через PR, ревьюит и мержит тимлид.
- `main` — стабильная версия для сабмита: тимлид вливает `dev → main` на вехах и перед сдачей.
- Модули зависят только от `contracts.py`, `constraints.py`, `fakes.py`.
- Новая зависимость — строкой в описании PR; `uv.lock` обновляет тимлид.
- Пушим только в свою ветку; в `dev` и `main` напрямую не пушим.
