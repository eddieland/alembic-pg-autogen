## Context

alembic-pg-autogen needs to inspect a live PostgreSQL database to discover what functions and triggers currently exist.
This is the first layer of a pipeline: inspect → canonicalize → diff → generate migration. The proposal establishes that
we query PostgreSQL's system catalog directly rather than simulating CREATE/DROP cycles like alembic_utils.

The project already has the module skeleton (`_compare.py`, `_ops.py`, `_render.py`) and integration test infrastructure
with testcontainers from the `library-foundation` change. This change adds the `_inspect` module.

## Goals / Non-Goals

**Goals:**

- Bulk-load all user-defined functions and triggers from PostgreSQL (>= 14) in minimal roundtrips.
- Return structured Python objects with identity fields separated from definitions.
- Use `pg_get_functiondef()` and `pg_get_triggerdef()` for canonical DDL.
- Accept a SQLAlchemy `Connection` — no connection management, no engine creation.
- Support schema filtering (which schemas to inspect).

**Non-Goals:**

- Canonicalizing desired (user-declared) state — that's a separate change.
- Diffing or comparing database state against desired state.
- Alembic hook integration (comparators, operations, renderers).
- Views, policies, indexes, or any object type beyond functions and triggers.
- Offline/`--sql` mode support (catalog inspection is inherently online).

## Decisions

### 1. Single module `_inspect.py` with two public functions

Provide `inspect_functions(conn, schemas)` and `inspect_triggers(conn, schemas)`. Each executes one catalog query and
returns a sequence of dataclass instances.

**Why not one function returning both?** Functions and triggers have different identity models and callers may need only
one type. Keeping them separate is simpler and avoids coupling.

**Why not a class?** There's no state to manage — these are pure query-and-transform operations. Functions are simpler
and more composable.

### 2. Dataclasses for return types: `FunctionInfo` and `TriggerInfo`

```
FunctionInfo:
    schema: str           # pg_namespace.nspname
    name: str             # pg_proc.proname
    identity_args: str    # argument types only (for overload matching)
    definition: str       # full DDL from pg_get_functiondef()

TriggerInfo:
    schema: str           # table's schema
    table_name: str       # pg_class.relname
    trigger_name: str     # pg_trigger.tgname
    definition: str       # full DDL from pg_get_triggerdef()
```

Identity fields are separated from the definition so callers can match objects without parsing DDL. The `definition`
field holds the complete canonical DDL from `pg_get_*` functions — no transformation applied.

**Alternative considered**: Named tuples. Rejected because dataclasses support slots, type checking, and future field
addition without breaking callers.

### 3. Identity model

- **Functions**: `(schema, name, identity_args)`. PostgreSQL allows function overloading — two functions can share a
  name if their argument types differ. Argument names and defaults are not part of identity. We extract `identity_args`
  from `pg_proc.proargtypes` joined with `pg_type` to get type names.
- **Triggers**: `(schema, table_name, trigger_name)`. Trigger names are unique per table but not globally.

### 4. Schema filtering defaults to user schemas

Both functions accept an optional `schemas` parameter (a sequence of schema names). When omitted, they query all schemas
except `pg_catalog` and `information_schema`. This is the common case — users want their own objects, not system
internals.

### 5. Raw SQL via `sqlalchemy.text()` for catalog queries

Catalog queries use `connection.execute(text(...))`. No ORM, no reflection — these are fixed queries against stable
system catalog tables.

**Why not SQLAlchemy's `inspect()`?** SQLAlchemy's inspector doesn't expose functions or triggers. We're querying system
catalogs that SQLAlchemy doesn't abstract.

### 6. Function filtering: `prokind IN ('f', 'p')`

Exclude aggregates (`prokind = 'a'`) and window functions (`prokind = 'w'`) because `pg_get_functiondef()` errors on
them. We only care about regular functions and procedures.

### 7. Trigger filtering: `NOT tgisinternal`

Internal triggers (created by constraints) are excluded. Only user-defined triggers are relevant for autogenerate.

## Risks / Trade-offs

**[PL/pgSQL body whitespace]** `pg_get_functiondef()` returns the function body (`prosrc`) verbatim for PL/pgSQL
functions — whitespace and comments are preserved as written, not canonicalized. This is a known PostgreSQL behavior. →
Accepted for now. The canonicalization layer (future change) will handle normalization. The inspector just returns what
PostgreSQL gives us.

**\[`pg_get_functiondef()` output varies across PG major versions\]** The deparser in `ruleutils.c` may format DDL
differently between PostgreSQL 14, 15, 16, and 17. → Mitigated by targeting PG >= 14 and using the non-pretty variant.
Cross-version comparison is not a goal — the inspector runs against one database at a time.

**[Large schema with many functions]** A single query returning all functions could be large. → Acceptable. PostgreSQL
handles bulk catalog queries efficiently. Even thousands of functions produce manageable result sets — the DDL text is
the main payload, and individual function bodies are rarely more than a few KB.

**\[Function identity via `proargtypes`\]** Extracting human-readable argument type strings from `proargtypes` OID array
requires joining `pg_type`. If types are in non-default schemas, we need schema-qualified names. → Use `format_type()`
which handles schema qualification and array notation correctly.

## Open Questions

- **Exact `identity_args` format**: Should we use `format_type()` output directly (e.g., `integer, text[]`) or normalize
  further? Leaning toward `format_type()` as-is — it's what PostgreSQL considers canonical.
- **Extension-owned objects**: Should we filter out functions/triggers owned by extensions (`pg_depend.deptype = 'e'`)?
  Probably yes in the long run, but may be premature for this change.
