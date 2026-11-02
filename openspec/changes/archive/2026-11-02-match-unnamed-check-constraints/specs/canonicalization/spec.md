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
