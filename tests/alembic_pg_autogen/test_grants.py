# These tests call private helpers of the comparator and the renderer directly.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from alembic.command import downgrade, revision, upgrade
from sqlalchemy import Column, Integer, MetaData, Table, text
from sqlalchemy.dialects.postgresql.base import PGDialect

from alembic_pg_autogen import (
    IGNORED,
    Action,
    AlterGrantOptionOp,
    CanonicalState,
    DiffResult,
    DropViewOp,
    GrantInfo,
    GrantOp,
    GrantPrivilegesOp,
    RevokePrivilegesOp,
    ValidateConstraintOp,
    ViewInfo,
    canonicalize_grants,
    diff_grants,
    inspect_grants,
)
from alembic_pg_autogen.canonicalize import all_grant_privileges
from alembic_pg_autogen.compare import _grant_migration_ops, _grant_states, _group_grants
from alembic_pg_autogen.render import (
    _privilege_rank,
    _render_alter_grant_option,
    _render_drop_view,
    _render_grant_privileges,
    _render_revoke_privileges,
    _render_validate_constraint,
)

if TYPE_CHECKING:
    from collections.abc import Generator

    from sqlalchemy import Connection
    from sqlalchemy.engine import Engine

    from .alembic_helpers import AlembicProject


def _grant(
    privilege: str = "SELECT",
    *,
    schema: str = "public",
    object_type: str = "TABLE",
    name: str = "orders",
    grantee: str = "app",
    grantable: bool = False,
) -> GrantInfo:
    return GrantInfo(schema, object_type, name, grantee, privilege, grantable)


def _ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.dialect = PGDialect()
    return ctx


def _sql(rendered: str) -> str:
    """Return the DDL inside a rendered ``op.execute(...)`` call."""
    assert rendered.startswith("op.execute(") and rendered.endswith(")"), rendered
    value = eval(rendered.removeprefix("op.execute(").removesuffix(")"))  # noqa: S307
    assert isinstance(value, str)
    return value


class TestGrantInfo:
    def test_fields(self):
        info = GrantInfo("s", "TABLE", "t", "app", "SELECT", True)
        assert info.schema == "s"
        assert info.object_type == "TABLE"
        assert info.name == "t"
        assert info.grantee == "app"
        assert info.privilege == "SELECT"
        assert info.grantable is True

    def test_grantable_defaults_to_false(self):
        assert GrantInfo("s", "TABLE", "t", "app", "SELECT").grantable is False

    def test_identity_is_all_fields_but_the_last(self):
        assert _grant(grantable=False)[:-1] == _grant(grantable=True)[:-1]


class TestGrantOps:
    def test_grant_reverse_is_revoke(self):
        grants = [_grant("SELECT"), _grant("INSERT")]
        reverse = GrantPrivilegesOp(grants).reverse()
        assert isinstance(reverse, RevokePrivilegesOp)
        assert reverse.grants == tuple(grants)

    def test_revoke_reverse_restores_grant_option(self):
        grants = [_grant("SELECT", grantable=True)]
        reverse = RevokePrivilegesOp(grants).reverse()
        assert isinstance(reverse, GrantPrivilegesOp)
        assert reverse.grants[0].grantable is True

    def test_alter_grant_option_reverse_flips_flag(self):
        op = AlterGrantOptionOp([_grant("SELECT", grantable=True), _grant("UPDATE", grantable=True)])
        reverse = op.reverse()
        assert isinstance(reverse, AlterGrantOptionOp)
        assert [g.grantable for g in reverse.grants] == [False, False]
        assert reverse.reverse().grants == op.grants

    def test_reverse_keeps_all_privileges_and_drops_implicit(self):
        revoke = RevokePrivilegesOp([_grant()], all_privileges=True, implicit=True)
        grant = revoke.reverse()
        assert grant.all_privileges is True
        again = grant.reverse()
        assert (again.all_privileges, again.implicit) == (True, False)
        assert AlterGrantOptionOp([_grant()], all_privileges=True).reverse().all_privileges is True

    def test_to_diff_tuple(self):
        grants = [_grant("SELECT"), _grant("INSERT")]
        assert GrantPrivilegesOp(grants).to_diff_tuple() == (
            "grant_privileges",
            "TABLE",
            "public",
            "orders",
            "app",
            ("SELECT", "INSERT"),
        )
        assert RevokePrivilegesOp(grants).to_diff_tuple()[0] == "revoke_privileges"
        assert AlterGrantOptionOp(grants).to_diff_tuple()[-1] is False

    def test_empty_group_is_rejected(self):
        with pytest.raises(ValueError, match="at least one privilege"):
            GrantPrivilegesOp([])

    @pytest.mark.parametrize(
        "other",
        [
            _grant("INSERT", grantee="other"),
            _grant("INSERT", name="items"),
            _grant("INSERT", schema="sales"),
            _grant("USAGE", object_type="SCHEMA"),
            _grant("INSERT", grantable=True),
        ],
    )
    def test_mixed_group_is_rejected(self, other: GrantInfo):
        with pytest.raises(ValueError, match="one object, grantee, and grant option"):
            RevokePrivilegesOp([_grant("SELECT"), other])


