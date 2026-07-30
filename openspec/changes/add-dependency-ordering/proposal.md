## Why

The current operation ordering in `_compare.py` is a fixed four-group sort: drop triggers → drop functions →
create/replace functions → create/replace triggers. This handles the trigger-depends-on-function case correctly but
ignores two real-world dependency patterns:

1. **Function-on-table** — a function that `SELECT`s, `INSERT`s, or otherwise references a table must be created after
   the table. Today, autogen emits the function before any of Alembic's `op.create_table()` calls when the function
   appears earlier in the ops list.
1. **Function-on-function** — a function that calls another function must be created after its callee.

This is the squashing pain point the user hit: when squashing a long chain of revisions into a single migration that
recreates tables and functions together, our 4-group ordering doesn't interleave correctly with Alembic's table ops, and
intra-group ordering is essentially insertion order from the user's `pg_functions` list.

This change introduces best-effort dependency-aware ordering so most realistic graphs (table → function → trigger,
function → function chains, multi-trigger-per-function fanout) sort correctly without user intervention. Cases the
resolver can't handle continue to fall back to the squash-mode flag from `add-squash-mode`.

## What Changes

- Extract managed-object dependencies from canonicalized DDL using `postgast`:
  - For triggers: the `(schema, table)` they're declared on and the function they `EXECUTE FUNCTION`. Both are
    syntactically explicit in `CREATE TRIGGER` and trivially extractable.
  - For functions: relations and other functions referenced from SQL-language bodies (parseable by `postgast`). plpgsql
    bodies are scanned best-effort for `FROM`/`JOIN`/`UPDATE`/`INSERT INTO`/`DELETE FROM` identifiers; refs the scanner
    can't resolve are recorded as "unresolved" and treated as no-dependency for ordering purposes.
- Build a `DependencyGraph` over `(kind, schema, name)` nodes covering managed functions, managed triggers, and
  referenced tables. Tables in the graph are leaves contributed by Alembic's metadata (or the inspected catalog) — we
  don't manage them, but we need them as ordering anchors.
- Replace `_order_ops`'s fixed groups with a topological sort:
  - Creates/replaces are emitted in topo order (dependencies before dependents).
  - Drops are emitted in reverse topo order (dependents before dependencies).
  - Within a single ordering tier (no edges between nodes), insertion order is preserved for stability.
  - Cycles are detected, broken at an arbitrary edge, and surfaced via a `warning`-level log + a comment in the rendered
    migration.
- The renderer SHALL emit a leading comment in the rendered migration listing the resolved dependency graph and any
  unresolved references, so the human reviewing the migration can sanity-check the ordering.

## Non-goals

- **Parsing arbitrary plpgsql** — we do best-effort identifier scanning, not a real plpgsql parser. Function bodies that
  compute table names dynamically or use `EXECUTE 'SELECT ...'` strings will produce unresolved refs. The squash flag
  remains the escape hatch for these cases.
- **Cross-revision dependency resolution** — we only sort within the current revision's ops. We don't try to detect that
  a function from a previous migration now references a new table.
- **View dependencies** — handled by `add-views` separately. This change is scoped to functions and triggers; once views
  land, the same graph machinery extends to them with a follow-up.
- **Ordering Alembic's own ops** — we don't touch how Alembic orders `op.create_table` etc. We only order our own ops
  and rely on the comparator pipeline to place them after Alembic's schema ops.

## Capabilities

### New Capabilities

- `dependency-graph`: DDL-driven dependency extraction and topological sort for managed PostgreSQL objects. Owns the
  parsing helpers, graph data structure, topo-sort algorithm, and unresolved-reference reporting.

### Modified Capabilities

- `alembic-compare`: Replace fixed-group `_order_ops` with topo-sorted ordering driven by the dependency graph.
- `alembic-render`: Emit a leading comment in the rendered migration body containing the resolved graph summary and
  unresolved references.

## Impact

- **Public API**: New exports — `DependencyGraph`, `extract_dependencies`, `topo_sort_ops` (or equivalents). Names
  finalized in design.md.
- **Migration output**: Existing migrations regenerate with potentially different op ordering. Order changes are
  semantically equivalent for valid graphs, but users diffing rendered migrations against a baseline will see noise on
  first regen. Documented in the upgrade notes.
- **Performance**: One extra postgast parse per function/trigger DDL (already parsed once during canonicalization;
  refactor to share the parsed AST). Topo sort is `O(V + E)` on a graph with at most a few hundred nodes in realistic
  schemas — negligible.
- **Tests**: New unit tests for the graph, the extractor, the topo sort (including stability and cycle handling), and
  e2e tests for ordering across mixed function/trigger/table scenarios.
