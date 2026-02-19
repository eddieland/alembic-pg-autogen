## 1. Operation Classes (`_ops.py`)

- [x] 1.1 Implement `CreateFunctionOp`, `ReplaceFunctionOp`, `DropFunctionOp` — each extends `MigrateOperation`, stores
  the relevant `FunctionInfo` field(s), and implements `reverse()` and `to_diff_tuple()`
- [x] 1.2 Implement `CreateTriggerOp`, `ReplaceTriggerOp`, `DropTriggerOp` — same pattern for `TriggerInfo`
- [x] 1.3 Add unit tests for all six op classes — verify construction, `reverse()` roundtrip, `to_diff_tuple()` output,
  and `MigrateOperation` inheritance (`tests/alembic_pg_autogen/test_ops.py`)

## 2. Renderers (`_render.py`)

- [x] 2.1 Register renderers for all six op classes via `renderers.dispatch_for()` — emit `op.execute()` calls with
  properly quoted DDL strings; `ReplaceTriggerOp` emits two statements (DROP then CREATE)
- [x] 2.2 Add unit tests for all six renderers — verify rendered Python code is syntactically valid, handles single
  quotes and backslashes in DDL, and does not add entries to `autogen_context.imports`
  (`tests/alembic_pg_autogen/test_render.py`)

## 3. Comparator (`_compare.py`)

- [x] 3.1 Implement `setup(plugin: Plugin)` that registers the comparator at `compare_target="schema"`,
  `compare_element="pg_objects"`
- [x] 3.2 Implement comparator function: read `pg_functions`/`pg_triggers` from `autogen_context.opts`, run inspect →
  canonicalize → diff pipeline, map `FunctionOp`/`TriggerOp` to `MigrateOperation` subclasses
- [x] 3.3 Implement dependency-safe ordering: emit ops as drop triggers → drop functions → create/replace functions →
  create/replace triggers
- [x] 3.4 Implement schema filtering: pass Alembic-provided `schemas` set to inspect functions, resolve `None` to
  default schema, filter canonicalized desired state to matching schemas

## 4. Wiring and Exports

- [x] 4.1 Add `[project.entry-points."alembic.plugins"]` to `pyproject.toml` pointing at `alembic_pg_autogen._compare`
- [x] 4.2 Update `__init__.py` — add all six op classes and `setup` to imports and `__all__`
- [x] 4.3 Update `AlembicProject` test helper `env.py` template — add `autogenerate_plugins` and `pg_functions` /
  `pg_triggers` kwargs to `context.configure()`
- [x] 4.4 Run `make lint` and fix any type errors, import ordering, or docstring issues

## 5. Integration Tests

- [x] 5.1 Test autogenerate with new function — create a function via DDL, run autogenerate, verify migration file
  contains the `CREATE OR REPLACE FUNCTION` via `op.execute()`
- [x] 5.2 Test autogenerate with modified function — create function in DB, provide different desired DDL, verify
  `REPLACE` op rendered in upgrade and old definition in downgrade
- [x] 5.3 Test autogenerate with dropped function — create function in DB, provide empty desired set, verify
  `DROP FUNCTION` rendered in upgrade and `CREATE OR REPLACE` in downgrade
- [x] 5.4 Test autogenerate with triggers — cover create, replace, and drop for triggers including the DROP+CREATE
  pattern for replace
- [x] 5.5 Test dependency ordering — provide both function and trigger changes, verify ops appear in the correct order
  in the generated migration
- [x] 5.6 Test no-op when state matches — run autogenerate when DB state matches desired state, verify no ops are
  emitted
- [x] 5.7 Test empty configuration — run autogenerate without `pg_functions`/`pg_triggers` kwargs, verify no errors and
  no ops emitted
