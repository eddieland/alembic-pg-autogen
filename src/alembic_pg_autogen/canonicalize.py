"""Desired-state canonicalization through PostgreSQL round-tripping."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from alembic_pg_autogen.inspect import inspect_functions, inspect_triggers, inspect_views

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy import Connection

    from alembic_pg_autogen.inspect import FunctionInfo, TriggerInfo, ViewInfo


class CanonicalState(NamedTuple):
    """Post-DDL catalog snapshot returned by :func:`canonicalize`."""

    functions: Sequence[FunctionInfo]
    triggers: Sequence[TriggerInfo]
    views: Sequence[ViewInfo] = ()


def canonicalize(
    conn: Connection,
    *,
    function_ddl: Sequence[str] = (),
    view_ddl: Sequence[str] = (),
    trigger_ddl: Sequence[str] = (),
    schemas: Sequence[str] | None = None,
) -> CanonicalState:
    """Canonicalize user-provided DDL by round-tripping through PostgreSQL.

    Executes the given DDL statements inside a savepoint, reads back canonical forms via ``inspect_functions`` /
    ``inspect_views`` / ``inspect_triggers``, then rolls back the savepoint — leaving the database unchanged.

    DDL executes in dependency order: functions first (standalone), then views (may reference functions), then triggers
    (may reference functions and INSTEAD OF triggers may be on views).

    Args:
        conn: An open SQLAlchemy connection (may have an active transaction).
        function_ddl: ``CREATE FUNCTION`` / ``CREATE PROCEDURE`` statements.
        view_ddl: ``CREATE VIEW`` statements.
        trigger_ddl: ``CREATE TRIGGER`` statements.
        schemas: Optional schema list passed to the inspect helpers.  When *None*, all user schemas are included.

    Returns:
        A :class:`CanonicalState` with the full post-DDL catalog state.

    Raises:
        sqlalchemy.exc.DBAPIError: If any DDL statement is invalid.
    """
    log.info(
        "Canonicalizing %d function, %d view, and %d trigger DDL statements",
        len(function_ddl),
        len(view_ddl),
        len(trigger_ddl),
    )
    import postgast

    savepoint = conn.begin_nested()
    try:
        for ddl in function_ddl:
            conn.execute(text(postgast.ensure_or_replace(ddl)))
        for ddl in view_ddl:
            conn.execute(text(postgast.ensure_or_replace(ddl)))
        for ddl in trigger_ddl:
            conn.execute(text(postgast.ensure_or_replace(ddl)))

        functions = inspect_functions(conn, schemas)
        views = inspect_views(conn, schemas)
        triggers = inspect_triggers(conn, schemas)
    finally:
        savepoint.rollback()
        log.debug("Canonicalization savepoint rolled back")

    if function_ddl and not functions:
        log.warning("Canonicalization produced no functions despite %d function DDL statements", len(function_ddl))
    if view_ddl and not views:
        log.warning("Canonicalization produced no views despite %d view DDL statements", len(view_ddl))
    if trigger_ddl and not triggers:
        log.warning("Canonicalization produced no triggers despite %d trigger DDL statements", len(trigger_ddl))

    return CanonicalState(functions=functions, triggers=triggers, views=views)


def canonicalize_functions(
    conn: Connection,
    ddl: Sequence[str],
    schemas: Sequence[str] | None = None,
) -> Sequence[FunctionInfo]:
    """Canonicalize function DDL and return the resulting ``FunctionInfo`` list.

    Convenience wrapper around :func:`canonicalize` with only *function_ddl* populated.
    """
    return canonicalize(conn, function_ddl=ddl, schemas=schemas).functions


def canonicalize_triggers(
    conn: Connection,
    ddl: Sequence[str],
    schemas: Sequence[str] | None = None,
) -> Sequence[TriggerInfo]:
    """Canonicalize trigger DDL and return the resulting ``TriggerInfo`` list.

    Convenience wrapper around :func:`canonicalize` with only *trigger_ddl* populated.
    """
    return canonicalize(conn, trigger_ddl=ddl, schemas=schemas).triggers


def canonicalize_views(
    conn: Connection,
    ddl: Sequence[str],
    schemas: Sequence[str] | None = None,
) -> Sequence[ViewInfo]:
    """Canonicalize view DDL and return the resulting ``ViewInfo`` list.

    Convenience wrapper around :func:`canonicalize` with only *view_ddl* populated.
    """
    return canonicalize(conn, view_ddl=ddl, schemas=schemas).views


def canonicalize_check_constraints(
    conn: Connection,
    *,
    schema: str | None,
    table_name: str,
    expressions: Mapping[str, str],
) -> Mapping[str, str]:
    """Canonicalize desired ``CHECK`` expressions by round-tripping them through PostgreSQL.

    Each expression is added to the live table as a throwaway ``NOT VALID`` constraint inside a savepoint, read back
    with ``pg_get_expr()``, and then rolled back — leaving the database unchanged.  ``NOT VALID`` keeps the round-trip
    cheap: PostgreSQL skips the full-table validation scan, so no existing row is examined and rows that would violate
    a newly declared constraint do not turn autogenerate into an error.

    The returned expressions are directly comparable with :attr:`CheckConstraintInfo.expression
    <alembic_pg_autogen.inspect.CheckConstraintInfo.expression>`, which is read back through the same
    ``pg_get_expr()`` deparse.  That is the whole point of the round-trip: ``amount >= 0`` in a SQLAlchemy model and
    ``(amount >= (0)::numeric)`` in the catalog are the same constraint, and only PostgreSQL can say so.

    Args:
        conn: An open SQLAlchemy connection (may have an active transaction).
        schema: Schema qualifying *table_name*, or *None* to resolve it through the connection's ``search_path``.
        table_name: The table the constraints belong to.  It must already exist in the database.
        expressions: Mapping of constraint name to the raw ``CHECK`` expression text to normalize.

    Returns:
        A mapping of constraint name to normalized expression.  Names whose expression could not be applied — an
        invalid expression, a column that does not exist yet, a table the connection may not lock — are absent from
        the result rather than raising, so a single unusable constraint cannot break autogenerate.
    """
    if not expressions:
        return {}

    preparer = conn.dialect.identifier_preparer
    qualified = preparer.quote(table_name)
    if schema is not None:
        qualified = f"{preparer.quote_schema(schema)}.{qualified}"
    probes = {f"{_PROBE_PREFIX}{index}": name for index, name in enumerate(expressions)}

    savepoint = conn.begin_nested()
    try:
        for probe, name in probes.items():
            # The expression is user-authored SQL from the model's own metadata, interpolated the same way the rest of
            # this module interpolates user DDL.  It only ever runs inside this savepoint.
            conn.execute(
                text(
                    f"ALTER TABLE {qualified} ADD CONSTRAINT {preparer.quote(probe)} "
                    f"CHECK ({expressions[name]}) NOT VALID"
                )
            )
        rows = conn.execute(text(_PROBE_QUERY), {"table": qualified, "names": list(probes)}).all()
        normalized = {probes[row.name]: row.expression for row in rows}
    except DBAPIError:
        log.warning(
            "Could not canonicalize check constraints on %s; treating them as unchanged", qualified, exc_info=True
        )
        normalized = {}
    finally:
        savepoint.rollback()
        log.debug("Check constraint canonicalization savepoint rolled back")

    missing = set(expressions) - set(normalized)
    if missing:
        log.warning("Canonicalization produced no expression for check constraints: %s", sorted(missing))

    return normalized


_PROBE_PREFIX = "_alembic_pg_autogen_probe_"

_PROBE_QUERY = """\
SELECT
    con.conname AS name,
    pg_catalog.pg_get_expr(con.conbin, con.conrelid, true) AS expression
FROM pg_catalog.pg_constraint con
WHERE con.conrelid = CAST(:table AS regclass)
  AND con.conname = ANY(:names)
"""
