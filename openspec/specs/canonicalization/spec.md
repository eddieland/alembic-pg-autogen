## ADDED Requirements

### Requirement: Combined canonicalization function

The module SHALL provide a `canonicalize` function that accepts sequences of function DDL strings, trigger DDL strings,
and view DDL strings, executes them inside a savepoint against a live PostgreSQL connection, reads back canonical forms
from the catalog using `inspect_functions`, `inspect_triggers`, and `inspect_views`, then rolls back the savepoint. It
SHALL return a `CanonicalState` NamedTuple containing the post-DDL catalog state.

#### Scenario: Canonicalize a single function

- **WHEN** `canonicalize(conn, function_ddl=["CREATE FUNCTION public.add(a int, b int) RETURNS int ..."])` is called
- **THEN** it returns a `CanonicalState` whose `functions` field contains `FunctionInfo` instances including the
  canonical form of the declared function as produced by `pg_get_functiondef()`
- **AND** `triggers` contains all triggers visible in the target schemas
- **AND** `views` contains all views visible in the target schemas

#### Scenario: Canonicalize a view

- **WHEN** `canonicalize(conn, view_ddl=["CREATE VIEW public.active_users AS SELECT id, name FROM users WHERE active"])`
  is called
- **THEN** it returns a `CanonicalState` whose `views` field contains `ViewInfo` instances including the canonical form
  of the declared view with the query body from `pg_get_viewdef()`

#### Scenario: Canonicalize functions, views, and triggers together

- **WHEN**
  `canonicalize(conn, function_ddl=["CREATE FUNCTION public.fn() ..."], view_ddl=["CREATE VIEW public.v AS SELECT public.fn()"], trigger_ddl=["CREATE TRIGGER trg AFTER INSERT ON public.t FOR EACH ROW EXECUTE FUNCTION public.fn()"])`
  is called
- **THEN** function DDL is executed first, then view DDL, then trigger DDL within the same savepoint
- **AND** the view can reference the just-created function without error
- **AND** the trigger can reference the just-created function without error
- **AND** the result contains canonical forms for the function, the view, and the trigger

#### Scenario: Canonicalize with no DDL

- **WHEN** `canonicalize(conn)` is called with no function, trigger, or view DDL (all default to empty)
- **THEN** it returns a `CanonicalState` containing the current catalog state (equivalent to calling
  `inspect_functions`, `inspect_triggers`, and `inspect_views` directly)

### Requirement: CanonicalState return type

The module SHALL provide a `CanonicalState` NamedTuple as the return type of `canonicalize`.

#### Scenario: CanonicalState fields

- **WHEN** a `CanonicalState` instance is created
- **THEN** it has the following fields:
  - `functions` (`Sequence[FunctionInfo]`): all functions visible in the target schemas after DDL execution
  - `triggers` (`Sequence[TriggerInfo]`): all triggers visible in the target schemas after DDL execution
  - `views` (`Sequence[ViewInfo]`): all views visible in the target schemas after DDL execution

### Requirement: Convenience wrapper canonicalize_functions

The module SHALL provide a `canonicalize_functions` function that accepts a sequence of function DDL strings and returns
a `Sequence[FunctionInfo]`. It SHALL delegate to `canonicalize` with only `function_ddl` populated.

#### Scenario: Canonicalize functions only

- **WHEN** `canonicalize_functions(conn, ["CREATE FUNCTION public.f1() ..."])` is called
- **THEN** it returns `Sequence[FunctionInfo]` containing canonical forms of all functions visible in the target schemas
  after executing the DDL

#### Scenario: Equivalent to canonicalize with function_ddl only

- **WHEN** `canonicalize_functions(conn, ddl, schemas=["public"])` is called
- **THEN** the result is identical to `canonicalize(conn, function_ddl=ddl, schemas=["public"]).functions`

### Requirement: Convenience wrapper canonicalize_triggers

