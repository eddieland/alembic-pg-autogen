## Context

The core autogenerate pipeline (inspect → canonicalize → diff → ops → render) is implemented and tested, but every
existing integration test uses a single function or single trigger in isolation. The library's motivating use case —
managing audit triggers at scale across many tables — has never been exercised end-to-end.

The tmp-research identified two audit patterns:

- **Shape A**: One audit function + one trigger per table (N functions, N triggers for N tables).
- **Shape B**: One shared audit function + one trigger per table (1 function, N triggers for N tables).

Both patterns use `SECURITY DEFINER` functions and PL/pgSQL bodies. Several edge cases were flagged as untested:
function body modifications, trigger additions/removals as tables change, `SECURITY DEFINER` attribute round-tripping,
and multi-table dependency ordering.

The existing `AlembicProject` fixture provides schema-isolated Alembic project directories backed by testcontainers
PostgreSQL. The `_autogenerate()` helper in `test_autogenerate.py` runs `alembic revision --autogenerate` and returns
the generated migration file content. Both can be reused directly.

## Goals / Non-Goals

**Goals:**

- Validate the full autogenerate pipeline with realistic multi-table audit trigger setups (both Shape A and Shape B).
- Test lifecycle scenarios: initial setup, function body modification, adding triggers for new tables, removing triggers
  for dropped tables, and the no-op steady-state case.
- Verify `SECURITY DEFINER` functions canonicalize and diff correctly.
- Confirm generated migrations are executable — run `upgrade()` and `downgrade()` against a live database and verify
  schema state.
- Establish confidence that the pipeline handles realistic entity counts (5–10 tables, not hundreds).

**Non-Goals:**

- Performance benchmarking or stress testing with hundreds of entities.
- Testing quoted identifiers, `--sql` offline mode, or other edge cases outside the audit pattern.
- Changing any library source code (unless tests expose a bug, which would be a separate follow-up change).
- Testing across multiple PostgreSQL versions (CI already covers 14+).

## Decisions

### 1. Single test module with helper functions, not shared fixtures

**Decision:** Create one new test module `test_e2e_audit.py` with local helper functions for building audit DDL.

**Rationale:** The audit patterns are specific to these tests and not reusable by the rest of the suite. Extracting them
into `conftest.py` or `alembic_helpers.py` would pollute shared infrastructure for no benefit. Local functions like
`_make_audit_function(schema, table)` and `_make_audit_trigger(schema, table)` keep the test self-contained.

**Alternative considered:** Parameterized fixtures with `@pytest.fixture(params=["shape_a", "shape_b"])`. Rejected
because the two shapes require fundamentally different DDL structures — parameterization would add complexity without
reducing code.

### 2. Reuse existing `AlembicProject` and `_autogenerate()` patterns

**Decision:** Follow the same test structure as `test_autogenerate.py` — use `AlembicProject` for schema isolation and
write a local `_autogenerate()` helper (or import the existing one).

**Rationale:** The existing pattern is proven and well-understood. These tests differ only in fixture complexity (more
tables, more DDL), not in how autogenerate is invoked.

### 3. Verify migration executability via Alembic's `upgrade()` / `downgrade()`

**Decision:** After generating a migration, import and execute its `upgrade()` and `downgrade()` functions against the
live database. Verify state with catalog inspection.

**Rationale:** Generating correct Python code is necessary but not sufficient. The migration must actually execute
without errors and produce the expected schema state. This is the gap between "renders correctly" and "works in
practice."

**Approach:** After autogenerate, run `alembic upgrade head` on the project, inspect catalog state to confirm objects
exist, then run `alembic downgrade base` and confirm objects are removed.

### 4. Use 5 tables as the baseline multi-table count

**Decision:** Test with 5 tables (e.g., `users`, `orders`, `payments`, `products`, `audit_log`) as the baseline for
multi-table scenarios.

**Rationale:** Enough to exercise ordering, bulk operations, and partial updates, without making tests slow. A
single-table test wouldn't catch ordering bugs; 5 tables is sufficient to surface them.

### 5. Test SECURITY DEFINER as part of the audit function patterns, not separately

**Decision:** Audit functions in the tests will use `SECURITY DEFINER` by default (as they would in production), rather
than having a separate test just for the attribute.

**Rationale:** The goal is realistic patterns, not attribute-level unit testing. If `SECURITY DEFINER` breaks
canonicalization, it will surface naturally in the audit pattern tests.

## Risks / Trade-offs

**[Slow test execution]** → Multi-table tests with Alembic autogenerate + upgrade + downgrade will be slower than
existing integration tests. Mitigated by keeping entity count modest (5 tables) and using the existing session-scoped
PostgreSQL container. All tests are marked `@pytest.mark.integration` so they can be skipped in fast CI runs.

**[Test fragility from DDL string matching]** → Tests that assert on generated migration content (e.g.,
`assert "CREATE TRIGGER" in content`) are sensitive to rendering changes. Mitigated by preferring behavioral assertions
(run upgrade, check catalog state) over string matching where possible.

**[False confidence from happy-path only]** → Testing only the audit pattern doesn't cover the full edge-case surface.
Mitigated by explicitly scoping this change to audit patterns and tracking other edge cases as separate follow-up
changes.
