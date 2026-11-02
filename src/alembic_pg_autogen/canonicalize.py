"""Desired-state canonicalization through PostgreSQL round-tripping."""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.schema import CreateIndex

from alembic_pg_autogen.inspect import IndexInfo, current_schema, inspect_functions, inspect_triggers, inspect_views
from alembic_pg_autogen.sentinels import IGNORED

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

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

    The function adds each expression as a throwaway ``NOT VALID`` constraint to an empty ``TEMP`` copy of the table,
    inside a savepoint. It reads each one back with ``pg_get_expr()``, and then rolls back the savepoint, so the
    database does not change. ``CREATE TEMP TABLE ... (LIKE ...)`` copies the column names, types, and collations that
    the deparse uses, so the copy gives the same canonical form as the real table. The copy takes only an
    ``ACCESS SHARE`` lock on the real table. An ``ALTER TABLE`` on the real table would take ``ACCESS EXCLUSIVE``, and
    every reader of the table would wait behind autogenerate.

    The returned expressions are directly comparable with :attr:`CheckConstraintInfo.expression
    <alembic_pg_autogen.inspect.CheckConstraintInfo.expression>`, which is read back through the same
    ``pg_get_expr()`` deparse. That is the whole point of the round trip: ``amount >= 0`` in a SQLAlchemy model and
    ``(amount >= (0)::numeric)`` in the catalog are the same constraint, and only PostgreSQL can say so.

    Each expression runs in its own nested savepoint. An expression that PostgreSQL rejects is absent from the result,
    and the other expressions still normalize.

    Args:
        conn: An open SQLAlchemy connection with an active transaction.
        schema: Schema qualifying *table_name*, or *None* to resolve it through the connection's ``search_path``.
        table_name: The table the constraints belong to. It must already exist in the database.
        expressions: Mapping of a key, such as the constraint name, to the raw ``CHECK`` expression text.

    Returns:
        A mapping of key to normalized expression. Keys whose expression could not be applied are absent from the
        result rather than raising, so a single unusable constraint cannot break autogenerate. Examples are an invalid
        expression, a column that does not exist yet, and a connection without a transaction.
    """
    if not expressions:
        return {}

    preparer = conn.dialect.identifier_preparer
    normalized: dict[str, str] = {}
    with _check_probe_clone(conn, schema=schema, table_name=table_name) as clone:
        if clone is not None:
            try:
                probes: dict[str, str] = {}
                for index, (key, expression) in enumerate(expressions.items()):
                    probe = f"{_PROBE_PREFIX}{index}"
                    statement = (
                        f"ALTER TABLE {clone} ADD CONSTRAINT {preparer.quote(probe)} CHECK ({expression}) NOT VALID"
                    )
                    probe_savepoint = conn.begin_nested()
                    if _execute_probe(conn, statement, expression, clone):
                        probe_savepoint.commit()
                        probes[probe] = key
                    else:
                        probe_savepoint.rollback()
                if probes:
                    rows = conn.execute(text(_PROBE_QUERY), {"table": clone, "names": list(probes)}).all()
                    normalized = {probes[row.name]: row.expression for row in rows}
            except SQLAlchemyError:
                log.warning("Could not canonicalize check constraints on %s", clone, exc_info=True)
                normalized = {}

    missing = set(expressions) - set(normalized)
    if missing:
        log.warning("Canonicalization produced no expression for check constraints: %s", sorted(missing))

    return normalized


def choose_check_constraint_names(
    conn: Connection,
    *,
    schema: str | None,
    table_name: str,
    expressions: Sequence[str],
    reserved: Iterable[str] = (),
) -> Sequence[str | None]:
    """Return the name that PostgreSQL gives each unnamed ``CHECK`` expression when it is added to a table.

    PostgreSQL names an unnamed constraint after the table and the referenced column, for example
    ``orders_amount_check``. It appends a number while the name is taken by any constraint in the schema, and it
    truncates the name at 63 bytes. This function does not predict that rule. It asks PostgreSQL. It creates an empty
    ``TEMP`` copy of the table inside a savepoint and adds each expression to the copy without a name, in the order
    given. When the copy chooses a name that the real schema or *reserved* already holds, the function adds a
    placeholder constraint under that name and asks again. Then it rolls back the savepoint. The copy takes only an
    ``ACCESS SHARE`` lock on the real table.

    The expressions are added one after the other. A later expression therefore sees the names of the earlier ones. A
    migration that adds the same constraints in the same order under the returned names gives the same result. Each
    expression runs in its own nested savepoint, so an expression that PostgreSQL rejects affects only its own entry.

    Args:
        conn: An open SQLAlchemy connection with an active transaction.
        schema: Schema qualifying *table_name*, or *None* to resolve it through the connection's ``search_path``.
        table_name: The table the constraints belong to. It must already exist in the database.
        expressions: The ``CHECK`` expression texts, in the order a migration adds them.
        reserved: Names that the same migration creates before these constraints, such as the named constraints that
            Alembic adds. PostgreSQL does not choose them.

    Returns:
        One name per expression, in the same order. The entry is *None* for an expression that PostgreSQL rejects, for
        example one that references a column that does not exist yet. Every entry is *None* when the copy cannot be
        created. A warning is logged in both cases.
    """
    if not expressions:
        return []

    names: list[str | None] = [None] * len(expressions)
    with _check_probe_clone(conn, schema=schema, table_name=table_name) as clone:
        if clone is None:
            return names
        try:
            names = _choose_names(conn, clone, _qualified_table(conn, schema, table_name), expressions, reserved)
        except SQLAlchemyError:
            log.warning("Could not choose names for check constraints on %s", clone, exc_info=True)

    return names


def _choose_names(
    conn: Connection, clone: str, real: str, expressions: Sequence[str], reserved: Iterable[str]
) -> list[str | None]:
    """Add each expression to *clone* without a name and return the names PostgreSQL chose, as the real table would.

    The copy lives in ``pg_temp``, so PostgreSQL cannot see the constraint names of the real schema. When it chooses
    one of those names, or a reserved name, the probe is rolled back and a placeholder takes the name. PostgreSQL
    tries ``base``, ``base1``, ``base2`` in order, so the next probe gives the name that the real table would get.
    """
    preparer = conn.dialect.identifier_preparer
    taken = set(conn.execute(text(_SCHEMA_CONSTRAINT_NAMES_QUERY), {"table": real}).scalars()) | set(reserved)
    names: list[str | None] = []
    for expression in expressions:
        statement = f"ALTER TABLE {clone} ADD CHECK ({expression}) NOT VALID"
        name: str | None = None
        while True:
            before = set(conn.execute(text(_CONSTRAINT_NAMES_QUERY), {"table": clone}).scalars())
            probe = conn.begin_nested()
            if not _execute_probe(conn, statement, expression, clone):
                probe.rollback()
                break
            (chosen,) = set(conn.execute(text(_CONSTRAINT_NAMES_QUERY), {"table": clone}).scalars()) - before
            if chosen not in taken:
                probe.commit()
                name = chosen
                taken.add(chosen)
                break
            probe.rollback()
            conn.execute(text(f"ALTER TABLE {clone} ADD CONSTRAINT {preparer.quote(chosen)} CHECK (true)"))
        names.append(name)
    return names


def canonicalize_indexes(
    conn: Connection,
    *,
    schema: str | None,
    table_name: str,
    indexes: Mapping[str, Index],
) -> Mapping[str, IndexInfo]:
    """Canonicalize desired indexes through a round trip in PostgreSQL.

    The function creates each index on an empty ``TEMP`` copy of the target table, inside a savepoint. It reads the
    index back with ``pg_get_indexdef()``. Then it rolls back the savepoint, so the database does not change. The
    result compares directly with :func:`inspect_indexes <alembic_pg_autogen.inspect.inspect_indexes>`, which reads
    the live catalog through the same deparse. For example, ``WHERE status IN ('a', 'b')`` in a SQLAlchemy model and
    ``WHERE (status = ANY (ARRAY['a'::text, 'b'::text]))`` in the catalog are the same index. Only PostgreSQL can
    confirm this.

    The copy keeps the test fast. A ``NOT VALID`` check constraint skips validation, but ``CREATE INDEX`` builds the
    index. On a table with 500,000 rows, an expression index took about 1.2 seconds and a GIN index took about 2.1
    seconds. On an empty copy, each took less than one millisecond. The copy also prevents a lock on the real table for
    the rest of the transaction. ``CREATE TEMP TABLE ... (LIKE ...)`` copies the column names, types, and collations
    that the deparse uses. Thus the copy gives the same canonical form as the real table.

    The copy has the name of the target table inside ``pg_temp``. The index DDL runs under a ``schema_translate_map``
    that changes the schema of the table to ``pg_temp``. The function needs both mechanisms. The map handles metadata
    with an explicit schema. The shared name handles metadata with no explicit schema.

    Args:
        conn: An open SQLAlchemy connection. It can have an active transaction.
        schema: The schema of *table_name*. With *None*, the ``search_path`` of the connection finds the table.
        table_name: The table of the indexes. The table must exist in the database.
        indexes: A mapping of index name to the SQLAlchemy :class:`~sqlalchemy.schema.Index` to normalize.

    Returns:
        A mapping of index name to :class:`~alembic_pg_autogen.inspect.IndexInfo`. Each entry holds *schema* and
        *table_name* as given, so the result compares directly with the live catalog. If the function cannot create an
        index, the result does not contain its name. The function does not raise, so one bad index cannot stop
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
    except SQLAlchemyError:
        # The copy must have the name of the target table, or the DDL does not find it. A temporary relation with
        # that name on this connection therefore blocks the test. The table itself has no problem. The function
        # has no place to create the test indexes, so it does not compare them in this run.
        log.warning(
            "Could not create a temporary probe clone of %s; treating its indexes as unchanged. A temporary relation "
            "already named %r on this connection would cause this.",
            qualified,
            table_name,
        )
        log.debug("Probe clone creation failed for %s", qualified, exc_info=True)
        savepoint.rollback()
        return {}

    try:
        # ``None`` covers metadata with no explicit schema. *schema* covers metadata with an explicit schema.
        probe_conn = conn.execution_options(schema_translate_map={None: _TEMP_SCHEMA, schema: _TEMP_SCHEMA})
        created: list[str] = []
        for name, index in indexes.items():
            probe = conn.begin_nested()
            # Catch ``SQLAlchemyError``, not ``DBAPIError``. SQLAlchemy compiles the statement inside ``execute()``.
            # An index that SQLAlchemy cannot render raises ``CompileError`` before the server gets the statement.
            # An example is an expression whose type has no literal renderer. When the code caught only the server
            # error, one such index stopped the run and the comparison of all other indexes on the table.
            try:
                probe_conn.execute(CreateIndex(index))
            except SQLAlchemyError:
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
    except SQLAlchemyError:
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
"""The schema name that the index DDL uses to find the copy.

``pg_temp`` always *finds* the temporary schema of the session. But PostgreSQL does not always *write* the schema with
that name. On PostgreSQL 15 and later, ``pg_get_indexdef()`` writes the table reference through
``get_namespace_name_or_temp()``. That function writes ``pg_temp`` in place of the real name ``pg_temp_N``. PostgreSQL
14 writes the real name, for example ``pg_temp_3.t``. The test query therefore accepts both spellings. See
:data:`_INDEX_PROBE_QUERY`."""

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
"""Read the test indexes back, and remove the identity before the shape.

The query accepts two spellings of the table reference of the copy. PostgreSQL 15 and later write the temporary schema
as ``pg_temp``. PostgreSQL 14 writes the real name, for example ``pg_temp_3``. The query tries ``pg_temp`` first and
the real name second. Thus it does not need the server version. If neither matches, the query returns NULL. The caller
then logs "could not normalize" and reports the index as unchanged. This result is safe. A partly stripped shape can
never equal a catalog shape, so it causes a permanent false difference.
"""