The module SHALL provide a `canonicalize_triggers` function that accepts a sequence of trigger DDL strings and returns a
`Sequence[TriggerInfo]`. It SHALL delegate to `canonicalize` with only `trigger_ddl` populated.

#### Scenario: Canonicalize triggers only

- **WHEN** `canonicalize_triggers(conn, ["CREATE TRIGGER trg AFTER INSERT ON public.t ..."])` is called
- **THEN** it returns `Sequence[TriggerInfo]` containing canonical forms of all triggers visible in the target schemas
  after executing the DDL

#### Scenario: Trigger references pre-existing function

- **WHEN** the database already contains a function `public.existing_fn()` and
  `canonicalize_triggers(conn, ["CREATE TRIGGER trg ... EXECUTE FUNCTION public.existing_fn()"])` is called
- **THEN** it succeeds because the function exists in the database before the savepoint

### Requirement: Savepoint isolation

Canonicalization SHALL use `conn.begin_nested()` (SAVEPOINT) to execute DDL. The savepoint SHALL always be rolled back,
leaving the database unchanged regardless of success or failure.

#### Scenario: Database unchanged after successful canonicalization

- **WHEN** `canonicalize(conn, function_ddl=["CREATE FUNCTION public.new_fn() ..."])` succeeds
- **THEN** the function `public.new_fn()` does not exist in the database after the call returns
- **AND** all pre-existing functions and triggers are unchanged

#### Scenario: Database unchanged after failed canonicalization

- **WHEN** `canonicalize(conn, function_ddl=["CREATE FUNCTION invalid SQL ..."])` raises an exception
- **THEN** no database state has been modified
- **AND** the connection remains usable for subsequent operations

#### Scenario: Works within caller's existing transaction

- **WHEN** the caller has an active transaction on the connection (e.g., during Alembic autogenerate)
- **THEN** `canonicalize` uses a savepoint within that transaction
- **AND** it does not commit, rollback, or otherwise affect the outer transaction

### Requirement: Execution order within savepoint

Within the savepoint, `canonicalize` SHALL execute all function DDL statements first, then all view DDL statements, then
all trigger DDL statements. This ensures views can reference functions, and triggers can reference both functions and
views (INSTEAD OF triggers).

#### Scenario: Functions created before views and triggers

- **WHEN**
  `canonicalize(conn, function_ddl=["CREATE FUNCTION public.fn() ..."], view_ddl=["CREATE VIEW public.v AS SELECT public.fn()"], trigger_ddl=["CREATE TRIGGER trg ... EXECUTE FUNCTION public.fn()"])`
  is called
- **THEN** the function DDL executes first
- **AND** the view DDL executes second, referencing the just-created function
- **AND** the trigger DDL executes third

#### Scenario: Individual DDL execution

- **WHEN** multiple DDL strings are provided in `view_ddl`
- **THEN** each DDL string is executed as a separate `conn.execute(text(ddl))` call (not batched into a single
  multi-statement string)

### Requirement: Full post-DDL catalog state

After executing DDL within the savepoint, `canonicalize` SHALL read back the full catalog state using
`inspect_functions`, `inspect_triggers`, and `inspect_views`. The result includes all functions, triggers, and views
visible in the target schemas — not only those created by the provided DDL.

#### Scenario: Pre-existing views included in result

- **WHEN** the database contains views `public.existing_view_a` and `public.existing_view_b` and
  `canonicalize(conn, view_ddl=["CREATE VIEW public.new_view AS ..."], schemas=["public"])` is called
- **THEN** the result's `views` field contains `ViewInfo` instances for `existing_view_a`, `existing_view_b`, AND
  `new_view`

#### Scenario: CREATE OR REPLACE updates canonical form

- **WHEN** the database contains `public.my_view` with one definition and
  `canonicalize(conn, view_ddl=["CREATE OR REPLACE VIEW public.my_view AS SELECT ... <different query> ..."])` is called
