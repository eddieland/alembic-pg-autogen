# Spec Delta

## ADDED Requirements

### Requirement: Validate constraint rendering

The renderer for `ValidateConstraintOp` SHALL emit an `op.execute()` call holding
`ALTER TABLE <table> VALIDATE CONSTRAINT <name>`. It SHALL quote the schema, table, and constraint name through the
dialect's identifier preparer, and it SHALL omit the schema qualifier when `schema` is `None`.

#### Scenario: Render with schema

- **WHEN** `ValidateConstraintOp("ck_orders_status", "orders", schema="sales")` is rendered
- **THEN** the output is `op.execute('ALTER TABLE sales.orders VALIDATE CONSTRAINT ck_orders_status')`

#### Scenario: Render without schema

- **WHEN** `ValidateConstraintOp("ck_orders_status", "orders")` is rendered
- **THEN** the output is `op.execute('ALTER TABLE orders VALIDATE CONSTRAINT ck_orders_status')`

#### Scenario: Identifiers that need quoting

- **WHEN** the table is `Order Items` and the constraint is `CK Status`
- **THEN** both identifiers render inside double quotes

### Requirement: NOT VALID check constraint rendering

The renderer for `CreateCheckConstraintNotValidOp` SHALL emit the same `op.create_check_constraint(...)` call that
Alembic renders for `CreateCheckConstraintOp`, with `postgresql_not_valid=True` appended as the last argument. When
`removed_values` is not empty, it SHALL emit comment lines before the call.

#### Scenario: Widening renders one line

- **WHEN** an operation with `removed_values=()` is rendered
- **THEN** the output is one line ending in `postgresql_not_valid=True)`
- **AND** the line starts with `op.create_check_constraint(`

#### Scenario: Narrowing renders a backfill comment

- **WHEN** an operation with `column="status"` and `removed_values=("c",)` on `ck_orders_status` is rendered
- **THEN** the output holds one or more lines that start with `#` before the call
- **AND** those lines name `status`, `'c'`, `ck_orders_status`, and the word `backfill`

#### Scenario: Batch mode keeps the batch prefix

- **WHEN** the operation is rendered inside `render_as_batch=True`
- **THEN** the call starts with `batch_op.create_check_constraint(`

### Requirement: No-op rendering

The renderer for `NoOp` SHALL emit `pass` followed by a comment that holds the reason, on one line.

#### Scenario: Render a no-op

- **WHEN** `NoOp("ck_orders_status on orders stays validated")` is rendered
- **THEN** the output is `pass  # ck_orders_status on orders stays validated`

#### Scenario: Downgrade body stays valid Python

- **WHEN** a revision's downgrade holds only a `NoOp`
- **THEN** the rendered function body compiles
