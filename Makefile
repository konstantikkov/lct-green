.PHONY: dev api web test fixture docker eval

# one-command local dev (backend :8000 + frontend :5173)
dev:
	@$(MAKE) -j2 api web

api:
	cd apps/api && python3 -m uvicorn app.main:app --port 8000 --reload

web:
	cd apps/web && npm run dev

install:
	cd apps/api && python3 -m pip install -r requirements.txt
	cd apps/web && npm install

fixture:
	cd apps/api && python3 scripts/make_fixture.py

test:
	cd apps/api && python3 -m pytest tests/ -q

docker:
	docker compose up --build

# --- evaluation & regression pipeline (WS3) ----------------------------------
# Level A deterministic checks gate (exit 1); judge benchmark runs in mock
# mode; GPT evaluator runs only when OPENAI_EVAL_MODEL capability is present.
eval:
	PYTHONPATH=apps/api python3 -m evals.run