- **THEN** the result's `views` field contains the canonical form of the new definition (not the original)

### Requirement: Schema scoping

`canonicalize` SHALL accept an optional `schemas` parameter. When provided, it is passed through to `inspect_functions`
and `inspect_triggers` to scope the post-DDL catalog read. When omitted, all user schemas are included (excluding
`pg_catalog` and `information_schema`).

#### Scenario: Scoped to specific schemas

- **WHEN** `canonicalize(conn, function_ddl=[...], schemas=["public", "audit"])` is called
- **THEN** the result contains only functions and triggers in the `public` and `audit` schemas

#### Scenario: Default includes all user schemas

- **WHEN** `canonicalize(conn, function_ddl=[...])` is called without specifying `schemas`
- **THEN** the result contains functions and triggers from all schemas except `pg_catalog` and `information_schema`

### Requirement: SQLAlchemy connection as input

`canonicalize`, `canonicalize_functions`, and `canonicalize_triggers` SHALL accept a SQLAlchemy `Connection` object as
their first argument. They SHALL NOT create connections, engines, or manage outer transactions.

#### Scenario: Uses provided connection

- **WHEN** any canonicalize function is called with a SQLAlchemy `Connection`
- **THEN** it executes DDL and catalog queries using that connection
- **AND** it does not create any new connections or engines

### Requirement: Error handling for invalid DDL

When a DDL statement fails to execute (syntax error, missing dependency, invalid SQL), `canonicalize` SHALL roll back
the savepoint and propagate the exception. The exception SHALL identify which DDL statement failed.

#### Scenario: Syntax error in function DDL

- **WHEN** `canonicalize(conn, function_ddl=["CREATE FUNCTION invalid sql garbage"])` is called
- **THEN** it raises an exception indicating the DDL failure
- **AND** the savepoint is rolled back
- **AND** the connection remains usable

#### Scenario: Missing dependency in trigger DDL

- **WHEN** `canonicalize(conn, trigger_ddl=["CREATE TRIGGER trg ... EXECUTE FUNCTION public.nonexistent_fn()"])` is
  called and `public.nonexistent_fn()` does not exist
- **THEN** it raises an exception indicating the missing function
- **AND** the savepoint is rolled back

### Requirement: DDL strings use sqlalchemy.text()

All DDL strings SHALL be executed using `conn.execute(text(ddl))`.

Before execution, DDL strings are transformed by `postgast.ensure_or_replace()` to inject `OR REPLACE` into `CREATE`
statements (see [ddl-parsing spec](../ddl-parsing/spec.md)). This AST-level rewrite is the only transformation applied;
the resulting SQL is then passed to PostgreSQL via `sqlalchemy.text()`.

#### Scenario: DDL transformed and executed

- **WHEN** a DDL string is provided to `canonicalize`
- **THEN** it is first passed through `postgast.ensure_or_replace()` to ensure `OR REPLACE` is present
- **AND** the result is wrapped in `sqlalchemy.text()` and executed

### Requirement: Convenience wrapper canonicalize_views

The module SHALL provide a `canonicalize_views` function that accepts a sequence of view DDL strings and returns a
`Sequence[ViewInfo]`. It SHALL delegate to `canonicalize` with only `view_ddl` populated.

#### Scenario: Canonicalize views only

- **WHEN** `canonicalize_views(conn, ["CREATE VIEW public.v1 AS SELECT ..."])` is called
- **THEN** it returns `Sequence[ViewInfo]` containing canonical forms of all views visible in the target schemas after
  executing the DDL

#### Scenario: Equivalent to canonicalize with view_ddl only

- **WHEN** `canonicalize_views(conn, ddl, schemas=["public"])` is called
- **THEN** the result is identical to `canonicalize(conn, view_ddl=ddl, schemas=["public"]).views`

### Requirement: Ignoring object types during canonicalization

