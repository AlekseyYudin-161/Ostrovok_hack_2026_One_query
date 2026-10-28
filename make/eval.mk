# Цели модуля M3 (DA): eval агента. Отчёт — evals/reports/eval_report.{json,md}, его читает страница метрик.
.PHONY: eval eval-offline eval-dev eval-security eval-connections eval-heldout
EVAL_ENV := $(if $(wildcard .env),--env-file .env,)

eval: ## eval на всех кейсах evals/golden/ (агент и модель — из .env) → evals/reports/
	uv run $(EVAL_ENV) python -m evals.runner

eval-offline: ## eval без сети и без денег: разбор запроса правилами (LLM_PROVIDER=fake)
	LLM_PROVIDER=fake LANGFUSE_TRACING_ENABLED=false uv run python -m evals.runner

eval-dev: ## только 20 dev-кейсов + 3 кейса из чата
	uv run $(EVAL_ENV) python -m evals.runner evals/golden/dev.jsonl evals/golden/seed_from_chat.jsonl

eval-security: ## срез security (D9): атаки в запросе и в отзывах → Injection ASR
	uv run $(EVAL_ENV) python -m evals.runner evals/golden/security.jsonl

eval-connections: ## срез состыковок (D10): ловушки дороги → ошибки стыковок
	uv run $(EVAL_ENV) python -m evals.runner evals/golden/connections.jsonl

eval-heldout: ## held-out (D8): кейсы, которых тимлид не видел (evals/golden/heldout/*.jsonl)
	uv run $(EVAL_ENV) python -m evals.runner $(wildcard evals/golden/heldout/*.jsonl) --split heldout --out evals/reports/heldout