class TestRenderGrants:
    def test_grant(self):
        rendered = _render_grant_privileges(_ctx(), GrantPrivilegesOp([_grant("INSERT"), _grant("SELECT")]))
        assert _sql(rendered) == "GRANT SELECT, INSERT ON TABLE public.orders TO app"

    def test_grant_with_grant_option(self):
        rendered = _render_grant_privileges(_ctx(), GrantPrivilegesOp([_grant(grantable=True)]))
        assert _sql(rendered) == "GRANT SELECT ON TABLE public.orders TO app WITH GRANT OPTION"

    def test_revoke(self):
        rendered = _render_revoke_privileges(_ctx(), RevokePrivilegesOp([_grant("DELETE", grantable=True)]))
        assert _sql(rendered) == "REVOKE DELETE ON TABLE public.orders FROM app"

    def test_add_grant_option(self):
        rendered = _render_alter_grant_option(_ctx(), AlterGrantOptionOp([_grant(grantable=True)]))
        assert _sql(rendered) == "GRANT SELECT ON TABLE public.orders TO app WITH GRANT OPTION"

    def test_remove_grant_option(self):
        rendered = _render_alter_grant_option(_ctx(), AlterGrantOptionOp([_grant(grantable=False)]))
        assert _sql(rendered) == "REVOKE GRANT OPTION FOR SELECT ON TABLE public.orders FROM app"

    def test_schema_target(self):
        grants = [
            _grant("CREATE", object_type="SCHEMA", schema="sales", name="sales"),
            _grant("USAGE", object_type="SCHEMA", schema="sales", name="sales"),
        ]
        rendered = _render_grant_privileges(_ctx(), GrantPrivilegesOp(grants))
        assert _sql(rendered) == "GRANT USAGE, CREATE ON SCHEMA sales TO app"

    def test_public_pseudo_role(self):
        rendered = _render_grant_privileges(_ctx(), GrantPrivilegesOp([_grant(grantee="public")]))
        assert _sql(rendered) == "GRANT SELECT ON TABLE public.orders TO PUBLIC"

    def test_role_named_uppercase_public_is_quoted(self):
        """``"PUBLIC"`` is a real role, not the pseudo-role, so the renderer must quote it."""
        rendered = _render_grant_privileges(_ctx(), GrantPrivilegesOp([_grant(grantee="PUBLIC")]))
        assert _sql(rendered) == 'GRANT SELECT ON TABLE public.orders TO "PUBLIC"'

    def test_identifiers_are_quoted(self):
        grant = _grant(schema="My Schema", name="Order Items", grantee="Report Reader")
        rendered = _render_revoke_privileges(_ctx(), RevokePrivilegesOp([grant]))
        assert _sql(rendered) == 'REVOKE SELECT ON TABLE "My Schema"."Order Items" FROM "Report Reader"'

    def test_percent_in_identifiers_is_not_doubled(self):
        """The psycopg preparer doubles ``%``, and ``op.execute()`` does not undo it."""
        grant = _grant(schema="50%off", name="50%off", grantee="50%role")
        assert _sql(_render_grant_privileges(_ctx(), GrantPrivilegesOp([grant]))) == (
            'GRANT SELECT ON TABLE "50%off"."50%off" TO "50%role"'
        )
        assert _sql(_render_drop_view(_ctx(), DropViewOp(ViewInfo("public", "50%off", "")))) == (
            'DROP VIEW public."50%off"'
        )
        validate = ValidateConstraintOp("ck_50%", "50%off", schema="public")
        assert _sql(_render_validate_constraint(_ctx(), validate)) == (
            'ALTER TABLE public."50%off" VALIDATE CONSTRAINT "ck_50%"'
        )

    def test_all_privileges(self):
        grants = [_grant("SELECT"), _grant("INSERT")]
        assert _sql(_render_grant_privileges(_ctx(), GrantPrivilegesOp(grants, all_privileges=True))) == (
            "GRANT ALL ON TABLE public.orders TO app"
        )
        assert _sql(_render_revoke_privileges(_ctx(), RevokePrivilegesOp(grants, all_privileges=True))) == (
            "REVOKE ALL ON TABLE public.orders FROM app"
        )
        option = AlterGrantOptionOp([g._replace(grantable=False) for g in grants], all_privileges=True)
        assert _sql(_render_alter_grant_option(_ctx(), option)) == (
            "REVOKE GRANT OPTION FOR ALL ON TABLE public.orders FROM app"
        )

    def test_implicit_revoke_is_a_comment(self):
        rendered = _render_revoke_privileges(_ctx(), RevokePrivilegesOp([_grant()], implicit=True))
        assert rendered == (
            "pass  # The DROP of public.orders in this migration does: REVOKE SELECT ON TABLE public.orders FROM app"
        )

    def test_privilege_order(self):
        names = ["CREATE", "UNKNOWN", "MAINTAIN", "SELECT", "TRIGGER", "USAGE", "DELETE"]
        assert sorted(names, key=_privilege_rank) == [
            "SELECT",
            "DELETE",
            "TRIGGER",
            "MAINTAIN",
            "USAGE",
            "CREATE",
            "UNKNOWN",
        ]


