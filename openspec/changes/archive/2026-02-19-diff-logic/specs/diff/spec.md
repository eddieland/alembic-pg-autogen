## ADDED Requirements

### Requirement: Action enum

The module SHALL provide an `Action` enum with three members representing the possible diff operations.

#### Scenario: Action members

- **WHEN** the `Action` enum is inspected
- **THEN** it has exactly three members: `CREATE`, `REPLACE`, and `DROP`
- **AND** their string values are `"create"`, `"replace"`, and `"drop"` respectively

### Requirement: FunctionOp type

The module SHALL provide a `FunctionOp` NamedTuple representing a single diff operation on a PostgreSQL function.

#### Scenario: FunctionOp fields

- **WHEN** a `FunctionOp` instance is created
- **THEN** it has the following fields:
  - `action` (`Action`): the operation type
  - `current` (`FunctionInfo | None`): the current database definition, present for `REPLACE` and `DROP`
  - `desired` (`FunctionInfo | None`): the desired definition, present for `CREATE` and `REPLACE`

#### Scenario: FunctionOp for CREATE

- **WHEN** a function exists in the desired state but not in the current state
- **THEN** the resulting `FunctionOp` has `action=Action.CREATE`, `current=None`, and `desired` set to the
  `FunctionInfo` from the desired state

#### Scenario: FunctionOp for REPLACE

- **WHEN** a function exists in both states with matching identity but different definitions
- **THEN** the resulting `FunctionOp` has `action=Action.REPLACE`, `current` set to the `FunctionInfo` from the current
  state, and `desired` set to the `FunctionInfo` from the desired state

#### Scenario: FunctionOp for DROP

- **WHEN** a function exists in the current state but not in the desired state
- **THEN** the resulting `FunctionOp` has `action=Action.DROP`, `current` set to the `FunctionInfo` from the current
  state, and `desired=None`

### Requirement: TriggerOp type

The module SHALL provide a `TriggerOp` NamedTuple representing a single diff operation on a PostgreSQL trigger.

#### Scenario: TriggerOp fields

- **WHEN** a `TriggerOp` instance is created
- **THEN** it has the following fields:
  - `action` (`Action`): the operation type
  - `current` (`TriggerInfo | None`): the current database definition, present for `REPLACE` and `DROP`
  - `desired` (`TriggerInfo | None`): the desired definition, present for `CREATE` and `REPLACE`

#### Scenario: TriggerOp for CREATE

- **WHEN** a trigger exists in the desired state but not in the current state
- **THEN** the resulting `TriggerOp` has `action=Action.CREATE`, `current=None`, and `desired` set to the `TriggerInfo`
  from the desired state

#### Scenario: TriggerOp for REPLACE

- **WHEN** a trigger exists in both states with matching identity but different definitions
- **THEN** the resulting `TriggerOp` has `action=Action.REPLACE`, `current` set to the `TriggerInfo` from the current
  state, and `desired` set to the `TriggerInfo` from the desired state

#### Scenario: TriggerOp for DROP

- **WHEN** a trigger exists in the current state but not in the desired state
- **THEN** the resulting `TriggerOp` has `action=Action.DROP`, `current` set to the `TriggerInfo` from the current
  state, and `desired=None`

### Requirement: DiffResult type

The module SHALL provide a `DiffResult` NamedTuple as the return type of `diff`.

#### Scenario: DiffResult fields

- **WHEN** a `DiffResult` instance is created
- **THEN** it has the following fields:
  - `function_ops` (`Sequence[FunctionOp]`): diff operations for functions
  - `trigger_ops` (`Sequence[TriggerOp]`): diff operations for triggers

### Requirement: diff function

The module SHALL provide a `diff` function that compares two `CanonicalState` snapshots and produces a `DiffResult`
containing all necessary create, replace, and drop operations for functions and triggers.

#### Scenario: No changes

- **WHEN** `diff(current, desired)` is called and both states contain the same functions and triggers with identical
  definitions
- **THEN** it returns a `DiffResult` with empty `function_ops` and empty `trigger_ops`

#### Scenario: Both states empty

- **WHEN** `diff(current, desired)` is called and both states have empty function and trigger sequences
- **THEN** it returns a `DiffResult` with empty `function_ops` and empty `trigger_ops`

#### Scenario: Create new function

- **WHEN** the desired state contains a function `public.new_fn(integer)` that does not exist in the current state
- **THEN** the result's `function_ops` contains a `FunctionOp` with `action=Action.CREATE` and `desired` set to the
  `FunctionInfo` for `public.new_fn(integer)`

#### Scenario: Create new trigger

- **WHEN** the desired state contains a trigger `audit_trg` on `public.events` that does not exist in the current state
- **THEN** the result's `trigger_ops` contains a `TriggerOp` with `action=Action.CREATE` and `desired` set to the
  `TriggerInfo` for `audit_trg` on `public.events`

#### Scenario: Drop removed function

- **WHEN** the current state contains a function `public.old_fn()` that does not exist in the desired state
- **THEN** the result's `function_ops` contains a `FunctionOp` with `action=Action.DROP` and `current` set to the
  `FunctionInfo` for `public.old_fn()`

#### Scenario: Drop removed trigger

- **WHEN** the current state contains a trigger `old_trg` on `public.events` that does not exist in the desired state
- **THEN** the result's `trigger_ops` contains a `TriggerOp` with `action=Action.DROP` and `current` set to the
  `TriggerInfo` for `old_trg` on `public.events`

