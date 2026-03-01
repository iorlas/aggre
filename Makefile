dev:
	docker compose -f docker-compose.local.yml up --build --watch

test:
	uv run pytest tests/

test-e2e:
	docker compose -f docker-compose.local.yml --profile test up -d test-db --wait
	AGGRE_TEST_DATABASE_URL=postgresql+psycopg2://aggre:aggre@localhost:5433/aggre_test \
		uv run pytest tests/ ; \
	EXIT=$$? ; \
	docker compose -f docker-compose.local.yml --profile test down test-db ; \
	exit $$EXIT

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run ty check src tests

validate:
	uv run dagster definitions validate

verify:
	bash .planning/verification/run.sh all
