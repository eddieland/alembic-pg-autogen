## 1. Data types and module setup

- [x] 1.1 Create `src/alembic_pg_autogen/_diff.py` with the `Action` enum (`CREATE`, `REPLACE`, `DROP` with string
  values `"create"`, `"replace"`, `"drop"`). Import `enum` and define `Action(enum.Enum)`.
- [x] 1.2 Add `FunctionOp` NamedTuple to `_diff.py`. Fields: `action` (`Action`), `current` (`FunctionInfo | None`),
  `desired` (`FunctionInfo | None`). Import `FunctionInfo` from `_inspect` under `TYPE_CHECKING`.
- [x] 1.3 Add `TriggerOp` NamedTuple to `_diff.py`. Fields: `action` (`Action`), `current` (`TriggerInfo | None`),
  `desired` (`TriggerInfo | None`). Import `TriggerInfo` from `_inspect` under `TYPE_CHECKING`.
- [x] 1.4 Add `DiffResult` NamedTuple to `_diff.py`. Fields: `function_ops` (`Sequence[FunctionOp]`), `trigger_ops`
  (`Sequence[TriggerOp]`).
- [x] 1.5 Export `Action`, `FunctionOp`, `TriggerOp`, `DiffResult`, and `diff` from `__init__.py` and add to `__all__`.

## 2. Core implementation

- [x] 2.1 Implement `diff(current: CanonicalState, desired: CanonicalState) -> DiffResult` in `_diff.py`. Import
  `CanonicalState` from `_canonicalize` under `TYPE_CHECKING`. The function delegates to a private helper for each
  object type (functions and triggers) and returns a `DiffResult`.
- [x] 2.2 Implement the private diff helper that works generically on sequences of `FunctionInfo` or `TriggerInfo`.
  Build a dict keyed by identity (`info[:3]`) for each side, then iterate to produce create/replace/drop operations.
  Sort the result by identity key. Return the sorted operation sequence.

## 3. Unit tests

- [x] 3.1 Add `tests/alembic_pg_autogen/test_diff.py`. Test `Action` enum has exactly three members with correct string
  values.
- [x] 3.2 Test `FunctionOp` and `TriggerOp` construction and field access for each action type (CREATE, REPLACE, DROP).
- [x] 3.3 Test `DiffResult` construction and field access.
- [x] 3.4 Test `diff` with both states empty — returns empty `function_ops` and `trigger_ops`.
- [x] 3.5 Test `diff` with identical states (same functions and triggers) — returns empty ops.
- [x] 3.6 Test `diff` detecting a new function (desired only) — produces `CREATE` op with `current=None`.
- [x] 3.7 Test `diff` detecting a dropped function (current only) — produces `DROP` op with `desired=None`.
- [x] 3.8 Test `diff` detecting a replaced function (same identity, different definition) — produces `REPLACE` op with
  both `current` and `desired` populated.
- [x] 3.9 Test `diff` detecting a new trigger — produces `CREATE` op.
- [x] 3.10 Test `diff` detecting a dropped trigger — produces `DROP` op.
- [x] 3.11 Test `diff` detecting a replaced trigger — produces `REPLACE` op.
- [x] 3.12 Test mixed scenario: current has functions A and B, desired has B (modified) and C. Verify exactly three ops:
  DROP A, REPLACE B, CREATE C.
- [x] 3.13 Test overloaded functions: same name, different `identity_args` are matched independently.
- [x] 3.14 Test same function name in different schemas are matched independently.
- [x] 3.15 Test same trigger name on different tables are matched independently.
- [x] 3.16 Test deterministic ordering: ops are sorted by identity key regardless of input order.
- [x] 3.17 Test whitespace-only definition difference produces a REPLACE op (string equality, no normalization).

## 4. Integration tests

- [x] 4.1 Add integration test in `tests/alembic_pg_autogen/test_diff_integration.py`: full pipeline (inspect →
  canonicalize → diff) with a function that exists in DB but not in desired state — produces DROP.
- [x] 4.2 Integration test: function in desired state but not in DB — produces CREATE.
- [x] 4.3 Integration test: function exists in DB, desired state has modified body — produces REPLACE.
- [x] 4.4 Integration test: trigger create/drop/replace through the full pipeline.

## 5. Quality

- [x] 5.1 Run `make lint` and fix any ruff, basedpyright, or codespell issues.
- [x] 5.2 Run `make test` and verify all tests pass (unit and integration).
