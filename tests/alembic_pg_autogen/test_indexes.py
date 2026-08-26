# These tests exercise the comparator's internals directly.
# pyright: reportPrivateUsage=false

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from alembic.operations.ops import CreateIndexOp, DropIndexOp, ModifyTableOps
from alembic.runtime.plugins import Plugin
from alembic.util import DispatchPriority, PriorityDispatchResult
from sqlalchemy import Column, DateTime, Index, Integer, MetaData, Table, Text, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB

from alembic_pg_autogen import CreateIndexConcurrentlyOp, DropIndexConcurrentlyOp, IndexInfo
from alembic_pg_autogen import compare_indexes as module
from alembic_pg_autogen.compare_indexes import (
    _compare_index_definitions,
    _describe,
    _index_ops,
    _names_already_emitted,
    setup,
)

from .test_autogenerate import _autogenerate

if TYPE_CHECKING:
    from sqlalchemy.engine import Dialect

    from .alembic_helpers import AlembicProject

PG_DIALECT = postgresql.dialect()


def _upgrade_body(content: str) -> str:
    """Return only the ``upgrade()`` half of a generated migration.

    Every operation this comparator emits is reversible, so the same call text appears again in ``downgrade()``.
    Assertions about what a migration does therefore have to say which direction they mean.
    """
    return content.split("def upgrade()")[1].split("def downgrade()")[0]


def _downgrade_body(content: str) -> str:
    """Return only the ``downgrade()`` half of a generated migration."""
    return content.split("def downgrade()")[1]


def _t_table(*indexes: Index, metadata: MetaData | None = None) -> Table:
    return Table(
        "t",
        metadata if metadata is not None else MetaData(),
        Column("id", Integer, primary_key=True),
        # Text, not String: the table DDL below uses ``text`` columns, and a mismatch would make Alembic emit an
        # ``alter_column`` that changes the very types the index predicate deparses against.
        Column("a", Text()),
        Column("b", Text()),
        Column("status", Text()),
        Column("data", JSONB()),
        Column("deleted_at", DateTime(timezone=True)),
        *indexes,
    )


class _StubAutogenContext:
    """Minimal stand-in for ``AutogenContext`` exposing only what the comparator touches."""

    connection: object | None
    dialect: Dialect
    opts: dict[str, object]
    name_filter_result: bool
    object_filter_result: bool

    def __init__(self, connection: object | None = None, **opts: object) -> None:
        self.connection = connection
        self.dialect = PG_DIALECT
        self.opts = dict(opts)
        self.name_filter_result = True
        self.object_filter_result = True

    def run_name_filters(self, *_args: Any, **_kw: Any) -> bool:
        return self.name_filter_result

    def run_object_filters(self, *_args: Any, **_kw: Any) -> bool:
        return self.object_filter_result


def _stub_context(connection: object | None = None, **opts: object) -> Any:
    """Return a stub typed as ``Any`` so it can stand in for ``AutogenContext`` without a structural cast."""
    return _StubAutogenContext(connection, **opts)


class TestNamesAlreadyEmitted:
    def test_empty_when_nothing_emitted(self):
        assert _names_already_emitted(ModifyTableOps("t", [])) == frozenset()

    def test_collects_index_names(self):
        ops = ModifyTableOps("t", [DropIndexOp("ix_a", "t"), CreateIndexOp("ix_b", "t", ["a"])])

        assert _names_already_emitted(ops) == frozenset({"ix_a", "ix_b"})

    def test_ignores_operations_without_an_index_name(self):
        class _Other:
            pass

        ops = ModifyTableOps("t", [_Other()])  # pyright: ignore[reportArgumentType]

        assert _names_already_emitted(ops) == frozenset()


class TestIndexOps:
    def test_default_emits_alembic_operations_drop_then_create(self):
        table = _t_table(Index("ix_t_a", "a"))
        index = next(iter(table.indexes))

        drop, create = _index_ops(index, index, concurrently=False)

        assert isinstance(drop, DropIndexOp)
        assert isinstance(create, CreateIndexOp)

    def test_concurrent_wraps_both_operations(self):
        table = _t_table(Index("ix_t_a", "a"))
        index = next(iter(table.indexes))

        drop, create = _index_ops(index, index, concurrently=True)

        assert isinstance(drop, DropIndexConcurrentlyOp)
        assert isinstance(create, CreateIndexConcurrentlyOp)
        assert drop.inner.kw["postgresql_concurrently"] is True
        assert create.inner.kw["postgresql_concurrently"] is True


