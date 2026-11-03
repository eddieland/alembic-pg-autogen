"""Desired-state canonicalization through PostgreSQL round-tripping."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from enum import Enum
from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.schema import CreateIndex
from sqlalchemy.sql.compiler import BIND_PARAMS

from alembic_pg_autogen.inspect import (
    GrantInfo,
    IndexInfo,
    current_schema,
    inspect_functions,
    inspect_triggers,
    inspect_views,
)
from alembic_pg_autogen.sentinels import IGNORED

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence
    from collections.abc import Set as AbstractSet
    from typing import Final

    from postgast.pg_query_pb2 import GrantStmt, RoleSpec
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
    ``ACCESS SHARE`` lock on the real table. An ``ALTER TABLE`` on the real table takes ``ACCESS EXCLUSIVE``, and every
    reader of the table would wait behind autogenerate.

    Some expressions cannot run on the copy. An example is a function call that takes the table's row type, because the
    copy has a row type of its own. When the server rejects an expression on the copy for a reason other than an
    unknown column, the function tries the expression once more on the real table. That attempt runs in its own
    savepoint with ``SET LOCAL lock_timeout``, so it waits at most one second for the lock.

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

    normalized: dict[str, str] = {}
    pending = dict(expressions)
    for probe_table in (_check_probe_clone, _check_probe_real_table):
        with probe_table(conn, schema=schema, table_name=table_name) as table:
            if table is not None:
                last = probe_table is _check_probe_real_table
                done, pending = _canonicalize_on(conn, table, pending, last_attempt=last)
                normalized.update(done)
        if not pending:
            break

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
    ``ACCESS SHARE`` lock on the real table. An expression that the copy rejects for a reason other than an unknown
    column is tried once more on the real table, as in :func:`canonicalize_check_constraints`.

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
        example one that references a column that does not exist yet. Every entry is *None* when no probe table can be
        used. A warning is logged in both cases.
    """
    if not expressions:
        return []

    names: list[str | None] = [None] * len(expressions)
    pending = list(enumerate(expressions))
    taken: set[str] | None = None
    for probe_table in (_check_probe_clone, _check_probe_real_table):
        with probe_table(conn, schema=schema, table_name=table_name) as table:
            if table is not None:
                try:
                    if taken is None:
                        real = _qualified_table(conn, schema, table_name)
                        rows = conn.execute(text(_SCHEMA_CONSTRAINT_NAMES_QUERY), {"table": real}).scalars()
                        taken = set(rows) | set(reserved)
                    last = probe_table is _check_probe_real_table
                    chosen, pending = _choose_names(conn, table, pending, taken, last_attempt=last)
                    for index, name in chosen.items():
                        names[index] = name
                except SQLAlchemyError:
                    log.warning("Could not choose names for check constraints on %s", table, exc_info=True)
        if not pending:
            break

    return names


def _canonicalize_on(
    conn: Connection, table: str, expressions: Mapping[str, str], *, last_attempt: bool
) -> tuple[dict[str, str], dict[str, str]]:
    """Probe each expression on *table* and return the normalized ones and the ones worth a retry elsewhere."""
    preparer = conn.dialect.identifier_preparer
    probes: dict[str, str] = {}
    retry: dict[str, str] = {}
    try:
        for index, (key, expression) in enumerate(expressions.items()):
            probe = f"{_PROBE_PREFIX}{index}"
            statement = f"ALTER TABLE {table} ADD CONSTRAINT {preparer.quote(probe)} CHECK ({expression}) NOT VALID"
            savepoint = conn.begin_nested()
            outcome = _execute_probe(conn, statement, expression, table, last_attempt=last_attempt)
            if outcome is _ProbeOutcome.ACCEPTED:
                savepoint.commit()
                probes[probe] = key
                continue
            savepoint.rollback()
            if outcome is _ProbeOutcome.RETRY:
                retry[key] = expression
        if not probes:
            return {}, retry
        rows = conn.execute(text(_PROBE_QUERY), {"table": table, "names": list(probes)}).all()
    except SQLAlchemyError:
        log.warning("Could not canonicalize check constraints on %s", table, exc_info=True)
        return {}, {}
    return {probes[row.name]: row.expression for row in rows}, retry


