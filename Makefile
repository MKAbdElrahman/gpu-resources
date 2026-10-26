.PHONY: install test lint format check

install:
	uv pip install -e '.[dev]'

test:
	pytest

lint:
	ruff check src tests

format:
	ruff check --fix src tests
	ruff format src tests

check: lint test
