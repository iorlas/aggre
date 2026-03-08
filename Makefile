dev:
	docker compose -f docker-compose.local.yml up --build --watch

test:
	uv run pytest tests/

test-e2e:
	docker compose -p aggre-test -f docker-compose.test.yml up -d --wait
	AGGRE_TEST_DATABASE_URL=postgresql+psycopg://aggre:aggre@localhost:5433/aggre_test \
		uv run pytest tests/ ; \
	EXIT=$$? ; \
	docker compose -p aggre-test -f docker-compose.test.yml down -v ; \
	exit $$EXIT

dev-remote:
	@ip=$$(python3 -c "import socket; print(socket.gethostbyname('aggre-shen'))" 2>/dev/null); \
	if [ -z "$$ip" ]; then echo "Error: Cannot resolve aggre-shen. Is Tailscale running?" >&2; exit 1; fi; \
	echo "aggre-shen → $$ip"; \
	TAILSCALE_REMOTE_IP=$$ip docker compose -f docker-compose.remote.yml up --build --watch

coverage-diff:
	uv run diff-cover coverage.xml --compare-branch=origin/main --fail-under=95

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run ty check src tests

worker:
	uv run python -m aggre.workflows

grafana:
	docker compose -f docker-compose.local.yml up grafana -d

verify:
	bash .planning/verification/run.sh all

whisper-server:
	whisper-server \
		--model $(HOME)/Models/whisper/ggml-large-v3-turbo.bin \
		--host 0.0.0.0 --port 8090

whisper-health:
	@curl -sf http://localhost:8090/health > /dev/null \
		&& echo "whisper.cpp server: healthy" \
		|| echo "whisper.cpp server: NOT running"