def _choose_names(
    conn: Connection, table: str, pending: Sequence[tuple[int, str]], taken: set[str], *, last_attempt: bool
) -> tuple[dict[int, str], list[tuple[int, str]]]:
    """Add each expression to *table* without a name and return the names PostgreSQL chose, as the real table would.

    A copy lives in ``pg_temp``, so PostgreSQL cannot see the constraint names of the real schema there. When it
    chooses one of those names, a reserved name, or a name chosen earlier, the probe is rolled back and a placeholder
    takes the name. PostgreSQL tries ``base``, ``base1``, ``base2`` in order, so the next probe gives the name that the
    real table would get. Each chosen name joins *taken*. The second return value holds the expressions worth a retry
    on another table.
    """
    preparer = conn.dialect.identifier_preparer
    chosen: dict[int, str] = {}
    retry: list[tuple[int, str]] = []
    for index, expression in pending:
        statement = f"ALTER TABLE {table} ADD CHECK ({expression}) NOT VALID"
        while True:
            before = set(conn.execute(text(_CONSTRAINT_NAMES_QUERY), {"table": table}).scalars())
            probe = conn.begin_nested()
            outcome = _execute_probe(conn, statement, expression, table, last_attempt=last_attempt)
            if outcome is not _ProbeOutcome.ACCEPTED:
                probe.rollback()
                if outcome is _ProbeOutcome.RETRY:
                    retry.append((index, expression))
                break
            (name,) = set(conn.execute(text(_CONSTRAINT_NAMES_QUERY), {"table": table}).scalars()) - before
            if name not in taken:
                probe.commit()
                chosen[index] = name
                taken.add(name)
                break
            probe.rollback()
            conn.execute(text(f"ALTER TABLE {table} ADD CONSTRAINT {preparer.quote(name)} CHECK (true) NOT VALID"))
    return chosen, retry


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


def canonicalize_grants(conn: Connection, ddl: Sequence[str]) -> Sequence[GrantInfo]:
    """Parse ``GRANT`` statements into the :class:`~alembic_pg_autogen.inspect.GrantInfo` form of the catalog.

    The function does not execute the statements. A grant can name a table that the same migration creates, so the
    table often does not exist yet. The parser of PostgreSQL (through postgast) reads each statement. The function then
    expands it to one ``GrantInfo`` per object, grantee, and privilege, as :func:`inspect_grants
    <alembic_pg_autogen.inspect.inspect_grants>` does. The connection supplies these values:

    - ``to_regclass()`` finds an existing relation through the ``search_path``, as the ``GRANT`` would. A name that
      finds no relation gets ``current_schema()``, because the migration creates that table in the first schema of
      the ``search_path``.
    - ``current_user`` and ``session_user`` replace ``CURRENT_USER``, ``CURRENT_ROLE``, and ``SESSION_USER``.
    - ``acldefault()`` gives the privileges of ``ALL`` for each object type. The set depends on the server version.
      For example, PostgreSQL 17 adds ``MAINTAIN`` for tables.

    Each statement must be a ``GRANT`` on named tables or schemas. A string can hold more than one statement.
    Equal grants merge into one. ``grantable`` is ``True`` if one of them has ``WITH GRANT OPTION``.

    Args:
        conn: An open SQLAlchemy connection.
        ddl: ``GRANT ... ON [TABLE] ...`` and ``GRANT ... ON SCHEMA ...`` statements.

    Returns:
        A sequence of :class:`~alembic_pg_autogen.inspect.GrantInfo` instances, sorted by identity.

    Raises:
        ValueError: If a statement is not a ``GRANT``, or if it uses a form that this library does not compare:
            ``REVOKE``, ``ON ALL TABLES IN SCHEMA``, ``GRANTED BY``, ``WITH GRANT OPTION`` for ``PUBLIC``, another
            object type, column privileges, or a privilege that the object type does not have. A name in
            ``ON [TABLE]`` that finds a sequence or another relation that is not a table also raises.
    """
    return parse_grants(conn, ddl).grants


class DeclaredGrants(NamedTuple):
    """The result of :func:`parse_grants`: the grants, and the targets that a statement declared with ``ALL``."""

    grants: Sequence[GrantInfo]
    """The grants in the form of :func:`canonicalize_grants`."""

    all_privileges: AbstractSet[tuple[str, str, str, str]]
    """The ``(schema, object_type, name, grantee)`` targets of ``GRANT ALL [PRIVILEGES]`` statements.

    The comparator renders ``ALL`` only for these targets. ``ALL`` means the privilege set of the server that runs the
    migration, so it must not replace a list of privileges that the declaration names one by one.
    """


