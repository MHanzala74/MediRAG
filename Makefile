.PHONY: run dev install lint test eval eval-retrieval eval-safety eval-synth docker-up docker-down help

help:
	@echo "  make install      Install dependencies"
	@echo "  make dev          Start with auto-reload"
	@echo "  make run          Start production server"
	@echo "  make eval         Run full DeepEval evaluation (all metrics)"
	@echo "  make eval-synth   Generate golden dataset from ingested docs"
	@echo "  make docker-up    Build & start all services"
	@echo "  make docker-down  Stop all services"

install:
	pip install -r requirements.txt

dev:
	uvicorn main:app --host 0.0.0.0 --port 8000 --reload

run:
	uvicorn main:app --host 0.0.0.0 --port 8000

docker-up:
	docker compose up --build

docker-down:
	docker compose down

# ── Evaluation (DeepEval) ────────────────────────────────────────────
eval:
	python -m evaluation.run_eval

eval-retrieval:
	python -m evaluation.run_eval --metrics retrieval

eval-safety:
	python -m evaluation.run_eval --metrics safety

eval-synth:
	python -m evaluation.synthesize --n 25 --out evaluation/data/synthetic.json

test:
	pytest evaluation tests -v
