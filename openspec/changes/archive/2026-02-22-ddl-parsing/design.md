## Context

Three modules currently handle DDL text manipulation via hand-rolled regexes:

- **`_compare.py`** — `_FUNCTION_RE` and `_TRIGGER_RE` extract `(schema, name)` from user-provided DDL strings so the
  comparator can match desired definitions against catalog entries. `_dequote_ident()` normalizes quoted identifiers to
  catalog form.
- **`_canonicalize.py`** — `_CREATE_FUNCTION_RE`, `_CREATE_TRIGGER_RE`, and `_CREATE_OR_REPLACE_RE` inject `OR REPLACE`
  into DDL before executing it in a savepoint.
- **`_render.py`** — DROP statements are built via f-string interpolation from `FunctionInfo`/`TriggerInfo` fields
  (`schema`, `name`, `identity_args`, `table_name`, `trigger_name`).

These regexes are fragile: they fail on quoted identifiers with dots, dollar-quoted bodies containing `CREATE`, and
multiline trigger DDL where `ON table` is on a different line. The `postgast` library wraps `libpg_query` (PostgreSQL's
actual parser) and provides purpose-built functions that handle all edge cases correctly.

## Goals / Non-Goals

**Goals:**

- Replace all regex-based DDL parsing with `postgast` AST-level equivalents.
- Eliminate `_dequote_ident()` and all `re.compile` patterns targeting DDL structure.
- Unify DROP statement generation through `postgast.to_drop()` instead of per-type string templates.

**Non-Goals:**

- Extending postgast coverage to DDL types not currently handled (indexes, policies, etc.).
- Providing a regex fallback path — postgast is a hard dependency.
- Changing the savepoint round-trip pattern or the `FunctionInfo`/`TriggerInfo` data model.
- Modifying the Alembic operations model (`_ops.py`) or comparator registration.

## Decisions

### D1: postgast as a hard dependency (not optional)

`postgast` is added to `[project.dependencies]` alongside `alembic` and `sqlalchemy`. No `try: import postgast`
fallback. The regex code paths are deleted entirely.

**Rationale:** Maintaining two parsing paths (regex + AST) doubles the test surface and defeats the purpose. Since this
package targets PostgreSQL specifically, requiring a PostgreSQL parser is reasonable. `postgast` wraps `libpg_query`
which ships pre-compiled C extensions — no runtime PostgreSQL server needed.

**Alternative considered:** Optional dependency with regex fallback. Rejected because the regex patterns are the problem
being solved, and keeping them negates the benefit.

### D2: Thin call-through, no wrapper layer

Each module calls `postgast` functions directly (`postgast.extract_function_identity()`, `postgast.ensure_or_replace()`,
`postgast.to_drop()`). No internal abstraction layer wrapping postgast.

**Rationale:** postgast's API is already purpose-built for these exact operations. Adding an intermediate layer would be
premature abstraction with no consumer beyond the three call sites.

**Alternative considered:** A `_ddl.py` module centralizing all postgast calls. Rejected — it would just re-export
postgast functions with no added value.

### D3: Identity extraction replaces `_parse_function_names()` and `_parse_trigger_identities()` inline

The existing helper functions are updated to call `postgast.extract_function_identity()` and
`postgast.extract_trigger_identity()` respectively. The function signatures and return types remain compatible with
their callers in the comparator pipeline.

- `postgast.extract_function_identity(ddl)` returns `(schema | None, name)`.
- `postgast.extract_trigger_identity(ddl)` returns `(schema | None, table_name, trigger_name)`.
- When `schema` is `None`, the comparator resolves it to the connection's `current_schema()` — same as today.

### D4: `_ensure_or_replace()` delegates to `postgast.ensure_or_replace()`

The canonicalization function becomes a one-liner: `postgast.ensure_or_replace(ddl)`. The per-type regex patterns
(`_CREATE_FUNCTION_RE`, `_CREATE_TRIGGER_RE`) are no longer needed because postgast handles all statement types
(functions, triggers, views) through AST inspection.

### D5: DROP rendering via `postgast.to_drop(op.current.definition)`

The `_render_drop_function`, `_render_drop_trigger`, and the DROP half of `_render_replace_trigger` all switch to
`postgast.to_drop(definition)`. This reads the `definition` field (which contains the full canonical DDL from
`pg_get_functiondef()` / `pg_get_triggerdef()`) and produces the correct DROP statement with proper quoting.

**Trade-off:** The rendered DROP text may differ cosmetically from the current f-string output (e.g., PostgreSQL parser
normalizes type names like `int` → `integer`). This is functionally correct but changes test expectations.

## Risks / Trade-offs

- **New native dependency** — `postgast` includes compiled C extensions from `libpg_query`. If the package doesn't ship
  wheels for a user's platform, they need a C compiler. → Mitigation: `libpg_query` provides wheels for all major
  platforms (Linux, macOS, Windows) and Python versions 3.9+.

- **Cosmetic output changes** — DROP statements and potentially other rendered SQL may differ textually from the current
  regex-based output due to PostgreSQL parser normalization. → Mitigation: Output is functionally equivalent. Tests are
  updated to match the new output.

- **postgast API stability** — The package is a dependency we don't control. → Mitigation: `postgast` is purpose-built
  for this project's needs. Pin to a compatible version range.
