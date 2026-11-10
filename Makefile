.PHONY: ui check

ui:
	cd src/aml_investigator/web/frontend && npm ci && npm run build

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	uv run pytest
	cd src/aml_investigator/web/frontend && npm ci && npm run check
