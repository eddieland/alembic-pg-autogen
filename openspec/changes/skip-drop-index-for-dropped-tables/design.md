## Context

Alembic's `_compare_tables()` handles a removed table in two steps. It first dispatches the `table` comparators with
`metadata_table=None`, which appends a `ModifyTableOps` that holds one `DropIndexOp` per index. It then appends the
`DropTableOp`. The reverse holds for an added table: `downgrade()` receives a `ModifyTableOps` of `DropIndexOp` entries
followed by a `DropTableOp`. The `DropIndexOp` entries are redundant in both functions.

The cookbook recipe removes them in a `process_revision_directives` function. This package already registers two Alembic
plugins, but `alembic.runtime.plugins.Plugin` only exposes `add_autogenerate_comparator()`. A comparator runs before the
directive tree exists, so the recipe cannot become a plugin. It has to stay a `process_revision_directives` hook that
the user passes to `context.configure()`.

## Goals / Non-Goals

**Goals:**

- Ship the recipe once, tested, so that projects stop copying it into `env.py`
- Handle `upgrade()` and `downgrade()`, and every `UpgradeOps` container of a multi-database script
- Compose with a `process_revision_directives` hook the project already uses
- Change nothing for a project that does not opt in

**Non-Goals:**

- Enabling the hook by default (no extension point exists)
- Removing anything other than a redundant `DropIndexOp`

## Decisions

### D1: Ship an Alembic `Rewriter`, not a plain function

```python
skip_drop_index_for_dropped_tables: Final = Rewriter()


@skip_drop_index_for_dropped_tables.rewrites(ops.UpgradeOps)
@skip_drop_index_for_dropped_tables.rewrites(ops.DowngradeOps)
def _remove_drop_index_for_dropped_tables(_context, _revision, directive):
    dropped = _dropped_tables(directive.ops)
    if dropped:
        directive.ops = list(_without_drop_index(directive.ops, dropped))
    return directive
```

A `Rewriter` is callable with the `process_revision_directives` signature, so the simple case is unchanged:
`process_revision_directives=skip_drop_index_for_dropped_tables`. Two properties make it better than the cookbook's
plain function:

- `Rewriter.chain()` accepts another `Rewriter` or a plain function. A project that already has a hook composes the two
  in one line, in either order. `chain()` returns a copy, so the module-level instance stays unchanged.
- `Rewriter._traverse_script()` visits every entry of `upgrade_ops_list` and `downgrade_ops_list`. The cookbook reads
  `directives[0].upgrade_ops`, which handles one container only. The multi-database template produces several.

**Alternative considered:** a plain function with the cookbook's body. It cannot be chained without wrapping, and the
multi-database case needs extra code.

### D2: Rewrite the container, not the `DropIndexOp`

`Rewriter.rewrites(ops.DropIndexOp)` would receive one operation at a time, with no view of its siblings. The decision
whether to keep a `DropIndexOp` depends on the `DropTableOp` entries elsewhere in the same function. The rewrite is
therefore registered for `UpgradeOps` and `DowngradeOps`. The function collects the dropped tables from the container,
filters the container in place, and returns it. The `Rewriter` then descends into the remaining children as usual.

### D3: Match on `(table_name, schema)`

`DropTableOp` and `DropIndexOp` both carry `table_name` and `schema`. Autogenerate fills both from the reflected table,
and uses `None` for the default schema on both sides. The rewriter compares the pair, so `archive.orders` never matches
`orders`. A `DropIndexOp` with no `table_name` matches nothing and stays.

### D4: Filter `ModifyTableOps` recursively and drop empty containers

Autogenerate nests the `DropIndexOp` inside a `ModifyTableOps` for the dropped table. The rewriter filters that
container's operations with the same rule and removes the container when nothing remains. A container that still holds
other operations stays, with those operations intact. This matches the cookbook exactly.

## Risks / Trade-offs

- **A module-level `Rewriter` instance is mutable.** `rewrites()` on the shared instance registers a function for every
  user of the package. The docstring points users at `chain()`, which copies. This matches how Alembic's own
  documentation treats `writer` as a module global.
- **The rewriter never sees the connection.** It decides from the directive tree alone, so it cannot check whether an
  index is in fact owned by the table. Autogenerate only emits a `DropIndexOp` with the owning table's name, so the
  directive tree is sufficient.
- **A hand-written `DropIndexOp` is removed too.** A project that adds its own `DropIndexOp` for a dropped table in an
  earlier hook loses it. That operation is redundant for the same reason, so the loss is harmless.
