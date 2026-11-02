# Spec Delta

## ADDED Requirements

### Requirement: Choose names for unnamed check constraints

The module SHALL provide a `choose_check_constraint_names(conn, *, schema, table_name, expressions)` function. It SHALL
return, for each expression in order, the name that PostgreSQL gives the expression when it is added without a name.

#### Scenario: Names follow the PostgreSQL rule

- **WHEN** the function is called for table `orders` with `["amount > 0", "qty <= amount", "1 = 1"]`
- **THEN** it returns `["orders_amount_check", "orders_check", "orders_check1"]`

#### Scenario: Existing and earlier names cause a suffix

- **WHEN** the table already holds `orders_amount_check` and the expressions are `["amount < 10", "amount <> 5"]`
- **THEN** it returns `["orders_amount_check1", "orders_amount_check2"]`

#### Scenario: Names of other tables in the schema cause a suffix

- **WHEN** table `other` in the same schema holds a constraint named `orders_amount_check`
- **THEN** the expression `amount > 0` on `orders` gets `orders_amount_check1`

#### Scenario: Reserved names cause a suffix

- **WHEN** `reserved` holds `orders_amount_check` and `orders_amount_check2`, and the expressions are
  `["amount > 0", "amount < 9"]`
- **THEN** it returns `["orders_amount_check1", "orders_amount_check3"]`

#### Scenario: The database is left unchanged

- **WHEN** the function returns
- **THEN** the savepoint has been rolled back
- **AND** no added constraint remains on the table
- **AND** rows that violate an expression do not cause an error, because each add is `NOT VALID`

#### Scenario: A rejected expression affects only its own entry

- **WHEN** one expression references a column that does not exist
- **THEN** its entry is `None` and a warning is logged
- **AND** the other entries hold their names

#### Scenario: A missing table gives no names

- **WHEN** the table does not exist
- **THEN** every entry is `None` and a warning is logged

#### Scenario: Empty input short-circuits

- **WHEN** the function is called with no expressions
- **THEN** it returns an empty sequence without using the connection

### Requirement: Check probes run on an empty TEMP copy

`canonicalize_check_constraints` and `choose_check_constraint_names` SHALL run each probe on an empty `TEMP` copy of the
table, created with `CREATE TEMP TABLE ... (LIKE ...)` inside a savepoint. Each probe SHALL run in its own nested
savepoint.

#### Scenario: An open reader does not block the probes

- **WHEN** another transaction holds `ACCESS SHARE` on the table and the probing connection sets `lock_timeout`
- **THEN** both functions return their results without waiting

#### Scenario: One rejected expression keeps the others

- **WHEN** `canonicalize_check_constraints` receives `{"bad": "qty > 0", "good": "amount >= 0"}` and `qty` does not
  exist
- **THEN** it returns `{"good": "amount >= 0::numeric"}`

#### Scenario: Colons and percent signs in literals

- **WHEN** an expression holds `'a :b'`, `'A%'`, or `q % 2`
- **THEN** the probe runs, because each colon that `text()` reads as a bind parameter is escaped

#### Scenario: A connection without a transaction degrades

- **WHEN** the connection uses `AUTOCOMMIT`
- **THEN** a warning is logged and no result is returned, and no exception propagates
