# Makefile for easy development workflows.
# See docs/development.md for docs.
# Note GitHub Actions call uv directly, not this Makefile.

.DEFAULT_GOAL := default

SRC_PATHS := src tests
DOC_PATHS := README.md

.PHONY: default install lint test upgrade build clean

default: install lint test

install:
	uv sync --all-extras

lint:
	uv run codespell --write-changes $(SRC_PATHS) $(DOC_PATHS)
	uv run ruff check --fix $(SRC_PATHS)
	uv run ruff format $(SRC_PATHS)
	uv run basedpyright --stats $(SRC_PATHS)

test:
	uv run pytest

upgrade:
	uv sync --upgrade --all-extras --dev

build:
	uv build

clean:
	-rm -rf dist/
	-rm -rf *.egg-info/
	-rm -rf .pytest_cache/
	-rm -rf .mypy_cache/
	-rm -rf .venv/
	-find . -type d -name "__pycache__" -exec rm -rf {} +
