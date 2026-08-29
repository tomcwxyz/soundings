.PHONY: help install install-spacy lint type test test-integration test-live test-db-create migrate seed seed-light up down logs decrypt-env publish-corpus

help:
	@grep -E '^[a-zA-Z_-]+:.*?## ' Makefile | awk 'BEGIN{FS=":.*?## "} {printf "  %-18s %s\n", $$1, $$2}'

install:  ## Sync dev deps with uv (run inside server/)
	cd server && uv sync

install-spacy:  ## Download the spaCy NER model used by the sanitisation pipeline
	cd server && uv run python -m spacy download en_core_web_sm

lint:  ## Run ruff
	cd server && uv run ruff check . && uv run ruff format --check .

type:  ## Run mypy
	cd server && uv run mypy soundings

test:  ## Run unit tests (no live APIs, no DB required for non-integration)
	cd server && uv run pytest -m "not live and not integration"

test-integration:  ## Run integration tests (needs running docker compose)
	cd server && uv run pytest -m integration

test-live:  ## Run live-API tests (nightly cron only)
	cd server && uv run pytest -m live

# One-time setup: integration tests truncate org/geography tables on cleanup,
# so they need a separate database from the dev `soundings` DB. The conftest
# refuses to run against `/soundings` for safety. Requires `make up` first
# (the Postgres container must be running).
test-db-create:  ## Create + migrate the soundings_test database (one-time)
	docker compose -f infra/docker-compose.yml --project-directory . exec -T postgres \
	  psql -U soundings -d postgres -c "CREATE DATABASE soundings_test OWNER soundings" 2>&1 | grep -v "already exists" || true
	docker compose -f infra/docker-compose.yml --project-directory . exec -T server \
	  bash -c 'DATABASE_URL="postgresql+asyncpg://soundings:$$POSTGRES_PASSWORD@postgres:5432/soundings_test" alembic upgrade head'

up:  ## Bring up the docker compose stack
	docker compose -f infra/docker-compose.yml --project-directory . up -d

down:  ## Tear down the stack
	docker compose -f infra/docker-compose.yml --project-directory . down

logs:  ## Tail logs
	docker compose -f infra/docker-compose.yml --project-directory . logs -f

migrate:  ## Run alembic upgrade head against running stack
	docker compose -f infra/docker-compose.yml --project-directory . exec server alembic upgrade head

seed:  ## Full geography + catalogue seed (~1 hour)
	docker compose -f infra/docker-compose.yml --project-directory . exec server python -m soundings.seed.run --full

seed-light:  ## Dev seed (single LTLA, ~5 min)
	docker compose -f infra/docker-compose.yml --project-directory . exec server python -m soundings.seed.run --light

OPS_ENV ?= ../soundings-ops/env/production.env.sops

decrypt-env:  ## Decrypt an encrypted instance env from the sibling soundings-ops repo
	@test -f "$(OPS_ENV)" || (echo "No encrypted ops env found at $(OPS_ENV). Use .env.example for local development."; exit 1)
	@command -v sops >/dev/null 2>&1 || (echo "sops is required to decrypt $(OPS_ENV)"; exit 1)
	sops --decrypt "$(OPS_ENV)" > .env

# PERIOD defaults to last month if not provided. OUT defaults to ./corpus/.
PERIOD ?=
OUT    ?= corpus
publish-corpus:  ## Materialise the monthly corpus into $(OUT)/ and create a local git tag
	cd server && uv run python -m soundings.publication.cli \
	  $(if $(PERIOD),--period $(PERIOD)) \
	  --out ../$(OUT)
