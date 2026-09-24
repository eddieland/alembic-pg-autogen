## ADDED Requirements

### Requirement: Render concurrent index operations inside an autocommit block

The module SHALL register renderers for `CreateIndexConcurrentlyOp` and `DropIndexConcurrentlyOp`. Each renderer emits
the rendered wrapped operation inside `op.get_context().autocommit_block()`.

#### Scenario: Rendered form

- **WHEN** a `CreateIndexConcurrentlyOp` is rendered
- **THEN** the output is `with op.get_context().autocommit_block():` followed by the inner call, indented by four spaces

#### Scenario: The Alembic renderer produces the inner call

- **WHEN** the inner operation has `postgresql_where`, `postgresql_ops`, or another dialect keyword
- **THEN** the rendered call is the Alembic output with each of its keywords, plus `postgresql_concurrently=True`

#### Scenario: The generated migration runs

- **WHEN** a migration that contains the rendered block is applied
- **THEN** PostgreSQL creates or drops the index concurrently
- **AND** PostgreSQL does not refuse the statement for a transaction block
