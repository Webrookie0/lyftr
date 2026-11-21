.PHONY: up down logs test test-frontend lint build verify verify-cache

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

test:
	cd backend && uv run python -c "import litellm"
	cd backend && uv run python -m unittest discover -s tests -v

test-frontend:
	cd frontend && npm test

lint:
	cd backend && uv run ruff check app tests
	cd backend && uv run ruff format --check app tests

build:
	cd frontend && npm run build

verify:
	python3 scripts/verify_live.py

verify-cache:
	python3 scripts/verify_cache.py
