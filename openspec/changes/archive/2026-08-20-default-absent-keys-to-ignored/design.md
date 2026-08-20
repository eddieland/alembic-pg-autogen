## Context

`_compare_pg_objects()` reads three desired-state keys from `autogen_context.opts` and resolves each absent key to `()`:

```python
if "pg_functions" not in opts and "pg_triggers" not in opts and "pg_views" not in opts:
    return PriorityDispatchResult.CONTINUE

pg_functions = _resolve_ddl(opts.get("pg_functions", ()))
```

`()` is a declaration — "there should be none of these" — so an undeclared type is inspected and everything found in it
is dropped. The guard above then carves out the all-absent case, which means absence has two different meanings
depending on the other keys. The `ignore-object-types` change added `IGNORED` as the opt-out but deliberately left the
absent-key semantics alone; this change finishes that work.

## Goals / Non-Goals

**Goals:**

- Omitting a key is non-destructive, and means the same thing regardless of the other keys
- No new configuration surface — the fix is a changed default, not a new option
- Keep the loud failure for a misspelled key, which the destructive default provided incidentally
- `pg_views=[]` keeps its current meaning exactly

**Non-Goals:**

- Name- or pattern-based exclusions within an object type
- Making an empty sequence non-destructive
- Raising on unknown configuration keys

## Decisions

### D1: The default value is the sentinel

`opts.get("pg_functions", IGNORED)` rather than a separate "was it present?" branch. `_resolve_ddl()` already passes
`IGNORED` through untouched and every downstream site already narrows on `is IGNORED`, so the sentinel reaches the exact
code paths an explicit `pg_functions=IGNORED` reaches. There is no second representation of "unmanaged" to keep in sync.

### D2: The all-absent fast path is deleted, not kept

With `IGNORED` as the default, three absent keys resolve to three sentinels and the existing all-`IGNORED` short-circuit
returns `CONTINUE` before the connection is touched. Keeping the `"pg_functions" not in opts and ...` guard would be a
second spelling of the same rule. The `INFO` log that lists unmanaged types is skipped in that case, as it already is
for explicit all-`IGNORED`, so a project that does not use this library at all stays silent.

### D3: Typo detection by close match, not by namespace

The destructive default caught `pg_view=[...]` immediately: the misspelling meant views were undeclared, so autogenerate
proposed dropping them all. Under the new default that typo silently disables view management instead.

Warning on *every* unrecognized `pg_*` key would police a namespace this library does not own — another plugin's
`pg_extensions` option is not a typo. Instead `difflib.get_close_matches` compares unrecognized `pg_*` keys against the
three recognized names at a 0.8 cutoff and warns only on a near miss, naming the intended key:

```
Unrecognized autogenerate option 'pg_view' — did you mean 'pg_views'?
```

This is checked once per autogenerate run, before the short-circuit, so it fires even when every type is unmanaged —
which is precisely the case a typo produces.

### D4: The log message covers omission

`log.info("PostgreSQL object types left unmanaged: %s", ...)` replaces the previous "Ignoring ..." wording. The list is
built from the resolved values, so it does not distinguish an explicit `IGNORED` from an omitted key — the observable
behavior is identical, and the message answers the question a confused user actually has ("why is my new view not
detected?").

## Risks / Trade-offs

- **Silent no-op on omission.** Forgetting `pg_views` now produces nothing instead of an unmissable pile of `DROP VIEW`.
  Mitigated by D3 for misspellings and by D4 for everything else; the failure mode is a missing operation the user
  notices, not a destructive one they might approve.
- **Behavior change for existing configurations.** Only configurations that set one key, omit another, and hold
  undeclared objects of the omitted type change — and they change by no longer emitting drops nobody asked for. Writing
  `pg_views=[]` is the way to keep the old behavior, and is what anyone deliberately clearing views already writes.
- **Close-match cutoff is a heuristic.** `pg_func` is too far from `pg_functions` to warn. A stricter cutoff would catch
  more typos at the cost of warning about unrelated options; 0.8 catches the single-character slips that make up the
  realistic cases.
- **Overlap with the unarchived `add-views` delta.** That change's version of the "Desired-state configuration keys"
  requirement is superseded here; its own scenarios already assert the non-destructive reading, so the two agree once
  this delta lands.