def parse_grants(conn: Connection, ddl: Sequence[str]) -> DeclaredGrants:
    """Parse ``GRANT`` statements as :func:`canonicalize_grants` does, and also report which targets used ``ALL``.

    Raises:
        ValueError: For the statements that :func:`canonicalize_grants` rejects.
    """
    if not ddl:
        return DeclaredGrants((), frozenset())

    import postgast

    context = _GrantContext.read(conn)
    grantable_by_key: dict[tuple[str, str, str, str, str], bool] = {}
    all_targets: set[tuple[str, str, str, str]] = set()
    for statement in ddl:
        for raw in postgast.parse(statement).stmts:
            if raw.stmt.WhichOneof("node") != "grant_stmt":
                raise ValueError(f"pg_grants accepts only GRANT statements: {statement!r}")
            grant_stmt = raw.stmt.grant_stmt
            for grant in _expand_grant(conn, grant_stmt, context, statement):
                key = grant[:-1]
                grantable_by_key[key] = grantable_by_key.get(key, False) or grant.grantable
                if not grant_stmt.privileges:  # ``GRANT ALL [PRIVILEGES]`` has an empty privilege list
                    all_targets.add(grant[:3] + (grant.grantee,))

    grants = [GrantInfo(*key, grantable=grantable) for key, grantable in sorted(grantable_by_key.items())]
    log.debug("Canonicalized %d GRANT statements into %d grants", len(ddl), len(grants))
    return DeclaredGrants(grants, frozenset(all_targets))


def all_grant_privileges(conn: Connection) -> Mapping[str, AbstractSet[str]]:
    """Return the privileges of ``GRANT ALL`` on the connected server, for ``"TABLE"`` and for ``"SCHEMA"``.

    The comparator uses the result to render ``ALL`` for a statement that holds every privilege of its object type.
    A migration with ``ALL`` then runs on a server version with fewer privileges, too.
    """
    row = conn.execute(text(_GRANT_CONTEXT_QUERY)).one()
    return {"TABLE": frozenset(row.table_privileges), "SCHEMA": frozenset(row.schema_privileges)}


class _GrantContext(NamedTuple):
    """Values from the server that :func:`canonicalize_grants` needs to expand a ``GRANT`` statement."""

    schema: str
    current_user: str
    session_user: str
    all_privileges: Mapping[str, Sequence[str]]

    @classmethod
    def read(cls, conn: Connection) -> _GrantContext:
        """Read the values in one query."""
        row = conn.execute(text(_GRANT_CONTEXT_QUERY)).one()
        return cls(
            schema=row.schema,
            current_user=row.current_user,
            session_user=row.session_user,
            all_privileges={"TABLE": tuple(row.table_privileges), "SCHEMA": tuple(row.schema_privileges)},
        )


_GRANT_CONTEXT_QUERY = """\
SELECT
    current_schema() AS schema,
    current_user AS current_user,
    session_user AS session_user,
    ARRAY(SELECT privilege_type FROM pg_catalog.aclexplode(pg_catalog.acldefault('r', 10::oid))) AS table_privileges,
    ARRAY(SELECT privilege_type FROM pg_catalog.aclexplode(pg_catalog.acldefault('n', 10::oid))) AS schema_privileges
"""
"""Read the default schema, the session roles, and the privileges of ``ALL`` for tables and schemas.

``acldefault()`` returns the default ACL of an owner. The owner holds every privilege of the object type, so the
privileges in that ACL are the privileges of ``ALL``. Role 10 is the bootstrap superuser. The role does not matter,
because the query reads only the privilege names.
"""

_RELATION_QUERY = """\
SELECT n.nspname AS schema, c.relkind AS kind
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE c.oid = pg_catalog.to_regclass(
    CASE
        WHEN CAST(:schema AS text) IS NULL THEN pg_catalog.quote_ident(:name)
        ELSE pg_catalog.quote_ident(:schema) || '.' || pg_catalog.quote_ident(:name)
    END
)
"""
"""Find a relation as ``GRANT`` finds it: through the ``search_path`` when the name has no schema.

``quote_ident()`` runs in SQL, so the names reach the server as bind parameters and need no quoting in Python.
"""

_TABLE_KINDS: Final = frozenset({"r", "p", "v", "m", "f"})
"""``relkind`` values that ``GRANT ... ON TABLE`` covers in this library: tables, views, and foreign tables."""

_RELATION_KIND_NAMES: Final[Mapping[str, str]] = {
    "S": "a sequence",
    "i": "an index",
    "I": "a partitioned index",
    "c": "a composite type",
    "t": "a TOAST table",
}


