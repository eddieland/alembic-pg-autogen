## MODIFIED Requirements

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

## ADDED Requirements

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
