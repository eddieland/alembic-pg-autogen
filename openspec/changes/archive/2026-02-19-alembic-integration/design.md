## Context

The core pipeline is complete: `inspect_functions`/`inspect_triggers` read the current database state, `canonicalize`
round-trips desired DDL through PostgreSQL for normalization, and `diff` compares the two `CanonicalState` snapshots to
produce `FunctionOp`/`TriggerOp` sequences. What remains is wiring this pipeline into Alembic's autogenerate machinery
so that `alembic revision --autogenerate` detects function/trigger changes and emits migration files.

Alembic 1.18+ provides a `Plugin` API for registering comparators, and a `Dispatcher`-based system for renderers. The
three stub modules (`_compare.py`, `_ops.py`, `_render.py`) correspond directly to these extension points. An
`AlembicProject` test helper already exists for integration testing.

## Goals / Non-Goals

**Goals:**

- Wire the inspect-canonicalize-diff pipeline into `alembic revision --autogenerate`
- Define operation classes that carry diff results through Alembic's op framework
- Render migration files with `op.execute()` calls containing raw DDL
- Provide a simple desired-state registration API via `context.configure()` kwargs
- Emit ops in dependency-safe order (functions before triggers for creates, reversed for drops)
- Generate correct downgrade migrations via `reverse()`

**Non-Goals:**

- Offline mode (`--sql`) — canonicalization requires a live connection
- Custom `op.create_function()` / `op.drop_trigger()` syntax — `op.execute()` is sufficient
- `@Operations.register_operation` / `@Operations.implementation_for` — not needed when rendering to `op.execute()`
- Reading `.sql` files or non-programmatic desired-state declaration

## Decisions

### D1: Plugin registration via entry points

Register the comparator using Alembic's `Plugin` API with a `pyproject.toml` entry point. The `_compare` module exports
a `setup(plugin: Plugin)` function that Alembic discovers automatically.

```toml
[project.entry-points."alembic.plugins"]
alembic_pg_autogen = "alembic_pg_autogen._compare"
```

Users activate the plugin in `env.py`:

```python
context.configure(
    connection=conn,
    target_metadata=target_metadata,
    autogenerate_plugins=["alembic.autogenerate.*", "alembic-pg-autogen.*"],
)
```

**Why not programmatic registration?** Entry points are the standard Alembic 1.18+ pattern. They decouple registration
from user code — installing the package is sufficient. The plugin only fires when explicitly activated via
`autogenerate_plugins`, so there is no overhead if installed but not configured.

### D2: Six operation classes (3 per object type)

Define six `MigrateOperation` subclasses in `_ops.py`:

| Class                                 | Upgrade DDL                                           | Downgrade (via `reverse()`)           |
| ------------------------------------- | ----------------------------------------------------- | ------------------------------------- |
| `CreateFunctionOp(desired)`           | `CREATE OR REPLACE FUNCTION ...`                      | `DropFunctionOp(desired)`             |
| `ReplaceFunctionOp(current, desired)` | `CREATE OR REPLACE FUNCTION ...` (desired)            | `ReplaceFunctionOp(desired, current)` |
| `DropFunctionOp(current)`             | `DROP FUNCTION schema.name(args)`                     | `CreateFunctionOp(current)`           |
| `CreateTriggerOp(desired)`            | `CREATE ... TRIGGER ...`                              | `DropTriggerOp(desired)`              |
| `ReplaceTriggerOp(current, desired)`  | `DROP TRIGGER ... + CREATE ... TRIGGER ...` (desired) | `ReplaceTriggerOp(desired, current)`  |
| `DropTriggerOp(current)`              | `DROP TRIGGER ... IF EXISTS ...`                      | `CreateTriggerOp(current)`            |

Each op stores the relevant `FunctionInfo` or `TriggerInfo` from the diff result. The `definition` field on those types
contains the full canonical DDL (from `pg_get_functiondef()` / `pg_get_triggerdef()`), which is what the renderer emits.

**Why six classes instead of two generic ones?** Each class has a distinct `reverse()` contract:

- CREATE reverses to DROP (no old definition needed)
- REPLACE reverses to REPLACE with swapped current/desired
- DROP reverses to CREATE (restores the old definition)

Collapsing these into fewer classes would require conditional logic in `reverse()` and renderers, obscuring the intent.

**Why no `@Operations.register_operation`?** The rendered migration code uses `op.execute()` which is a built-in Alembic
operation. Custom op classes are autogenerate-time constructs only — they are never instantiated during migration
execution. This keeps migration files self-contained (no library imports needed to run them).

### D3: Desired-state registration via `context.configure()` kwargs

Users declare desired functions and triggers as DDL strings passed through `context.configure()`:

```python
context.configure(
    connection=conn,
    target_metadata=target_metadata,
    autogenerate_plugins=["alembic.autogenerate.*", "alembic-pg-autogen.*"],
    pg_functions=[
        "CREATE OR REPLACE FUNCTION audit.log_change() RETURNS trigger ...",
    ],
    pg_triggers=[
        "CREATE TRIGGER audit_trg AFTER INSERT OR UPDATE ON orders ...",
    ],
)
```

