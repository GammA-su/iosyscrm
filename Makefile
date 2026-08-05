.PHONY: install dev migrate revision test lint format typecheck check seed

install:
	uv sync

dev:
	docker compose --profile dev up

migrate:
	uv run alembic upgrade head

revision:
	uv run alembic revision --autogenerate -m "$(m)"

test:
	uv run pytest

lint:
	uv run ruff check . && uv run ruff format --check .

format:
	uv run ruff check --fix . && uv run ruff format .

typecheck:
	uv run mypy app

check: lint typecheck test

seed:
	@echo "Jeu de données de démonstration : disponible une fois le schéma livré (lot T1)."
	@exit 1
