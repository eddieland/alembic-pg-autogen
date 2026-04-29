## 1. Investigate postgast capabilities

- [ ] 1.1 Verify what `postgast` exposes for relation references and function-call references inside a parsed AST
  (`postgast.parse(ddl)`). Document findings in `design.md` under "Open Questions".
- [ ] 1.2 If postgast lacks one of those extractors, decide whether to contribute upstream or implement an
  identifier-scan fallback inline. Update `design.md` accordingly.

## 2. Dependency graph module

- [ ] 2.1 Create `src/alembic_pg_autogen/dependencies.py` with module docstring, `DependencyGraph` NamedTuple (`nodes`,
  `edges`, `unresolved`, `cycles`), and a `Node` NamedTuple keyed on `(kind, schema, name)` with metadata payload.
- [ ] 2.2 Implement `_extract_function_deps(ast, body, default_schema, known_functions, known_tables)` returning a tuple
  of `(resolved_edges, unresolved_refs)`. Use postgast for SQL bodies, regex/identifier-scan for plpgsql bodies.
- [ ] 2.3 Implement `_extract_trigger_deps(ast, default_schema)` returning the table edge and the function edge.
- [ ] 2.4 Implement `build_graph(functions, triggers, tables, default_schema)` that wires together node creation and
  edge extraction. Each `FunctionInfo` / `TriggerInfo` MUST carry its parsed AST (see task 5.1).
- [ ] 2.5 Implement `topo_sort(graph, ops, *, reverse=False)` using Kahn's algorithm with stable insertion-order
  tiebreak on equal in-degree. Return ordered list of ops; cycles are broken at the back-edge and recorded in
  `graph.cycles`.
- [ ] 2.6 Add unit tests in `tests/alembic_pg_autogen/test_dependencies.py` covering: simple chain, function fanout,
  multi-function trigger, cycle detection + break, unresolved-ref recording, stable tiebreak when no edges exist.

## 3. Comparator integration

- [ ] 3.1 Replace the four-group `_order_ops` body in `src/alembic_pg_autogen/compare.py` with a call to
  `topo_sort(graph, create_replace_ops, reverse=False)` for create/replace and
  `topo_sort(graph, drop_ops, reverse=True)` for drops.
- [ ] 3.2 Update `_order_ops` signature to accept the dependency graph (built earlier in `_compare_pg_objects`).
- [ ] 3.3 In `_compare_pg_objects`, source tables for the graph from `autogen_context.opts["target_metadata"]` (already
  threaded through Alembic) plus the inspected catalog. Build the graph after `_filter_to_declared` and before
  `_order_ops`.
- [ ] 3.4 Stash the resolved graph on `upgrade_ops` (or pass via a context attribute) so the renderer can read it for
  the comment block.
- [ ] 3.5 Add comparator tests in `tests/alembic_pg_autogen/test_autogenerate.py` verifying ordering for: function
  depending on a freshly-created table, function-on-function chain, trigger-on-function-on-table, and a cycle case that
  warns + breaks.

## 4. Render layer

- [ ] 4.1 In `src/alembic_pg_autogen/render.py`, add a renderer hook that emits the dependency-graph comment block at
  the top of the upgrade body. Implement as a separate registered renderer (or via the existing renderer's preamble) —
  pick whichever Alembic supports cleanly.
- [ ] 4.2 Format the comment block per design D5: resolved nodes with their incoming edges, then any unresolved-ref
  lines, then any cycle lines. Skip the block entirely when this revision has no managed-object diffs.
- [ ] 4.3 Add render tests in `tests/alembic_pg_autogen/test_render.py` covering: graph-comment block formatting, empty
  graph (no comment), unresolved-ref formatting, cycle-warning formatting.

## 5. AST reuse refactor

- [ ] 5.1 Refactor `canonicalize.py` so each `FunctionInfo` / `TriggerInfo` carries its parsed `postgast` AST alongside
  the `definition` string (new field; append for compat per the views convention). This avoids re-parsing during
  dependency extraction.
- [ ] 5.2 Update `_parse_function_names` and `_parse_trigger_identities` in `compare.py` to use the cached AST instead
  of re-parsing.
- [ ] 5.3 Verify benchmarks (or just timing on a 100-function schema) show no regression.

## 6. Public exports & docs

- [ ] 6.1 Update `src/alembic_pg_autogen/__init__.py` to export `DependencyGraph`, `build_graph`, `topo_sort` (and any
  helper types).
- [ ] 6.2 Update `tests/alembic_pg_autogen/test_import.py` to verify the new public exports.
- [ ] 6.3 Add a "Dependency-aware ordering" section to `docs/quickstart.rst` showing the graph comment in a sample
  migration and explaining best-effort semantics.
- [ ] 6.4 Cross-link to the squash-mode docs as the escape hatch when the resolver can't help.

## 7. Validation

- [ ] 7.1 Run `make lint` and fix any issues.
- [ ] 7.2 Run `make test` and verify all existing + new tests pass, especially that previously-asserted op orderings
  still hold for graphs without edges (stability test).
- [ ] 7.3 Manually regenerate one of the e2e fixture migrations and visually confirm the graph comment looks right.
