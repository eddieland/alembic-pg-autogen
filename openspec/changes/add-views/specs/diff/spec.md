## ADDED Requirements

### Requirement: ViewOp type

The module SHALL provide a `ViewOp` NamedTuple representing a single diff operation on a PostgreSQL view.

#### Scenario: ViewOp fields

- **WHEN** a `ViewOp` instance is created
- **THEN** it has the following fields:
  - `action` (`Action`): the operation type
  - `current` (`ViewInfo | None`): the current database definition, present for `REPLACE` and `DROP`
  - `desired` (`ViewInfo | None`): the desired definition, present for `CREATE` and `REPLACE`

#### Scenario: ViewOp for CREATE

- **WHEN** a view exists in the desired state but not in the current state
- **THEN** the resulting `ViewOp` has `action=Action.CREATE`, `current=None`, and `desired` set to the `ViewInfo` from
  the desired state

#### Scenario: ViewOp for REPLACE

- **WHEN** a view exists in both states with matching identity but different definitions
- **THEN** the resulting `ViewOp` has `action=Action.REPLACE`, `current` set to the `ViewInfo` from the current state,
  and `desired` set to the `ViewInfo` from the desired state

#### Scenario: ViewOp for DROP

- **WHEN** a view exists in the current state but not in the desired state
- **THEN** the resulting `ViewOp` has `action=Action.DROP`, `current` set to the `ViewInfo` from the current state, and
  `desired=None`

## MODIFIED Requirements

### Requirement: DiffResult type

The module SHALL provide a `DiffResult` NamedTuple as the return type of `diff`.

#### Scenario: DiffResult fields

- **WHEN** a `DiffResult` instance is created
- **THEN** it has the following fields:
  - `function_ops` (`Sequence[FunctionOp]`): diff operations for functions
  - `trigger_ops` (`Sequence[TriggerOp]`): diff operations for triggers
  - `view_ops` (`Sequence[ViewOp]`): diff operations for views

### Requirement: diff function

The module SHALL provide a `diff` function that compares two `CanonicalState` snapshots and produces a `DiffResult`
containing all necessary create, replace, and drop operations for functions, triggers, and views.

#### Scenario: No changes

- **WHEN** `diff(current, desired)` is called and both states contain the same functions, triggers, and views with
  identical definitions
- **THEN** it returns a `DiffResult` with empty `function_ops`, empty `trigger_ops`, and empty `view_ops`

#### Scenario: Both states empty

- **WHEN** `diff(current, desired)` is called and both states have empty function, trigger, and view sequences
- **THEN** it returns a `DiffResult` with empty `function_ops`, empty `trigger_ops`, and empty `view_ops`

#### Scenario: Create new view

- **WHEN** the desired state contains a view `public.active_users` that does not exist in the current state
- **THEN** the result's `view_ops` contains a `ViewOp` with `action=Action.CREATE` and `desired` set to the `ViewInfo`
  for `public.active_users`

#### Scenario: Drop removed view

- **WHEN** the current state contains a view `public.old_view` that does not exist in the desired state
- **THEN** the result's `view_ops` contains a `ViewOp` with `action=Action.DROP` and `current` set to the `ViewInfo` for
  `public.old_view`

#### Scenario: Replace changed view

- **WHEN** both states contain a view with identity `(public, my_view)` but their `definition` fields differ
- **THEN** the result's `view_ops` contains a `ViewOp` with `action=Action.REPLACE`, `current` from the current state,
  and `desired` from the desired state

#### Scenario: Unchanged views produce no operations

- **WHEN** both states contain a view with identity `(public, stable_view)` and identical `definition` fields
- **THEN** the result's `view_ops` does not contain any operation for that view

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

### Requirement: Identity-based matching for views

The `diff` function SHALL match views between current and desired states using the identity key `(schema, name)`. Two
`ViewInfo` instances with the same identity key represent the same database view.

#### Scenario: Same view name in different schemas

- **WHEN** the current state contains `public.summary` and `reporting.summary` with different definitions
- **THEN** they are treated as separate views (different identity keys) and matched independently

#### Scenario: View ops sorted by identity

- **WHEN** the diff produces operations for views `public.z_view`, `audit.a_view`, and `public.a_view`
- **THEN** `view_ops` is ordered: `audit.a_view`, `public.a_view`, `public.z_view`

### Requirement: Identity key convention

The `_diff_items` helper SHALL use `item[:-1]` (all fields except the last) as the identity key. This replaces the
previous `item[:3]` convention and supports Info NamedTuples with any number of identity fields, as long as `definition`
is the last field.

#### Scenario: FunctionInfo identity via item[:-1]

- **WHEN** `_diff_items` processes `FunctionInfo(schema, name, identity_args, definition)` instances
- **THEN** the identity key is `(schema, name, identity_args)` — identical to the previous `item[:3]` behavior

#### Scenario: TriggerInfo identity via item[:-1]

- **WHEN** `_diff_items` processes `TriggerInfo(schema, table_name, trigger_name, definition)` instances
- **THEN** the identity key is `(schema, table_name, trigger_name)` — identical to the previous `item[:3]` behavior

#### Scenario: ViewInfo identity via item[:-1]

- **WHEN** `_diff_items` processes `ViewInfo(schema, name, definition)` instances
- **THEN** the identity key is `(schema, name)`

### Requirement: Public exports

The module SHALL export `Action`, `FunctionOp`, `TriggerOp`, `ViewOp`, `DiffResult`, and `diff` as public API via the
package's `__init__.py` and `__all__`.

#### Scenario: All types importable from package root

- **WHEN** a user writes `from alembic_pg_autogen import Action, FunctionOp, TriggerOp, ViewOp, DiffResult, diff`
- **THEN** the import succeeds

#### Scenario: Listed in \_\_all\_\_

- **WHEN** `alembic_pg_autogen.__all__` is inspected
- **THEN** it contains `"Action"`, `"FunctionOp"`, `"TriggerOp"`, `"ViewOp"`, `"DiffResult"`, and `"diff"`
