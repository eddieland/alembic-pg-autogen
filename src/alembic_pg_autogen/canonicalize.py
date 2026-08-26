"""Desired-state canonicalization through PostgreSQL round-tripping."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import CreateIndex

from alembic_pg_autogen.inspect import IndexInfo, current_schema, inspect_functions, inspect_triggers, inspect_views
from alembic_pg_autogen.sentinels import IGNORED

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy import Connection, Index

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


def canonicalize_indexes(
    conn: Connection,
    *,
    schema: str | None,
    table_name: str,
    indexes: Mapping[str, Index],
) -> Mapping[str, IndexInfo]:
    """Canonicalize desired indexes by round-tripping them through PostgreSQL.

    Each index is created inside a savepoint on an empty ``TEMP`` clone of the target table, read back with
    ``pg_get_indexdef()``, and then rolled back — leaving the database unchanged.  The result is directly comparable
    with :func:`inspect_indexes <alembic_pg_autogen.inspect.inspect_indexes>`, which reads the live catalog through
    the same deparse.  That is the whole point of the round-trip: ``WHERE status IN ('a', 'b')`` in a SQLAlchemy model
    and ``WHERE (status = ANY (ARRAY['a'::text, 'b'::text]))`` in the catalog are the same index, and only PostgreSQL
    can say so.

    Probing a clone rather than the real table is what makes this affordable.  Unlike a ``NOT VALID`` check constraint,
    ``CREATE INDEX`` really does build the index: on a 500k-row table an expression index took roughly 1.2 seconds and a
    GIN index roughly 2.1, against under a millisecond on an empty clone.  The clone also keeps autogenerate from
    holding a lock on the real table for the rest of the transaction.  ``CREATE TEMP TABLE ... (LIKE ...)`` copies the
    column names, types, and collations that the deparse depends on, so the canonical form it produces is the same one
    the real table would have produced.

    The clone takes the target table's own name inside ``pg_temp``, and the index DDL is executed under a
    ``schema_translate_map`` that redirects the table's schema there.  Both are needed: the map handles metadata that
    names its schema explicitly, and the shared name handles metadata that leaves it implicit.

    Args:
        conn: An open SQLAlchemy connection (may have an active transaction).
        schema: Schema qualifying *table_name*, or *None* to resolve it through the connection's ``search_path``.
        table_name: The table the indexes belong to.  It must already exist in the database.
        indexes: Mapping of index name to the SQLAlchemy :class:`~sqlalchemy.schema.Index` to normalize.

    Returns:
        A mapping of index name to :class:`~alembic_pg_autogen.inspect.IndexInfo`, carrying *schema* and *table_name*
        as given so the result compares directly against the live catalog.  Names whose index could not be created —
        an expression that does not compile, a column that does not exist yet, an operator class the access method
        does not accept — are absent from the result rather than raising, so a single unusable index cannot break
        autogenerate.
    """
    if not indexes:
        return {}

    preparer = conn.dialect.identifier_preparer
    qualified = preparer.quote(table_name)
    if schema is not None:
        qualified = f"{preparer.quote_schema(schema)}.{qualified}"

    normalized: dict[str, IndexInfo] = {}
    savepoint = conn.begin_nested()
    try:
        conn.execute(text(f"CREATE TEMP TABLE {preparer.quote(table_name)} (LIKE {qualified})"))
        # ``None`` covers metadata that leaves the schema implicit; *schema* covers metadata that names it.
        probe_conn = conn.execution_options(schema_translate_map={None: _TEMP_SCHEMA, schema: _TEMP_SCHEMA})
        created: list[str] = []
        for name, index in indexes.items():
            probe = conn.begin_nested()
            try:
                probe_conn.execute(CreateIndex(index))
            except DBAPIError:
                probe.rollback()
                log.warning("Could not canonicalize index %r on %s; treating it as unchanged", name, qualified)
                log.debug("Index canonicalization failure for %r", name, exc_info=True)
                continue
            probe.commit()
            created.append(name)

        if created:
            rows = conn.execute(text(_INDEX_PROBE_QUERY), {"names": created}).all()
            for row in rows:
                if row.shape is None:
                    log.warning(
                        "Could not normalize the probed definition of index %r; treating it as unchanged", row.name
                    )
                    continue
                normalized[row.name] = IndexInfo(
                    schema=schema if schema is not None else current_schema(conn),
                    table_name=table_name,
                    name=row.name,
                    unique=row.unique,
                    shape=row.shape,
                )
    except DBAPIError:
        log.warning("Could not canonicalize indexes on %s; treating them as unchanged", qualified, exc_info=True)
        normalized = {}
    finally:
        savepoint.rollback()
        log.debug("Index canonicalization savepoint rolled back")

    missing = set(indexes) - set(normalized)
    if missing:
        log.warning("Canonicalization produced no definition for indexes: %s", sorted(missing))

    return normalized


_TEMP_SCHEMA = "pg_temp"
"""Alias the probe clone is addressed through when redirecting the index DDL.

