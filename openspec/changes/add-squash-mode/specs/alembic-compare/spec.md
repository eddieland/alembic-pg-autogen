## ADDED Requirements

### Requirement: Comparator skip flag

The comparator SHALL honor a `pg_autogen_skip` opt in `autogen_context.opts`. When the value is truthy, the comparator
SHALL return `PriorityDispatchResult.CONTINUE` immediately, without inspecting the database, parsing user DDL, or
appending any operations to `upgrade_ops.ops`. When the value is falsy or absent, the comparator SHALL behave as
specified in the existing requirements.

#### Scenario: Skip flag is set

- **WHEN** `context.configure()` is called with `pg_autogen_skip=True` and any combination of `pg_functions` /
  `pg_triggers`
- **THEN** the comparator does not call `inspect_functions` or `inspect_triggers`
- **AND** the comparator does not call `canonicalize`
- **AND** `upgrade_ops.ops` is unchanged
- **AND** the comparator returns `PriorityDispatchResult.CONTINUE`

#### Scenario: Skip flag is False

- **WHEN** `context.configure()` is called with `pg_autogen_skip=False`
- **THEN** the comparator runs the full inspect-canonicalize-diff pipeline as if the flag were absent

#### Scenario: Skip flag is absent

- **WHEN** `context.configure()` is called without `pg_autogen_skip`
- **THEN** the comparator runs the full inspect-canonicalize-diff pipeline (no behavior change from prior specification)

#### Scenario: Skip flag emits visibility log

- **WHEN** the comparator short-circuits because `pg_autogen_skip` is truthy
- **THEN** an `info`-level log entry is emitted identifying the skip, exactly once per autogen invocation
