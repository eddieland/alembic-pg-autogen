## MODIFIED Requirements

### Requirement: Dependency-safe operation ordering

The comparator SHALL order operations using a topological sort over a dependency graph derived from the canonicalized
DDL of managed functions and triggers and the set of tables known to the Alembic environment. Functions SHALL be ordered
after any tables and functions they reference. Triggers SHALL be ordered after the table they're declared on and the
function they execute. Drops SHALL be emitted in reverse topological order (dependents before dependencies).

The sort SHALL be stable: when two nodes have no ordering relationship, their relative order in the output equals their
relative order in the input ops list.

#### Scenario: Function references a freshly-created table

- **WHEN** the diff produces a `CreateFunctionOp` for `public.audit_changes` whose body references `public.audit_log`,
  and Alembic produces an `op.create_table("audit_log")` in the same revision
- **THEN** the function op is emitted after the table op in `upgrade_ops.ops`

#### Scenario: Function-on-function chain

- **WHEN** the diff produces `CreateFunctionOp` for `public.high_level` whose body calls `public.low_level`, and another
  `CreateFunctionOp` for `public.low_level`
- **THEN** the `low_level` op is emitted before the `high_level` op regardless of insertion order in `pg_functions`

#### Scenario: Trigger after its function and table

- **WHEN** the diff produces a `CreateTriggerOp` for a trigger on `public.my_table` calling `public.set_updated_at`, and
  a `CreateFunctionOp` for `public.set_updated_at`
- **THEN** the function op is emitted before the trigger op
- **AND** the trigger op is emitted after `public.my_table` exists (either pre-existing or via Alembic's table op)

#### Scenario: Drops in reverse dependency order

- **WHEN** the diff produces `DropFunctionOp` for `public.set_updated_at` and `DropTriggerOp` for a trigger that calls
  it
- **THEN** the trigger drop is emitted before the function drop

#### Scenario: No edges — stable insertion order

- **WHEN** the diff produces multiple ops with no resolvable dependencies between them
- **THEN** their relative order in `upgrade_ops.ops` equals their order in the input `function_ops` / `trigger_ops`
  sequences

#### Scenario: Cycle detected and broken

- **WHEN** dependency extraction produces a cycle (e.g. `function:public.a` calls `function:public.b` which calls
  `function:public.a`)
- **THEN** the comparator emits a `warning`-level log entry naming all members of the cycle
- **AND** the cycle is broken at one back-edge so the topo sort can complete
- **AND** the cycle is recorded on the resolved `DependencyGraph` for the renderer to surface

#### Scenario: Unresolved reference

- **WHEN** a function body contains a reference the extractor cannot resolve to a managed function or known table (e.g.
  dynamic SQL via `EXECUTE`)
- **THEN** the reference is recorded on the resolved `DependencyGraph` as unresolved
- **AND** the reference does NOT contribute an edge to the graph
- **AND** the comparator continues without raising

### Requirement: Comparator pipeline orchestration

The comparator SHALL execute the full inspect-canonicalize-diff pipeline when desired-state DDL is provided, build a
dependency graph from the canonicalized state, sort operations via the graph, and stash the graph on the upgrade ops for
the renderer.

#### Scenario: Full pipeline with graph

- **WHEN** the comparator fires with non-empty `pg_functions` and/or `pg_triggers`
- **THEN** it executes these steps in order:
  1. Inspect current functions and triggers from the database
  1. Canonicalize the desired DDL (parsed ASTs are retained on the canonical state for reuse)
  1. Diff current vs. desired
  1. Build a `DependencyGraph` from the canonical state, the diff result, and the table set sourced from
     `target_metadata` plus the catalog
  1. Topologically sort the diff ops via the graph
  1. Append the sorted ops to `upgrade_ops.ops`
  1. Attach the `DependencyGraph` to `upgrade_ops` (or equivalent state visible to the renderer) for the migration
     comment

#### Scenario: Graph attaches even when no managed-object ops are produced

- **WHEN** the diff produces no function or trigger ops but the graph was built (e.g. only Alembic table ops change)
- **THEN** no graph comment is rendered (renderer SHALL skip the comment block when there are zero managed-object ops in
  this revision)
