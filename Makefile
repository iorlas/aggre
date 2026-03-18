dev:
	docker compose -f docker-compose.local.yml up --build --watch

test:
	uv run pytest tests/

test-e2e:
	$(eval AGGRE_TEST_PORT := $(shell python3 -c "import socket; s=socket.socket(); s.bind(('',0)); print(s.getsockname()[1]); s.close()"))
	$(eval AGGRE_TEST_PROJECT := aggre-test-$(shell basename $(CURDIR)))
	AGGRE_TEST_PORT=$(AGGRE_TEST_PORT) docker compose -p $(AGGRE_TEST_PROJECT) -f docker-compose.test.yml up -d --wait
	AGGRE_TEST_DATABASE_URL=postgresql+psycopg://aggre:aggre@localhost:$(AGGRE_TEST_PORT)/aggre_test \
		uv run pytest tests/ ; \
	EXIT=$$? ; \
	AGGRE_TEST_PORT=$(AGGRE_TEST_PORT) docker compose -p $(AGGRE_TEST_PROJECT) -f docker-compose.test.yml down -v ; \
	exit $$EXIT

dev-remote:
	@ip=$$(python3 -c "import socket; print(socket.gethostbyname('aggre-shen'))" 2>/dev/null); \
	if [ -z "$$ip" ]; then echo "Error: Cannot resolve aggre-shen. Is Tailscale running?" >&2; exit 1; fi; \
	echo "aggre-shen → $$ip"; \
	TAILSCALE_REMOTE_IP=$$ip docker compose -f docker-compose.remote.yml up --build --watch

coverage-diff:
	uv run diff-cover coverage.xml --compare-branch=origin/main --fail-under=95

lint:  ## Check only — safe for AI, CI, pre-commit. Never modifies files.
	@uv run ruff format --check || (echo "Formatting issues found. Run 'make fix' to auto-fix." && exit 1)
	@uv run ruff check || (echo "Lint issues found. Fixable ones can be resolved with 'make fix'." && exit 1)
	@uv run ty check
	@uv run yamllint -c .yamllint.yml .
	@uv run python scripts/check-json.py

fix:  ## Auto-fix formatting and import sorting. Modifies files.
	uv run ruff check --fix
	uv run ruff format

worker:
	uv run python -m aggre.workflows

grafana:
	docker compose -f docker-compose.local.yml up grafana -d

verify:
	bash .planning/verification/run.sh all

whisper-server:
	whisper-server \
		--model $(HOME)/Models/whisper/ggml-large-v3-turbo.bin \
		--host 0.0.0.0 --port 8090 --convert

whisper-health:
	@curl -sf http://localhost:8090/health > /dev/null \
		&& echo "whisper.cpp server: healthy" \
		|| echo "whisper.cpp server: NOT running"