_PROBE_PREFIX = "_alembic_pg_autogen_probe_"

_BIND_PARAMS = re.compile(r"(?<![:\w\x5c]):(\w+)(?!:)", re.UNICODE)
"""The pattern that ``TextClause`` uses to find bind parameters. User-authored SQL escapes each match."""

_PROBE_QUERY = """\
SELECT
    con.conname AS name,
    pg_catalog.pg_get_expr(con.conbin, con.conrelid, true) AS expression
FROM pg_catalog.pg_constraint con
WHERE con.conrelid = CAST(:table AS regclass)
  AND con.conname = ANY(:names)
"""

_CONSTRAINT_NAMES_QUERY = """\
SELECT con.conname
FROM pg_catalog.pg_constraint con
WHERE con.conrelid = CAST(:table AS regclass)
"""

_SCHEMA_CONSTRAINT_NAMES_QUERY = """\
SELECT con.conname
FROM pg_catalog.pg_constraint con
WHERE con.connamespace = (SELECT c.relnamespace FROM pg_catalog.pg_class c WHERE c.oid = CAST(:table AS regclass))
"""


@contextmanager
def _check_probe_clone(conn: Connection, *, schema: str | None, table_name: str) -> Iterator[str | None]:
    """Yield the qualified name of an empty ``TEMP`` copy of the table, inside a savepoint, or *None* on failure.

    The copy has the name of the target table inside ``pg_temp``. PostgreSQL names an unnamed constraint after its
    table, so the shared name keeps the names that the copy chooses equal to those of the real table. The savepoint is
    always rolled back, so the copy never outlives the block.
    """
    qualified = _qualified_table(conn, schema, table_name)
    clone = f"{_TEMP_SCHEMA}.{conn.dialect.identifier_preparer.quote(table_name)}"

    try:
        savepoint = conn.begin_nested()
    except SQLAlchemyError:
        # An AUTOCOMMIT connection has no transaction to hold a savepoint.
        log.warning("Could not open a savepoint to probe check constraints on %s", qualified, exc_info=True)
        yield None
        return

    try:
        try:
            conn.execute(text(f"CREATE TEMP TABLE {clone} (LIKE {qualified})"))
        except SQLAlchemyError:
            # A temporary relation with the same name on this connection blocks the copy, as for indexes.
            log.warning(
                "Could not create a temporary probe clone of %s; treating its check constraints as unchanged",
                qualified,
                exc_info=True,
            )
            yield None
        else:
            yield clone
    finally:
        savepoint.rollback()
        log.debug("Check constraint probe savepoint rolled back")


