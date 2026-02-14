lint:
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run ty check src tests
