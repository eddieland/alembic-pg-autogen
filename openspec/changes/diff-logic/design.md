## Context

The catalog inspector provides `inspect_functions()` and `inspect_triggers()` for current DB state. The canonicalization
layer provides `canonicalize()` which round-trips desired DDL through PostgreSQL and returns `CanonicalState` — the full
post-DDL catalog snapshot using the same `FunctionInfo`/`TriggerInfo` types. Both layers produce sequences of
NamedTuples with identity fields and canonical `definition` strings.

What's missing is the comparison: given two sets of `FunctionInfo`/`TriggerInfo` (current vs. desired), determine which
objects need to be created, replaced, or dropped. This is the diff layer.

## Goals / Non-Goals

**Goals:**

- Compare two sequences of `FunctionInfo` (or `TriggerInfo`) by identity, producing typed diff operations.
- Detect three cases: create (desired only), replace (both present, definitions differ), drop (current only).
- Provide a single entry-point function that diffs both functions and triggers in one call.
- Return operation types that carry full `FunctionInfo`/`TriggerInfo` so downstream Alembic ops/renderers can emit DDL
  without re-querying.
- Be fully unit-testable without a database — operates on in-memory NamedTuples.

**Non-Goals:**

- No Alembic integration — `MigrateOperation` subclasses, comparator hooks, and renderers belong in a later spec.
- No dependency ordering — ordering create/drop operations (e.g., functions before triggers that reference them) is an
  Alembic-integration concern.
- No SQL parsing or transformation — definitions are compared as strings after canonicalization.
- No "managed object" registry — the caller decides what goes into the desired set. The diff layer compares whatever it
  receives.
- No whitespace normalization of PL/pgSQL bodies — this was considered but deferred (see Risks).

## Decisions

### Single `diff()` function with typed return

A single `diff(current, desired)` function accepts a `CanonicalState` for each side and returns a `DiffResult`
NamedTuple containing separate sequences for function ops and trigger ops.

```python
class DiffResult(NamedTuple):
    function_ops: Sequence[FunctionOp]
    trigger_ops: Sequence[TriggerOp]
```

**Rationale**: The caller (future Alembic comparator) will have both `CanonicalState` objects readily available — one
from `inspect_*` and one from `canonicalize()`. A single call keeps the API minimal. Separating function and trigger ops
in the return makes it easy for the Alembic layer to order them (functions before triggers for create, reverse for
drop).

**Alternative considered**: Separate `diff_functions()` and `diff_triggers()` functions. Rejected — the combined API
mirrors the existing `canonicalize()` pattern and there's no scenario where only one is needed.

### Operation types as NamedTuples with an action enum

Each operation is a NamedTuple carrying an action tag and the relevant info object(s):

```python
class Action(enum.Enum):
    CREATE = "create"
    REPLACE = "replace"
    DROP = "drop"


class FunctionOp(NamedTuple):
    action: Action
    current: FunctionInfo | None  # present for REPLACE and DROP
    desired: FunctionInfo | None  # present for CREATE and REPLACE


class TriggerOp(NamedTuple):
    action: Action
    current: TriggerInfo | None
    desired: TriggerInfo | None
```

**Rationale**: Carrying both `current` and `desired` for replace operations lets renderers produce diff comments, log
what changed, or reference the old definition if needed. For create, only `desired` is populated; for drop, only
`current`. The `Action` enum enables exhaustive pattern matching in downstream code.

**Alternative considered**: Separate classes per action (`CreateFunction`, ReplaceFunction`, `DropFunction\`, etc.).
More types with less uniformity — harder to iterate over generically. The enum approach is simpler and extends to new
object types without tripling the class count.

**Alternative considered**: A single generic `DiffOp[T]` parameterized on the info type. Attractive in theory, but
BasedPyright struggles with generic NamedTuples, and the two concrete types (`FunctionOp`, `TriggerOp`) are clearer for
the Alembic layer which needs to dispatch differently on object type anyway.

### Identity key extraction

Functions are identified by `(schema, name, identity_args)`. Triggers by `(schema, table_name, trigger_name)`. These are
already the first three fields of their respective NamedTuples, so the identity key is simply `info[:3]` — a tuple
slice. No extra key-function abstraction is needed.

**Rationale**: Both `FunctionInfo` and `TriggerInfo` are NamedTuples with identity fields as their first three fields
followed by `definition` as the fourth. Slicing to `[:3]` is correct, readable, and type-safe.

### String equality for definition comparison

Objects with matching identity keys are compared by `current.definition == desired.definition`. No normalization,
stripping, or transformation.

**Rationale**: Both sides have already been canonicalized by PostgreSQL via `pg_get_functiondef()` /
`pg_get_triggerdef()`. The entire purpose of the canonicalization layer is to make string comparison reliable. Adding
normalization here would undermine that design.

**Exception — PL/pgSQL bodies**: `pg_get_functiondef()` returns PL/pgSQL function bodies verbatim (whitespace, comments,
formatting preserved). Two semantically identical bodies with different whitespace will produce a replace op. This is a
known false-positive case accepted in the canonicalization design doc. See Risks for discussion.

### Deterministic operation ordering

Within `DiffResult.function_ops` and `DiffResult.trigger_ops`, operations are ordered by identity key (lexicographic on
the identity tuple). This makes output deterministic and diff-friendly in tests.

**Alternative considered**: Order by action type (all creates, then replaces, then drops). Rejected — the Alembic layer
will need to reorder anyway (creates first in upgrade, drops first in downgrade), so alphabetical by identity is the
simplest stable sort for the diff layer.

### No "unchanged" entries in output

Objects that exist in both current and desired with identical definitions are not included in the diff result. The diff
layer only emits operations — things that need to change.

**Rationale**: Including unchanged entries would bloat the output and force downstream code to filter. The caller
already has both snapshots if it needs the full picture.

## Risks / Trade-offs

**[PL/pgSQL whitespace false positives]** → Functions with `LANGUAGE plpgsql` store bodies verbatim. A trivial
whitespace change (e.g., added blank line) produces a replace op even though behavior is unchanged. Mitigation: accepted
as noise. `CREATE OR REPLACE FUNCTION` is idempotent and cheap. A future enhancement could add optional whitespace
normalization for PL/pgSQL bodies, but this is explicitly out of scope for this change. The risk is noisy migrations,
not incorrect ones.

**[Desired set must be complete]** → The diff layer treats the desired set as the full declaration of what should exist.
If the caller forgets to include an existing managed function in the desired set, the diff will produce a drop
operation. This is by design — the diff layer does not track history or maintain a registry. The Alembic comparator is
responsible for assembling the correct desired set.

**[No dependency ordering]** → The diff result is a flat list with no dependency information. If a trigger references a
function, and both are being created, the caller must ensure the function is created first. Mitigation: the Alembic
integration layer (future spec) will handle ordering. Within the diff layer, this is not a concern.

**[Identity collision in desired set]** → If two desired entries have the same identity key, the later one silently wins
(dict insertion order). This is unlikely in practice (it means the user declared the same function twice with different
bodies). No special handling — last-write-wins is simple and predictable.
