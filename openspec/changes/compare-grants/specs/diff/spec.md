## ADDED Requirements

### Requirement: GrantOp type

The module SHALL provide a `GrantOp` NamedTuple with the fields `action`, `current`, and `desired`, as `FunctionOp`
does. `DiffResult` SHALL have a `grant_ops` field with the default `()`. `CanonicalState` SHALL have a `grants` field
with the default `()`.

#### Scenario: Grant actions

- **WHEN** `diff()` compares grants
- **THEN** a privilege only in *desired* gives `CREATE`
- **AND** a privilege only in *current* gives `DROP`
- **AND** a privilege on both sides with a different `grantable` gives `REPLACE`

### Requirement: Payload comparison by whole tuple

`_diff_items` SHALL treat the last field of each item as the payload and compare matched items as whole tuples.

#### Scenario: Existing types unchanged

- **WHEN** two functions, triggers, or views have the same identity
- **THEN** they differ exactly when their `definition` differs, as before this change
