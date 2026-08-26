## ADDED Requirements

### Requirement: Concurrent index operations

The module SHALL provide `CreateIndexConcurrentlyOp` and `DropIndexConcurrentlyOp`, each wrapping one of Alembic's own
index operations so that it renders as a concurrent build outside the migration's transaction.

#### Scenario: Wrapping sets the concurrently keyword

- **WHEN** `CreateIndexConcurrentlyOp(inner)` is constructed
- **THEN** `inner.kw["postgresql_concurrently"]` is `True`
- **AND** the wrapped operation is available as `.inner`

#### Scenario: Reverse stays concurrent

- **WHEN** `CreateIndexConcurrentlyOp.reverse()` is called
- **THEN** it returns a `DropIndexConcurrentlyOp` wrapping the reverse of the inner operation
- **AND** the same holds in the other direction

#### Scenario: Diff tuples mirror Alembic's

- **WHEN** `to_diff_tuple()` is called
- **THEN** `CreateIndexConcurrentlyOp` returns `("add_index", <Index>)` and `DropIndexConcurrentlyOp` returns
  `("remove_index", <Index>)`, matching Alembic's own entries
