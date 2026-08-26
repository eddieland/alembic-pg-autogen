"""Comparator that verifies PostgreSQL-specific index definitions during autogenerate.

Alembic compares an index's columns, expressions, uniqueness, and ``NULLS NOT DISTINCT`` flag.  What its index
signature does not include is the rest of what makes a PostgreSQL index what it is: the ``WHERE`` predicate of a
partial index, the access method, the ``INCLUDE`` columns, and the operator classes.  Change any of those in a model
and autogenerate emits nothing — not a wrong migration, but no migration at all, forever.  Alembic's expression
comparison is also a text heuristic that strips casts and quotes before comparing, so two genuinely different
expressions can reduce to the same string.

This module closes both gaps for PostgreSQL by asking PostgreSQL itself.  Each metadata index is round-tripped
through the server and compared against the catalog's own ``pg_get_indexdef()`` output, so a changed predicate,
operator class, access method, or expression produces a drop/create pair instead of silently drifting.

It complements Alembic's index comparator rather than replacing it.  Alembic owns existence — indexes present on only
one side — and it owns any index it has already decided differs; this comparator runs afterwards, at
:attr:`~alembic.util.DispatchPriority.LAST`, and only considers indexes that are present on both sides and that
Alembic left alone.  The two never emit an operation for the same index.

It is registered as its own plugin (``alembic_pg_autogen.indexes``) so it can be disabled independently of the
function/trigger/view and check constraint comparators, and only fires for the ``postgresql`` dialect.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from alembic.operations import ops
from alembic.util import DispatchPriority, PriorityDispatchResult

from alembic_pg_autogen.canonicalize import canonicalize_indexes
from alembic_pg_autogen.inspect import current_schema, inspect_indexes
from alembic_pg_autogen.ops import CreateIndexConcurrentlyOp, DropIndexConcurrentlyOp

if TYPE_CHECKING:
    from alembic.autogenerate.api import AutogenContext
    from alembic.operations.ops import MigrateOperation, ModifyTableOps
    from alembic.runtime.plugins import Plugin
    from sqlalchemy import Index, Table

log = logging.getLogger(__name__)

CONCURRENTLY_OPTION = "pg_index_concurrently"
"""Autogenerate option that switches rendering to ``CREATE INDEX CONCURRENTLY``."""


def setup(plugin: Plugin) -> None:
    """Register the index definition comparator with Alembic's plugin system."""
    plugin.add_autogenerate_comparator(
        _compare_index_definitions,
        "table",
        "index_definitions",
        qualifier="postgresql",
        # Alembic's own index comparator runs at MEDIUM.  Running last lets this one see what Alembic decided and
        # leave those indexes alone, so a single index never draws two drop/create pairs.
        priority=DispatchPriority.LAST,
    )
    log.debug("alembic-pg-autogen index comparator registered")


def _compare_index_definitions(
    autogen_context: AutogenContext,
    modify_table_ops: ModifyTableOps,
    schema: str | None,
    table_name: str,
    conn_table: Table | None,
    metadata_table: Table | None,
) -> PriorityDispatchResult:
    """Emit drop/create operations for indexes whose PostgreSQL-specific definition changed."""
    # A table that exists on only one side is handled by Alembic: the index travels with the CREATE/DROP TABLE.
    if conn_table is None or metadata_table is None:
        return PriorityDispatchResult.CONTINUE

    conn = autogen_context.connection
    if conn is None:  # offline autogenerate has nothing to normalize against
        return PriorityDispatchResult.CONTINUE

    # ``Index.name`` is a ``quoted_name``, a ``str`` subclass; ``str()`` keeps the mapping key type invariant-safe.
    metadata_indexes = {str(index.name): index for index in metadata_table.indexes if index.name is not None}
    if not metadata_indexes:
        return PriorityDispatchResult.CONTINUE

    resolved_schema = schema if schema is not None else current_schema(conn)
    current = {info.name: info for info in inspect_indexes(conn, schemas=[resolved_schema], table_names=[table_name])}

    already_handled = _names_already_emitted(modify_table_ops)
    shared = [
        name
        for name in sorted(set(metadata_indexes) & set(current))
        if name not in already_handled
        and autogen_context.run_name_filters(name, "index", {"table_name": table_name, "schema_name": schema})
    ]
    if not shared:
        return PriorityDispatchResult.CONTINUE

    reflected = {str(index.name): index for index in conn_table.indexes if index.name is not None}
    candidates = {name: metadata_indexes[name] for name in shared if name in reflected}
    if not candidates:
        return PriorityDispatchResult.CONTINUE

    desired = canonicalize_indexes(conn, schema=resolved_schema, table_name=table_name, indexes=candidates)

    concurrently = bool(autogen_context.opts.get(CONCURRENTLY_OPTION, False))  # pyright: ignore[reportAttributeAccessIssue]
    for name in sorted(candidates):
        canonical = desired.get(name)
        if canonical is None:
            continue
        existing = current[name]
        if (canonical.unique, canonical.shape) == (existing.unique, existing.shape):
            continue

        metadata_index = metadata_indexes[name]
        conn_index = reflected[name]
        if not autogen_context.run_object_filters(metadata_index, name, "index", False, conn_index):
            continue

        log.info(
            "Detected changed index %r on table %r: %r to %r",
            name,
            table_name,
            _describe(existing.unique, existing.shape),
            _describe(canonical.unique, canonical.shape),
        )
        modify_table_ops.ops.extend(_index_ops(conn_index, metadata_index, concurrently=concurrently))

    return PriorityDispatchResult.CONTINUE


def _names_already_emitted(modify_table_ops: ModifyTableOps) -> frozenset[str]:
    """Return the names of indexes Alembic's own comparator has already emitted operations for.

    Alembic runs first and owns two decisions this comparator must not second-guess: whether an index exists on both
    sides, and — for the parts of an index it does compare — whether it changed.  Any index it has already acted on is
    therefore off limits, or the migration would carry two drop/create pairs for one index.
    """
    names: set[str] = set()
    for op in modify_table_ops.ops:
        name = getattr(op, "index_name", None)
        if isinstance(name, str):
            names.add(name)
    return frozenset(names)


def _index_ops(conn_index: Index, metadata_index: Index, *, concurrently: bool) -> list[MigrateOperation]:
    """Build the drop/create pair for a changed index, in that order.

    Building both operations with ``from_index()`` is what makes the migration reversible: each op carries a real
    :class:`~sqlalchemy.schema.Index`, so ``downgrade()`` restores the definition read from the catalog.
    """
    drop = ops.DropIndexOp.from_index(conn_index)
    create = ops.CreateIndexOp.from_index(metadata_index)
    if not concurrently:
        return [drop, create]
    return [DropIndexConcurrentlyOp(drop), CreateIndexConcurrentlyOp(create)]


def _describe(unique: bool, shape: str) -> str:
    """Render an index's canonical payload for a log message."""
    return f"{'UNIQUE ' if unique else ''}{shape}"
