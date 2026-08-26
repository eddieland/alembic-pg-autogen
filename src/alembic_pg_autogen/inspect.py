"""Catalog inspection for PostgreSQL functions, triggers, views, check constraints, and indexes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy import Connection

log = logging.getLogger(__name__)


class FunctionInfo(NamedTuple):
    """A PostgreSQL function or procedure as loaded from the system catalog."""

    schema: str
    name: str
    identity_args: str
    definition: str


class TriggerInfo(NamedTuple):
    """A PostgreSQL trigger as loaded from the system catalog."""

    schema: str
    table_name: str
    trigger_name: str
    definition: str


class ViewInfo(NamedTuple):
    """A PostgreSQL view as loaded from the system catalog.

    Identity is ``(schema, name)``; ``definition`` is the last field by convention.
    """

    schema: str
    name: str
    definition: str


class CheckConstraintInfo(NamedTuple):
    """A PostgreSQL ``CHECK`` constraint as loaded from the system catalog.

    Identity is ``(schema, table_name, name)``.  Unlike the other catalog types, the trailing payload field is
    ``expression`` rather than ``definition``: check constraints are rendered by Alembic's own
    ``op.create_check_constraint()`` / ``op.drop_constraint()`` operations, so what matters here is the normalized
    expression text that PostgreSQL deparses from the stored parse tree, not an executable ``ALTER TABLE`` statement.
    """

    schema: str
    table_name: str
    name: str
    expression: str


class IndexInfo(NamedTuple):
    """A PostgreSQL index as loaded from the system catalog.

    Identity is ``(schema, table_name, name)``. The payload is ``(unique, shape)`` rather than a single ``definition``
    field, because a ``CREATE INDEX`` statement carries part of its meaning ahead of the part that varies: ``UNIQUE``
    sits in the statement head, next to the name and table that make up the identity. Splitting it out keeps *shape*
    a self-contained fragment that compares as a plain string.

    ``shape`` is everything ``pg_get_indexdef()`` emits from ``USING`` onward: access method, key expressions and
    their operator classes, ``INCLUDE``, ``NULLS NOT DISTINCT``, storage parameters, and the ``WHERE`` predicate::

        USING btree (lower(email)) INCLUDE (name) WHERE (deleted_at IS NULL)

    Because the index name and table are stripped, two shapes are comparable even when they were read back from
    different tables, which is exactly what :func:`canonicalize_indexes
    <alembic_pg_autogen.canonicalize.canonicalize_indexes>` relies on when it probes a throwaway clone.
    """

    schema: str
    table_name: str
    name: str
    unique: bool
    shape: str


def inspect_functions(conn: Connection, schemas: Sequence[str] | None = None) -> Sequence[FunctionInfo]:
    """Bulk-load function definitions from PostgreSQL system catalogs.

    Queries ``pg_proc`` joined with ``pg_namespace`` to retrieve all user-defined functions and procedures.  Uses
    ``pg_get_functiondef()`` for canonical DDL and ``pg_get_function_identity_arguments()`` for the
    overload-distinguishing argument signature.

    Args:
        conn: An open SQLAlchemy connection.
        schemas: Optional list of schema names to inspect.  When *None*, all schemas except ``pg_catalog`` and
            ``information_schema`` are included.

    Returns:
        A sequence of :class:`FunctionInfo` instances, one per function/procedure.
    """
    schema_filter, params = _build_schema_filter(schemas)
    query = text(_FUNCTIONS_QUERY.format(schema_filter=schema_filter))
    rows = conn.execute(query, params)
    result = [
        FunctionInfo(schema=r.schema, name=r.name, identity_args=r.identity_args, definition=r.definition) for r in rows
    ]
    log.debug("Inspected %d functions (schemas=%s)", len(result), schemas)
    return result


def inspect_triggers(conn: Connection, schemas: Sequence[str] | None = None) -> Sequence[TriggerInfo]:
    """Bulk-load trigger definitions from PostgreSQL system catalogs.

    Queries ``pg_trigger`` joined with ``pg_class`` and ``pg_namespace`` to retrieve all user-defined (non-internal)
    triggers.  Uses ``pg_get_triggerdef()`` for canonical DDL.

    Args:
        conn: An open SQLAlchemy connection.
        schemas: Optional list of schema names to inspect.  When *None*, all schemas except ``pg_catalog`` and
            ``information_schema`` are included.

    Returns:
        A sequence of :class:`TriggerInfo` instances, one per trigger.
    """
    schema_filter, params = _build_schema_filter(schemas)
    query = text(_TRIGGERS_QUERY.format(schema_filter=schema_filter))
    rows = conn.execute(query, params)
    result = [
        TriggerInfo(schema=r.schema, table_name=r.table_name, trigger_name=r.trigger_name, definition=r.definition)
        for r in rows
    ]
    log.debug("Inspected %d triggers (schemas=%s)", len(result), schemas)
    return result


def inspect_views(conn: Connection, schemas: Sequence[str] | None = None) -> Sequence[ViewInfo]:
    """Bulk-load view definitions from PostgreSQL system catalogs.

    Queries ``pg_class`` joined with ``pg_namespace`` to retrieve all user-defined regular views (``relkind = 'v'``).
    Reconstructs the full ``CREATE OR REPLACE VIEW schema.name AS`` DDL by combining ``quote_ident()`` and
    ``pg_get_viewdef(oid, true)`` in the SQL query so that ``ViewInfo.definition`` contains complete DDL.

    Args:
        conn: An open SQLAlchemy connection.
        schemas: Optional list of schema names to inspect.  When *None*, all schemas except ``pg_catalog`` and
            ``information_schema`` are included.

    Returns:
        A sequence of :class:`ViewInfo` instances, one per view.
    """
    schema_filter, params = _build_schema_filter(schemas)
    query = text(_VIEWS_QUERY.format(schema_filter=schema_filter))
    rows = conn.execute(query, params)
    result = [ViewInfo(schema=r.schema, name=r.name, definition=r.definition) for r in rows]
    log.debug("Inspected %d views (schemas=%s)", len(result), schemas)
    return result


def inspect_check_constraints(
    conn: Connection,
    schemas: Sequence[str] | None = None,
    table_names: Sequence[str] | None = None,
) -> Sequence[CheckConstraintInfo]:
    """Bulk-load ``CHECK`` constraint expressions from PostgreSQL system catalogs.

    Queries ``pg_constraint`` joined with ``pg_class`` and ``pg_namespace`` for table-level check constraints
    (``contype = 'c'``).  Uses ``pg_get_expr(conbin, conrelid, true)`` to deparse the stored expression tree, which is
    the normalized form PostgreSQL itself produces — the same form :func:`canonicalize_check_constraints
    <alembic_pg_autogen.canonicalize.canonicalize_check_constraints>` reads back for desired-state expressions, so the
    two are directly comparable as strings.

    Constraints owned by an extension are excluded, as are domain constraints (they have no ``conrelid``) and — on
    PostgreSQL 18+ — ``NOT NULL`` constraints, which use ``contype = 'n'``.

    Args:
        conn: An open SQLAlchemy connection.
        schemas: Schemas to inspect.  When *None*, every schema except ``pg_catalog`` and ``information_schema``.
        table_names: Tables to restrict the query to.  When *None*, all tables are included.

    Returns:
        A sequence of :class:`CheckConstraintInfo` instances, one per check constraint.
    """
    schema_filter, params = _build_schema_filter(schemas)
    if table_names is not None:
        table_filter = "c.relname = ANY(:table_names)"
        params["table_names"] = list(table_names)
    else:
        table_filter = "true"
    query = text(_CHECK_CONSTRAINTS_QUERY.format(schema_filter=schema_filter, table_filter=table_filter))
    rows = conn.execute(query, params)
    result = [
        CheckConstraintInfo(schema=r.schema, table_name=r.table_name, name=r.name, expression=r.expression)
        for r in rows
    ]
    log.debug("Inspected %d check constraints (schemas=%s, tables=%s)", len(result), schemas, table_names)
    return result


def inspect_indexes(
    conn: Connection,
    schemas: Sequence[str] | None = None,
    table_names: Sequence[str] | None = None,
) -> Sequence[IndexInfo]:
    """Bulk-load index definitions from PostgreSQL system catalogs.

    Queries ``pg_index`` joined with ``pg_class`` and ``pg_namespace``, using ``pg_get_indexdef()`` to obtain the
    canonical ``CREATE INDEX`` statement PostgreSQL itself would emit. The statement's identity prefix, ``CREATE
    [UNIQUE] INDEX <name> ON <schema>.<table>``, is stripped in SQL so that :attr:`IndexInfo.shape` holds only the
    part that describes what the index *does*.

    Stripping is verified rather than assumed: the prefix is rebuilt with ``quote_ident()`` and compared against the
    definition's leading characters. An index whose definition does not start with the expected prefix is omitted
    entirely rather than reported with an unstripped shape, since an unstripped shape could never match a canonicalized
    one and would show up as a permanent phantom difference.

    Indexes that implement a constraint are excluded: a primary key, unique, or exclusion constraint owns its index,
    and Alembic compares those as constraints. Extension-owned indexes are excluded on the same basis as extension-owned
    functions and triggers.

    Args:
        conn: An open SQLAlchemy connection.
        schemas: Schemas to inspect. When *None*, every schema except ``pg_catalog`` and ``information_schema``.
        table_names: Tables to restrict the query to. When *None*, all tables are included.

    Returns:
        A sequence of :class:`IndexInfo` instances, one per index.
    """
    schema_filter, params = _build_schema_filter(schemas)
    if table_names is not None:
        table_filter = "tc.relname = ANY(:table_names)"
        params["table_names"] = list(table_names)
    else:
        table_filter = "true"
    query = text(_INDEXES_QUERY.format(schema_filter=schema_filter, table_filter=table_filter))
    rows = conn.execute(query, params)
    result: list[IndexInfo] = []
    for r in rows:
        if r.shape is None:
            log.warning(
                "Could not normalize the definition of index %r on %r.%r; skipping it",
                r.name,
                r.schema,
                r.table_name,
            )
            continue
        result.append(IndexInfo(schema=r.schema, table_name=r.table_name, name=r.name, unique=r.unique, shape=r.shape))
    log.debug("Inspected %d indexes (schemas=%s, tables=%s)", len(result), schemas, table_names)
    return result


def current_schema(conn: Connection) -> str:
    """Return the connection's current schema, i.e. the first entry of its ``search_path``."""
    schema = conn.execute(text("SELECT current_schema()")).scalar()
    assert schema is not None, "Failed to read current_schema()"
    return schema


