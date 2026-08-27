## ADDED Requirements

### Requirement: MaterializedViewOp type

The module SHALL provide a `MaterializedViewOp` NamedTuple representing a single diff operation on a PostgreSQL
materialized view.

#### Scenario: MaterializedViewOp fields

- **WHEN** a `MaterializedViewOp` instance is created
- **THEN** it has the following fields:
  - `action` (`Action`): the operation type
  - `current` (`MaterializedViewInfo | None`): the current database definition, present for `REPLACE` and `DROP`
  - `desired` (`MaterializedViewInfo | None`): the desired definition, present for `CREATE` and `REPLACE`

#### Scenario: MaterializedViewOp for CREATE

- **WHEN** a materialized view exists in the desired state but not in the current state
- **THEN** the resulting `MaterializedViewOp` has `action=Action.CREATE`, `current=None`, and `desired` set to the
  `MaterializedViewInfo` from the desired state

#### Scenario: MaterializedViewOp for REPLACE

- **WHEN** a materialized view exists in both states with matching identity but different definitions
- **THEN** the resulting `MaterializedViewOp` has `action=Action.REPLACE`, `current` from the current state, and
  `desired` from the desired state

#### Scenario: MaterializedViewOp for DROP

- **WHEN** a materialized view exists in the current state but not in the desired state
- **THEN** the resulting `MaterializedViewOp` has `action=Action.DROP`, `current` from the current state, and
  `desired=None`

### Requirement: Identity-based matching for materialized views

The `diff` function SHALL match materialized views between current and desired states using the identity key
`(schema, name)`, following the `item[:-1]` convention. Definitions SHALL be compared with exact string equality, which
works because both sides come from the same `pg_get_viewdef()` read-back.

#### Scenario: Equivalent formatting produces no operation

- **WHEN** the current and desired states hold the same materialized view, declared with different whitespace, casing,
  or column alias syntax
- **THEN** no operation is produced, because canonicalization already normalized both definitions to identical text

#### Scenario: Same name in different schemas

- **WHEN** the current state contains `public.summary_mv` and `reporting.summary_mv` with different definitions
- **THEN** they are treated as separate materialized views and matched independently

#### Scenario: Materialized view ops sorted by identity

- **WHEN** the diff produces operations for materialized views `public.z_mv`, `audit.a_mv`, and `public.a_mv`
- **THEN** `materialized_view_ops` is ordered: `audit.a_mv`, `public.a_mv`, `public.z_mv`

#### Scenario: Materialized views and regular views never match each other

- **WHEN** the current state contains a regular view `public.x` and the desired state contains a materialized view
  `public.x`
- **THEN** the result contains a `DROP` in `view_ops` and a `CREATE` in `materialized_view_ops`, because the two object
  types are diffed independently

## MODIFIED Requirements

### Requirement: DiffResult type

The module SHALL provide a `DiffResult` NamedTuple as the return type of `diff`.

#### Scenario: DiffResult fields

- **WHEN** a `DiffResult` instance is created
- **THEN** it has the following fields:
  - `function_ops` (`Sequence[FunctionOp]`): diff operations for functions
  - `trigger_ops` (`Sequence[TriggerOp]`): diff operations for triggers
  - `view_ops` (`Sequence[ViewOp]`): diff operations for views
  - `materialized_view_ops` (`Sequence[MaterializedViewOp]`): diff operations for materialized views
- **AND** `materialized_view_ops` is appended after `view_ops`, so existing positional access is unaffected

### Requirement: Public exports

The module SHALL export `Action`, `FunctionOp`, `TriggerOp`, `ViewOp`, `MaterializedViewOp`, `DiffResult`, and `diff` as
public API via the package's `__init__.py` and `__all__`.

#### Scenario: All types importable from package root

- **WHEN** a user writes
  `from alembic_pg_autogen import Action, FunctionOp, TriggerOp, ViewOp, MaterializedViewOp, DiffResult, diff`
- **THEN** the import succeeds

#### Scenario: Listed in \_\_all\_\_

- **WHEN** `alembic_pg_autogen.__all__` is inspected
- **THEN** it contains `"Action"`, `"FunctionOp"`, `"TriggerOp"`, `"ViewOp"`, `"MaterializedViewOp"`, `"DiffResult"`,
  and `"diff"`
