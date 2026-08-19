"""Desired-state canonicalization through PostgreSQL round-tripping."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import text

from alembic_pg_autogen.inspect import inspect_functions, inspect_triggers, inspect_views
from alembic_pg_autogen.sentinels import IGNORED

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy import Connection

    from alembic_pg_autogen.inspect import FunctionInfo, TriggerInfo, ViewInfo
    from alembic_pg_autogen.sentinels import Ignored


class CanonicalState(NamedTuple):
    """Post-DDL catalog snapshot returned by :func:`canonicalize`."""

    functions: Sequence[FunctionInfo]
    triggers: Sequence[TriggerInfo]
    views: Sequence[ViewInfo] = ()


def canonicalize(
    conn: Connection,
    *,
    function_ddl: Sequence[str] | Ignored = (),
    view_ddl: Sequence[str] | Ignored = (),
    trigger_ddl: Sequence[str] | Ignored = (),
    schemas: Sequence[str] | None = None,
) -> CanonicalState:
    """Canonicalize user-provided DDL by round-tripping through PostgreSQL.

    Executes the given DDL statements inside a savepoint, reads back canonical forms via ``inspect_functions`` /
    ``inspect_views`` / ``inspect_triggers``, then rolls back the savepoint — leaving the database unchanged.

    DDL executes in dependency order: functions first (standalone), then views (may reference functions), then triggers
    (may reference functions and INSTEAD OF triggers may be on views).

    An object type passed as :data:`~alembic_pg_autogen.IGNORED` is skipped entirely: no DDL is executed for it and its
    catalog is not read back, so the corresponding :class:`CanonicalState` field is empty.

    Args:
        conn: An open SQLAlchemy connection (may have an active transaction).
        function_ddl: ``CREATE FUNCTION`` / ``CREATE PROCEDURE`` statements, or :data:`~alembic_pg_autogen.IGNORED`.
        view_ddl: ``CREATE VIEW`` statements, or :data:`~alembic_pg_autogen.IGNORED`.
        trigger_ddl: ``CREATE TRIGGER`` statements, or :data:`~alembic_pg_autogen.IGNORED`.
        schemas: Optional schema list passed to the inspect helpers.  When *None*, all user schemas are included.

    Returns:
        A :class:`CanonicalState` with the full post-DDL catalog state.

    Raises:
        sqlalchemy.exc.DBAPIError: If any DDL statement is invalid.
    """
    function_stmts = _declared(function_ddl)
    view_stmts = _declared(view_ddl)
    trigger_stmts = _declared(trigger_ddl)
    log.info(
        "Canonicalizing %d function, %d view, and %d trigger DDL statements",
        len(function_stmts),
        len(view_stmts),
        len(trigger_stmts),
    )
    import postgast

    functions: Sequence[FunctionInfo] = ()
    views: Sequence[ViewInfo] = ()
    triggers: Sequence[TriggerInfo] = ()

    savepoint = conn.begin_nested()
    try:
        for ddl in function_stmts:
            conn.execute(text(postgast.ensure_or_replace(ddl)))
        for ddl in view_stmts:
            conn.execute(text(postgast.ensure_or_replace(ddl)))
        for ddl in trigger_stmts:
            conn.execute(text(postgast.ensure_or_replace(ddl)))

        if function_ddl is not IGNORED:
            functions = inspect_functions(conn, schemas)
        if view_ddl is not IGNORED:
            views = inspect_views(conn, schemas)
        if trigger_ddl is not IGNORED:
            triggers = inspect_triggers(conn, schemas)
    finally:
        savepoint.rollback()
        log.debug("Canonicalization savepoint rolled back")

    if function_stmts and not functions:
        log.warning("Canonicalization produced no functions despite %d function DDL statements", len(function_stmts))
    if view_stmts and not views:
        log.warning("Canonicalization produced no views despite %d view DDL statements", len(view_stmts))
    if trigger_stmts and not triggers:
        log.warning("Canonicalization produced no triggers despite %d trigger DDL statements", len(trigger_stmts))

    return CanonicalState(functions=functions, triggers=triggers, views=views)


def canonicalize_functions(
    conn: Connection,
    ddl: Sequence[str],
    schemas: Sequence[str] | None = None,
) -> Sequence[FunctionInfo]:
    """Canonicalize function DDL and return the resulting ``FunctionInfo`` list.

    Convenience wrapper around :func:`canonicalize` with only *function_ddl* populated; the other object types are
    ignored, so their catalogs are not read back.
    """
    return canonicalize(
        conn,
        function_ddl=ddl,
        view_ddl=IGNORED,
        trigger_ddl=IGNORED,
        schemas=schemas,
    ).functions


def canonicalize_triggers(
    conn: Connection,
    ddl: Sequence[str],
    schemas: Sequence[str] | None = None,
) -> Sequence[TriggerInfo]:
    """Canonicalize trigger DDL and return the resulting ``TriggerInfo`` list.

    Convenience wrapper around :func:`canonicalize` with only *trigger_ddl* populated; the other object types are
    ignored, so their catalogs are not read back.
    """
    return canonicalize(
        conn,
        trigger_ddl=ddl,
        function_ddl=IGNORED,
        view_ddl=IGNORED,
        schemas=schemas,
    ).triggers


def canonicalize_views(
    conn: Connection,
    ddl: Sequence[str],
    schemas: Sequence[str] | None = None,
) -> Sequence[ViewInfo]:
    """Canonicalize view DDL and return the resulting ``ViewInfo`` list.

    Convenience wrapper around :func:`canonicalize` with only *view_ddl* populated; the other object types are
    ignored, so their catalogs are not read back.
    """
    return canonicalize(
        conn,
        view_ddl=ddl,
        function_ddl=IGNORED,
        trigger_ddl=IGNORED,
        schemas=schemas,
    ).views


def _declared(ddl: Sequence[str] | Ignored) -> Sequence[str]:
    """Return the DDL statements to execute, treating :data:`~alembic_pg_autogen.IGNORED` as "none"."""
    return () if ddl is IGNORED else ddl