_EXCLUDED_SCHEMAS = ("pg_catalog", "information_schema")

_VIEWS_QUERY = """\
SELECT
    n.nspname AS schema,
    c.relname AS name,
    'CREATE OR REPLACE VIEW ' || quote_ident(n.nspname) || '.' || quote_ident(c.relname)
        || ' AS' || chr(10) || pg_get_viewdef(c.oid, true) AS definition
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'v'
  AND ({schema_filter})
ORDER BY n.nspname, c.relname
"""

_CHECK_CONSTRAINTS_QUERY = """\
SELECT
    n.nspname AS schema,
    c.relname AS table_name,
    con.conname AS name,
    pg_catalog.pg_get_expr(con.conbin, con.conrelid, true) AS expression
FROM pg_catalog.pg_constraint con
JOIN pg_catalog.pg_class c ON c.oid = con.conrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE con.contype = 'c'
  AND ({schema_filter})
  AND ({table_filter})
  AND NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_depend d
      WHERE d.classid = 'pg_catalog.pg_constraint'::regclass
        AND d.objid = con.oid
        AND d.deptype = 'e'
  )
ORDER BY n.nspname, c.relname, con.conname
"""

_INDEXES_QUERY = """\
SELECT
    n.nspname AS schema,
    tc.relname AS table_name,
    ic.relname AS name,
    i.indisunique AS unique,
    CASE
        WHEN left(d.definition, length(d.prefix)) = d.prefix
        THEN substr(d.definition, length(d.prefix) + 1)
    END AS shape
FROM pg_catalog.pg_index i
JOIN pg_catalog.pg_class ic ON ic.oid = i.indexrelid
JOIN pg_catalog.pg_class tc ON tc.oid = i.indrelid
JOIN pg_catalog.pg_namespace n ON n.oid = tc.relnamespace
CROSS JOIN LATERAL (
    SELECT
        pg_catalog.pg_get_indexdef(i.indexrelid) AS definition,
        'CREATE '
            || CASE WHEN i.indisunique THEN 'UNIQUE ' ELSE '' END
            || 'INDEX '
            || pg_catalog.quote_ident(ic.relname)
            || ' ON '
            || pg_catalog.quote_ident(n.nspname) || '.' || pg_catalog.quote_ident(tc.relname)
            || ' ' AS prefix
) d
WHERE tc.relkind IN ('r', 'm', 'p')
  AND ({schema_filter})
  AND ({table_filter})
  AND NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_constraint con
      WHERE con.conindid = i.indexrelid
        AND con.contype IN ('p', 'u', 'x')
  )
  AND NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_depend dep
      WHERE dep.classid = 'pg_catalog.pg_class'::regclass
        AND dep.objid = i.indexrelid
        AND dep.deptype = 'e'
  )
ORDER BY n.nspname, tc.relname, ic.relname
"""

