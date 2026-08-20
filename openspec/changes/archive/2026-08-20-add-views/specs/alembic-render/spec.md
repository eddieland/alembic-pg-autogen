## ADDED Requirements

### Requirement: View create rendering

The renderer for `CreateViewOp` SHALL emit an `op.execute()` call containing the view's full DDL from
`desired.definition`.

#### Scenario: Render create view

- **WHEN** a `CreateViewOp` is rendered with `desired.definition` containing
  `"CREATE OR REPLACE VIEW public.active_users AS\n SELECT ..."`
- **THEN** the output is an `op.execute(...)` call wrapping that DDL string

### Requirement: View replace rendering

The renderer for `ReplaceViewOp` SHALL emit an `op.execute()` call containing the desired view's full DDL. The output is
identical in form to create rendering because `CREATE OR REPLACE VIEW` is idempotent.

#### Scenario: Render replace view

- **WHEN** a `ReplaceViewOp` is rendered with `desired.definition` containing updated view DDL
- **THEN** the output is an `op.execute(...)` call wrapping the desired DDL string

### Requirement: View drop rendering

The renderer for `DropViewOp` SHALL emit an `op.execute()` call with a `DROP VIEW` statement constructed from the view's
schema and name.

#### Scenario: Render drop view

- **WHEN** a `DropViewOp` is rendered with `current.schema="public"`, `current.name="old_view"`
- **THEN** the output is `op.execute("DROP VIEW public.old_view")`

#### Scenario: Render drop view in non-default schema

- **WHEN** a `DropViewOp` is rendered with `current.schema="reporting"`, `current.name="monthly_summary"`
- **THEN** the output is `op.execute("DROP VIEW reporting.monthly_summary")`

### Requirement: View renderer registration

The module SHALL register a renderer function for each of the three view operation classes using
`alembic.autogenerate.render.renderers.dispatch_for()`. Registration SHALL occur at module import time.

#### Scenario: All three view op types have renderers

- **WHEN** `_render.py` is imported
- **THEN** renderers are registered for `CreateViewOp`, `ReplaceViewOp`, and `DropViewOp`

#### Scenario: Renderer signature

- **WHEN** a view renderer is called by Alembic
- **THEN** it accepts `(autogen_context: AutogenContext, op: MigrateOperation)` and returns `str`

### Requirement: No library imports in rendered migration files

The view renderers SHALL NOT inject any imports from `alembic_pg_autogen` into the rendered migration files. The
rendered code SHALL use only `op.execute()` which is provided by Alembic's built-in imports.

#### Scenario: No imports added for view operations

- **WHEN** any view renderer is called
- **THEN** it does not add entries to `autogen_context.imports`

### Requirement: DDL string quoting in rendered output

The view renderers SHALL properly quote DDL strings in the generated Python code so that the migration file is valid
Python. View DDL may contain single quotes (e.g., string literals in WHERE clauses).

#### Scenario: View DDL with single quotes

- **WHEN** a view DDL contains single-quoted string literals (e.g., `WHERE status = 'active'`)
- **THEN** the rendered `op.execute(...)` call uses a quoting strategy that preserves the DDL without Python syntax
  errors