class TestDiffGrants:
    def test_create_drop_and_replace(self):
        current = [_grant("SELECT"), _grant("DELETE"), _grant("UPDATE")]
        desired = [_grant("SELECT"), _grant("INSERT"), _grant("UPDATE", grantable=True)]

        ops = diff_grants(current, desired)

        by_privilege = {(op.current or op.desired).privilege: op for op in ops}  # pyright: ignore[reportOptionalMemberAccess]
        assert set(by_privilege) == {"DELETE", "INSERT", "UPDATE"}
        assert by_privilege["DELETE"].action is Action.DROP
        assert by_privilege["INSERT"].action is Action.CREATE
        assert by_privilege["UPDATE"].action is Action.REPLACE

    def test_equal_grants_give_no_ops(self):
        grants = [_grant("SELECT", grantable=True)]
        assert diff_grants(grants, grants) == []

    def test_snapshot_tuples_keep_three_fields(self):
        """Code that unpacks a ``CanonicalState`` or a ``DiffResult`` into three names keeps working."""
        functions, triggers, views = CanonicalState((), ())
        function_ops, trigger_ops, view_ops = DiffResult((), ())
        assert (functions, triggers, views, function_ops, trigger_ops, view_ops) == ((), (), (), (), (), ())


class TestGrantMigrationOps:
    def test_groups_by_object_grantee_and_option(self):
        grant_ops = [
            GrantOp(Action.CREATE, None, _grant("SELECT")),
            GrantOp(Action.CREATE, None, _grant("INSERT")),
            GrantOp(Action.CREATE, None, _grant("SELECT", grantee="other")),
            GrantOp(Action.CREATE, None, _grant("DELETE", grantable=True)),
            GrantOp(Action.DROP, _grant("TRUNCATE"), None),
            GrantOp(Action.REPLACE, _grant("UPDATE"), _grant("UPDATE", grantable=True)),
        ]

        early, late = _grant_migration_ops(grant_ops, {}, frozenset())

        assert [type(op) for op in early] == [RevokePrivilegesOp, AlterGrantOptionOp]
        assert [op.to_diff_tuple() for op in late] == [
            ("grant_privileges", "TABLE", "public", "orders", "app", ("SELECT", "INSERT")),
            ("grant_privileges", "TABLE", "public", "orders", "app", ("DELETE",)),
            ("grant_privileges", "TABLE", "public", "orders", "other", ("SELECT",)),
        ]

    def test_no_ops(self):
        assert _grant_migration_ops([], {}, frozenset()) == ([], [])

    def test_full_privilege_set_renders_all(self):
        grant_ops = [GrantOp(Action.CREATE, None, _grant(p)) for p in ("SELECT", "INSERT")]
        grant_ops.append(GrantOp(Action.CREATE, None, _grant("SELECT", grantee="other")))

        _, late = _grant_migration_ops(grant_ops, {"TABLE": frozenset({"SELECT", "INSERT"})}, frozenset())

        assert all(isinstance(op, GrantPrivilegesOp) for op in late)
        assert [op.all_privileges for op in late if isinstance(op, GrantPrivilegesOp)] == [True, False]

    def test_dropped_relation_gets_implicit_revoke_and_no_option_change(self):
        grant_ops = [
            GrantOp(Action.DROP, _grant("SELECT"), None),
            GrantOp(Action.REPLACE, _grant("UPDATE"), _grant("UPDATE", grantable=True)),
            GrantOp(Action.DROP, _grant("SELECT", name="kept"), None),
        ]

        early, _ = _grant_migration_ops(grant_ops, {}, frozenset({("public", "orders")}))

        assert all(isinstance(op, RevokePrivilegesOp) for op in early)
        assert [(op.grants[0].name, op.implicit) for op in early if isinstance(op, RevokePrivilegesOp)] == [
            ("kept", False),
            ("orders", True),
        ]

    def test_group_grants_splits_by_object(self):
        groups = _group_grants([_grant(name="b"), _grant(name="a"), _grant("INSERT", name="a")])
        assert [[g.name for g in group] for group in groups] == [["a", "a"], ["b"]]


@pytest.fixture
def pg_conn(pg_engine: Engine) -> Generator[Connection]:
    """Provide a connection that rolls back all DDL, roles included, after each test."""
    with pg_engine.connect() as conn:
        txn = conn.begin()
        yield conn
        txn.rollback()


@pytest.fixture
def scratch(pg_conn: Connection) -> tuple[Connection, str]:
    """Create a schema, two tables, a view, a sequence, and two roles inside the rolled-back transaction."""
    schema = f"grants_{uuid.uuid4().hex[:10]}"
    for sql in (
        f"CREATE SCHEMA {schema}",
        f"CREATE TABLE {schema}.orders (id int, note text)",
        f"CREATE TABLE {schema}.items (id int)",
        f"CREATE VIEW {schema}.order_ids AS SELECT id FROM {schema}.orders",
        f"CREATE SEQUENCE {schema}.counter",
        f"CREATE ROLE {schema}_reader",
        f'CREATE ROLE "{schema}_Writer"',
    ):
        pg_conn.execute(text(sql))
    return pg_conn, schema


