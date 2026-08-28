## ADDED Requirements

### Requirement: Materialized view renderer registration

The module SHALL register a renderer function for each of the three materialized view operation classes using
`alembic.autogenerate.render.renderers.dispatch_for()`. Registration SHALL occur at module import time.

#### Scenario: All three materialized view op types have renderers

- **WHEN** the render module is imported
- **THEN** renderers are registered for `CreateMaterializedViewOp`, `ReplaceMaterializedViewOp`, and
  `DropMaterializedViewOp`

### Requirement: Materialized view create rendering

The renderer for `CreateMaterializedViewOp` SHALL emit an `op.execute()` call containing the full DDL from
`desired.definition`. The canonical definition carries no data clause, so the migration populates the materialized view
at execution time, which is PostgreSQL's default.

#### Scenario: Render create materialized view

- **WHEN** a `CreateMaterializedViewOp` is rendered with `desired.definition` containing
  `"CREATE MATERIALIZED VIEW public.mv_sales AS\n SELECT ..."`
- **THEN** the output is an `op.execute(...)` call wrapping that DDL string

### Requirement: Materialized view replace rendering

The renderer for `ReplaceMaterializedViewOp` SHALL emit a list of `op.execute()` calls: first a `DROP MATERIALIZED VIEW`
statement, then the desired DDL from `desired.definition`, then one call per statement in `index_ddl`. Multiple
statements are required because PostgreSQL has no `CREATE OR REPLACE MATERIALIZED VIEW` and the drop removes indexes
silently. `postgast.to_drop()` rejects materialized view DDL, so the `DROP` statement SHALL be built from
`current.schema` and `current.name` through the dialect's identifier preparer.

#### Scenario: Render replace with index preservation

- **WHEN** a `ReplaceMaterializedViewOp` is rendered with one `index_ddl` entry
- **THEN** the output is a list of three `op.execute(...)` calls:
  1. the `DROP MATERIALIZED VIEW <schema>.<name>` statement
  1. the desired `CREATE MATERIALIZED VIEW` DDL
  1. the `CREATE INDEX` statement from `index_ddl`

#### Scenario: Render replace without indexes

- **WHEN** a `ReplaceMaterializedViewOp` is rendered with an empty `index_ddl`
- **THEN** the output is a list of two `op.execute(...)` calls: the `DROP` and the `CREATE`

#### Scenario: Preserved index references a removed column

- **WHEN** the desired query removes a column that a preserved index references
- **THEN** the rendered migration fails at execution time with a PostgreSQL error
- **AND** the user removes or rewrites the index statement in the generated migration file
- **AND** this behavior is documented, because failing loudly is safer than dropping the index silently

### Requirement: Materialized view drop rendering

The renderer for `DropMaterializedViewOp` SHALL emit an `op.execute()` call with a `DROP MATERIALIZED VIEW` statement
constructed from the object's schema and name. Both identifiers SHALL pass through the dialect's identifier preparer
(`autogen_context.dialect.identifier_preparer`), which double-quotes only the identifiers that require it. The statement
SHALL NOT use `CASCADE`, so a drop with dependents fails loudly instead of removing undeclared objects.

#### Scenario: Render drop materialized view

- **WHEN** a `DropMaterializedViewOp` is rendered with `current.schema="public"`, `current.name="old_mv"`
- **THEN** the output is `op.execute("DROP MATERIALIZED VIEW public.old_mv")`

#### Scenario: Render drop with an identifier that requires quoting

- **WHEN** a `DropMaterializedViewOp` is rendered with `current.schema="public"`, `current.name="Sales Summary"`
- **THEN** the output is `op.execute('DROP MATERIALIZED VIEW public."Sales Summary"')`
