## ADDED Requirements

### Requirement: IGNORED sentinel

The package SHALL provide an `IGNORED` sentinel value in an `alembic_pg_autogen.sentinels` module, exported from the
package root together with an `Ignored` type alias for annotating values that may be the sentinel. `IGNORED` SHALL be a
singleton that is not equal to any sequence.

#### Scenario: Importable from the package root

- **WHEN** a user writes `from alembic_pg_autogen import IGNORED, Ignored`
- **THEN** the import succeeds
- **AND** both names appear in `alembic_pg_autogen.__all__`

#### Scenario: Distinguishable from an empty sequence

- **WHEN** `IGNORED` is compared to `()` or `[]`
- **THEN** it compares unequal
- **AND** `value is IGNORED` is the supported way to test for it

#### Scenario: Readable in log output

- **WHEN** `IGNORED` is formatted with `repr()`, `str()`, or an f-string
- **THEN** the result is `IGNORED`

#### Scenario: Narrowing for type checkers

- **WHEN** a parameter is annotated `Sequence[str] | Ignored` and guarded by `if value is not IGNORED:`
- **THEN** a type checker narrows `value` to `Sequence[str]` inside the guard without a cast