def _expand_grant(conn: Connection, grant: GrantStmt, context: _GrantContext, statement: str) -> list[GrantInfo]:
    """Expand one parsed ``GRANT`` to one :class:`GrantInfo` per object, grantee, and privilege."""
    from postgast.pg_query_pb2 import ACL_TARGET_OBJECT, OBJECT_SCHEMA, OBJECT_TABLE, ROLESPEC_PUBLIC

    if not grant.is_grant:
        raise ValueError(f"pg_grants accepts only GRANT statements, not REVOKE: {statement!r}")
    if grant.targtype != ACL_TARGET_OBJECT:
        raise ValueError(f"pg_grants does not support GRANT ... ON ALL ... IN SCHEMA: {statement!r}")
    if grant.HasField("grantor"):
        raise ValueError(
            f"pg_grants does not support GRANTED BY, because the comparison ignores grantors: {statement!r}"
        )
    if grant.grant_option and any(node.role_spec.roletype == ROLESPEC_PUBLIC for node in grant.grantees):
        raise ValueError(f"PostgreSQL does not allow WITH GRANT OPTION for PUBLIC: {statement!r}")

    objects: list[tuple[str, str, str]]
    if grant.objtype == OBJECT_TABLE:
        object_type = "TABLE"
        objects = []
        for node in grant.objects:
            schema = _table_schema(conn, node.range_var.schemaname or None, node.range_var.relname, statement)
            objects.append((schema or context.schema, object_type, node.range_var.relname))
    elif grant.objtype == OBJECT_SCHEMA:
        object_type = "SCHEMA"
        objects = [(node.string.sval, object_type, node.string.sval) for node in grant.objects]
    else:
        raise ValueError(f"pg_grants supports only GRANT ... ON TABLE and GRANT ... ON SCHEMA: {statement!r}")

    allowed = context.all_privileges[object_type]
    privileges: list[str] = []
    for node in grant.privileges:
        access = node.access_priv
        if access.cols:
            raise ValueError(f"pg_grants does not support column privileges: {statement!r}")
        privilege = access.priv_name.upper()
        if privilege not in allowed:
            raise ValueError(f"{privilege} is not a {object_type} privilege in pg_grants: {statement!r}")
        privileges.append(privilege)
    if not privileges:  # ``GRANT ALL [PRIVILEGES]`` has an empty privilege list
        privileges = list(allowed)

    grantees = [_grantee_name(node.role_spec, context) for node in grant.grantees]
    return [
        GrantInfo(schema, kind, name, grantee, privilege, grant.grant_option)
        for schema, kind, name in objects
        for grantee in grantees
        for privilege in privileges
    ]


def _table_schema(conn: Connection, schema: str | None, name: str, statement: str) -> str | None:
    """Return the schema of the relation that ``GRANT ... ON [TABLE]`` names, or *schema* if no relation exists.

    Raises:
        ValueError: If the relation exists and is not a table, a view, or a foreign table. PostgreSQL accepts
            ``GRANT SELECT ON seq`` for a sequence, but :func:`inspect_grants` does not read sequences, so the grant
            would appear again on each run.
    """
    row = conn.execute(text(_RELATION_QUERY), {"schema": schema, "name": name}).one_or_none()
    if row is None:
        return schema
    if row.kind not in _TABLE_KINDS:
        kind = _RELATION_KIND_NAMES.get(row.kind, f"a relation of kind {row.kind!r}")
        raise ValueError(
            f"pg_grants supports only tables, views, and foreign tables. {name!r} is {kind}: {statement!r}"
        )
    return row.schema


def _grantee_name(role: RoleSpec, context: _GrantContext) -> str:
    """Return the role name of a grantee in the form that :func:`inspect_grants` returns."""
    from postgast.pg_query_pb2 import (
        ROLESPEC_CURRENT_ROLE,
        ROLESPEC_CURRENT_USER,
        ROLESPEC_PUBLIC,
        ROLESPEC_SESSION_USER,
    )

    if role.roletype == ROLESPEC_PUBLIC:
        return "public"
    if role.roletype in (ROLESPEC_CURRENT_USER, ROLESPEC_CURRENT_ROLE):
        return context.current_user
    if role.roletype == ROLESPEC_SESSION_USER:
        return context.session_user
    return role.rolename


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

_REAL_TABLE_LOCK_TIMEOUT = "1s"
"""The longest wait for the lock of the real table, when a probe cannot run on the ``TEMP`` copy."""

