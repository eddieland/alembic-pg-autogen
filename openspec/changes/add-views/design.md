## Context

The library currently supports two PostgreSQL object types — functions and triggers — across a four-layer architecture:
inspection (`_inspect.py`), canonicalization (`_canonicalize.py`), diffing (`_diff.py`), and Alembic integration
(`_compare.py`, `_ops.py`, `_render.py`). All layers assume a convention where each catalog type is a `NamedTuple` with
identity fields followed by a `definition` field, and `_diff_items` uses `item[:3]` to extract the identity key.

Views are the first object type where `pg_get_viewdef()` returns only the query body rather than complete DDL, and where
the identity key is shorter than 3 fields. These differences require two small architectural adjustments.

## Goals / Non-Goals

**Goals:**

- Add full view support (inspect, canonicalize, diff, compare, ops, render) following existing patterns
- Establish the "reconstructed DDL" pattern for future object types without complete `pg_get_*()` functions
- Refine `_diff_items` to work with any identity key length, not just 3

**Non-Goals:**

- Materialized views (`relkind = 'm'`) — different lifecycle, separate change
- View dependency ordering — no graph analysis; PostgreSQL errors on invalid ordering
- `WITH CHECK OPTION` / `SECURITY BARRIER` — not included in `pg_get_viewdef()` output; out of scope
- Column aliases in `CREATE VIEW v (a, b) AS ...` — stored in `pg_attribute`, not in the view query body
- `INSTEAD OF` triggers on views — existing trigger support already handles them once the view exists

## Decisions

### D1: ViewInfo shape — 3-field NamedTuple

`ViewInfo(schema, name, definition)` — two identity fields and one definition field.

This is shorter than `FunctionInfo` (4 fields) and `TriggerInfo` (4 fields) because views have no overloading and no
table binding. The identity is simply `(schema, name)`.

**Alternative considered:** Adding a placeholder third identity field to match the current convention. Rejected — it
adds meaningless data and hides the real structure from readers.

### D2: Reconstruct full DDL in the SQL query

`pg_get_viewdef(oid, true)` returns only the SELECT body. The inspection query reconstructs full DDL in SQL:

```sql
SELECT
    n.nspname AS schema,
    c.relname AS name,
    'CREATE OR REPLACE VIEW ' || quote_ident(n.nspname) || '.' || quote_ident(c.relname)
        || ' AS' || chr(10) || pg_get_viewdef(c.oid, true) AS definition
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'v'
  AND ({schema_filter})
ORDER BY n.nspname, c.relname
```

This way `ViewInfo.definition` contains the full `CREATE OR REPLACE VIEW schema.name AS\n<query>` — consistent with how
`FunctionInfo.definition` contains the full `CREATE OR REPLACE FUNCTION ...` DDL. Both inspection and canonicalization
use the same query, guaranteeing string-equal definitions for identical views.

**Alternative considered:** Storing only the query body and having renderers reconstruct the DDL. Rejected — it pushes
view-specific logic into the rendering layer and breaks the "definition is the complete DDL" contract.

**Why `quote_ident()`:** Unlike `pg_get_functiondef()` which handles quoting internally, we construct the DDL preamble
ourselves. Using `quote_ident()` ensures identifiers with special characters are properly quoted — matching what
PostgreSQL would produce.

### D3: Generalize `_diff_items` identity key — `item[:-1]` instead of `item[:3]`

The current `_diff_items` uses `item[:3]` for the identity key. This works because both `FunctionInfo` and `TriggerInfo`
have exactly 3 identity fields followed by `definition`. With `ViewInfo` having only 2 identity fields, `item[:3]` would
include the definition.

Change to `item[:-1]` (all fields except the last). This works for all three types:

- `FunctionInfo(schema, name, identity_args, definition)` → identity = `(schema, name, identity_args)` ✓
- `TriggerInfo(schema, table_name, trigger_name, definition)` → identity = `(schema, table_name, trigger_name)` ✓
- `ViewInfo(schema, name, definition)` → identity = `(schema, name)` ✓

Convention: every Info NamedTuple has identity fields first and `definition` last.

**Alternative considered:** Adding an `identity_fields` parameter to `_diff_items`. Rejected — `item[:-1]` is simpler
and self-documents the convention ("definition is always the last field").

### D4: Canonicalization execution order — functions → views → triggers

Within the savepoint, DDL executes in this order:

1. **Functions** — standalone, no dependencies on other managed objects
1. **Views** — may reference functions (e.g., `SELECT my_func(x) FROM t`)
1. **Triggers** — may reference functions; INSTEAD OF triggers may be ON views

This is the topological order: functions are leaves, views depend on functions, triggers depend on both.

### D5: Operation ordering — 6 groups

Extends the current 4-group ordering to 6:

1. Drop triggers (frees views and functions)
1. Drop views (frees functions)
1. Drop functions
1. Create/replace functions (must exist before views reference them)
1. Create/replace views (must exist before INSTEAD OF triggers reference them)
1. Create/replace triggers

Downgrade reversal produces the same valid ordering via `MigrateOperation.reverse()`.

### D6: CanonicalState and DiffResult — append new fields

New fields are appended to preserve positional compatibility for the common `state.functions` / `state.triggers`
patterns:

- `CanonicalState(functions, triggers, views)` — `views` appended after `triggers`
- `DiffResult(function_ops, trigger_ops, view_ops)` — `view_ops` appended after `trigger_ops`

Positional destructuring like `funcs, trigs = state` would break, but this is unlikely given the project's age.

### D7: View identity regex

A new `_VIEW_RE` regex for extracting `(schema, name)` from `CREATE [OR REPLACE] VIEW` DDL:

```python
_VIEW_RE = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+(?:(\w+)\.)?(\w+)", re.IGNORECASE)
```

Same style as `_FUNCTION_RE`. Simpler because there's no trigger-like `ON table` clause.

## Risks / Trade-offs

**\[`pg_get_viewdef()` omits view options\]** → Views created with `WITH CHECK OPTION`, `SECURITY BARRIER`, or column
aliases will not round-trip these attributes through canonicalization. The diff will not detect changes to these
properties. **Mitigation:** Document as a known limitation. These options are uncommon in practice and can be added
later by reading `pg_class.reloptions` and `pg_attribute`.

**\[`CanonicalState` field addition is breaking\]** → Any code that unpacks `CanonicalState` positionally breaks.
**Mitigation:** Acceptable at this project stage. The only consumer is internal (`_compare.py`), and keyword access
(`state.functions`) is unaffected.

**\[`item[:-1]` convention is implicit\]** → The identity-key convention relies on field ordering rather than an
explicit contract. **Mitigation:** Document in docstrings. The alternative (a protocol or method) adds abstraction
without practical benefit — all three types follow the pattern.

## Open Questions

_(none — all significant decisions resolved above)_