class TestConcurrentOps:
    def test_reverse_stays_concurrent(self):
        table = _t_table(Index("ix_t_a", "a"))
        index = next(iter(table.indexes))
        create = CreateIndexConcurrentlyOp(CreateIndexOp.from_index(index))

        reversed_op = create.reverse()

        assert isinstance(reversed_op, DropIndexConcurrentlyOp)
        assert isinstance(reversed_op.reverse(), CreateIndexConcurrentlyOp)

    def test_diff_tuples_mirror_alembics(self):
        table = _t_table(Index("ix_t_a", "a"))
        index = next(iter(table.indexes))

        create = CreateIndexConcurrentlyOp(CreateIndexOp.from_index(index))
        drop = DropIndexConcurrentlyOp(DropIndexOp.from_index(index))

        assert create.to_diff_tuple()[0] == "add_index"
        assert drop.to_diff_tuple()[0] == "remove_index"


class TestDescribe:
    @pytest.mark.parametrize(
        ("unique", "shape", "expected"),
        [
            (False, "USING btree (a)", "USING btree (a)"),
            (True, "USING btree (a)", "UNIQUE USING btree (a)"),
        ],
    )
    def test_renders_uniqueness(self, unique: bool, shape: str, expected: str):
        assert _describe(unique, shape) == expected


class TestComparatorSkips:
    """Everything the comparator declines to act on, short of emitting operations."""

    @pytest.fixture
    def catalog(self, monkeypatch: pytest.MonkeyPatch):
        """Stub the catalog and canonicalization calls so the comparator can run without a database."""

        def install(current: dict[str, str], normalized: dict[str, str]) -> list[str]:
            canonicalized: list[str] = []

            def fake_inspect(_conn: object, **_kw: Any) -> list[IndexInfo]:
                return [IndexInfo("public", "t", name, False, shape) for name, shape in current.items()]

            def fake_canonicalize(_conn: object, **kwargs: Any) -> dict[str, IndexInfo]:
                canonicalized.extend(kwargs["indexes"])
                return {name: IndexInfo("public", "t", name, False, shape) for name, shape in normalized.items()}

            def fake_current_schema(_conn: object) -> str:
                return "public"

            monkeypatch.setattr(module, "inspect_indexes", fake_inspect)
            monkeypatch.setattr(module, "canonicalize_indexes", fake_canonicalize)
            monkeypatch.setattr(module, "current_schema", fake_current_schema)
            return canonicalized

        return install

    def _run(self, table: Table, context: Any = None, existing: list[Any] | None = None) -> ModifyTableOps:
        modify_table_ops = ModifyTableOps("t", list(existing or []))
        result = _compare_index_definitions(
            context if context is not None else _stub_context(connection=object()),
            modify_table_ops,
            "public",
            "t",
            table,
            table,
        )
        assert result is PriorityDispatchResult.CONTINUE
        return modify_table_ops

    def test_index_missing_from_the_catalog_is_alembics_job(self, catalog: Any):
        probed = catalog({"ix_other": "USING btree (b)"}, {})
        table = _t_table(Index("ix_t_a", "a"))

        assert self._run(table).is_empty()
        assert probed == []

    def test_index_alembic_already_emitted_for_is_left_alone(self, catalog: Any):
        probed = catalog({"ix_t_a": "USING btree (a)"}, {"ix_t_a": "USING btree (lower(a))"})
        table = _t_table(Index("ix_t_a", "a"))

        ops = self._run(table, existing=[DropIndexOp("ix_t_a", "t")])

        assert len(ops.ops) == 1
        assert probed == []

    def test_index_that_could_not_be_canonicalized_is_treated_as_unchanged(self, catalog: Any):
        catalog({"ix_t_a": "USING btree (a)"}, {})
        table = _t_table(Index("ix_t_a", "a"))

        assert self._run(table).is_empty()

    def test_identical_definition_emits_nothing(self, catalog: Any):
        catalog({"ix_t_a": "USING btree (a)"}, {"ix_t_a": "USING btree (a)"})
        table = _t_table(Index("ix_t_a", "a"))

        assert self._run(table).is_empty()

    def test_object_filter_can_veto_the_change(self, catalog: Any):
        catalog({"ix_t_a": "USING btree (a)"}, {"ix_t_a": "USING btree (lower(a))"})
        table = _t_table(Index("ix_t_a", "a"))
        context = _stub_context(connection=object())
        context.object_filter_result = False

        assert self._run(table, context).is_empty()

    def test_name_filter_can_veto_the_change(self, catalog: Any):
        catalog({"ix_t_a": "USING btree (a)"}, {"ix_t_a": "USING btree (lower(a))"})
        table = _t_table(Index("ix_t_a", "a"))
        context = _stub_context(connection=object())
        context.name_filter_result = False

        assert self._run(table, context).is_empty()

    def test_differing_definition_emits_drop_then_create(self, catalog: Any):
        catalog({"ix_t_a": "USING btree (a)"}, {"ix_t_a": "USING btree (a) WHERE (deleted_at IS NULL)"})
        table = _t_table(Index("ix_t_a", "a"))

        ops = self._run(table).ops

        assert [type(op).__name__ for op in ops] == ["DropIndexOp", "CreateIndexOp"]

    def test_uniqueness_difference_is_a_change(self, catalog: Any, monkeypatch: pytest.MonkeyPatch):
        def fake_canonicalize(_conn: object, **_kw: Any) -> dict[str, IndexInfo]:
            return {"ix_t_a": IndexInfo("public", "t", "ix_t_a", True, "USING btree (a)")}

        catalog({"ix_t_a": "USING btree (a)"}, {})
        # Re-stub the canonicalizer so uniqueness is the only difference left.
        monkeypatch.setattr(module, "canonicalize_indexes", fake_canonicalize)
        table = _t_table(Index("ix_t_a", "a"))

        ops = self._run(table).ops

        assert [type(op).__name__ for op in ops] == ["DropIndexOp", "CreateIndexOp"]

    def test_concurrent_option_wraps_the_emitted_operations(self, catalog: Any):
        catalog({"ix_t_a": "USING btree (a)"}, {"ix_t_a": "USING btree (a) WHERE (deleted_at IS NULL)"})
        table = _t_table(Index("ix_t_a", "a"))
        context = _stub_context(connection=object(), pg_index_concurrently=True)

        ops = self._run(table, context).ops

        assert [type(op).__name__ for op in ops] == ["DropIndexConcurrentlyOp", "CreateIndexConcurrentlyOp"]


