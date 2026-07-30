## ADDED Requirements

### Requirement: Dependency-graph comment block

When the comparator attaches a `DependencyGraph` to `upgrade_ops` and the resolved diff includes one or more
managed-object ops, the renderer SHALL emit a Python comment block at the top of the upgrade body summarizing the graph:
each managed-object op participating in this revision, its incoming edges, any unresolved references, and any detected
cycles.

#### Scenario: Resolved graph with no unresolved refs and no cycles

- **WHEN** the renderer receives a `DependencyGraph` covering ops with all dependencies resolved cleanly
- **THEN** the migration body opens with a comment block listing each op node and its incoming edges, e.g.:
  ```
  # alembic-pg-autogen dependency graph (resolved):
  #   function:public.set_updated_at  (no deps)
  #   trigger:public.set_updated_at_on_update  ← table:public.my_table, function:public.set_updated_at
  ```
- **AND** the comment block contains no "Unresolved" or "WARNING" lines

#### Scenario: Unresolved references present

- **WHEN** the graph contains one or more entries in `unresolved`
- **THEN** the comment block includes an "Unresolved references" subsection naming each unresolved reference and the
  source op that produced it, e.g.:
  ```
  # Unresolved references (treated as no-dependency):
  #   function:public.audit_changes → "EXECUTE 'SELECT ...'" (dynamic SQL)
  ```

#### Scenario: Cycle detected

- **WHEN** the graph contains one or more entries in `cycles`
- **THEN** the comment block includes a "WARNING: dependency cycle detected" subsection naming each cycle's members in
  order, e.g.:
  ```
  # WARNING: dependency cycle detected: function:public.a → function:public.b → function:public.a
  ```

#### Scenario: No managed-object ops

- **WHEN** the diff produces zero function or trigger ops in this revision
- **THEN** the renderer SHALL NOT emit a graph comment block (even if a graph is attached)

#### Scenario: Comment block precedes all op renders

- **WHEN** the migration body is rendered
- **THEN** the dependency-graph comment block (if any) appears before the first `op.execute(...)` or other emitted op
  call in the upgrade body
