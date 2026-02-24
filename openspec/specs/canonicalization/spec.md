## ADDED Requirements

### Requirement: Combined canonicalization function

The module SHALL provide a `canonicalize` function that accepts sequences of function DDL strings and trigger DDL
strings, executes them inside a savepoint against a live PostgreSQL connection, reads back canonical forms from the
catalog using `inspect_functions` and `inspect_triggers`, then rolls back the savepoint. It SHALL return a
`CanonicalState` NamedTuple containing the post-DDL catalog state.

#### Scenario: Canonicalize a single function

- **WHEN** `canonicalize(conn, function_ddl=["CREATE FUNCTION public.add(a int, b int) RETURNS int ..."])` is called
- **THEN** it returns a `CanonicalState` whose `functions` field contains `FunctionInfo` instances including the
  canonical form of the declared function as produced by `pg_get_functiondef()`
- **AND** `triggers` contains all triggers visible in the target schemas

#### Scenario: Canonicalize functions and triggers together

- **WHEN**
  `canonicalize(conn, function_ddl=["CREATE FUNCTION public.audit_fn() ..."], trigger_ddl=["CREATE TRIGGER audit_trg AFTER INSERT ON public.t FOR EACH ROW EXECUTE FUNCTION public.audit_fn()"])`
  is called
- **THEN** the function DDL is executed before the trigger DDL within the same savepoint
- **AND** the trigger can reference the just-created function without error
- **AND** the result contains canonical forms for both the function and the trigger

#### Scenario: Canonicalize with no DDL

- **WHEN** `canonicalize(conn)` is called with no function or trigger DDL (both default to empty)
- **THEN** it returns a `CanonicalState` containing the current catalog state (equivalent to calling `inspect_functions`
  and `inspect_triggers` directly)

### Requirement: CanonicalState return type

The module SHALL provide a `CanonicalState` NamedTuple as the return type of `canonicalize`.

#### Scenario: CanonicalState fields

- **WHEN** a `CanonicalState` instance is created
- **THEN** it has the following fields:
  - `functions` (`Sequence[FunctionInfo]`): all functions visible in the target schemas after DDL execution
  - `triggers` (`Sequence[TriggerInfo]`): all triggers visible in the target schemas after DDL execution

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

Within the savepoint, `canonicalize` SHALL execute all function DDL statements before any trigger DDL statements. This
ensures triggers can reference functions declared in the same batch.

#### Scenario: Functions created before triggers

- **WHEN**
  `canonicalize(conn, function_ddl=["CREATE FUNCTION public.fn() ..."], trigger_ddl=["CREATE TRIGGER trg ... EXECUTE FUNCTION public.fn()"])`
  is called
- **THEN** the function DDL executes first
- **AND** the trigger DDL executes second, referencing the just-created function

#### Scenario: Individual DDL execution

- **WHEN** multiple DDL strings are provided in `function_ddl`
- **THEN** each DDL string is executed as a separate `conn.execute(text(ddl))` call (not batched into a single
  multi-statement string)

### Requirement: Full post-DDL catalog state

After executing DDL within the savepoint, `canonicalize` SHALL read back the full catalog state using
`inspect_functions` and `inspect_triggers`. The result includes all functions and triggers visible in the target schemas
— not only those created by the provided DDL.

#### Scenario: Pre-existing objects included in result

- **WHEN** the database contains functions `public.existing_a()` and `public.existing_b()` and
  `canonicalize(conn, function_ddl=["CREATE FUNCTION public.new_fn() ..."], schemas=["public"])` is called
- **THEN** the result's `functions` field contains `FunctionInfo` instances for `existing_a`, `existing_b`, AND `new_fn`

#### Scenario: CREATE OR REPLACE updates canonical form

- **WHEN** the database contains `public.my_func()` with one definition and
  `canonicalize(conn, function_ddl=["CREATE OR REPLACE FUNCTION public.my_func() ... <different body> ..."])` is called
- **THEN** the result's `functions` field contains the canonical form of the new definition (not the original)

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