class TestComparatorShortCircuits:
    """Cases the comparator rejects before touching the catalog at all."""

    def test_new_table_is_alembics_job(self):
        table = _t_table(Index("ix_t_a", "a"))
        modify_table_ops = ModifyTableOps("t", [])

        result = _compare_index_definitions(
            _stub_context(connection=object()), modify_table_ops, "public", "t", None, table
        )

        assert result is PriorityDispatchResult.CONTINUE
        assert modify_table_ops.is_empty()

    def test_dropped_table_is_alembics_job(self):
        table = _t_table(Index("ix_t_a", "a"))
        modify_table_ops = ModifyTableOps("t", [])

        result = _compare_index_definitions(
            _stub_context(connection=object()), modify_table_ops, "public", "t", table, None
        )

        assert result is PriorityDispatchResult.CONTINUE
        assert modify_table_ops.is_empty()

    def test_offline_autogenerate_skipped(self):
        table = _t_table(Index("ix_t_a", "a"))
        modify_table_ops = ModifyTableOps("t", [])

        result = _compare_index_definitions(
            _stub_context(connection=None), modify_table_ops, "public", "t", table, table
        )

        assert result is PriorityDispatchResult.CONTINUE
        assert modify_table_ops.is_empty()

    def test_table_without_indexes_skipped(self):
        table = _t_table()
        modify_table_ops = ModifyTableOps("t", [])

        result = _compare_index_definitions(
            _stub_context(connection=object()), modify_table_ops, "public", "t", table, table
        )

        assert result is PriorityDispatchResult.CONTINUE
        assert modify_table_ops.is_empty()


class TestSetup:
    def test_registers_table_level_comparator_last(self):
        plugin = Plugin("test_alembic_pg_autogen_indexes")
        try:
            setup(plugin)
            dispatched = plugin.autogenerate_comparators.dispatch("table", qualifier="postgresql")
            registry = plugin.autogenerate_comparators._registry  # pyright: ignore[reportAttributeAccessIssue]
            keys = [key for key in registry if key[0] == "table"]
        finally:
            plugin.remove()

        assert dispatched is not None
        assert keys == [("table", "postgresql", DispatchPriority.LAST)]


