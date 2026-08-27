# Contributing

Contributions are welcome. Fork the repository and open a pull request.

## Getting started

1. [Fork the repository](https://github.com/eddieland/alembic-pg-autogen/fork) on GitHub.

1. Clone your fork and create a branch:

   ```bash
   git clone https://github.com/<your-username>/alembic-pg-autogen.git
   cd alembic-pg-autogen
   git checkout -b my-change
   ```

1. Install the dependencies. This step requires [uv](https://docs.astral.sh/uv/):

   ```bash
   make install
   ```

1. Make your changes, then lint and test them:

   ```bash
   make lint       # format, spell-check, type-check
   make test       # full suite (requires Docker for integration tests)
   make test-unit  # unit tests only (no Docker)
   ```

1. Commit your changes, push the branch, and open a pull request against `main`.

## Code style

- **Line length**: 120 characters.
- **Ruff** handles formatting, import sorting, and linting. `make lint` applies each fix in place.
- **BasedPyright** checks types.
- Wildcard imports are banned. Always write explicit imports.
- Public modules, functions, and classes in `src/` require docstrings in the Google convention.
- Prefer `typing.NamedTuple` over `dataclass` for data containers.

## Tests

Tests live in `tests/`. Pytest also discovers tests in `src/`. Integration tests carry the `@pytest.mark.integration`
marker. They require a PostgreSQL container that runs under Docker.

## License

You agree to license your contributions under the MIT License when you open a pull request.
