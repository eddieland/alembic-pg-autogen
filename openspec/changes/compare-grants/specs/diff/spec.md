## ADDED Requirements

### Requirement: GrantOp type and diff_grants function

The module SHALL provide a `GrantOp` NamedTuple with the fields `action`, `current`, and `desired`, as `FunctionOp`
does. It SHALL provide `diff_grants(current, desired)`, which returns a sequence of `GrantOp` sorted by identity.

#### Scenario: Grant actions

- **WHEN** `diff_grants()` compares grants
- **THEN** a privilege only in *desired* gives `CREATE`
- **AND** a privilege only in *current* gives `DROP`
- **AND** a privilege on both sides with a different `grantable` gives `REPLACE`

#### Scenario: Snapshot tuples keep three fields

- **WHEN** code unpacks a `CanonicalState` or a `DiffResult` into three names
- **THEN** the unpacking works as before this change, because grants are not a field of either tuple

### Requirement: Payload comparison by whole tuple

`_diff_items` SHALL treat the last field of each item as the payload and compare matched items as whole tuples.

#### Scenario: Existing types unchanged

- **WHEN** two functions, triggers, or views have the same identity
- **THEN** they differ exactly when their `definition` differs, as before this change
