"""Desired-state canonicalization through PostgreSQL round-tripping."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final, NamedTuple

from sqlalchemy import text

from alembic_pg_autogen._inspect import inspect_functions, inspect_triggers

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy import Connection

    from alembic_pg_autogen._inspect import FunctionInfo, TriggerInfo

_CREATE_FUNCTION_RE: Final = re.compile(r"CREATE\s+FUNCTION\b", re.IGNORECASE)
_CREATE_TRIGGER_RE: Final = re.compile(r"CREATE\s+TRIGGER\b", re.IGNORECASE)
_CREATE_OR_REPLACE_RE: Final = re.compile(r"CREATE\s+OR\s+REPLACE\b", re.IGNORECASE)


class CanonicalState(NamedTuple):
    """Post-DDL catalog snapshot returned by :func:`canonicalize`."""

    functions: Sequence[FunctionInfo]
    triggers: Sequence[TriggerInfo]


def canonicalize(
    conn: Connection,
    *,
    function_ddl: Sequence[str] = (),
    trigger_ddl: Sequence[str] = (),
    schemas: Sequence[str] | None = None,
) -> CanonicalState:
    """Canonicalize user-provided DDL by round-tripping through PostgreSQL.

    Executes the given DDL statements inside a savepoint, reads back canonical
    forms via ``inspect_functions`` / ``inspect_triggers``, then rolls back the
    savepoint — leaving the database unchanged.

    Function DDL is executed before trigger DDL so that triggers may reference
    functions declared in the same batch.

    Args:
        conn: An open SQLAlchemy connection (may have an active transaction).
        function_ddl: ``CREATE FUNCTION`` / ``CREATE PROCEDURE`` statements.
        trigger_ddl: ``CREATE TRIGGER`` statements.
        schemas: Optional schema list passed to the inspect helpers.  When
            *None*, all user schemas are included.

    Returns:
        A :class:`CanonicalState` with the full post-DDL catalog state.

    Raises:
        sqlalchemy.exc.DBAPIError: If any DDL statement is invalid.
    """
    savepoint = conn.begin_nested()
    try:
        for ddl in function_ddl:
            conn.execute(text(_ensure_or_replace(ddl, _CREATE_FUNCTION_RE)))
        for ddl in trigger_ddl:
            conn.execute(text(_ensure_or_replace(ddl, _CREATE_TRIGGER_RE)))

        functions = inspect_functions(conn, schemas)
        triggers = inspect_triggers(conn, schemas)
    finally:
        savepoint.rollback()

    return CanonicalState(functions=functions, triggers=triggers)


def canonicalize_functions(
    conn: Connection,
    ddl: Sequence[str],
    schemas: Sequence[str] | None = None,
) -> Sequence[FunctionInfo]:
    """Canonicalize function DDL and return the resulting ``FunctionInfo`` list.

    Convenience wrapper around :func:`canonicalize` with only *function_ddl*
    populated.
    """
    return canonicalize(conn, function_ddl=ddl, schemas=schemas).functions


def canonicalize_triggers(
    conn: Connection,
    ddl: Sequence[str],
    schemas: Sequence[str] | None = None,
) -> Sequence[TriggerInfo]:
    """Canonicalize trigger DDL and return the resulting ``TriggerInfo`` list.

    Convenience wrapper around :func:`canonicalize` with only *trigger_ddl*
    populated.
    """
    return canonicalize(conn, trigger_ddl=ddl, schemas=schemas).triggers


def _ensure_or_replace(ddl: str, pattern: re.Pattern[str]) -> str:
    """Rewrite ``CREATE FUNCTION/TRIGGER`` to ``CREATE OR REPLACE`` if needed.

    DDL executed during canonicalization may collide with objects already in the
    database.  Using ``OR REPLACE`` avoids ``DuplicateFunction`` /
    ``DuplicateObject`` errors inside the savepoint.  Statements that already
    contain ``OR REPLACE`` are returned unchanged.
    """
    if _CREATE_OR_REPLACE_RE.search(ddl):
        return ddl
    return pattern.sub(lambda m: m.group(0).replace("CREATE", "CREATE OR REPLACE", 1), ddl, count=1)