@pytest.mark.integration
class TestIndexAutogenerateIntegration:
    """Every gap verified against a live PostgreSQL, plus the equivalences that must stay silent."""

    def _project_with(self, project: AlembicProject, index_ddl: str) -> None:
        project.execute(
            "CREATE TABLE t (id serial PRIMARY KEY, a text, b text, status text, data jsonb, deleted_at timestamptz)"
        )
        if index_ddl:
            project.execute(index_ddl)

    @pytest.mark.parametrize(
        ("label", "index_ddl", "metadata_index"),
        [
            (
                "predicate added",
                "CREATE INDEX ix_t_a ON t (a)",
                lambda: Index("ix_t_a", "a", postgresql_where=text("deleted_at IS NULL")),
            ),
            (
                "predicate removed",
                "CREATE INDEX ix_t_a ON t (a) WHERE deleted_at IS NULL",
                lambda: Index("ix_t_a", "a"),
            ),
            (
                "predicate changed",
                "CREATE INDEX ix_t_a ON t (a) WHERE deleted_at IS NULL",
                lambda: Index("ix_t_a", "a", postgresql_where=text("status = 'x'")),
            ),
            (
                "access method changed",
                "CREATE INDEX ix_t_a ON t (data)",
                lambda: Index("ix_t_a", "data", postgresql_using="gin"),
            ),
            (
                "operator class added",
                "CREATE INDEX ix_t_a ON t (a)",
                lambda: Index("ix_t_a", "a", postgresql_ops={"a": "text_pattern_ops"}),
            ),
            (
                "gin operator class changed",
                "CREATE INDEX ix_t_a ON t USING gin (data)",
                lambda: Index("ix_t_a", "data", postgresql_using="gin", postgresql_ops={"data": "jsonb_path_ops"}),
            ),
            (
                "include columns added",
                "CREATE INDEX ix_t_a ON t (a)",
                lambda: Index("ix_t_a", "a", postgresql_include=["b"]),
            ),
            (
                "expression differing only by a cast",
                "CREATE INDEX ix_t_a ON t (a)",
                lambda: Index("ix_t_a", text("(a::int)")),
            ),
        ],
    )
    def test_changed_definition_produces_drop_and_create(
        self, alembic_project: AlembicProject, label: str, index_ddl: str, metadata_index: Any
    ):
        """Each of these is silent in stock Alembic: no operation, on every run."""
        self._project_with(alembic_project, index_ddl)
        metadata = MetaData()
        _t_table(metadata_index(), metadata=metadata)

        upgrade = _upgrade_body(_autogenerate(alembic_project, target_metadata=metadata))

        assert "op.drop_index" in upgrade, label
        assert "op.create_index" in upgrade, label
        assert "ix_t_a" in upgrade, label

    @pytest.mark.parametrize(
        ("label", "index_ddl", "metadata_index"),
        [
            ("identical plain index", "CREATE INDEX ix_t_a ON t (a)", lambda: Index("ix_t_a", "a")),
            (
                "predicate IN(...) rewrite",
                "CREATE INDEX ix_t_a ON t (a) WHERE status IN ('x','y')",
                lambda: Index("ix_t_a", "a", postgresql_where=text("status IN ('x','y')")),
            ),
            (
                "predicate parenthesization",
                "CREATE INDEX ix_t_a ON t (a) WHERE deleted_at IS NULL",
                lambda: Index("ix_t_a", "a", postgresql_where=text("deleted_at IS NULL")),
            ),
            (
                "identical expression",
                "CREATE INDEX ix_t_a ON t (lower(a))",
                lambda: Index("ix_t_a", text("lower(a)")),
            ),
            (
                "identical operator class",
                "CREATE INDEX ix_t_a ON t (a text_pattern_ops)",
                lambda: Index("ix_t_a", "a", postgresql_ops={"a": "text_pattern_ops"}),
            ),
            (
                "identical include",
                "CREATE INDEX ix_t_a ON t (a) INCLUDE (b)",
                lambda: Index("ix_t_a", "a", postgresql_include=["b"]),
            ),
            (
                "literal cast rewrite",
                "CREATE INDEX ix_t_a ON t (coalesce(a, ''))",
                lambda: Index("ix_t_a", text("coalesce(a, '')")),
            ),
        ],
    )
    def test_equivalent_definition_produces_no_index_ops(
        self, alembic_project: AlembicProject, label: str, index_ddl: str, metadata_index: Any
    ):
        """The whole point: these differ as text and are the same index."""
        self._project_with(alembic_project, index_ddl)
        metadata = MetaData()
        _t_table(metadata_index(), metadata=metadata)

        content = _autogenerate(alembic_project, target_metadata=metadata)

        assert "op.drop_index" not in content, label
        assert "op.create_index" not in content, label

    def test_one_drop_create_pair_when_alembic_also_detects_the_change(self, alembic_project: AlembicProject):
        """Alembic compares expressions itself; the two comparators must not both emit."""
        self._project_with(alembic_project, "CREATE INDEX ix_t_a ON t (lower(a))")
        metadata = MetaData()
        _t_table(Index("ix_t_a", text("upper(a)")), metadata=metadata)

        upgrade = _upgrade_body(_autogenerate(alembic_project, target_metadata=metadata))

        assert upgrade.count("op.drop_index") == 1
        assert upgrade.count("op.create_index") == 1

    def test_index_only_in_metadata_is_left_to_alembic(self, alembic_project: AlembicProject):
        self._project_with(alembic_project, "")
        metadata = MetaData()
        _t_table(Index("ix_t_a", "a"), metadata=metadata)

        upgrade = _upgrade_body(_autogenerate(alembic_project, target_metadata=metadata))

        assert upgrade.count("op.create_index") == 1
        assert "op.drop_index" not in upgrade

    def test_constraint_backed_index_is_not_compared(self, alembic_project: AlembicProject):
        alembic_project.execute("CREATE TABLE t (id serial PRIMARY KEY, a text UNIQUE)")
        metadata = MetaData()
        Table(
            "t",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("a", Text(), unique=True),
        )

        upgrade = _upgrade_body(_autogenerate(alembic_project, target_metadata=metadata))

        assert "op.drop_index" not in upgrade

    def test_downgrade_restores_the_catalog_definition(self, alembic_project: AlembicProject):
        self._project_with(alembic_project, "CREATE INDEX ix_t_a ON t (a)")
        metadata = MetaData()
        _t_table(Index("ix_t_a", "a", postgresql_where=text("deleted_at IS NULL")), metadata=metadata)

        downgrade = _downgrade_body(_autogenerate(alembic_project, target_metadata=metadata))

        assert "op.drop_index" in downgrade
        assert "op.create_index" in downgrade

    def test_concurrent_option_renders_an_autocommit_block(self, alembic_project: AlembicProject):
        self._project_with(alembic_project, "CREATE INDEX ix_t_a ON t (a)")
        metadata = MetaData()
        _t_table(Index("ix_t_a", "a", postgresql_where=text("deleted_at IS NULL")), metadata=metadata)

        content = _autogenerate(alembic_project, target_metadata=metadata, pg_index_concurrently=True)

        assert "with op.get_context().autocommit_block():" in content
        assert "postgresql_concurrently=True" in content

    def test_canonicalization_leaves_no_probe_indexes_behind(self, alembic_project: AlembicProject):
        self._project_with(alembic_project, "CREATE INDEX ix_t_a ON t (a)")
        metadata = MetaData()
        _t_table(Index("ix_t_a", "a", postgresql_where=text("deleted_at IS NULL")), metadata=metadata)

        _autogenerate(alembic_project, target_metadata=metadata)

        with alembic_project.connect() as conn:
            names = set(
                conn.execute(
                    text("SELECT indexname FROM pg_indexes WHERE schemaname = :schema"),
                    {"schema": alembic_project.schema},
                ).scalars()
            )

        # ``alembic_version`` brings its own primary key index; what must not survive is anything from the probe.
        assert names == {"t_pkey", "ix_t_a", "alembic_version_pkc"}