The comparator reads `autogen_context.opts["pg_functions"]` and `autogen_context.opts["pg_triggers"]`. Both default to
empty lists if absent (no ops emitted, no errors).

**Why `context.configure()` kwargs over a registry module?** This avoids global mutable state and integrates naturally
with Alembic's existing configuration flow. Users already edit `env.py` for Alembic configuration — adding two keyword
arguments is minimal friction.

**Why DDL strings over decomposed objects?** DDL strings are what users write and what the canonicalization step
consumes. Decomposed `FunctionInfo`/`TriggerInfo` objects are an internal representation produced *by* canonicalization,
not *for* it. Users should not need to know the internal types to declare desired state.

### D4: Dependency-safe operation ordering

The comparator emits ops to `upgrade_ops.ops` in this order:

1. Drop triggers (free referencing functions for removal)
1. Drop functions
1. Create/replace functions (must exist before triggers reference them)
1. Create/replace triggers

Alembic's `upgrade_ops.reverse_into(downgrade_ops)` reverses the list and calls `reverse()` on each op, producing the
mirror-safe downgrade order automatically.

### D5: Schema filtering follows Alembic conventions

The comparator receives `schemas: set[str | None]` from Alembic's dispatch (populated from `include_schemas` and
`include_name` configuration). This set is passed through to `inspect_functions(conn, schemas=...)` and
`inspect_triggers(conn, schemas=...)` for the current-state query.

For desired state, all provided DDL is canonicalized, but the resulting `CanonicalState` is filtered to only include
objects whose schema is in the `schemas` set before diffing. This ensures consistency with Alembic's schema inclusion
rules.

When `schemas` contains `None` (the default — meaning the default schema), the comparator resolves it to the
connection's current `search_path` default schema for filtering purposes.

### D6: Renderer output is `op.execute()` with raw DDL

Renderers return `op.execute()` calls containing the DDL string. No library imports are injected into migration files.

For functions (CREATE/REPLACE): `op.execute("CREATE OR REPLACE FUNCTION ...")`

For triggers (CREATE): `op.execute("CREATE TRIGGER ...")`

For triggers (REPLACE): two statements — `op.execute("DROP TRIGGER ...")` then `op.execute("CREATE TRIGGER ...")` —
because `CREATE OR REPLACE TRIGGER` requires PG 14+ and the DDL from `pg_get_triggerdef()` uses `CREATE TRIGGER`
(without `OR REPLACE`).

For drops: `op.execute("DROP FUNCTION schema.name(args)")` or `op.execute("DROP TRIGGER name ON schema.table")`

DDL strings containing single quotes are handled with Python triple-quoting or `textwrap.dedent` as needed.

### D7: Comparator pipeline flow

The comparator function registered at the `"schema"` dispatch level:

```
_compare_pg_objects(autogen_context, upgrade_ops, schemas)
  1. Read pg_functions / pg_triggers from autogen_context.opts
  2. If both empty → return CONTINUE (nothing to do)
  3. conn = autogen_context.connection
  4. current_functions = inspect_functions(conn, schemas)
     current_triggers = inspect_triggers(conn, schemas)
     current = CanonicalState(current_functions, current_triggers)
  5. desired = canonicalize(conn, pg_functions, pg_triggers)
  6. Filter desired to schemas in the schemas set
  7. result = diff(current, desired)
  8. Map FunctionOp/TriggerOp → MigrateOperation subclasses
  9. Append to upgrade_ops.ops in dependency order (D4)
  10. Return CONTINUE
```

## Risks / Trade-offs

**Canonicalization modifies the database during autogenerate** → The `canonicalize()` function executes DDL inside a
savepoint that is always rolled back. Users may not expect DDL execution during `--autogenerate`. Mitigation: the
savepoint/rollback is battle-tested from prior changes and documented.

**False-positive diffs from PL/pgSQL body whitespace** → PostgreSQL stores PL/pgSQL bodies verbatim, so reformatting a
function body in user code triggers a REPLACE op even when semantics are unchanged. Mitigation:
`CREATE OR REPLACE FUNCTION` is idempotent and cheap. A false positive produces a harmless no-op migration.

**Silent no-op if plugin not activated** → If the package is installed but `autogenerate_plugins` doesn't include
`"alembic-pg-autogen.*"`, the comparator never fires. Mitigation: document the activation step clearly. Consider adding
a diagnostic log message when the setup function runs.

**Trigger REPLACE requires DROP + CREATE** → Unlike functions, triggers don't have `CREATE OR REPLACE` syntax in the DDL
returned by `pg_get_triggerdef()`. A REPLACE is rendered as DROP then CREATE, which briefly removes the trigger.
Mitigation: this happens within a migration transaction (Alembic wraps migrations in transactions by default).

## Open Questions

- **`to_diff_tuple()` shape**: Alembic's `MigrateOperation` supports an optional `to_diff_tuple()` method for debugging
  and comparison. Need to decide the exact tuple shape for each op class. Likely
  `("create_function", schema, name, identity_args)` etc.
- **Empty migration suppression**: When no function/trigger changes are detected but table changes exist, no special
  handling is needed. When *only* function/trigger changes exist and no table changes, Alembic may still generate a
  non-empty migration. Need to verify this works correctly with `process_revision_directives` empty-migration checks.