def _qualified_table(conn: Connection, schema: str | None, table_name: str) -> str:
    """Return *table_name* quoted, and qualified by *schema* when one is given."""
    preparer = conn.dialect.identifier_preparer
    qualified = preparer.quote(table_name)
    if schema is not None:
        qualified = f"{preparer.quote_schema(schema)}.{qualified}"
    return qualified


def _execute_probe(conn: Connection, statement: str, expression: str, clone: str) -> bool:
    """Run one probe statement and return whether PostgreSQL accepted it.

    The caller wraps the call in a nested savepoint, so a rejected statement rolls back only that savepoint. The
    statement holds user-authored SQL from the model's own metadata, so its colons are escaped before
    :func:`~sqlalchemy.text` reads them. ``SQLAlchemyError`` is caught, not ``DBAPIError``, because a statement error
    raises before the server sees the statement.
    """
    try:
        conn.execute(text(escape_bind_colons(statement)))
    except SQLAlchemyError:
        log.warning("Could not probe check expression %r on %s", expression, clone)
        log.debug("Check expression probe failure for %r", expression, exc_info=True)
        return False
    return True


def escape_bind_colons(sql: str) -> str:
    r"""Escape each colon that :func:`~sqlalchemy.text` would read as a bind parameter, such as ``:b`` in ``'a :b'``.

    The pattern is the one SQLAlchemy uses for bind parameters in ``TextClause``. A cast such as ``::numeric`` does not
    match it. ``text()`` turns each ``\:`` back into a plain colon.
    """
    return _BIND_PARAMS.sub(r"\\\g<0>", sql)


def _declared(ddl: Sequence[str] | Ignored) -> Sequence[str]:
    """Return the DDL statements to execute, treating :data:`~alembic_pg_autogen.IGNORED` as "none"."""
    return () if ddl is IGNORED else ddl
