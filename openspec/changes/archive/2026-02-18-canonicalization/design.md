## Context

The catalog-inspector layer provides `inspect_functions()` and `inspect_triggers()`, which bulk-load the current
database state as `FunctionInfo` and `TriggerInfo` NamedTuples via `pg_get_functiondef()` / `pg_get_triggerdef()`. To
compare desired state against current state, we need to normalize user-provided DDL into the same canonical
representation.

User-written SQL diverges from PostgreSQL's canonical output in predictable ways: whitespace differences, implicit vs.
explicit defaults, unquoted vs. quoted identifiers, casing variations. Rather than parsing SQL to normalize it
ourselves, we let PostgreSQL do the work: execute the DDL inside a savepoint, read back canonical forms from the
catalog, then roll back the savepoint. The database is never modified.

## Goals / Non-Goals

**Goals:**

- Canonicalize user-provided function and trigger DDL strings through PostgreSQL, returning `FunctionInfo`/`TriggerInfo`
  instances identical in format to what `inspect_functions`/`inspect_triggers` produce.
- Handle triggers that reference newly-declared functions (both created in the same savepoint).
- Leave the database unchanged after canonicalization (savepoint rollback).
- Provide clear error reporting when DDL is invalid.

**Non-Goals:**

- No SQL parsing or template matching — PostgreSQL is the only canonicalizer.
- No higher-level declaration types (e.g., decomposed function builders) — users pass raw DDL strings.
- No diffing — the diff layer is a separate spec that consumes the canonical output from this layer.
- No PL/pgSQL body normalization — `pg_get_functiondef()` stores PL/pgSQL bodies verbatim; whitespace normalization
  belongs in the diff layer.
- No drop detection — tracking which objects are "managed" is a diff/comparator concern.
- No offline mode — a live PostgreSQL connection is required by design.

## Decisions

### Combined API instead of separate functions

The proposal mentions separate `canonicalize_functions()` and `canonicalize_triggers()` functions. During design, a
critical dependency emerged: triggers reference functions. If a user declares a new function and a trigger that calls
it, canonicalizing triggers in a separate savepoint would fail because the function was already rolled back.

**Decision**: A single `canonicalize()` function accepts both function DDL and trigger DDL, executing them in one
savepoint with functions created first. Convenience wrappers `canonicalize_functions()` and `canonicalize_triggers()`
are thin facades that call `canonicalize()` with only one DDL type populated.

**Alternative considered**: Require the caller to manage savepoints manually. Rejected — leaking transaction management
to the caller is error-prone and violates the "simple API" goal.

### Return full post-DDL catalog state

After executing DDL and reading back canonical forms, the function returns all functions/triggers visible in the target
schemas — not just the ones the user declared.

**Rationale**: Without parsing the DDL strings, we cannot determine the identity (schema, name, identity_args) of the
objects being created. The snapshot-diff approach (inspect before → apply DDL → inspect after → return only changed
entries) almost works but has a flaw: if a user's DDL canonicalizes to the *exact* same definition as the current
database state, the entry is filtered out, and the diff layer would incorrectly treat it as an unmanaged object.

Returning the full catalog state is simpler, correct, and flexible. The diff layer (next spec) already has the
pre-canonicalization state from its own `inspect_*` calls and can compute the delta.

**Alternative considered**: Snapshot diff (before/after savepoint). Rejected due to the unchanged-object edge case
described above, plus the cost of two extra inspect queries.

### Savepoint via SQLAlchemy `begin_nested()`

Canonicalization uses `conn.begin_nested()` (SAVEPOINT) rather than a full transaction rollback. This is essential
because during Alembic autogenerate, the comparator receives an existing connection with an active transaction. Rolling
back the full transaction would break Alembic's workflow.

### Execute DDL statements individually

Each DDL string is executed as a separate `conn.execute(text(ddl))` call rather than batching all statements into a
single multi-statement string.

**Rationale**: Individual execution provides clear error attribution — when a DDL statement fails, the exception
identifies exactly which statement is invalid. The overhead is negligible (one roundtrip per DDL statement, typically
\<1 second total for hundreds of statements).

**Alternative considered**: Batch execution (concatenate all DDL with semicolons). Rejected — loses error attribution
and complicates error recovery.

### Functions before triggers in execution order

Within the savepoint, all function DDL is executed before any trigger DDL. This satisfies the common dependency pattern
where triggers reference functions declared in the same batch (e.g., audit trigger + audit function).

The API enforces this by accepting function and trigger DDL as separate parameters. Users do not need to manually order
their DDL.

### Schemas parameter for scoping

The `canonicalize()` function accepts an optional `schemas` parameter, passed through to `inspect_functions()` and
`inspect_triggers()` for the post-DDL catalog read. This lets callers scope results to relevant schemas, reducing noise
from unrelated objects in other schemas.

### Return type: `CanonicalState` NamedTuple

```python
class CanonicalState(NamedTuple):
    functions: Sequence[FunctionInfo]
    triggers: Sequence[TriggerInfo]
```

Consistent with project preference for NamedTuples. Named fields are clearer than a raw
`tuple[Sequence[FunctionInfo], Sequence[TriggerInfo]]`.

## Risks / Trade-offs

**[Full catalog return includes unrelated objects]** → The diff layer must filter results by comparing against its known
desired set. This is straightforward (set operations on identity tuples) and keeps the canonicalization layer simple.

**[DDL execution side effects within savepoint]** → DDL statements could have side effects (e.g., `CREATE OR REPLACE`
overwrites an existing function's definition within the savepoint). The savepoint rollback undoes all changes, but if an
exception occurs before rollback, the savepoint remains open. Mitigation: use try/finally to guarantee rollback.

**[CREATE OR REPLACE TRIGGER requires PG >= 14]** → The project already targets PG >= 14, so this is not a concern. But
if users pass plain `CREATE TRIGGER` (without `OR REPLACE`), it will fail if the trigger already exists. This is correct
behavior — the error surfaces the user's DDL mistake.

**[PL/pgSQL bodies are not canonicalized]** → `pg_get_functiondef()` returns PL/pgSQL function bodies verbatim (stored
in `prosrc`). Two semantically identical functions with different whitespace will produce different canonical output.
Mitigation: deferred to the diff layer, which will normalize whitespace before comparing. False positives from this are
noisy but harmless (`CREATE OR REPLACE FUNCTION` is idempotent).
