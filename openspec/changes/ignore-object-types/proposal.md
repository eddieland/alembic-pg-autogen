## Why

Every object type the comparator knows about is managed unconditionally: whatever is found in the inspected schemas but
not declared is dropped. That is the right default for a declarative tool, but it forces an all-or-nothing adoption — a
project that declares `pg_functions` gets `DROP VIEW` for every view it has not yet moved into migrations. Today the
only way out is to declare every existing object of a type you are not ready to manage.

## What Changes

- Add an `IGNORED` sentinel (and its `Ignored` type alias) that can be passed as the value of `pg_functions`,
  `pg_triggers`, or `pg_views`
- An ignored object type is skipped end to end: its catalog is not inspected, its desired state is empty, and no
  operations are emitted for it — existing objects are left untouched
- `canonicalize()` accepts `IGNORED` for `function_ddl` / `view_ddl` / `trigger_ddl` and skips the corresponding
  readback query
- `canonicalize_functions()` / `canonicalize_views()` / `canonicalize_triggers()` now ignore the object types they do
  not return, saving two catalog queries per call
- Autogenerate short-circuits when every object type is ignored

`IGNORED` is deliberately distinct from an empty sequence: `pg_views=[]` still means "there should be no views" and
drops existing ones.

## Non-goals

- **Per-object ignore rules** — this is a coarse, per-type switch, not a name/pattern-based exclusion list
- **Changing the meaning of an absent config key** — an absent key still means "declare nothing of this type", and
  therefore still drops undeclared objects of that type
- **Ignoring an object type for drops only** — `IGNORED` disables creates and replaces for that type as well; a type is
  either managed or it is not

## Capabilities

### New Capabilities

_(none — the sentinel threads through existing layers)_

### Modified Capabilities

- `library-foundation`: New `alembic_pg_autogen.sentinels` module; new `IGNORED` and `Ignored` public exports
- `canonicalization`: `canonicalize()` accepts `IGNORED` per object type and skips that type's readback
- `alembic-compare`: `pg_functions` / `pg_triggers` / `pg_views` accept `IGNORED`, leaving that object type unmanaged

## Impact

- **Public API**: New exports — `IGNORED`, `Ignored`. Additive; existing configurations behave exactly as before
- **Performance**: One fewer catalog query per ignored type, both in inspection and in canonicalization readback
- **Docs**: README and quick-start gain an "Ignoring an object type" section
