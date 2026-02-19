## Why

The core pipeline (inspect → canonicalize → diff) produces Python objects describing what changed, but cannot yet
generate Alembic migration files. Without wiring into Alembic's autogenerate hooks, users must write migration DDL by
hand — defeating the purpose of the library.

## What Changes

- **`_ops.py`**: Define `MigrateOperation` subclasses that carry `FunctionOp` / `TriggerOp` payloads into Alembic's
  operation framework.
- **`_compare.py`**: Register an autogenerate comparator that runs the full pipeline — inspect current state,
  canonicalize desired state, diff, emit operations — during `alembic revision --autogenerate`.
- **`_render.py`**: Register renderers that emit Python code (using `op.execute()` with raw DDL) into generated
  migration files.
- **Desired-state registration**: Provide an API for users to declare which functions and triggers should be managed,
  integrated into Alembic's `context.configure()` flow.

## Non-goals

- **Offline mode (`--sql`)**: Canonicalization requires a live database connection. Offline autogenerate is out of
  scope.
- **Cross-database portability**: Migration files may reference library types. This library targets the author's
  PostgreSQL applications.
- **SQL file ingestion**: Users declare desired state programmatically (Style A from research). Reading `.sql` files is
  not in scope.

## Capabilities

### New Capabilities

- `alembic-operations`: `MigrateOperation` subclasses representing function and trigger create/replace/drop operations,
  with dependency-aware ordering (functions before triggers for create, triggers before functions for drop).
- `alembic-compare`: Autogenerate comparator hook that orchestrates the inspect-canonicalize-diff pipeline and emits
  operations into the autogenerate context. Includes the desired-state registration API.
- `alembic-render`: Renderer functions that convert operation instances into executable Python code for migration files,
  emitting `op.execute()` calls with the appropriate DDL.

### Modified Capabilities

_(none — existing specs define types consumed as-is)_

## Impact

- **Modules**: `_ops.py`, `_compare.py`, `_render.py` go from stubs to implementations.
- **Public API**: New types and the registration function exported from `__init__.py` and `__all__`.
- **Dependencies**: No new runtime dependencies (Alembic is already required).
- **Testing**: Integration tests using the existing `AlembicProject` helper to exercise the full autogenerate workflow.
- **User workflow**: After this change, `alembic revision --autogenerate` detects function/trigger changes
  automatically.