``pg_temp`` always *resolves* to the session's temporary schema, but it is not always how PostgreSQL *prints* one.
``pg_get_indexdef()`` deparses the table reference with ``get_namespace_name_or_temp()`` on PostgreSQL 15 and newer,
which collapses the backing ``pg_temp_N`` namespace to the alias ``pg_temp``; PostgreSQL 14 has no such collapsing and
prints ``pg_temp_3.t``.  The probe query therefore has to accept both spellings — see :data:`_INDEX_PROBE_QUERY`."""

_INDEX_PROBE_QUERY = f"""\
SELECT
    ic.relname AS name,
    i.indisunique AS unique,
    CASE
        WHEN left(d.definition, length(d.aliased)) = d.aliased THEN substr(d.definition, length(d.aliased) + 1)
        WHEN left(d.definition, length(d.qualified)) = d.qualified THEN substr(d.definition, length(d.qualified) + 1)
    END AS shape
FROM pg_catalog.pg_index i
JOIN pg_catalog.pg_class ic ON ic.oid = i.indexrelid
JOIN pg_catalog.pg_class tc ON tc.oid = i.indrelid
JOIN pg_catalog.pg_namespace tn ON tn.oid = tc.relnamespace
CROSS JOIN LATERAL (
    SELECT
        pg_catalog.pg_get_indexdef(i.indexrelid) AS definition,
        h.head || '{_TEMP_SCHEMA}.' || pg_catalog.quote_ident(tc.relname) || ' ' AS aliased,
        h.head || pg_catalog.quote_ident(tn.nspname) || '.' || pg_catalog.quote_ident(tc.relname) || ' ' AS qualified
    FROM (
        SELECT 'CREATE '
            || CASE WHEN i.indisunique THEN 'UNIQUE ' ELSE '' END
            || 'INDEX '
            || pg_catalog.quote_ident(ic.relname)
            || ' ON ' AS head
    ) h
) d
WHERE ic.relname = ANY(:names)
  AND tc.relnamespace = pg_my_temp_schema()
"""
"""Read the probed indexes back, stripping the identity that precedes the shape.

Two spellings of the clone's table reference are accepted because PostgreSQL changed how it prints one: 15 and newer
collapse the temporary namespace to the ``pg_temp`` alias, while 14 prints the backing ``pg_temp_3``.  Trying the alias
first and the real namespace second covers both without asking the server its version.  A definition matching neither
yields NULL, which the caller reports as "could not normalize" and treats as unchanged — the safe direction, since a
half-stripped shape could never equal a catalog one and would show up as a permanent phantom difference.
"""

_PROBE_PREFIX = "_alembic_pg_autogen_probe_"

_PROBE_QUERY = """\
SELECT
    con.conname AS name,
    pg_catalog.pg_get_expr(con.conbin, con.conrelid, true) AS expression
FROM pg_catalog.pg_constraint con
WHERE con.conrelid = CAST(:table AS regclass)
  AND con.conname = ANY(:names)
"""


def _declared(ddl: Sequence[str] | Ignored) -> Sequence[str]:
    """Return the DDL statements to execute, treating :data:`~alembic_pg_autogen.IGNORED` as "none"."""
    return () if ddl is IGNORED else ddl
