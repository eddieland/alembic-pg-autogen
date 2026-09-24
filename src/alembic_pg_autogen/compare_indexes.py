"""Comparator that checks PostgreSQL index definitions during autogenerate.

Alembic compares the columns, the expressions, uniqueness, and the ``NULLS NOT DISTINCT`` flag of an index. The Alembic
index signature does not contain the ``WHERE`` predicate of a partial index. It also does not contain the access method,
the ``INCLUDE`` columns, or the operator classes. If a model changes one of these parts, autogenerate emits no
migration. Alembic compares expressions as text, and it removes casts and quotes first. Thus two different expressions
can give the same string.

This module asks PostgreSQL to compare the definitions. It creates each metadata index in the server and compares the
result with the ``pg_get_indexdef()`` output of the catalog. A changed predicate, operator class, access method, or
expression then gives a drop/create pair.

This comparator adds to the Alembic index comparator. It does not replace it. Alembic owns existence, which covers
indexes on one side only. Alembic also owns each index that it already found different. This comparator runs after
Alembic, at :attr:`~alembic.util.DispatchPriority.LAST`. It examines only indexes that exist on both sides and that have
no Alembic operation. Thus the two comparators never emit an operation for the same index.

The comparator is a separate plugin (``alembic_pg_autogen.indexes``). Users can disable it without the function,
trigger, view, and check constraint comparators. It runs only for the ``postgresql`` dialect.
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
"""Autogenerate option that renders index operations with ``CREATE INDEX CONCURRENTLY``."""


def setup(plugin: Plugin) -> None:
    """Register the index definition comparator with the Alembic plugin system."""
    plugin.add_autogenerate_comparator(
        _compare_index_definitions,
        "table",
        "index_definitions",
        qualifier="postgresql",
        # The Alembic index comparator runs at MEDIUM. This comparator runs last, so it sees the Alembic operations
        # and skips those indexes. Thus one index never gets two drop/create pairs.
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
    """Emit drop/create operations for indexes with a changed PostgreSQL definition."""
    # Alembic handles a table that exists on one side only. The index is part of the CREATE TABLE or DROP TABLE.
    if conn_table is None or metadata_table is None:
        return PriorityDispatchResult.CONTINUE

    conn = autogen_context.connection
    if conn is None:  # offline autogenerate has no database for the normalization
        return PriorityDispatchResult.CONTINUE

    # ``Index.name`` is a ``quoted_name``, a subclass of ``str``. ``str()`` gives the mapping one exact key type.
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
    """Return the names of indexes that already have an operation from the Alembic comparator.

    Alembic runs first and owns two decisions. It decides if an index exists on both sides. For the parts of an index
    that it compares, it decides if the index changed. This comparator skips each index that has an Alembic operation.
    Otherwise the migration contains two drop/create pairs for one index.
    """
    names: set[str] = set()
    for op in modify_table_ops.ops:
        name = getattr(op, "index_name", None)
        if isinstance(name, str):
            names.add(name)
    return frozenset(names)


def _index_ops(conn_index: Index, metadata_index: Index, *, concurrently: bool) -> list[MigrateOperation]:
    """Build the drop/create pair for a changed index, in that order.

    ``from_index()`` makes the migration reversible. Each operation holds a real :class:`~sqlalchemy.schema.Index`.
    Thus ``downgrade()`` restores the definition from the catalog.
    """
    drop = ops.DropIndexOp.from_index(conn_index)
    create = ops.CreateIndexOp.from_index(metadata_index)
    if not concurrently:
        return [drop, create]
    return [DropIndexConcurrentlyOp(drop), CreateIndexConcurrentlyOp(create)]


def _describe(unique: bool, shape: str) -> str:
    """Format the canonical payload of an index for a log message."""
    return f"{'UNIQUE ' if unique else ''}{shape}"