_UNDEFINED_COLUMN = "42703"
"""The SQLSTATE of an unknown column, which a probe on the real table cannot fix."""

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
    with _probe_savepoint(conn, qualified) as savepoint:
        if savepoint is None:
            yield None
            return
        try:
            conn.execute(text(f"CREATE TEMP TABLE {clone} (LIKE {qualified})"))
        except SQLAlchemyError:
            # A temporary relation with the same name on this connection blocks the copy, as for indexes.
            log.warning(
                "Could not create a temporary probe clone of %s; probing the real table instead",
                qualified,
                exc_info=True,
            )
            yield None
        else:
            yield clone


@contextmanager
def _check_probe_real_table(conn: Connection, *, schema: str | None, table_name: str) -> Iterator[str | None]:
    """Yield the qualified name of the real table, inside a savepoint with a short ``lock_timeout``, or *None*.

    A probe on the real table takes ``ACCESS EXCLUSIVE``. ``SET LOCAL lock_timeout`` limits the wait for that lock.
    Rolling back the savepoint releases the lock and restores the previous ``lock_timeout``.
    """
    qualified = _qualified_table(conn, schema, table_name)
    with _probe_savepoint(conn, qualified) as savepoint:
        if savepoint is None:
            yield None
            return
        try:
            conn.execute(text(f"SET LOCAL lock_timeout = '{_REAL_TABLE_LOCK_TIMEOUT}'"))
        except SQLAlchemyError:
            log.warning("Could not limit the lock wait on %s; skipping the probe there", qualified, exc_info=True)
            yield None
        else:
            yield qualified


@contextmanager
def _probe_savepoint(conn: Connection, qualified: str) -> Iterator[object | None]:
    """Open a savepoint for a probe, yield it, and always roll it back. Yield *None* when no savepoint can open."""
    try:
        savepoint = conn.begin_nested()
    except SQLAlchemyError:
        # An AUTOCOMMIT connection has no transaction to hold a savepoint.
        log.warning("Could not open a savepoint to probe check constraints on %s", qualified, exc_info=True)
        yield None
        return
    try:
        yield savepoint
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


class _ProbeOutcome(Enum):
    """The result of one probe statement."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    """Rejected for a reason that another probe table cannot change, such as an unknown column."""
    RETRY = "retry"
    """Rejected by the server for a reason that the real table may not share, such as a row type of the copy."""


def _execute_probe(
    conn: Connection, statement: str, expression: str, table: str, *, last_attempt: bool
) -> _ProbeOutcome:
    """Run one probe statement and report whether PostgreSQL accepted it.

    The caller wraps the call in a nested savepoint, so a rejected statement rolls back only that savepoint. The
    statement holds user-authored SQL from the model's own metadata, so its colons are escaped before
    :func:`~sqlalchemy.text` reads them. ``SQLAlchemyError`` is caught, not ``DBAPIError``, because a statement error
    raises before the server sees the statement. A warning is logged only when no later attempt follows.
    """
    try:
        conn.execute(text(escape_bind_colons(statement)))
    except SQLAlchemyError as exc:
        log.debug("Check expression probe failure for %r on %s", expression, table, exc_info=True)
        outcome = _ProbeOutcome.REJECTED
        if isinstance(exc, DBAPIError):
            sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
            if sqlstate != _UNDEFINED_COLUMN and not last_attempt:
                outcome = _ProbeOutcome.RETRY
        if outcome is _ProbeOutcome.REJECTED:
            log.warning("Could not probe check expression %r on %s: %s", expression, table, _first_line(exc))
        return outcome
    return _ProbeOutcome.ACCEPTED


def _first_line(exc: SQLAlchemyError) -> str:
    """Return the first line of the database's error message, or of the exception text."""
    message = str(getattr(exc, "orig", None) or exc)
    return message.splitlines()[0] if message else type(exc).__name__


def escape_bind_colons(sql: str) -> str:
    r"""Escape each colon that :func:`~sqlalchemy.text` would read as a bind parameter, such as ``:b`` in ``'a :b'``.

    The pattern is the one SQLAlchemy's compiler uses for bind parameters in a ``TextClause``. A cast such as
    ``::numeric`` and a character class such as ``[[:alpha:]]`` do not match it. The compiler turns each ``\:`` back
    into a plain colon.
    """
    return BIND_PARAMS.sub(r"\\\g<0>", sql)


def _declared(ddl: Sequence[str] | Ignored) -> Sequence[str]:
    """Return the DDL statements to execute, treating :data:`~alembic_pg_autogen.IGNORED` as "none"."""
    return () if ddl is IGNORED else ddl