@pytest.mark.integration
class TestInspectGrants:
    def test_reads_table_view_and_schema_grants(self, scratch: tuple[Connection, str]):
        conn, schema = scratch
        reader, writer = f"{schema}_reader", f"{schema}_Writer"
        for sql in (
            f"GRANT SELECT, INSERT ON {schema}.orders TO {reader}",
            f'GRANT UPDATE ON {schema}.orders TO "{writer}" WITH GRANT OPTION',
            f"GRANT SELECT ON {schema}.order_ids TO PUBLIC",
            f"GRANT USAGE ON SCHEMA {schema} TO {reader}",
        ):
            conn.execute(text(sql))

        # Role names sort in byte order, so "..._Writer" comes before "..._reader".
        assert inspect_grants(conn, [schema]) == [
            GrantInfo(schema, "SCHEMA", schema, reader, "USAGE", False),
            GrantInfo(schema, "TABLE", "order_ids", "public", "SELECT", False),
            GrantInfo(schema, "TABLE", "orders", writer, "UPDATE", True),
            GrantInfo(schema, "TABLE", "orders", reader, "INSERT", False),
            GrantInfo(schema, "TABLE", "orders", reader, "SELECT", False),
        ]

    def test_owner_privileges_are_not_grants(self, scratch: tuple[Connection, str]):
        conn, schema = scratch
        conn.execute(text(f"GRANT SELECT ON {schema}.orders TO {schema}_reader"))  # makes relacl non-null

        grantees = {grant.grantee for grant in inspect_grants(conn, [schema])}

        assert grantees == {f"{schema}_reader"}

    def test_sequence_and_column_privileges_are_excluded(self, scratch: tuple[Connection, str]):
        conn, schema = scratch
        conn.execute(text(f"GRANT USAGE ON SEQUENCE {schema}.counter TO {schema}_reader"))
        conn.execute(text(f"GRANT SELECT (note) ON {schema}.orders TO {schema}_reader"))

        assert inspect_grants(conn, [schema]) == []

    def test_extension_members_are_included(self, scratch: tuple[Connection, str]):
        """A declared grant on an extension object must be visible, or it never converges."""
        conn, schema = scratch
        conn.execute(text(f"GRANT SELECT ON {schema}.items TO {schema}_reader"))
        conn.execute(text(f"ALTER EXTENSION plpgsql ADD TABLE {schema}.items"))

        assert [grant.name for grant in inspect_grants(conn, [schema])] == ["items"]

    def test_default_privileges(self, scratch: tuple[Connection, str]):
        conn, schema = scratch
        reader = f"{schema}_reader"
        conn.execute(text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT ON TABLES TO {reader}"))
        conn.execute(text(f"ALTER DEFAULT PRIVILEGES GRANT INSERT ON TABLES TO {reader}"))
        conn.execute(text(f"CREATE TABLE {schema}.later (id int)"))

        defaults = inspect_grants(conn, [schema], source="default")

        assert GrantInfo(schema, "TABLE", "later", reader, "SELECT", False) in defaults
        assert GrantInfo(schema, "TABLE", "later", reader, "INSERT", False) in defaults
        assert {g for g in inspect_grants(conn, [schema]) if g.name == "later"} <= set(defaults)

    def test_unknown_source(self, pg_conn: Connection):
        with pytest.raises(ValueError, match="source must be one of"):
            inspect_grants(pg_conn, source="live")  # pyright: ignore[reportArgumentType]

    def test_two_grantors_give_one_row(self, scratch: tuple[Connection, str]):
        conn, schema = scratch
        reader, writer = f"{schema}_reader", f"{schema}_Writer"
        conn.execute(text(f'GRANT USAGE ON SCHEMA {schema} TO "{writer}"'))
        conn.execute(text(f'GRANT SELECT ON {schema}.orders TO "{writer}" WITH GRANT OPTION'))
        conn.execute(text(f'SET LOCAL ROLE "{writer}"'))
        conn.execute(text(f"GRANT SELECT ON {schema}.orders TO {reader}"))
        conn.execute(text("RESET ROLE"))
        conn.execute(text(f"GRANT SELECT ON {schema}.orders TO {reader} WITH GRANT OPTION"))

        reader_grants = [grant for grant in inspect_grants(conn, [schema]) if grant.grantee == reader]

        assert reader_grants == [GrantInfo(schema, "TABLE", "orders", reader, "SELECT", True)]

    def test_schema_filter(self, scratch: tuple[Connection, str]):
        conn, schema = scratch
        conn.execute(text(f"GRANT SELECT ON {schema}.orders TO {schema}_reader"))

        assert inspect_grants(conn, ["no_such_schema"]) == []
        assert any(grant.schema == schema for grant in inspect_grants(conn))

    def test_initial_privileges_of_public_schema(self, pg_conn: Connection):
        initial = inspect_grants(pg_conn, ["public"], source="initial")

        assert GrantInfo("public", "SCHEMA", "public", "public", "USAGE", False) in initial

    def test_new_objects_have_no_initial_privileges(self, scratch: tuple[Connection, str]):
        conn, schema = scratch
        conn.execute(text(f"GRANT SELECT ON {schema}.orders TO {schema}_reader"))

        assert inspect_grants(conn, [schema], source="initial") == []


@pytest.mark.integration
class TestCanonicalizeGrants:
    def test_expands_objects_grantees_and_privileges(self, scratch: tuple[Connection, str]):
        conn, schema = scratch
        reader = f"{schema}_reader"

        grants = canonicalize_grants(
            conn, [f"GRANT select, INSERT ON TABLE {schema}.orders, {schema}.items TO {reader}, PUBLIC"]
        )

        assert len(grants) == 8
        assert GrantInfo(schema, "TABLE", "items", "public", "INSERT", False) in grants
        assert GrantInfo(schema, "TABLE", "orders", reader, "SELECT", False) in grants

    def test_matches_inspection_after_execution(self, scratch: tuple[Connection, str]):
        """The parsed form equals what PostgreSQL stores when it runs the same statements."""
        conn, schema = scratch
        ddl = [
            f"GRANT ALL ON {schema}.orders TO {schema}_reader",
            f'GRANT SELECT ON {schema}.order_ids TO "{schema}_Writer" WITH GRANT OPTION',
            f"GRANT ALL PRIVILEGES ON SCHEMA {schema} TO PUBLIC; GRANT USAGE ON SCHEMA {schema} TO {schema}_reader",
        ]

        desired = canonicalize_grants(conn, ddl)
        for statement in ddl:
            conn.execute(text(statement))

        assert desired == inspect_grants(conn, [schema])

    def test_unqualified_new_table_uses_current_schema(self, scratch: tuple[Connection, str]):
        conn, schema = scratch
        conn.execute(text(f"SET LOCAL search_path TO {schema}"))

        (grant,) = canonicalize_grants(conn, [f"GRANT SELECT ON not_created_yet TO {schema}_reader"])

        assert grant.schema == schema

    def test_unqualified_existing_table_follows_search_path(self, scratch: tuple[Connection, str]):
        """``GRANT`` finds a table in a later ``search_path`` schema, so the parsed form must find the same table."""
        conn, schema = scratch
        conn.execute(text(f"CREATE SCHEMA {schema}_first"))
        conn.execute(text(f"SET LOCAL search_path TO {schema}_first, {schema}"))

        (grant,) = canonicalize_grants(conn, [f"GRANT SELECT ON orders TO {schema}_reader"])

        assert grant.schema == schema

    def test_quoted_name_with_percent_finds_table(self, scratch: tuple[Connection, str]):
        conn, schema = scratch
        conn.execute(text(f'CREATE TABLE {schema}."50%off" (id int)'))
        conn.execute(text(f"SET LOCAL search_path TO {schema}"))

        (grant,) = canonicalize_grants(conn, [f'GRANT SELECT ON "50%off" TO {schema}_reader'])

        assert grant[:3] == (schema, "TABLE", "50%off")

    def test_sequence_without_keyword_is_rejected(self, scratch: tuple[Connection, str]):
        conn, schema = scratch

        with pytest.raises(ValueError, match="is a sequence"):
            canonicalize_grants(conn, [f"GRANT SELECT ON {schema}.counter TO {schema}_reader"])

    def test_all_grant_privileges_match_server(self, pg_conn: Connection):
        privileges = all_grant_privileges(pg_conn)

        assert {"SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"} <= privileges["TABLE"]
        assert privileges["SCHEMA"] == {"USAGE", "CREATE"}

    def test_session_role_keywords(self, pg_conn: Connection):
        user = pg_conn.execute(text("SELECT current_user")).scalar()

        grants = canonicalize_grants(
            pg_conn,
            [
                "GRANT SELECT ON t TO CURRENT_USER",
                "GRANT INSERT ON t TO CURRENT_ROLE",
                "GRANT UPDATE ON t TO SESSION_USER",
            ],
        )

        assert {grant.grantee for grant in grants} == {user}

    def test_duplicates_merge_and_keep_grant_option(self, pg_conn: Connection):
        grants = canonicalize_grants(
            pg_conn, ["GRANT SELECT ON public.t TO app", "GRANT SELECT ON public.t TO app WITH GRANT OPTION"]
        )

        assert grants == [GrantInfo("public", "TABLE", "t", "app", "SELECT", True)]

    def test_quoted_identifiers_keep_case(self, pg_conn: Connection):
        (grant,) = canonicalize_grants(pg_conn, ['GRANT USAGE ON SCHEMA "Sales" TO "Report Reader"'])

        assert grant == GrantInfo("Sales", "SCHEMA", "Sales", "Report Reader", "USAGE", False)

    def test_empty(self, pg_conn: Connection):
        assert canonicalize_grants(pg_conn, []) == ()

    @pytest.mark.parametrize(
        ("ddl", "message"),
        [
            ("REVOKE SELECT ON t FROM app", "not REVOKE"),
            ("CREATE TABLE t (id int)", "only GRANT statements"),
            ("GRANT SELECT ON t TO app; SELECT 1", "only GRANT statements"),
            ("GRANT SELECT ON ALL TABLES IN SCHEMA public TO app", "ON ALL"),
            ("GRANT USAGE ON SEQUENCE s TO app", "only GRANT ... ON TABLE"),
            ("GRANT EXECUTE ON FUNCTION f() TO app", "only GRANT ... ON TABLE"),
            ("GRANT SELECT (id) ON t TO app", "column privileges"),
            ("GRANT USAGE ON TABLE t TO app", "USAGE is not a TABLE privilege"),
            ("GRANT SELECT ON SCHEMA s TO app", "SELECT is not a SCHEMA privilege"),
            ("GRANT reader TO app", "only GRANT statements"),
            ("GRANT SELECT ON t TO app GRANTED BY CURRENT_USER", "GRANTED BY"),
            ("GRANT SELECT ON t TO app, PUBLIC WITH GRANT OPTION", "WITH GRANT OPTION for PUBLIC"),
        ],
    )
    def test_unsupported_statements(self, pg_conn: Connection, ddl: str, message: str):
        with pytest.raises(ValueError, match=message):
            canonicalize_grants(pg_conn, [ddl])


class _FakeAutogenContext:
    """The parts of ``AutogenContext`` that ``_grant_states`` reads, with filters that allow everything."""

    def __init__(self, version_table: str = "alembic_version") -> None:
        self.migration_context: Any = MagicMock(version_table=version_table, version_table_schema=None)
        self.metadata: object = None

    def run_name_filters(self, *_args: object) -> bool:
        return True

    def run_object_filters(self, *_args: object) -> bool:
        return True


@pytest.mark.integration
class TestGrantStates:
    def test_initial_privileges_are_unmanaged(self, pg_conn: Connection):
        """An empty ``pg_grants`` must not revoke ``USAGE`` on the ``public`` schema from ``PUBLIC``."""
        ctx: Any = _FakeAutogenContext()
        current, desired = _grant_states(ctx, pg_conn, ["GRANT USAGE ON SCHEMA public TO PUBLIC"], ["public"])

        public_usage = GrantInfo("public", "SCHEMA", "public", "public", "USAGE", False)
        assert public_usage not in current
        assert public_usage not in desired

    def test_recreated_public_schema_keeps_public_usage(self, pg_conn: Connection):
        """``DROP SCHEMA public`` removes the ``pg_init_privs`` row, so the rule cannot rely on it alone."""
        pg_conn.execute(text(f"ALTER SCHEMA public RENAME TO public_{uuid.uuid4().hex[:8]}"))
        pg_conn.execute(text("CREATE SCHEMA public"))
        pg_conn.execute(text("GRANT USAGE ON SCHEMA public TO PUBLIC"))
        ctx: Any = _FakeAutogenContext()

        current, _ = _grant_states(ctx, pg_conn, [], ["public"])
        declared_current, declared = _grant_states(
            ctx, pg_conn, ["GRANT CREATE ON SCHEMA public TO PUBLIC"], ["public"]
        )

        assert current == []
        assert [g.privilege for g in declared_current] == []
        assert [g.privilege for g in declared] == ["CREATE"]

    def test_default_privileges_are_unmanaged(self, scratch: tuple[Connection, str]):
        conn, schema = scratch
        conn.execute(text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT ON TABLES TO {schema}_reader"))
        conn.execute(text(f"CREATE TABLE {schema}.later (id int)"))
        ctx: Any = _FakeAutogenContext()

        current, desired = _grant_states(ctx, conn, [f"GRANT SELECT ON {schema}.later TO {schema}_reader"], [schema])

        assert (current, desired) == ([], [])

    def test_version_table_is_unmanaged(self, scratch: tuple[Connection, str]):
        conn, schema = scratch
        conn.execute(text(f"GRANT SELECT ON {schema}.orders TO {schema}_reader"))
        conn.execute(text(f"SET LOCAL search_path TO {schema}"))
        ctx: Any = _FakeAutogenContext(version_table="orders")

        assert _grant_states(ctx, conn, [], [schema]) == ([], [])

    def test_declared_grant_on_extension_member_converges(self, scratch: tuple[Connection, str]):
        conn, schema = scratch
        conn.execute(text(f"GRANT SELECT ON {schema}.items TO {schema}_reader"))
        conn.execute(text(f"ALTER EXTENSION plpgsql ADD TABLE {schema}.items"))
        ctx: Any = _FakeAutogenContext()

        current, desired = _grant_states(ctx, conn, [f"GRANT SELECT ON {schema}.items TO {schema}_reader"], [schema])

        assert diff_grants(current, desired) == []

    def test_grants_outside_compared_schemas_warn_by_name(self, pg_conn: Connection, caplog: pytest.LogCaptureFixture):
        ddl = [
            "GRANT USAGE ON SCHEMA sales TO app",
            "GRANT SELECT ON sales.o TO app",
            "GRANT SELECT ON public.t TO app",
        ]
        ctx: Any = _FakeAutogenContext()

        with caplog.at_level(logging.WARNING, logger="alembic_pg_autogen.compare"):
            _, desired = _grant_states(ctx, pg_conn, ddl, ["public"])

        assert [g.name for g in desired] == ["t"]
        assert "SCHEMA sales" in caplog.text
        assert "TABLE sales.o" in caplog.text
        assert "include_schemas" in caplog.text


@pytest.fixture
def roles(pg_engine: Engine) -> Generator[tuple[str, str]]:
    """Create two committed roles for autogenerate tests, and remove their privileges and the roles afterwards."""
    suffix = uuid.uuid4().hex[:10]
    reader, writer = f"reader_{suffix}", f"Writer {suffix}"
    with pg_engine.connect() as conn:
        conn.execute(text(f"CREATE ROLE {reader}"))
        conn.execute(text(f'CREATE ROLE "{writer}"'))
        conn.commit()
    yield reader, writer
    with pg_engine.connect() as conn:
        for role in (reader, writer):
            conn.execute(text(f'DROP OWNED BY "{role}"'))
            conn.execute(text(f'DROP ROLE "{role}"'))
        conn.commit()


def _upgrade_body(content: str) -> str:
    return content.split("def upgrade()")[1].split("def downgrade()")[0]


def _downgrade_body(content: str) -> str:
    return content.split("def downgrade()")[1]


def _autogenerate(project: AlembicProject, metadata: MetaData | None = None, /, **attrs: object) -> str:
    """Autogenerate the next revision and return its source."""
    cfg = project.config
    cfg.attributes["target_metadata"] = metadata
    for key, value in attrs.items():
        cfg.attributes[key] = value
    versions = Path(cfg.get_main_option("script_location")) / "versions"  # pyright: ignore[reportArgumentType]
    before = set(versions.glob("*.py"))
    script = revision(cfg, message="test", autogenerate=True)
    assert script is not None
    (created,) = set(versions.glob("*.py")) - before
    return created.read_text()


def _orders() -> MetaData:
    """Return metadata that keeps the ``orders (id int)`` table of a test, so Alembic emits no ``drop_table``."""
    metadata = MetaData()
    Table("orders", metadata, Column("id", Integer))
    return metadata


def _project_grants(project: AlembicProject) -> list[GrantInfo]:
    with project.connect() as conn:
        return list(inspect_grants(conn, [project.schema]))


@pytest.mark.integration
class TestAutogenerateGrants:
    def test_new_grant(self, alembic_project: AlembicProject, roles: tuple[str, str]):
        schema = alembic_project.schema
        reader, _ = roles
        alembic_project.execute(f"CREATE TABLE {schema}.orders (id int)")

        content = _autogenerate(alembic_project, _orders(), pg_grants=[f"GRANT SELECT, INSERT ON orders TO {reader}"])

        assert f"GRANT SELECT, INSERT ON TABLE {schema}.orders TO {reader}" in _upgrade_body(content)
        assert f"REVOKE SELECT, INSERT ON TABLE {schema}.orders FROM {reader}" in _downgrade_body(content)

    def test_matching_grant_gives_no_ops(self, alembic_project: AlembicProject, roles: tuple[str, str]):
        schema = alembic_project.schema
        reader, writer = roles
        alembic_project.execute(f"CREATE TABLE {schema}.orders (id int)")
        alembic_project.execute(f"GRANT SELECT ON {schema}.orders TO {reader}")
        alembic_project.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{writer}" WITH GRANT OPTION')

        content = _autogenerate(
            alembic_project,
            _orders(),
            pg_grants=[
                f"GRANT SELECT ON TABLE orders TO {reader}",
                f'GRANT USAGE ON SCHEMA {schema} TO "{writer}" WITH GRANT OPTION',
            ],
        )

        assert "op.execute" not in content

    def test_undeclared_grant_is_revoked(self, alembic_project: AlembicProject, roles: tuple[str, str]):
        schema = alembic_project.schema
        reader, _ = roles
        alembic_project.execute(f"CREATE TABLE {schema}.orders (id int)")
        alembic_project.execute(f"GRANT SELECT, DELETE ON {schema}.orders TO {reader}")

        content = _autogenerate(alembic_project, _orders(), pg_grants=[f"GRANT SELECT ON orders TO {reader}"])

        assert f"REVOKE DELETE ON TABLE {schema}.orders FROM {reader}" in _upgrade_body(content)
        assert f"GRANT DELETE ON TABLE {schema}.orders TO {reader}" in _downgrade_body(content)
        assert "SELECT" not in content.split("def upgrade()")[1]

    def test_grant_option_changes(self, alembic_project: AlembicProject, roles: tuple[str, str]):
        schema = alembic_project.schema
        reader, writer = roles
        alembic_project.execute(f"CREATE TABLE {schema}.orders (id int)")
        alembic_project.execute(f"GRANT SELECT ON {schema}.orders TO {reader} WITH GRANT OPTION")
        alembic_project.execute(f'GRANT SELECT ON {schema}.orders TO "{writer}"')

        content = _autogenerate(
            alembic_project,
            _orders(),
            pg_grants=[
                f"GRANT SELECT ON orders TO {reader}",
                f'GRANT SELECT ON orders TO "{writer}" WITH GRANT OPTION',
            ],
        )

        upgrade_body = _upgrade_body(content)
        assert f"REVOKE GRANT OPTION FOR SELECT ON TABLE {schema}.orders FROM {reader}" in upgrade_body
        assert f'GRANT SELECT ON TABLE {schema}.orders TO "{writer}" WITH GRANT OPTION' in upgrade_body
        assert "REVOKE SELECT" not in upgrade_body

    @pytest.mark.parametrize("attrs", [{"pg_grants": IGNORED}, {"pg_views": []}], ids=["ignored", "absent"])
    def test_unmanaged_grants_are_left_alone(
        self, alembic_project: AlembicProject, roles: tuple[str, str], attrs: dict[str, object]
    ):
        schema = alembic_project.schema
        reader, _ = roles
        alembic_project.execute(f"CREATE TABLE {schema}.orders (id int)")
        alembic_project.execute(f"GRANT SELECT ON {schema}.orders TO {reader}")

        assert "REVOKE" not in _autogenerate(alembic_project, _orders(), **attrs)

    def test_new_table_and_grant_apply_in_order(self, alembic_project: AlembicProject, roles: tuple[str, str]):
        """The grant follows ``create_table`` in ``upgrade()``, and the revoke precedes ``drop_table`` in reverse."""
        schema = alembic_project.schema
        reader, writer = roles
        metadata = MetaData()
        Table("orders", metadata, Column("id", Integer, primary_key=True))
        pg_grants = [
            f'GRANT SELECT, UPDATE ON orders TO {reader}, "{writer}"',
            f"GRANT USAGE ON SCHEMA {schema} TO {reader}",
        ]

        content = _autogenerate(alembic_project, metadata, pg_grants=pg_grants)

        upgrade_body = _upgrade_body(content)
        assert upgrade_body.index("op.create_table(") < upgrade_body.index("GRANT SELECT, UPDATE")
        downgrade_body = _downgrade_body(content)
        assert downgrade_body.index("REVOKE SELECT, UPDATE") < downgrade_body.index("op.drop_table(")

        cfg = alembic_project.config
        upgrade(cfg, "head")
        assert len(_project_grants(alembic_project)) == 5
        assert "op.execute" not in _autogenerate(alembic_project, metadata, pg_grants=pg_grants)
        downgrade(cfg, "base")
        assert _project_grants(alembic_project) == []

    def test_dropped_table_needs_no_revoke(self, alembic_project: AlembicProject, roles: tuple[str, str]):
        """A ``REVOKE`` before ``DROP TABLE`` fails on a grant chain. The ``DROP`` alone removes every privilege."""
        schema = alembic_project.schema
        reader, writer = roles
        alembic_project.execute(f"CREATE TABLE {schema}.orders (id int)")
        alembic_project.execute(f"GRANT USAGE ON SCHEMA {schema} TO {reader}")
        alembic_project.execute(f"GRANT SELECT ON {schema}.orders TO {reader} WITH GRANT OPTION")
        alembic_project.execute(f'SET ROLE {reader}; GRANT SELECT ON {schema}.orders TO "{writer}"; RESET ROLE')

        content = _autogenerate(alembic_project, MetaData(), pg_grants=[f"GRANT USAGE ON SCHEMA {schema} TO {reader}"])

        upgrade_body = _upgrade_body(content)
        assert "REVOKE SELECT" in upgrade_body
        assert "op.execute('REVOKE" not in upgrade_body
        cfg = alembic_project.config
        upgrade(cfg, "head")
        downgrade(cfg, "base")
        restored = {(g.name, g.grantee, g.grantable) for g in _project_grants(alembic_project)}
        assert {("orders", reader, True), ("orders", writer, False)} <= restored

    def test_default_privileges_are_not_revoked(self, alembic_project: AlembicProject, roles: tuple[str, str]):
        schema = alembic_project.schema
        reader, _ = roles
        alembic_project.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT ON TABLES TO {reader}")
        alembic_project.execute(f"CREATE TABLE {schema}.orders (id int)")

        content = _autogenerate(alembic_project, _orders(), pg_grants=[])

        assert "REVOKE" not in content

    def test_version_table_is_not_revoked(self, alembic_project: AlembicProject, roles: tuple[str, str]):
        schema = alembic_project.schema
        reader, _ = roles
        _autogenerate(alembic_project, MetaData(), pg_grants=[])
        upgrade(alembic_project.config, "head")
        alembic_project.execute(f"GRANT SELECT ON {schema}.alembic_version TO {reader}")

        assert "REVOKE" not in _autogenerate(alembic_project, MetaData(), pg_grants=[])

    @pytest.mark.parametrize("hook", ["include_object", "include_name"])
    def test_filtered_table_is_not_revoked(self, alembic_project: AlembicProject, roles: tuple[str, str], hook: str):
        schema = alembic_project.schema
        reader, _ = roles
        alembic_project.execute(f"CREATE TABLE {schema}.excluded (id int)")
        alembic_project.execute(f"GRANT SELECT ON {schema}.excluded TO {reader}")

        def include_object(_obj: object, name: str | None, type_: str, *_rest: object) -> bool:
            return not (type_ == "table" and name == "excluded")

        def include_name(name: str | None, type_: str, _parents: object) -> bool:
            return not (type_ == "table" and name == "excluded")

        content = _autogenerate(
            alembic_project,
            MetaData(),
            pg_grants=[],
            **{hook: include_object if hook == "include_object" else include_name},
        )

        assert "REVOKE" not in content
        assert "drop_table" not in content

    def test_percent_in_table_name(self, alembic_project: AlembicProject, roles: tuple[str, str]):
        schema = alembic_project.schema
        reader, _ = roles
        alembic_project.execute(f'CREATE TABLE {schema}."50%off" (id int)')
        metadata = MetaData()
        Table("50%off", metadata, Column("id", Integer))
        pg_grants = [f'GRANT SELECT ON "50%off" TO {reader}']

        _autogenerate(alembic_project, metadata, pg_grants=pg_grants)
        upgrade(alembic_project.config, "head")

        assert _project_grants(alembic_project) == [GrantInfo(schema, "TABLE", "50%off", reader, "SELECT", False)]

    def test_grant_all_renders_all(self, alembic_project: AlembicProject, roles: tuple[str, str]):
        """``ALL`` keeps a migration from a newer server valid on an older one."""
        schema = alembic_project.schema
        reader, _ = roles
        metadata = MetaData()
        Table("orders", metadata, Column("id", Integer, primary_key=True))
        pg_grants = [f"GRANT ALL ON orders TO {reader}"]

        content = _autogenerate(alembic_project, metadata, pg_grants=pg_grants)

        assert f"GRANT ALL ON TABLE {schema}.orders TO {reader}" in _upgrade_body(content)
        assert f"REVOKE ALL ON TABLE {schema}.orders FROM {reader}" in _downgrade_body(content)
        upgrade(alembic_project.config, "head")
        assert "op.execute" not in _autogenerate(alembic_project, metadata, pg_grants=pg_grants)
