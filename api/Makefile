.PHONY: install dev lint format typecheck test test-cov run seed-db seed-doctors dev-link docker-build docker-run clean

install:
	uv sync --extra dev

dev:
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 --app-dir src

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run ty check src

test:
	uv run pytest

test-cov:
	uv run pytest --cov --cov-report=term-missing

run:
	uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir src

seed-db:
	uv run python scripts/seed_question_bank.py

seed-doctors:
	uv run python scripts/seed_doctors.py

# Mint a real session + intake link to develop the frontend against.
dev-link:
	uv run python scripts/dev_intake_link.py

docker-build:
	docker build -t prelude-health-api:latest .

docker-run:
	docker run --rm -p 8000:8000 --env-file .env prelude-health-api:latest

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .ty_cache htmlcov .coverage