#### Scenario: Replace changed function

- **WHEN** both states contain a function with identity `(public, my_func, integer)` but their `definition` fields
  differ
- **THEN** the result's `function_ops` contains a `FunctionOp` with `action=Action.REPLACE`, `current` from the current
  state, and `desired` from the desired state

#### Scenario: Replace changed trigger

- **WHEN** both states contain a trigger with identity `(public, events, audit_trg)` but their `definition` fields
  differ
- **THEN** the result's `trigger_ops` contains a `TriggerOp` with `action=Action.REPLACE`, `current` from the current
  state, and `desired` from the desired state

#### Scenario: Unchanged objects produce no operations

- **WHEN** both states contain a function with identity `(public, stable_fn, )` and identical `definition` fields
- **THEN** the result's `function_ops` does not contain any operation for that function

#### Scenario: Mixed operations across multiple objects

- **WHEN** the current state contains functions A and B, and the desired state contains functions B (modified) and C
- **THEN** the result's `function_ops` contains exactly three operations: `DROP` for A, `REPLACE` for B, and `CREATE`
  for C

### Requirement: Identity-based matching for functions

The `diff` function SHALL match functions between current and desired states using the identity key
`(schema, name, identity_args)`. Two `FunctionInfo` instances with the same identity key represent the same database
function.

#### Scenario: Overloaded functions matched independently

- **WHEN** the current state contains `public.my_func(integer)` and `public.my_func(text)`, and the desired state
  contains `public.my_func(integer)` (modified) and `public.my_func(text)` (unchanged)
- **THEN** the result contains a `REPLACE` for `public.my_func(integer)` and no operation for `public.my_func(text)`

#### Scenario: Same name in different schemas

- **WHEN** the current state contains `public.helper()` and `audit.helper()` with different definitions
- **THEN** they are treated as separate functions (different identity keys) and matched independently

### Requirement: Identity-based matching for triggers

The `diff` function SHALL match triggers between current and desired states using the identity key
`(schema, table_name, trigger_name)`. Two `TriggerInfo` instances with the same identity key represent the same database
trigger.

#### Scenario: Same trigger name on different tables

- **WHEN** the current state contains trigger `audit_trg` on `public.orders` and `audit_trg` on `public.users`
- **THEN** they are treated as separate triggers (different identity keys) and matched independently

#### Scenario: Same trigger name in different schemas

- **WHEN** the current state contains trigger `trg` on `public.events` and `trg` on `audit.events`
- **THEN** they are treated as separate triggers and matched independently

### Requirement: String equality for definition comparison

The `diff` function SHALL compare definitions using exact string equality (`==`). It SHALL NOT normalize, strip, or
transform definition strings before comparison.

#### Scenario: Definitions compared verbatim

- **WHEN** two functions have the same identity key and their `definition` fields are byte-for-byte identical
- **THEN** no operation is produced for that function

#### Scenario: Whitespace difference produces replace

- **WHEN** two functions have the same identity key but their `definition` fields differ only in whitespace within a
  PL/pgSQL body
- **THEN** a `REPLACE` operation is produced (whitespace normalization is not applied)

### Requirement: Deterministic operation ordering

Within `DiffResult.function_ops` and `DiffResult.trigger_ops`, operations SHALL be ordered lexicographically by their
identity key tuple. This ensures deterministic output regardless of input ordering.

#### Scenario: Function ops sorted by identity

- **WHEN** the diff produces operations for functions `public.z_func()`, `audit.a_func()`, and `public.a_func(integer)`
- **THEN** `function_ops` is ordered: `audit.a_func()`, `public.a_func(integer)`, `public.z_func()`

#### Scenario: Trigger ops sorted by identity

- **WHEN** the diff produces operations for triggers on `public.users/z_trg`, `audit.events/a_trg`, and
  `public.events/a_trg`
- **THEN** `trigger_ops` is ordered: `audit.events/a_trg`, `public.events/a_trg`, `public.users/z_trg`

#### Scenario: Ordering is stable across runs

- **WHEN** `diff` is called twice with the same inputs but in different sequence order
- **THEN** both calls return identical `DiffResult` instances

### Requirement: diff accepts CanonicalState inputs

The `diff` function SHALL accept two `CanonicalState` NamedTuples as its positional arguments: the first representing
the current database state, the second representing the desired state.

#### Scenario: Function signature

- **WHEN** `diff` is called
- **THEN** its signature is `diff(current: CanonicalState, desired: CanonicalState) -> DiffResult`

#### Scenario: Works with inspect and canonicalize output

- **WHEN** `current` is constructed from `inspect_functions` and `inspect_triggers` results, and `desired` is the return
  value of `canonicalize()`
- **THEN** `diff(current, desired)` produces the correct operations without any transformation of the inputs

### Requirement: Public exports

The module SHALL export `Action`, `FunctionOp`, `TriggerOp`, `DiffResult`, and `diff` as public API via the package's
`__init__.py` and `__all__`.

#### Scenario: All types importable from package root

- **WHEN** a user writes `from alembic_pg_autogen import Action, FunctionOp, TriggerOp, DiffResult, diff`
- **THEN** the import succeeds

#### Scenario: Listed in \_\_all\_\_

- **WHEN** `alembic_pg_autogen.__all__` is inspected
- **THEN** it contains `"Action"`, `"FunctionOp"`, `"TriggerOp"`, `"DiffResult"`, and `"diff"`
