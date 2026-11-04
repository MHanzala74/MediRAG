.PHONY: run dev install lint test docker-up docker-down help

help:
	@echo "  make install      Install dependencies"
	@echo "  make dev          Start with auto-reload"
	@echo "  make run          Start production server"
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
