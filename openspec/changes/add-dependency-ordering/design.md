## Context

Today `_order_ops` in `src/alembic_pg_autogen/compare.py` produces a fixed four-group sort. This works for the simple
case (single function, single trigger that calls it) but breaks down whenever:

- Functions reference tables that Alembic creates in the same revision — the function ends up before the table.
- Functions call other functions — caller and callee are siblings in the "create/replace functions" group, so order is
  determined by `pg_functions` list position rather than the dependency.
- A user writes a trigger whose function exists but the function references a table being created in the same revision —
  the trigger group runs after the function group, but if the function is mis-ordered relative to its table, the whole
  revision fails.

`postgast` already parses every managed function and trigger DDL during canonicalization (`extract_function_identity`,
`extract_trigger_identity`). The same AST exposes referenced relation and function calls. We can extract a dependency
graph for free, with the only new cost being the graph data structure and topo sort.

## Goals / Non-Goals

**Goals:**

- Replace fixed-group ordering with topo-sorted ordering for managed function and trigger ops.
- Make the resolver fail-soft: unresolved references degrade to "no dependency" rather than blocking autogen.
- Surface the resolved graph and any unresolved references in the rendered migration so the human reviewer can
  sanity-check.

**Non-Goals:**

- Full plpgsql parsing. Identifier-scan-with-fallback is the contract.
- Ordering Alembic's `op.create_table` / `op.drop_table` calls. Those remain in Alembic's hands; our ops sort themselves
  only.
- Views. Handled in `add-views`. The graph design SHOULD make adding views trivial later, but they're out of scope here.

## Decisions

### D1: Dependency extraction layers

Three extraction layers, each fail-soft:

1. **Trigger → table** — read directly from the `CREATE TRIGGER ... ON <table>` clause via
   `postgast.extract_trigger_identity` (already used by `_parse_trigger_identities`).
1. **Trigger → function** — read directly from the `EXECUTE FUNCTION <name>(...)` clause. Add a new
   `postgast.extract_trigger_function_call(ast)` helper if not already present.
1. **Function → relations & functions** — walk the AST. SQL-language functions parse cleanly; `postgast` should expose
   relation and function-call references in the parse tree. plpgsql bodies are not parsed by `postgast`, so we run an
   identifier-scan against the body string (looking for tokens following `FROM`/`JOIN`/`UPDATE`/`INSERT INTO`/
   `DELETE FROM`/`SELECT * FROM`) to find table-shaped references. Function calls inside plpgsql are detected by regex
   over `(\w+)\s*\(` and matched against the set of managed function identities.

References that don't resolve to a managed object or to a table the comparator knows about are recorded as "unresolved"
and surfaced in the migration comment. They do not contribute edges.

**Alternative considered:** Run a real plpgsql parser. Rejected — the dependency burden is large and the value over an
identifier scan is small for the common cases (audit triggers, helper functions). We can add it later if users hit
ambiguous cases often.

### D2: Graph node identity — `(kind, schema, name)`

Three node kinds: `function`, `trigger`, `table`. Triggers and functions also carry their full identity tuple as
metadata (`identity_args` for functions, `(table, trigger)` for triggers), but the dependency edge key is always
`(kind, schema, name)`.

Tables are first-class nodes even though we don't manage them — they're ordering anchors for function-on-table edges.
Tables are sourced from:

1. The `target_metadata` passed to `context.configure()` — gives us every table SQLAlchemy knows about.
1. The current catalog (via the existing `inspect_*` calls) — gives us every table that already exists.

Tables in the graph have no outgoing edges in our world (we don't track foreign keys); they're leaves.

**Alternative considered:** Use `(schema, name)` without `kind`. Rejected — function and table can share names in
PostgreSQL, and we'd lose the type when matching identifiers from plpgsql scans.

### D3: Topological sort with stable insertion-order tiebreak

Algorithm: Kahn's algorithm with a stable queue (preserve original insertion order among nodes with the same in-degree
when popping). This guarantees that for graphs with no edges (pre-feature behavior), output equals input order — so
existing tests that assert specific orderings still pass for graphs that don't add edges.

For drops, run the same algorithm on the reverse graph, then reverse the output. Equivalent to "topo sort then reverse,"
but explicit about the intent.

