## ADDED Requirements

### Requirement: Canonicalize check constraint expressions

The module SHALL provide a `canonicalize_check_constraints(conn, *, schema, table_name, expressions)` function that
normalizes desired `CHECK` expressions by round-tripping them through PostgreSQL and returns a mapping of constraint
name to normalized expression.

#### Scenario: Round-trip produces the catalog's form

- **WHEN** `canonicalize_check_constraints` is called with `{"ck_orders_amount": "amount >= 0"}` for a table whose
  `amount` column is `numeric`
- **THEN** the returned expression equals what `inspect_check_constraints` reports for an existing constraint written
  the same way (e.g. `amount >= 0::numeric`)

#### Scenario: Equivalent expressions normalize identically

- **WHEN** the database holds `CHECK ( (amount)  >=  (0) )` and the desired expression is `amount >= 0`
- **THEN** both normalize to the same string
- **AND** comparing them yields no difference

#### Scenario: Rewritten constructs normalize identically

- **WHEN** the database holds `CHECK (status IN ('new','done'))` and the desired expression is
  `status in ( 'new' , 'done' )`
- **THEN** both normalize to PostgreSQL's `= ANY (ARRAY[...])` form and compare equal

#### Scenario: Changed expressions differ

- **WHEN** the database holds `CHECK (amount >= 0)` and the desired expression is `amount > 0`
- **THEN** the normalized expressions differ

#### Scenario: Probes are added NOT VALID

- **WHEN** an expression is normalized against a table containing rows that violate it
- **THEN** normalization still succeeds, because the probe constraint is added `NOT VALID` and no row is validated

#### Scenario: The database is left unchanged

- **WHEN** `canonicalize_check_constraints` returns
- **THEN** the savepoint has been rolled back
- **AND** no probe constraint remains on the table

#### Scenario: Unusable expressions are omitted, not raised

- **WHEN** an expression references a column that does not exist, or the table cannot be altered
- **THEN** the affected constraint name is absent from the returned mapping
- **AND** a warning is logged
- **AND** no exception propagates to the caller

#### Scenario: Empty input short-circuits

- **WHEN** `expressions` is empty
- **THEN** an empty mapping is returned without opening a savepoint or issuing SQL

#### Scenario: Unqualified tables resolve through search_path

- **WHEN** `schema` is `None`
- **THEN** the table is resolved through the connection's `search_path`
