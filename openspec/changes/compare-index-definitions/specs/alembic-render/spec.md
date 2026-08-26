## ADDED Requirements

### Requirement: Render concurrent index operations inside an autocommit block

The module SHALL register renderers for `CreateIndexConcurrentlyOp` and `DropIndexConcurrentlyOp` that emit the wrapped
operation's own rendering nested inside `op.get_context().autocommit_block()`.

#### Scenario: Rendered shape

- **WHEN** a `CreateIndexConcurrentlyOp` is rendered
- **THEN** the output is `with op.get_context().autocommit_block():` followed by the inner call indented by four spaces

#### Scenario: Inner call is Alembic's own

- **WHEN** the inner operation carries `postgresql_where`, `postgresql_ops`, or any other dialect keyword
- **THEN** the rendered call is the one Alembic's renderer produces, keyword for keyword, plus
  `postgresql_concurrently=True`

#### Scenario: Generated migration runs

- **WHEN** a migration containing the rendered block is applied
- **THEN** the index is created or dropped concurrently without PostgreSQL rejecting it for running inside a transaction
  block
