# Makefile for easy development workflows.
# See docs/development.md for docs.
# Note GitHub Actions call uv directly, not this Makefile.

.DEFAULT_GOAL := help

SRC_PATHS := src tests
DOC_PATHS := README.md CLAUDE.md docs/ openspec/

##@ Development

default: install lint test ## Install, lint, and test (full check)

install: ## Install dependencies
	uv sync --all-extras

fmt: ## Run autoformatters and autofixers
	uv run mdformat $(DOC_PATHS)
	uv run codespell --write-changes $(SRC_PATHS) $(DOC_PATHS)
	uv run ruff check --fix $(SRC_PATHS)
	uv run ruff format $(SRC_PATHS)

lint: fmt ## Format, then type-check (basedpyright)
	uv run basedpyright --stats $(SRC_PATHS)

test: ## Run tests (unit + integration, requires Docker)
	uv run pytest

test-unit: ## Run unit tests only (no Docker required)
	uv run pytest -m "not integration"

##@ Build & Release

build: ## Build package
	uv build

##@ Maintenance

upgrade: ## Upgrade all dependencies
	uv sync --upgrade --all-extras --dev

clean: ## Remove build artifacts, caches, .venv
	-rm -rf dist/
	-rm -rf *.egg-info/
	-rm -rf .pytest_cache/
	-rm -rf .mypy_cache/
	-rm -rf .venv/
	-find . -type d -name "__pycache__" -exec rm -rf {} +

##@ Help

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} \
		/^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } \
		/^[a-zA-Z_-]+:.*?## / { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@echo

.PHONY: default install fmt lint test test-unit build upgrade clean help
