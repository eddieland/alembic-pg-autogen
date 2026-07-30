## ADDED Requirements

### Requirement: Graph node identity

The dependency graph SHALL identify nodes by the tuple `(kind, schema, name)` where `kind` is one of `"function"`,
`"trigger"`, or `"table"`. Function and trigger nodes SHALL carry their full identity tuple as metadata (`identity_args`
for functions; `(table, trigger)` for triggers) so callers can distinguish overloaded functions and multiple triggers on
the same table.

#### Scenario: Function with overloads share schema and name

- **WHEN** two functions named `public.compute(int)` and `public.compute(text)` are both managed
- **THEN** the graph contains a single `(function, public, compute)` node carrying both identity tuples in metadata
- **AND** edges target the shared node; tiebreak between the two overloads is left to the op-list ordering

#### Scenario: Table nodes are leaves

- **WHEN** a table node is added to the graph (from `target_metadata` or the catalog)
- **THEN** the table node has no outgoing edges in the graph (we don't track foreign keys or other table-to-table
  relationships)

### Requirement: Function dependency extraction

The extractor SHALL produce edges from a function node to every node it references that resolves to a known managed
function or known table. SQL-language function bodies SHALL be parsed via `postgast`. plpgsql function bodies SHALL be
scanned via best-effort identifier matching against the set of known managed functions and known tables.

#### Scenario: SQL-language function references a table

- **WHEN** a function body is `SELECT * FROM public.audit_log`
- **THEN** the graph contains an edge from `(function, public, the_function)` to `(table, public, audit_log)`

#### Scenario: SQL-language function calls another function

- **WHEN** a function body is `SELECT public.helper(x)`
- **THEN** the graph contains an edge to `(function, public, helper)` provided `helper` is a managed function

#### Scenario: plpgsql function table reference

- **WHEN** a plpgsql function body contains `INSERT INTO public.audit_log ...`
- **AND** `public.audit_log` is in the set of known tables
- **THEN** the graph contains an edge to `(table, public, audit_log)`

#### Scenario: plpgsql function dynamic reference

- **WHEN** a plpgsql function body contains `EXECUTE 'SELECT * FROM ' || my_table_var`
- **THEN** no edge is created for the dynamic reference
- **AND** an entry is added to `graph.unresolved` describing the source op and the unresolved fragment

#### Scenario: Reference to unknown identifier

- **WHEN** a function body references an identifier that is neither a managed function nor a known table
- **THEN** no edge is created
- **AND** the reference is NOT recorded in `graph.unresolved` (only ambiguous/dynamic refs are surfaced; unknown
  identifiers are assumed to be PostgreSQL built-ins or unmanaged objects out of our scope)

### Requirement: Trigger dependency extraction

The extractor SHALL produce two edges per trigger: one to the table node it is declared on, and one to the function node
it executes.

#### Scenario: Standard trigger

- **WHEN** a trigger DDL is
  `CREATE TRIGGER t BEFORE UPDATE ON public.my_table FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()`
- **THEN** the graph contains edges from `(trigger, public, t)` to `(table, public, my_table)` and to
  `(function, public, set_updated_at)`

### Requirement: Topological sort with stable tiebreak

The `topo_sort` function SHALL accept a graph and a list of ops and return a list of ops ordered such that every op
appears after all ops it depends on. When two ops have no ordering relationship, their relative order in the output
SHALL equal their relative order in the input.

#### Scenario: Empty graph (no edges)

- **WHEN** `topo_sort` is called with a graph that has no edges
- **THEN** the output ops list equals the input ops list element-for-element

#### Scenario: Linear chain

- **WHEN** ops `[A, B, C]` are passed and edges are `B→A`, `C→B`
- **THEN** the output is `[A, B, C]`

#### Scenario: Reverse mode for drops

- **WHEN** `topo_sort(graph, ops, reverse=True)` is called
- **THEN** the output orders dependents before their dependencies (suitable for drop ops)

### Requirement: Cycle handling

When the graph contains a cycle, `topo_sort` SHALL break the cycle at one back-edge so the sort can complete, record the
cycle on `graph.cycles`, and emit a `warning`-level log entry naming the cycle members.

#### Scenario: Mutually recursive functions

- **WHEN** functions `public.a` and `public.b` reference each other
- **THEN** `graph.cycles` contains an entry naming both
- **AND** a `warning` log entry is emitted
- **AND** `topo_sort` returns a complete ordering (cycle broken at one edge)
