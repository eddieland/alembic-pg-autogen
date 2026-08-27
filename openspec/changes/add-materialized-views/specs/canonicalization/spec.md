## ADDED Requirements

### Requirement: Materialized view canonicalization

`canonicalize()` SHALL accept a `matview_ddl` parameter holding `CREATE MATERIALIZED VIEW` DDL strings, or `IGNORED`.
Because PostgreSQL has no `CREATE OR REPLACE MATERIALIZED VIEW`, canonicalization SHALL first execute
`DROP MATERIALIZED VIEW IF EXISTS <schema>.<name> CASCADE` inside the savepoint for each declared materialized view.
Because `CREATE MATERIALIZED VIEW` runs its query at creation time, canonicalization SHALL force `WITH NO DATA` on each
statement before execution, so autogenerate never executes the view query. Read-back SHALL use
`inspect_materialized_views`, and the result SHALL populate a `materialized_views` field on `CanonicalState`.

#### Scenario: Canonicalize a materialized view

- **WHEN** `canonicalize(conn, matview_ddl=["CREATE MATERIALIZED VIEW public.mv AS SELECT id FROM t"])` is called
- **THEN** the returned `CanonicalState.materialized_views` contains a `MaterializedViewInfo` with the canonical query
  body from `pg_get_viewdef()`

#### Scenario: Existing materialized view is dropped first inside the savepoint

- **WHEN** the database already contains `public.mv` and `canonicalize` receives DDL declaring `public.mv` with a
  different query
- **THEN** the savepoint drops the existing object before executing the declared DDL
- **AND** the read-back reflects the declared query, not the pre-existing one
- **AND** `CREATE MATERIALIZED VIEW IF NOT EXISTS` is not used, because it silently keeps the old definition

#### Scenario: The view query is not executed

- **WHEN** the declared query calls a function that raises when evaluated
- **THEN** canonicalization succeeds, because the statement executes as `WITH NO DATA`
- **AND** the canonical definition equals the one a `WITH DATA` creation would produce

#### Scenario: Rollback restores the pre-existing object completely

- **WHEN** the database contains a populated `public.mv` with a unique index, and `canonicalize` runs with `matview_ddl`
  declaring `public.mv`
- **THEN** after the savepoint rollback `public.mv` exists with its original definition
- **AND** its index still exists
- **AND** `pg_class.relispopulated` is still true

#### Scenario: Cascade effects stay inside the savepoint

- **WHEN** an undeclared regular view depends on a declared materialized view
- **THEN** the drop-first step uses `CASCADE`, so canonicalization does not fail
- **AND** the dependent view exists again after the savepoint rollback

#### Scenario: Ignored materialized views are not read back

- **WHEN** `canonicalize(conn, function_ddl=[...], matview_ddl=IGNORED)` is called against a database containing
  materialized views
- **THEN** no materialized view DDL executes and no drop-first statements execute
- **AND** the returned `materialized_views` field is empty
- **AND** no `inspect_materialized_views` query is issued

### Requirement: Convenience wrapper canonicalize_materialized_views

The module SHALL provide a `canonicalize_materialized_views` function that accepts a sequence of
`CREATE MATERIALIZED VIEW` DDL strings and returns a `Sequence[MaterializedViewInfo]`. It SHALL delegate to
`canonicalize` with only `matview_ddl` populated and the other object types ignored.

#### Scenario: Canonicalize materialized views only

- **WHEN** `canonicalize_materialized_views(conn, ["CREATE MATERIALIZED VIEW public.mv AS ..."])` is called
- **THEN** it returns the canonical forms of all materialized views visible in the target schemas after executing the
  DDL
- **AND** only the materialized view catalog is read back

## MODIFIED Requirements

### Requirement: CanonicalState return type

The module SHALL provide a `CanonicalState` NamedTuple as the return type of `canonicalize`.

#### Scenario: CanonicalState fields

- **WHEN** a `CanonicalState` instance is created
- **THEN** it has the following fields:
  - `functions` (`Sequence[FunctionInfo]`): all functions visible in the target schemas after DDL execution
  - `triggers` (`Sequence[TriggerInfo]`): all triggers visible in the target schemas after DDL execution
  - `views` (`Sequence[ViewInfo]`): all views visible in the target schemas after DDL execution
  - `materialized_views` (`Sequence[MaterializedViewInfo]`): all materialized views visible in the target schemas after
    DDL execution
- **AND** `materialized_views` is appended after `views`, so existing positional access is unaffected

### Requirement: Execution order within savepoint

Within the savepoint, `canonicalize` SHALL execute DDL in this order: drop-first statements for declared materialized
views, then function DDL, then view DDL, then materialized view DDL, then trigger DDL. Views can reference functions.
Materialized views can reference functions and regular views. Triggers can reference functions and views (INSTEAD OF
triggers). PostgreSQL rejects triggers on materialized views, so trigger DDL cannot depend on the materialized view
step.

#### Scenario: Functions created before views and triggers

- **WHEN**
  `canonicalize(conn, function_ddl=["CREATE FUNCTION public.fn() ..."], view_ddl=["CREATE VIEW public.v AS SELECT public.fn()"], trigger_ddl=["CREATE TRIGGER trg ... EXECUTE FUNCTION public.fn()"])`
  is called
- **THEN** the function DDL executes first
- **AND** the view DDL executes second, referencing the just-created function
- **AND** the trigger DDL executes third

#### Scenario: Materialized views execute after functions and views

- **WHEN** `matview_ddl` declares a materialized view whose query references a declared function and a declared regular
  view
- **THEN** the materialized view DDL executes after both, so the references resolve

#### Scenario: A regular view over a materialized view is unsupported

- **WHEN** `view_ddl` declares a regular view whose query reads a declared materialized view that does not exist in the
  database yet
- **THEN** canonicalization raises the PostgreSQL error, because view DDL executes before materialized view DDL
- **AND** this ordering limitation is documented

#### Scenario: Individual DDL execution

- **WHEN** multiple DDL strings are provided in any of `function_ddl`, `view_ddl`, `matview_ddl`, or `trigger_ddl`
- **THEN** each DDL string is executed as a separate `conn.execute(text(ddl))` call (not batched into a single
  multi-statement string)

### Requirement: DDL strings use sqlalchemy.text()

All DDL strings SHALL be executed using `conn.execute(text(ddl))`.

Before execution, function, view, and trigger DDL strings are transformed by `postgast.ensure_or_replace()` to inject
`OR REPLACE` into `CREATE` statements (see [ddl-parsing spec](../ddl-parsing/spec.md)). Materialized view DDL is
transformed differently: `postgast.ensure_or_replace()` passes it through unchanged, so `canonicalize` instead sets
`skip_data` on the parsed statement and re-emits it with `postgast.deparse()` to force `WITH NO DATA`. These AST-level
rewrites are the only transformations applied; the resulting SQL is then passed to PostgreSQL via `sqlalchemy.text()`.

#### Scenario: DDL transformed and executed

- **WHEN** a function, view, or trigger DDL string is provided to `canonicalize`
- **THEN** it is first passed through `postgast.ensure_or_replace()` to ensure `OR REPLACE` is present
- **AND** the result is wrapped in `sqlalchemy.text()` and executed

#### Scenario: Materialized view DDL forced to WITH NO DATA

- **WHEN** a `matview_ddl` string is provided without a data clause, or with `WITH DATA`
- **THEN** the statement executed inside the savepoint carries `WITH NO DATA`
- **AND** the user's declared string is not modified outside the savepoint execution
