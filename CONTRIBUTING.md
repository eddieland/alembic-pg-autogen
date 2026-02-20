# Contributing

Contributions are welcome! Please fork the repository and open a pull request.

## Getting started

1. [Fork the repository](https://github.com/eddieland/alembic-pg-autogen/fork) on GitHub

1. Clone your fork and create a branch:

   ```bash
   git clone https://github.com/<your-username>/alembic-pg-autogen.git
   cd alembic-pg-autogen
   git checkout -b my-change
   ```

1. Install dependencies (requires [uv](https://docs.astral.sh/uv/)):

   ```bash
   make install
   ```

1. Make your changes, then lint and test:

   ```bash
   make lint       # format, spell-check, type-check
   make test       # full suite (requires Docker for integration tests)
   make test-unit  # unit tests only (no Docker)
   ```

1. Commit, push, and open a pull request against `main`.

## Code style

- **Line length**: 120 characters.
- **Ruff** handles formatting, import sorting, and linting. `make lint` auto-fixes in place.
- **BasedPyright** for type checking.
- Wildcard imports are banned — always use explicit imports.
- Public modules, functions, and classes in `src/` require docstrings (Google convention).
- Prefer `typing.NamedTuple` over `dataclass` for data containers.

## Tests

Tests live in `tests/` and are discovered in `src/` as well. Integration tests are marked with
`@pytest.mark.integration` and require a running PostgreSQL container via Docker.

## License

By contributing you agree that your contributions will be licensed under the MIT License.
