bootstrap:  ## Set up dev environment (run once after clone)
	uv sync
	@command -v prek >/dev/null 2>&1 && prek install || (command -v pre-commit >/dev/null 2>&1 && pre-commit install || echo "⚠ Install prek (brew install prek) or pre-commit for git hooks")
	@echo "✓ Dev environment ready. Run 'make lint' to verify."

dev:
	docker compose -f docker-compose.local.yml up --build --watch

test:  ## Run tests. Spins up ephemeral postgres if AGGRE_TEST_DATABASE_URL is not set.
	@if [ -n "$$AGGRE_TEST_DATABASE_URL" ]; then \
		uv run pytest tests/; \
	else \
		PORT=$$(python3 -c "import socket; s=socket.socket(); s.bind(('',0)); print(s.getsockname()[1]); s.close()"); \
		PROJECT=aggre-test-$$(basename "$$PWD"); \
		AGGRE_TEST_PORT=$$PORT docker compose -p $$PROJECT -f docker-compose.test.yml up -d --wait; \
		AGGRE_TEST_DATABASE_URL=postgresql+psycopg://aggre:aggre@localhost:$$PORT/aggre_test \
			uv run pytest tests/; \
		EXIT=$$?; \
		AGGRE_TEST_PORT=$$PORT docker compose -p $$PROJECT -f docker-compose.test.yml down -v; \
		exit $$EXIT; \
	fi


coverage-diff:
	uv run diff-cover coverage.xml --compare-branch=origin/main --fail-under=95

check: lint test  ## Full quality gate — lint then test.

lint:  ## Check only — safe for AI, CI, pre-commit. Never modifies files.
	@agent-harness lint

audit:  ## Check for known vulnerabilities and leaked secrets.
	@agent-harness security-audit

fix:  ## Auto-fix formatting and import sorting, then verify with lint.
	@agent-harness fix

worker:
	uv run python -m aggre.workflows

grafana:
	docker compose -f docker-compose.local.yml up grafana -d

verify:
	bash verification/run.sh all

WHISPER_MODEL_DIR = $(HOME)/Models/whisper
WHISPER_MODEL = $(WHISPER_MODEL_DIR)/ggml-large-v3-turbo.bin
WHISPER_MODEL_URL = https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin

whisper-model:
	@if [ ! -f "$(WHISPER_MODEL)" ]; then \
		echo "Downloading whisper model to $(WHISPER_MODEL)..."; \
		mkdir -p "$(WHISPER_MODEL_DIR)"; \
		curl -L --progress-bar -o "$(WHISPER_MODEL).tmp" "$(WHISPER_MODEL_URL)" \
			&& mv "$(WHISPER_MODEL).tmp" "$(WHISPER_MODEL)" \
			|| { rm -f "$(WHISPER_MODEL).tmp"; echo "Error: failed to download model"; exit 1; }; \
		echo "Model downloaded."; \
	else \
		echo "Whisper model already exists at $(WHISPER_MODEL)"; \
	fi

whisper-server: whisper-model
	@command -v whisper-server >/dev/null 2>&1 || { \
		echo "Error: whisper-server not found in PATH"; \
		echo ""; \
		echo "Install whisper.cpp server:"; \
		echo ""; \
		echo "  macOS (Homebrew):"; \
		echo "    brew install whisper-cpp"; \
		echo ""; \
		echo "  Linux (build from source):"; \
		echo "    git clone https://github.com/ggerganov/whisper.cpp"; \
		echo "    cd whisper.cpp && cmake -B build && cmake --build build --config Release"; \
		echo "    sudo cp build/bin/whisper-server /usr/local/bin/"; \
		echo ""; \
		exit 1; \
	}
	whisper-server \
		--model $(WHISPER_MODEL) \
		--host 0.0.0.0 --port 8090 --convert

whisper-health:
	@curl -sf http://localhost:8090/health > /dev/null \
		&& echo "whisper.cpp server: healthy" \
		|| echo "whisper.cpp server: NOT running"