### D4: Cycle handling

Cycles produce:

- A `warning`-level log entry naming the cycle members.
- A comment in the rendered migration:
  `# WARNING: dependency cycle detected: function:public.a → function:public.b → function:public.a`.
- The cycle is broken by removing the edge that would close the cycle (i.e. the back-edge encountered during DFS).
  Remaining nodes are sorted normally; the broken edge means one direction may run before its dependency is satisfied,
  but the cycle was unrunnable anyway.

Cycles in real schemas are rare but possible (mutually-recursive functions). The user is best positioned to fix them by
restructuring DDL.

### D5: Render layer — graph comment

The renderer emits, before the first op call in the upgrade body, a Python comment block:

```python
# alembic-pg-autogen dependency graph (resolved):
#   function:public.set_updated_at  (no deps)
#   trigger:public.set_updated_at_on_update  ← table:public.my_table, function:public.set_updated_at
#   function:public.audit_changes  ← table:public.audit_log
# Unresolved references (treated as no-dependency):
#   function:public.audit_changes → "EXECUTE 'SELECT ...'" (dynamic SQL)
```

This is purely informational — the comment is generated from the graph snapshot taken at sort time. If there are no
unresolved refs and no cycles, only the resolved-graph block is emitted.

**Alternative considered:** Don't emit any comment, keep migrations clean. Rejected — silent best-effort ordering erodes
user trust. The comment is the contract: "here's what we saw, here's what we couldn't see, you decide if that's right."

### D6: Where the parsing logic lives

New module `src/alembic_pg_autogen/dependencies.py` (no underscore — public, follows the project's flat convention).
Exports:

- `DependencyGraph` — `NamedTuple` carrying `nodes`, `edges`, `unresolved`, `cycles`.
- `build_graph(functions, triggers, tables) -> DependencyGraph`.
- `topo_sort(graph, ops, *, reverse=False) -> list[MigrateOperation]`.

`compare.py` imports these and calls them in `_order_ops`. The current `_order_ops` signature changes to also take the
managed `CanonicalState` and the `target_metadata` (to source tables); the spec delta documents the new contract.

### D7: AST reuse with canonicalization

Today `canonicalize` and `_parse_*_names` both call `postgast.parse(ddl)`. To avoid a third parse for dependency
extraction, refactor so the parsed AST flows through the pipeline:

- `canonicalize` returns ASTs alongside `CanonicalState` (or attaches them to each `FunctionInfo`/`TriggerInfo`).
- `_compare_pg_objects` passes the ASTs into `build_graph`.

This is a small refactor but avoids paying the parse cost three times per DDL string.

## Risks / Trade-offs

**[Best-effort plpgsql scan misses dependencies]** → A function that references a table only inside an `EXECUTE` string
won't have an edge to that table, and could be ordered before it. **Mitigation:** Surface unresolved refs in the comment
so the user sees what wasn't parsed; the squash flag remains the escape hatch.

**[Migration ordering changes regenerate noisy diffs]** → Existing rendered migrations may reorder when regenerated.
**Mitigation:** Stable insertion-order tiebreak preserves pre-feature ordering for dependency-free graphs. Documentation
calls out the change. We don't auto-regenerate existing migrations.

**[Graph comment grows unwieldy on large schemas]** → Hundreds of nodes produce a wall of comment text. **Mitigation:**
The comment lists only nodes that participate in this revision's diff (creates/replaces/drops). Nodes unchanged by this
revision are not included even if they're in the graph for ordering purposes.

**[Cycles silently produce broken migrations]** → If a cycle is broken arbitrarily, the migration may fail at runtime.
**Mitigation:** Loud `warning` log + migration comment. Future enhancement: make cycles a hard error toggleable via an
opt.

## Open Questions

- **Does `postgast` already expose relation and function-call references from the parse tree?** Need to verify before
  finalizing the extractor implementation. If not, we may need to contribute upstream or fall back to identifier-scan
  for SQL functions too.
- **Should the graph comment be opt-out?** Some users may prefer clean migrations. Consider a `pg_autogen_graph_comment`
  opt that defaults to `True`. Punting to a follow-up unless reviewer pushback during implementation.
