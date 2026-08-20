## Why

An absent `pg_functions` / `pg_triggers` / `pg_views` key resolves to `()`, which means "there should be no objects of
this type" and therefore drops every existing one. That default only bites when *another* key is set, because the
comparator short-circuits when all three are absent — so the meaning of omitting `pg_views` depends on whether you also
set `pg_functions`. Declaring functions and nothing else silently proposes `DROP VIEW` for every view in the schema,
which is exactly the partial-adoption case the `IGNORED` sentinel was added to serve.

`openspec/specs/alembic-compare/spec.md` already specifies the safe behavior — "existing database triggers in managed
schemas are NOT dropped (only explicitly declared triggers are managed)" — and the implementation contradicts it. This
change resolves that in favor of the spec.

## What Changes

- An absent `pg_functions` / `pg_triggers` / `pg_views` key defaults to `IGNORED` rather than `()`, leaving that object
  type unmanaged: not inspected, not diffed, no operations emitted
- The "no keys configured at all" fast path is removed as a special case — it falls out of the existing all-`IGNORED`
  short-circuit
- The `INFO` log listing unmanaged object types now covers types left unmanaged by omission, not only explicit `IGNORED`
- Unrecognized `pg_*` options that closely resemble a recognized key (`pg_view`, `pg_function`) produce a warning naming
  the intended key, so the typo class that the old destructive default caught loudly still fails loudly

An empty sequence is unaffected: `pg_views=[]` still declares "there should be no views" and still drops existing ones.

## Non-goals

- **Changing what an empty sequence means** — `pg_views=[]` stays destructive; the `collect_view_ddl() or IGNORED` idiom
  remains the answer for dynamically built DDL lists
- **Removing `IGNORED`** — it stays public and documented; it is still how you record an explicit opt-out at the
  configuration site and how you assign a conditional value
- **Per-object ignore rules** — still a per-type switch, not a name or pattern based exclusion list
- **Rejecting unknown options** — unrecognized `pg_*` keys warn when they look like a typo; nothing is raised, and keys
  belonging to other plugins are left alone

## Capabilities

### Modified Capabilities

- `alembic-compare`: absent desired-state keys default to `IGNORED`; typo-like `pg_*` options are reported

## Impact

- **Public API**: No new exports. Behavior change for configurations that set at least one key, omit another, and have
  undeclared objects of the omitted type — those stop generating `DROP` operations
- **Performance**: Omitted types cost no catalog queries, matching explicit `IGNORED`
- **Docs**: README and quick-start shift from "every object type is managed by default" to "a type is managed once you
  declare it"