`canonicalize()` SHALL accept `IGNORED` in place of a DDL sequence for `function_ddl`, `view_ddl`, or `trigger_ddl`. For
an ignored object type it SHALL execute no DDL and SHALL NOT query that type's catalog during readback, leaving the
corresponding `CanonicalState` field empty.

#### Scenario: Ignored object type is not read back

- **WHEN** `canonicalize(conn, function_ddl=[...], view_ddl=IGNORED, trigger_ddl=IGNORED)` is called against a database
  that already contains views and triggers
- **THEN** the returned `CanonicalState` contains the canonical form of the declared functions
- **AND** its `views` and `triggers` fields are empty
- **AND** no `inspect_views` or `inspect_triggers` query is issued

#### Scenario: Empty sequence still reads back

- **WHEN** `canonicalize(conn, view_ddl=())` is called against a database containing views
- **THEN** the returned `CanonicalState.views` contains the existing views
- **AND** the behavior is unchanged from before the sentinel existed

#### Scenario: Warnings account for ignored types

- **WHEN** an object type is ignored
- **THEN** no "canonicalization produced no <type>" warning is logged for it

### Requirement: Convenience wrappers ignore unrelated object types

`canonicalize_functions()`, `canonicalize_views()`, and `canonicalize_triggers()` SHALL ignore the two object types they
do not return, so each wrapper issues a single catalog readback query.

#### Scenario: Single readback per wrapper

- **WHEN** `canonicalize_views(conn, ddl)` is called
- **THEN** only the view catalog is read back
- **AND** the returned `ViewInfo` sequence is identical to what the wrapper returned before the sentinel existed

### Requirement: Canonicalize check constraint expressions

The module SHALL provide a `canonicalize_check_constraints(conn, *, schema, table_name, expressions)` function that
normalizes desired `CHECK` expressions by round-tripping them through PostgreSQL and returns a mapping of constraint
name to normalized expression.

#### Scenario: Round-trip produces the catalog's form

- **WHEN** `canonicalize_check_constraints` is called with `{"ck_orders_amount": "amount >= 0"}` for a table whose
  `amount` column is `numeric`
- **THEN** the returned expression equals what `inspect_check_constraints` reports for an existing constraint written
  the same way (e.g. `amount >= 0::numeric`)

#### Scenario: Equivalent expressions normalize identically

- **WHEN** the database holds `CHECK ( (amount)  >=  (0) )` and the desired expression is `amount >= 0`
- **THEN** both normalize to the same string
- **AND** comparing them yields no difference

#### Scenario: Rewritten constructs normalize identically

- **WHEN** the database holds `CHECK (status IN ('new','done'))` and the desired expression is
  `status in ( 'new' , 'done' )`
- **THEN** both normalize to PostgreSQL's `= ANY (ARRAY[...])` form and compare equal

#### Scenario: Changed expressions differ

- **WHEN** the database holds `CHECK (amount >= 0)` and the desired expression is `amount > 0`
- **THEN** the normalized expressions differ

#### Scenario: Probes are added NOT VALID

- **WHEN** an expression is normalized against a table containing rows that violate it
- **THEN** normalization still succeeds, because the probe constraint is added `NOT VALID` and no row is validated

#### Scenario: The database is left unchanged

- **WHEN** `canonicalize_check_constraints` returns
- **THEN** the savepoint has been rolled back
- **AND** no probe constraint remains on the table

#### Scenario: Unusable expressions are omitted, not raised

- **WHEN** an expression references a column that does not exist, or the table cannot be altered
- **THEN** the affected constraint name is absent from the returned mapping
- **AND** a warning is logged
- **AND** no exception propagates to the caller

#### Scenario: Empty input short-circuits

- **WHEN** `expressions` is empty
- **THEN** an empty mapping is returned without opening a savepoint or issuing SQL

#### Scenario: Unqualified tables resolve through search_path

- **WHEN** `schema` is `None`
- **THEN** the table is resolved through the connection's `search_path`