@pytest.mark.integration
class TestIndexMigrationRoundTrip:
    """Generate, apply, and re-generate: the migration must run and autogenerate must converge."""

    def _apply(self, project: AlembicProject, metadata: MetaData, **attrs: object) -> str:
        from pathlib import Path

        from alembic.command import revision, upgrade

        cfg = project.config
        cfg.attributes["target_metadata"] = metadata
        for key, value in attrs.items():
            cfg.attributes[key] = value
        versions = Path(cfg.get_main_option("script_location")) / "versions"  # pyright: ignore[reportArgumentType]
        before = set(versions.glob("*.py"))
        assert revision(cfg, message="apply", autogenerate=True) is not None
        upgrade(cfg, "head")
        (created,) = set(versions.glob("*.py")) - before
        return created.read_text()

    def _regenerate(self, project: AlembicProject, metadata: MetaData, **attrs: object) -> str:
        from pathlib import Path

        from alembic.command import revision

        cfg = project.config
        cfg.attributes["target_metadata"] = metadata
        for key, value in attrs.items():
            cfg.attributes[key] = value
        versions = Path(cfg.get_main_option("script_location")) / "versions"  # pyright: ignore[reportArgumentType]
        before = set(versions.glob("*.py"))
        assert revision(cfg, message="again", autogenerate=True) is not None
        (created,) = set(versions.glob("*.py")) - before
        return created.read_text()

    @pytest.mark.parametrize(
        ("label", "index_ddl", "metadata_index", "expected_shape"),
        [
            (
                "predicate",
                "CREATE INDEX ix_t_a ON t (a)",
                lambda: Index("ix_t_a", "a", postgresql_where=text("status IN ('x','y')")),
                "WHERE (status = ANY (ARRAY['x'::text, 'y'::text]))",
            ),
            (
                "operator class",
                "CREATE INDEX ix_t_a ON t (a)",
                lambda: Index("ix_t_a", "a", postgresql_ops={"a": "text_pattern_ops"}),
                "USING btree (a text_pattern_ops)",
            ),
            (
                "gin operator class",
                "CREATE INDEX ix_t_a ON t USING gin (data)",
                lambda: Index("ix_t_a", "data", postgresql_using="gin", postgresql_ops={"data": "jsonb_path_ops"}),
                "USING gin (data jsonb_path_ops)",
            ),
            (
                "include",
                "CREATE INDEX ix_t_a ON t (a)",
                lambda: Index("ix_t_a", "a", postgresql_include=["b"]),
                "INCLUDE (b)",
            ),
        ],
    )
    def test_migration_applies_and_autogenerate_converges(
        self,
        alembic_project: AlembicProject,
        label: str,
        index_ddl: str,
        metadata_index: Any,
        expected_shape: str,
    ):
        alembic_project.execute(
            "CREATE TABLE t (id serial PRIMARY KEY, a text, b text, status text, data jsonb, deleted_at timestamptz)"
        )
        alembic_project.execute(index_ddl)
        metadata = MetaData()
        _t_table(metadata_index(), metadata=metadata)

        self._apply(alembic_project, metadata)

        with alembic_project.connect() as conn:
            applied = conn.execute(
                text("SELECT indexdef FROM pg_indexes WHERE schemaname = :s AND indexname = 'ix_t_a'"),
                {"s": alembic_project.schema},
            ).scalar_one()
        assert expected_shape in applied, label

        second = _upgrade_body(self._regenerate(alembic_project, metadata))
        assert "op.create_index" not in second, label
        assert "op.drop_index" not in second, label

    def test_concurrent_migration_applies(self, alembic_project: AlembicProject):
        """The autocommit block is load-bearing: PostgreSQL rejects CONCURRENTLY inside a transaction."""
        alembic_project.execute(
            "CREATE TABLE t (id serial PRIMARY KEY, a text, b text, status text, data jsonb, deleted_at timestamptz)"
        )
        alembic_project.execute("CREATE INDEX ix_t_a ON t (a)")
        metadata = MetaData()
        _t_table(Index("ix_t_a", "a", postgresql_where=text("deleted_at IS NULL")), metadata=metadata)

        content = self._apply(alembic_project, metadata, pg_index_concurrently=True)

        assert "with op.get_context().autocommit_block():" in content
        with alembic_project.connect() as conn:
            applied = conn.execute(
                text("SELECT indexdef FROM pg_indexes WHERE schemaname = :s AND indexname = 'ix_t_a'"),
                {"s": alembic_project.schema},
            ).scalar_one()
        assert "WHERE (deleted_at IS NULL)" in applied
