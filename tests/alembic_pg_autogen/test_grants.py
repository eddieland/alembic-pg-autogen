# These tests call private helpers of the comparator and the renderer directly.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING
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
    GrantInfo,
    GrantOp,
    GrantPrivilegesOp,
    RevokePrivilegesOp,
    canonicalize_grants,
    diff,
    inspect_grants,
)
from alembic_pg_autogen.compare import _grant_migration_ops, _grant_states, _group_grants
from alembic_pg_autogen.render import (
    _privilege_rank,
    _render_alter_grant_option,
    _render_grant_privileges,
    _render_revoke_privileges,
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
        current = CanonicalState((), (), grants=[_grant("SELECT"), _grant("DELETE"), _grant("UPDATE")])
        desired = CanonicalState((), (), grants=[_grant("SELECT"), _grant("INSERT"), _grant("UPDATE", grantable=True)])

        ops = diff(current, desired).grant_ops

        by_privilege = {(op.current or op.desired).privilege: op for op in ops}  # pyright: ignore[reportOptionalMemberAccess]
        assert set(by_privilege) == {"DELETE", "INSERT", "UPDATE"}
        assert by_privilege["DELETE"].action is Action.DROP
        assert by_privilege["INSERT"].action is Action.CREATE
        assert by_privilege["UPDATE"].action is Action.REPLACE

    def test_equal_grants_give_no_ops(self):
        state = CanonicalState((), (), grants=[_grant("SELECT", grantable=True)])
        assert diff(state, state).grant_ops == []

    def test_diff_result_default(self):
        assert diff(CanonicalState((), ()), CanonicalState((), ())).grant_ops == []


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

        early, late = _grant_migration_ops(grant_ops)

        assert [type(op) for op in early] == [RevokePrivilegesOp, AlterGrantOptionOp]
        assert [op.to_diff_tuple() for op in late] == [
            ("grant_privileges", "TABLE", "public", "orders", "app", ("SELECT", "INSERT")),
            ("grant_privileges", "TABLE", "public", "orders", "app", ("DELETE",)),
            ("grant_privileges", "TABLE", "public", "orders", "other", ("SELECT",)),
        ]

    def test_no_ops(self):
        assert _grant_migration_ops([]) == ([], [])

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

    def test_extension_members_are_excluded(self, scratch: tuple[Connection, str]):
        conn, schema = scratch
        conn.execute(text(f"GRANT SELECT ON {schema}.items TO {schema}_reader"))
        conn.execute(text(f"GRANT SELECT ON {schema}.orders TO {schema}_reader"))
        conn.execute(text(f"ALTER EXTENSION plpgsql ADD TABLE {schema}.items"))

        assert [grant.name for grant in inspect_grants(conn, [schema])] == ["orders"]

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
        initial = inspect_grants(pg_conn, ["public"], initial=True)

        assert GrantInfo("public", "SCHEMA", "public", "public", "USAGE", False) in initial

    def test_new_objects_have_no_initial_privileges(self, scratch: tuple[Connection, str]):
        conn, schema = scratch
        conn.execute(text(f"GRANT SELECT ON {schema}.orders TO {schema}_reader"))

        assert inspect_grants(conn, [schema], initial=True) == []


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

    def test_unqualified_table_uses_current_schema(self, scratch: tuple[Connection, str]):
        conn, schema = scratch
        conn.execute(text(f"SET LOCAL search_path TO {schema}"))

        (grant,) = canonicalize_grants(conn, [f"GRANT SELECT ON orders TO {schema}_reader"])

        assert grant.schema == schema

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
        ],
    )
    def test_unsupported_statements(self, pg_conn: Connection, ddl: str, message: str):
        with pytest.raises(ValueError, match=message):
            canonicalize_grants(pg_conn, [ddl])


@pytest.mark.integration
class TestGrantStates:
    def test_initial_privileges_are_unmanaged(self, pg_conn: Connection):
        """An empty ``pg_grants`` must not revoke ``USAGE`` on the ``public`` schema from ``PUBLIC``."""
        current, desired = _grant_states(pg_conn, ["GRANT USAGE ON SCHEMA public TO PUBLIC"], ["public"])

        public_usage = GrantInfo("public", "SCHEMA", "public", "public", "USAGE", False)
        assert public_usage not in current
        assert public_usage not in desired

    def test_desired_grants_outside_compared_schemas_are_dropped(self, pg_conn: Connection):
        _, desired = _grant_states(pg_conn, ["GRANT SELECT ON elsewhere.t TO app"], ["public"])

        assert desired == []


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


def _project_grants(project: AlembicProject) -> list[GrantInfo]:
    with project.connect() as conn:
        return list(inspect_grants(conn, [project.schema]))


@pytest.mark.integration
class TestAutogenerateGrants:
    def test_new_grant(self, alembic_project: AlembicProject, roles: tuple[str, str]):
        schema = alembic_project.schema
        reader, _ = roles
        alembic_project.execute(f"CREATE TABLE {schema}.orders (id int)")

        content = _autogenerate(alembic_project, pg_grants=[f"GRANT SELECT, INSERT ON orders TO {reader}"])

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

        content = _autogenerate(alembic_project, pg_grants=[f"GRANT SELECT ON orders TO {reader}"])

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

        assert "REVOKE" not in _autogenerate(alembic_project, **attrs)

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

    def test_revoke_precedes_drop_table(self, alembic_project: AlembicProject, roles: tuple[str, str]):
        schema = alembic_project.schema
        reader, _ = roles
        alembic_project.execute(f"CREATE TABLE {schema}.orders (id int)")
        alembic_project.execute(f"GRANT SELECT ON {schema}.orders TO {reader}")

        content = _autogenerate(alembic_project, MetaData(), pg_grants=[])

        upgrade_body = _upgrade_body(content)
        assert upgrade_body.index("REVOKE SELECT") < upgrade_body.index("op.drop_table(")
        upgrade(alembic_project.config, "head")
        with alembic_project.connect() as conn:
            assert conn.execute(text(f"SELECT to_regclass('{schema}.orders')")).scalar() is None

    def test_grant_on_new_view_follows_create_view(self, alembic_project: AlembicProject, roles: tuple[str, str]):
        schema = alembic_project.schema
        reader, _ = roles

        content = _autogenerate(
            alembic_project,
            pg_views=[f"CREATE VIEW {schema}.answer AS SELECT 42 AS value"],
            pg_grants=[f"GRANT SELECT ON {schema}.answer TO {reader}"],
        )

        upgrade_body = _upgrade_body(content)
        assert upgrade_body.index("CREATE OR REPLACE VIEW") < upgrade_body.index("GRANT SELECT")
        upgrade(alembic_project.config, "head")
        assert _project_grants(alembic_project) == [GrantInfo(schema, "TABLE", "answer", reader, "SELECT", False)]
