"""Catalog inspection for PostgreSQL functions and triggers."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy import Connection


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


def inspect_functions(conn: Connection, schemas: Sequence[str] | None = None) -> Sequence[FunctionInfo]:
    """Bulk-load function definitions from PostgreSQL system catalogs.

    Queries ``pg_proc`` joined with ``pg_namespace`` to retrieve all user-defined
    functions and procedures.  Uses ``pg_get_functiondef()`` for canonical DDL and
    ``pg_get_function_identity_arguments()`` for the overload-distinguishing
    argument signature.

    Args:
        conn: An open SQLAlchemy connection.
        schemas: Optional list of schema names to inspect.  When *None*, all
            schemas except ``pg_catalog`` and ``information_schema`` are included.

    Returns:
        A sequence of :class:`FunctionInfo` instances, one per function/procedure.
    """
    schema_filter, params = _build_schema_filter(schemas)
    query = text(_FUNCTIONS_QUERY.format(schema_filter=schema_filter))
    rows = conn.execute(query, params)
    return [
        FunctionInfo(schema=r.schema, name=r.name, identity_args=r.identity_args, definition=r.definition) for r in rows
    ]


def inspect_triggers(conn: Connection, schemas: Sequence[str] | None = None) -> Sequence[TriggerInfo]:
    """Bulk-load trigger definitions from PostgreSQL system catalogs.

    Queries ``pg_trigger`` joined with ``pg_class`` and ``pg_namespace`` to
    retrieve all user-defined (non-internal) triggers.  Uses
    ``pg_get_triggerdef()`` for canonical DDL.

    Args:
        conn: An open SQLAlchemy connection.
        schemas: Optional list of schema names to inspect.  When *None*, all
            schemas except ``pg_catalog`` and ``information_schema`` are included.

    Returns:
        A sequence of :class:`TriggerInfo` instances, one per trigger.
    """
    schema_filter, params = _build_schema_filter(schemas)
    query = text(_TRIGGERS_QUERY.format(schema_filter=schema_filter))
    rows = conn.execute(query, params)
    return [
        TriggerInfo(schema=r.schema, table_name=r.table_name, trigger_name=r.trigger_name, definition=r.definition)
        for r in rows
    ]


_EXCLUDED_SCHEMAS = ("pg_catalog", "information_schema")

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
ORDER BY n.nspname, c.relname, t.tgname
"""


def _build_schema_filter(schemas: Sequence[str] | None) -> tuple[str, dict[str, object]]:
    """Build the SQL WHERE clause fragment and bind params for schema filtering."""
    if schemas is not None:
        return "n.nspname = ANY(:schemas)", {"schemas": list(schemas)}
    excluded = list(_EXCLUDED_SCHEMAS)
    return "n.nspname != ALL(:excluded_schemas)", {"excluded_schemas": excluded}
