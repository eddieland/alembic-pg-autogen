# Spec Delta

## Purpose

Classifies a change between two check constraint expressions that each restrict one column to a set of string values, so
the comparator can tell a widening from a narrowing.

## ADDED Requirements

### Requirement: Parse a value set expression

The module SHALL provide `parse_value_set(expression)` that returns a `ValueSet` NamedTuple with fields `column` (`str`)
and `values` (`frozenset[str]`) when the expression restricts one column to a set of string literals. It SHALL return
`None` for every other expression. It SHALL accept the `IN` form that SQLAlchemy compiles and the `= ANY (ARRAY)` form
that `pg_get_expr(conbin, conrelid, true)` deparses.

#### Scenario: IN form

- **WHEN** `parse_value_set("status IN ('a', 'b')")` is called
- **THEN** it returns `ValueSet(column="status", values=frozenset({"a", "b"}))`

#### Scenario: ANY form as deparsed by PostgreSQL

- **WHEN** `parse_value_set("status = ANY (ARRAY['a'::text, 'b'::text])")` is called
- **THEN** it returns `ValueSet(column="status", values=frozenset({"a", "b"}))`

#### Scenario: Casts on the column and the array are ignored

- **WHEN** the expression is `(status)::text = ANY ((ARRAY['a'::character varying, 'b'::character varying])::text[])`
- **THEN** it returns `ValueSet(column="status", values=frozenset({"a", "b"}))`

#### Scenario: Qualified column names use the last segment

- **WHEN** the expression is `orders.status IN ('a')`
- **THEN** the returned `column` is `"status"`

#### Scenario: Negated forms are not value sets

- **WHEN** the expression is `status NOT IN ('a', 'b')` or `status <> ALL (ARRAY['a'::text])`
- **THEN** it returns `None`

#### Scenario: Non-string literals are not value sets

- **WHEN** the expression is `priority IN (1, 2)`
- **THEN** it returns `None`

#### Scenario: Other expressions are not value sets

- **WHEN** the expression is `amount > 0`, `status IN ('a') AND amount > 0`, or text that PostgreSQL cannot parse
- **THEN** it returns `None`

### Requirement: Classify a value set change

The module SHALL provide `ValueSetChange`, an enum with members `WIDENING`, `NARROWING`, `DISJOINT`, and `UNKNOWN`. It
SHALL provide `compare_value_sets(current, desired)` over two `ValueSet` instances and
`classify_value_set_change(current, desired)` over two expression strings. Both SHALL return a `ValueSetChange`.

#### Scenario: Widening

- **WHEN** the current values are `{a, b}` and the desired values are `{a, b, c}` on the same column
- **THEN** the result is `WIDENING`

#### Scenario: Narrowing

- **WHEN** the current values are `{a, b, c}` and the desired values are `{a, b}` on the same column
- **THEN** the result is `NARROWING`

#### Scenario: Values added and removed

- **WHEN** the current values are `{a, b}` and the desired values are `{b, c}` on the same column
- **THEN** the result is `DISJOINT`

#### Scenario: No value in common

- **WHEN** the current values are `{a}` and the desired values are `{b}` on the same column
- **THEN** the result is `DISJOINT`

#### Scenario: Equal sets

- **WHEN** the current values equal the desired values on the same column
- **THEN** the result is `UNKNOWN`

#### Scenario: Different columns

- **WHEN** the two value sets name different columns
- **THEN** the result is `UNKNOWN`

#### Scenario: Either expression is not a value set

- **WHEN** `classify_value_set_change("amount > 0", "status IN ('a')")` is called
- **THEN** the result is `UNKNOWN`

#### Scenario: Mixed forms classify

- **WHEN** `classify_value_set_change("status = ANY (ARRAY['a'::text])", "status IN ('a', 'b')")` is called
- **THEN** the result is `WIDENING`
