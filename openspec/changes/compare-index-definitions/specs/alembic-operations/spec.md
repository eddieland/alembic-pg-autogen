## ADDED Requirements

### Requirement: Concurrent index operations

The module SHALL provide `CreateIndexConcurrentlyOp` and `DropIndexConcurrentlyOp`. Each class wraps one Alembic index
operation. The wrapped operation renders as a concurrent build outside the transaction of the migration.

#### Scenario: The wrapper sets the concurrently keyword

- **WHEN** `CreateIndexConcurrentlyOp(inner)` is created
- **THEN** `inner.kw["postgresql_concurrently"]` is `True`
- **AND** the wrapped operation is available as `.inner`

#### Scenario: The reverse operation is also concurrent

- **WHEN** `CreateIndexConcurrentlyOp.reverse()` is called
- **THEN** it returns a `DropIndexConcurrentlyOp` that wraps the reverse of the inner operation
- **AND** the same rule applies in the other direction

#### Scenario: Diff tuples match Alembic

- **WHEN** `to_diff_tuple()` is called
- **THEN** `CreateIndexConcurrentlyOp` returns `("add_index", <Index>)` and `DropIndexConcurrentlyOp` returns
  `("remove_index", <Index>)`, the same entries that Alembic returns