_FUNCTIONS_QUERY = """\
SELECT
    n.nspname AS schema,
    p.proname AS name,
    pg_get_functiondef(p.oid) AS definition,
    pg_catalog.pg_get_function_identity_arguments(p.oid) AS identity_args
FROM pg_catalog.pg_proc p
JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
WHERE p.prokind IN ('f', 'p')
  AND ({schema_filter})
  AND NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_depend d
      WHERE d.classid = 'pg_catalog.pg_proc'::regclass
        AND d.objid = p.oid
        AND d.deptype = 'e'
  )
ORDER BY n.nspname, p.proname, identity_args
"""

_TRIGGERS_QUERY = """\
SELECT
    n.nspname AS schema,
    c.relname AS table_name,
    t.tgname AS trigger_name,
    pg_catalog.pg_get_triggerdef(t.oid) AS definition
FROM pg_catalog.pg_trigger t
JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE NOT t.tgisinternal
  AND ({schema_filter})
  AND NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_depend d
      WHERE d.classid = 'pg_catalog.pg_trigger'::regclass
        AND d.objid = t.oid
        AND d.deptype = 'e'
  )
ORDER BY n.nspname, c.relname, t.tgname
"""


def _build_schema_filter(schemas: Sequence[str] | None) -> tuple[str, dict[str, object]]:
    """Build the SQL WHERE clause fragment and bind params for schema filtering."""
    if schemas is not None:
        return "n.nspname = ANY(:schemas)", {"schemas": list(schemas)}
    excluded = list(_EXCLUDED_SCHEMAS)
    return "n.nspname != ALL(:excluded_schemas)", {"excluded_schemas": excluded}
