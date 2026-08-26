from __future__ import annotations

import importlib
import logging
from collections.abc import Generator
from typing import Any, cast

import pytest
from sqlalchemy import Column, Connection, Integer, MetaData, Table, Text, text
from sqlalchemy.engine import Engine

from alembic_pg_autogen import (
    IGNORED,
    CanonicalState,
    canonicalize,
    canonicalize_check_constraints,
    canonicalize_functions,
    canonicalize_indexes,
    canonicalize_triggers,
    canonicalize_views,
    inspect_check_constraints,
    inspect_indexes,
)

FN_DDL = "CREATE FUNCTION public.f() RETURNS void LANGUAGE sql AS $$ SELECT 1 $$"
VIEW_DDL = "CREATE VIEW public.v AS SELECT 1"
TRG_DDL = "CREATE TRIGGER trg AFTER INSERT ON public.t FOR EACH ROW EXECUTE FUNCTION public.f()"


class TestCanonicalStateUnit:
    """3.1 — CanonicalState construction and field access."""

    def test_construction_and_fields(self):
        state = CanonicalState(functions=[], triggers=[])
        assert state.functions == []
        assert state.triggers == []

    def test_views_default_empty(self):
        state = CanonicalState(functions=[], triggers=[])
        assert state.views == ()

    def test_views_explicit(self):
        from alembic_pg_autogen import ViewInfo

        v = ViewInfo("public", "v", "def")
        state = CanonicalState(functions=[], triggers=[], views=[v])
        assert len(state.views) == 1

    def test_is_tuple(self):
        state = CanonicalState(functions=[], triggers=[])
        assert isinstance(state, tuple)
        assert state[0] == []
        assert state[1] == []


@pytest.fixture
def pg_conn(pg_engine: Engine) -> Generator[Connection]:
    """Provide an isolated connection that rolls back all DDL after each test."""
    with pg_engine.connect() as conn:
        txn = conn.begin()
        yield conn
        txn.rollback()


@pytest.mark.integration
class TestCanonicalizeIntegration:
    """3.2–3.8 — Integration tests for canonicalize, canonicalize_functions, canonicalize_triggers."""

    # 3.2 — Canonical DDL via canonicalize_functions
    def test_function_canonical_ddl(self, pg_conn: Connection):
        ddl = [
            "CREATE FUNCTION public.test_canon_add(  a   integer,   b   integer  ) "
            "RETURNS integer LANGUAGE sql AS $$ SELECT a + b $$"
        ]
        results = canonicalize_functions(pg_conn, ddl)

        funcs = [f for f in results if f.name == "test_canon_add"]
        assert len(funcs) == 1
        f = funcs[0]
        assert f.schema == "public"
        assert f.identity_args != ""
        # pg_get_functiondef normalizes whitespace in the header
        assert "test_canon_add" in f.definition

    # 3.3 — Function + trigger together via canonicalize
    def test_function_and_trigger_together(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE TABLE public.test_canon_tbl (id integer PRIMARY KEY)"))

        result = canonicalize(
            pg_conn,
            function_ddl=[
                "CREATE FUNCTION public.test_canon_trg_fn() "
                "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$"
            ],
            trigger_ddl=[
                "CREATE TRIGGER test_canon_trg BEFORE INSERT ON public.test_canon_tbl "
                "FOR EACH ROW EXECUTE FUNCTION public.test_canon_trg_fn()"
            ],
            schemas=["public"],
        )

        funcs = [f for f in result.functions if f.name == "test_canon_trg_fn"]
        assert len(funcs) == 1
        trigs = [t for t in result.triggers if t.trigger_name == "test_canon_trg"]
        assert len(trigs) == 1
        assert "test_canon_trg" in trigs[0].definition

    # 3.4 — Database unchanged after canonicalize
    def test_database_unchanged(self, pg_conn: Connection):
        canonicalize(
            pg_conn,
            function_ddl=[
                "CREATE FUNCTION public.test_canon_ephemeral() RETURNS integer LANGUAGE sql AS $$ SELECT 42 $$"
            ],
        )

        row = pg_conn.execute(
            text(
                "SELECT count(*) AS cnt FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE p.proname = 'test_canon_ephemeral' AND n.nspname = 'public'"
            )
        ).scalar()
        assert row == 0

    # 3.5 — Invalid DDL raises and connection stays usable
    def test_invalid_ddl_raises(self, pg_conn: Connection):
        with pytest.raises(Exception):  # noqa: B017
            canonicalize(pg_conn, function_ddl=["CREATE FUNCTION invalid sql garbage"])

        # Connection is still usable
        result = pg_conn.execute(text("SELECT 1 AS val")).scalar()
        assert result == 1

    # 3.6 — Schema scoping
    def test_schema_scoping(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE SCHEMA IF NOT EXISTS test_canon_other"))
        pg_conn.execute(
            text("CREATE FUNCTION test_canon_other.test_canon_scoped() RETURNS integer LANGUAGE sql AS $$ SELECT 1 $$")
        )

        result = canonicalize(
            pg_conn,
            function_ddl=["CREATE FUNCTION public.test_canon_pub() RETURNS integer LANGUAGE sql AS $$ SELECT 2 $$"],
            schemas=["public"],
        )

        schemas = {f.schema for f in result.functions}
        assert "public" in schemas
        assert "test_canon_other" not in schemas

    # 3.7 — Pre-existing functions included in result
    def test_preexisting_included(self, pg_conn: Connection):
        pg_conn.execute(
            text("CREATE FUNCTION public.test_canon_existing() RETURNS integer LANGUAGE sql AS $$ SELECT 10 $$")
        )

        result = canonicalize(
            pg_conn,
            function_ddl=["CREATE FUNCTION public.test_canon_new() RETURNS integer LANGUAGE sql AS $$ SELECT 20 $$"],
            schemas=["public"],
        )

        names = {f.name for f in result.functions}
        assert "test_canon_existing" in names
        assert "test_canon_new" in names

    # 3.8 — CREATE OR REPLACE updates canonical form
    def test_create_or_replace(self, pg_conn: Connection):
        pg_conn.execute(
            text("CREATE FUNCTION public.test_canon_replace() RETURNS integer LANGUAGE sql AS $$ SELECT 1 $$")
        )

        result = canonicalize(
            pg_conn,
            function_ddl=[
                "CREATE OR REPLACE FUNCTION public.test_canon_replace() "
                "RETURNS integer LANGUAGE sql AS $$ SELECT 999 $$"
            ],
            schemas=["public"],
        )

        funcs = [f for f in result.functions if f.name == "test_canon_replace"]
        assert len(funcs) == 1
        assert "999" in funcs[0].definition


@pytest.mark.integration
class TestCanonicalizeViewsIntegration:
    """View canonicalization tests."""

    def test_view_round_trip(self, pg_conn: Connection):
        ddl = ["CREATE VIEW public.test_cv_view AS SELECT 1 AS val"]
        results = canonicalize_views(pg_conn, ddl)

        views = [v for v in results if v.name == "test_cv_view"]
        assert len(views) == 1
        v = views[0]
        assert v.schema == "public"
        assert "CREATE OR REPLACE VIEW" in v.definition
        assert "test_cv_view" in v.definition

    def test_view_database_unchanged(self, pg_conn: Connection):
        canonicalize(
            pg_conn,
            view_ddl=["CREATE VIEW public.test_cv_ephemeral AS SELECT 42 AS x"],
        )

        row = pg_conn.execute(
            text(
                "SELECT count(*) FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relname = 'test_cv_ephemeral' AND n.nspname = 'public' AND c.relkind = 'v'"
            )
        ).scalar()
        assert row == 0

    def test_view_referencing_function(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE FUNCTION public.test_cv_fn() RETURNS integer LANGUAGE sql AS $$ SELECT 1 $$"))

        result = canonicalize(
            pg_conn,
            view_ddl=["CREATE VIEW public.test_cv_fn_view AS SELECT public.test_cv_fn() AS result"],
            schemas=["public"],
        )

        views = [v for v in result.views if v.name == "test_cv_fn_view"]
        assert len(views) == 1
        assert "test_cv_fn" in views[0].definition

    def test_create_or_replace_view(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE VIEW public.test_cv_replace AS SELECT 1 AS val"))

        result = canonicalize(
            pg_conn,
            view_ddl=["CREATE OR REPLACE VIEW public.test_cv_replace AS SELECT 999 AS val"],
            schemas=["public"],
        )

        views = [v for v in result.views if v.name == "test_cv_replace"]
        assert len(views) == 1
        assert "999" in views[0].definition


@pytest.mark.integration
class TestCanonicalizeIgnoredIntegration:
    """``IGNORED`` skips both DDL execution and catalog readback for an object type."""

    def test_ignored_types_are_not_read_back(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE VIEW public.test_ci_existing AS SELECT 1 AS val"))

        result = canonicalize(
            pg_conn,
            function_ddl=["CREATE FUNCTION public.test_ci_fn() RETURNS integer LANGUAGE sql AS $$ SELECT 1 $$"],
            view_ddl=IGNORED,
            trigger_ddl=IGNORED,
            schemas=["public"],
        )

        assert any(f.name == "test_ci_fn" for f in result.functions)
        assert list(result.views) == []
        assert list(result.triggers) == []

    def test_empty_sequence_still_reads_back(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE VIEW public.test_ci_seen AS SELECT 1 AS val"))

        result = canonicalize(pg_conn, view_ddl=(), schemas=["public"])

        assert any(v.name == "test_ci_seen" for v in result.views)


class TestCanonicalizeUnit:
    """Statement ordering and savepoint handling, pinned without a live server.

    A recording connection returns no rows, so every catalog read comes back empty — which is exactly what makes the
    sequence of statements visible.  The behaviour against real DDL is covered by the integration tests above.
    """

    def test_ddl_executes_in_dependency_order(self):
        """Functions first, then views (which may call them), then triggers (which may reference both)."""
        conn = _RecordingConnection()

        canonicalize(
            _as_conn(conn),
            function_ddl=[FN_DDL],
            view_ddl=[VIEW_DDL],
            trigger_ddl=[TRG_DDL],
        )

        assert [_ddl_kind(s) for s in conn.statements if _ddl_kind(s)] == ["function", "view", "trigger"]

    def test_catalog_is_read_after_the_ddl_runs(self):
        conn = _RecordingConnection()

        canonicalize(_as_conn(conn), function_ddl=[FN_DDL])

        ddl_index = next(i for i, s in enumerate(conn.statements) if _ddl_kind(s) == "function")
        catalog_index = next(i for i, s in enumerate(conn.statements) if "pg_catalog.pg_proc" in s)
        assert ddl_index < catalog_index

    def test_savepoint_is_rolled_back(self):
        conn = _RecordingConnection()

        canonicalize(_as_conn(conn), function_ddl=[FN_DDL])

        assert conn.savepoints == [True]

    def test_savepoint_is_rolled_back_when_ddl_fails(self):
        """A failed round-trip must still leave the database exactly as it was found."""
        conn = _RecordingConnection(fail_on="CREATE OR REPLACE FUNCTION")

        with pytest.raises(RuntimeError):
            canonicalize(_as_conn(conn), function_ddl=[FN_DDL])

        assert conn.savepoints == [True]

    def test_ignored_types_are_neither_executed_nor_inspected(self):
        conn = _RecordingConnection()

        state = canonicalize(
            _as_conn(conn),
            function_ddl=[FN_DDL],
            view_ddl=IGNORED,
            trigger_ddl=IGNORED,
        )

        assert [_ddl_kind(s) for s in conn.statements if _ddl_kind(s)] == ["function"]
        assert not any("pg_catalog.pg_trigger" in s for s in conn.statements)
        assert list(state.views) == []
        assert list(state.triggers) == []

    def test_empty_sequence_still_reads_the_catalog_back(self):
        """Declaring nothing is a real desired state — the catalog must be read so drops can be detected."""
        conn = _RecordingConnection()

        canonicalize(_as_conn(conn), function_ddl=[], view_ddl=[], trigger_ddl=[])

        assert any("pg_catalog.pg_proc" in s for s in conn.statements)
        assert any("pg_catalog.pg_class" in s for s in conn.statements)
        assert any("pg_catalog.pg_trigger" in s for s in conn.statements)

    def test_ddl_that_produced_nothing_warns(self, caplog: pytest.LogCaptureFixture):
        """The recording connection reports an empty catalog, which is the symptom this warning exists for."""
        with caplog.at_level(logging.WARNING, logger="alembic_pg_autogen.canonicalize"):
            canonicalize(
                _as_conn(_RecordingConnection()),
                function_ddl=[FN_DDL],
                view_ddl=[VIEW_DDL],
                trigger_ddl=[TRG_DDL],
            )

        assert "produced no functions" in caplog.text
        assert "produced no views" in caplog.text
        assert "produced no triggers" in caplog.text


class TestCanonicalizeWrappersUnit:
    """Each convenience wrapper manages exactly one object type and ignores the rest."""

    def test_functions_wrapper_ignores_views_and_triggers(self):
        conn = _RecordingConnection()

        canonicalize_functions(_as_conn(conn), [FN_DDL])

        assert any("pg_catalog.pg_proc" in s for s in conn.statements)
        assert not any("pg_catalog.pg_trigger" in s for s in conn.statements)

    def test_views_wrapper_ignores_functions_and_triggers(self):
        conn = _RecordingConnection()

        canonicalize_views(_as_conn(conn), [VIEW_DDL])

        assert any("pg_catalog.pg_class" in s for s in conn.statements)
        assert not any("pg_catalog.pg_proc" in s for s in conn.statements)

    def test_triggers_wrapper_ignores_functions_and_views(self):
        conn = _RecordingConnection()

        canonicalize_triggers(_as_conn(conn), [TRG_DDL])

        assert any("pg_catalog.pg_trigger" in s for s in conn.statements)
        assert not any("pg_catalog.pg_proc" in s for s in conn.statements)

    def test_wrapper_passes_schemas_through(self):
        conn = _RecordingConnection()

        canonicalize_functions(_as_conn(conn), [FN_DDL], ["audit"])

        assert {"schemas": ["audit"]} in conn.params


def _ddl_kind(statement: str) -> str | None:
    """Classify a canonicalization DDL statement, ignoring the catalog queries around it."""
    for keyword, kind in (("FUNCTION", "function"), ("VIEW", "view"), ("TRIGGER", "trigger")):
        if statement.startswith(f"CREATE OR REPLACE {keyword}"):
            return kind
    return None


def _as_conn(conn: _RecordingConnection) -> Any:
    """Return the recorder typed as ``Any`` so it can stand in for ``Connection`` without a structural cast."""
    return conn


class _RecordingSavepoint:
    _log: list[bool]

    def __init__(self, log: list[bool]) -> None:
        self._log = log

    def rollback(self) -> None:
        self._log.append(True)


class _RecordingConnection:
    """Records every statement and returns no rows, standing in for a ``Connection``."""

    statements: list[str]
    params: list[dict[str, object]]
    savepoints: list[bool]
    _fail_on: str | None

    def __init__(self, fail_on: str | None = None) -> None:
        self.statements = []
        self.params = []
        self.savepoints = []
        self._fail_on = fail_on

    def begin_nested(self) -> _RecordingSavepoint:
        return _RecordingSavepoint(self.savepoints)

    def execute(self, statement: object, params: dict[str, object] | None = None) -> list[object]:
        rendered = str(statement)
        self.statements.append(rendered)
        if params is not None:
            self.params.append(params)
        if self._fail_on is not None and self._fail_on in rendered:
            raise RuntimeError(f"simulated failure for {rendered!r}")
        return []


class TestCanonicalizeCheckConstraintsUnit:
    def test_empty_mapping_never_touches_the_connection(self):
        # No expressions means no savepoint and no SQL, so an object that raises on any use is safe to pass.
        unusable = cast("Connection", object())

        assert canonicalize_check_constraints(unusable, schema="public", table_name="orders", expressions={}) == {}


@pytest.mark.integration
class TestCanonicalizeCheckConstraintsIntegration:
    def test_round_trip_matches_the_catalog(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE TABLE public.test_cck_orders (amount numeric)"))
        pg_conn.execute(text("ALTER TABLE public.test_cck_orders ADD CONSTRAINT ck_test_cck CHECK (amount >= 0)"))

        normalized = canonicalize_check_constraints(
            pg_conn, schema="public", table_name="test_cck_orders", expressions={"ck_test_cck": "amount >= 0"}
        )

        current = inspect_check_constraints(pg_conn, schemas=["public"], table_names=["test_cck_orders"])
        assert normalized["ck_test_cck"] == current[0].expression

    def test_redundant_parentheses_normalize_to_the_catalog_form(self, pg_conn: Connection):
        """``(amount) >= (0)`` and ``amount >= 0`` are one constraint; only PostgreSQL can say so."""
        pg_conn.execute(text("CREATE TABLE public.test_cck_parens (amount numeric)"))
        pg_conn.execute(
            text("ALTER TABLE public.test_cck_parens ADD CONSTRAINT ck_test_parens CHECK ( (amount)  >=  (0) )")
        )

        normalized = canonicalize_check_constraints(
            pg_conn, schema="public", table_name="test_cck_parens", expressions={"ck_test_parens": "amount >= 0"}
        )

        current = inspect_check_constraints(pg_conn, schemas=["public"], table_names=["test_cck_parens"])
        assert normalized["ck_test_parens"] == current[0].expression

    def test_in_list_rewrite_matches_the_catalog_form(self, pg_conn: Connection):
        """PostgreSQL stores ``IN (...)`` as ``= ANY (ARRAY[...])`` with the column's own literal types."""
        pg_conn.execute(text("CREATE TABLE public.test_cck_status (status varchar(16))"))
        pg_conn.execute(
            text("ALTER TABLE public.test_cck_status ADD CONSTRAINT ck_test_status CHECK (status IN ('new','done'))")
        )

        normalized = canonicalize_check_constraints(
            pg_conn,
            schema="public",
            table_name="test_cck_status",
            expressions={"ck_test_status": "status in ( 'new' , 'done' )"},
        )

        current = inspect_check_constraints(pg_conn, schemas=["public"], table_names=["test_cck_status"])
        assert "ANY" in current[0].expression
        assert normalized["ck_test_status"] == current[0].expression

    def test_changed_expression_differs_from_catalog(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE TABLE public.test_cck_changed (amount numeric)"))
        pg_conn.execute(text("ALTER TABLE public.test_cck_changed ADD CONSTRAINT ck_test_changed CHECK (amount >= 0)"))

        normalized = canonicalize_check_constraints(
            pg_conn, schema="public", table_name="test_cck_changed", expressions={"ck_test_changed": "amount > 0"}
        )

        current = inspect_check_constraints(pg_conn, schemas=["public"], table_names=["test_cck_changed"])
        assert normalized["ck_test_changed"] != current[0].expression

    def test_existing_rows_do_not_block_canonicalization(self, pg_conn: Connection):
        """NOT VALID means a constraint the current data violates still normalizes instead of raising."""
        pg_conn.execute(text("CREATE TABLE public.test_cck_rows (amount numeric)"))
        pg_conn.execute(text("INSERT INTO public.test_cck_rows VALUES (-5)"))

        normalized = canonicalize_check_constraints(
            pg_conn, schema="public", table_name="test_cck_rows", expressions={"ck_test_rows": "amount >= 0"}
        )

        assert "ck_test_rows" in normalized

    def test_unusable_expression_is_omitted_rather_than_raised(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE TABLE public.test_cck_bad (amount numeric)"))

        normalized = canonicalize_check_constraints(
            pg_conn, schema="public", table_name="test_cck_bad", expressions={"ck_test_bad": "no_such_column > 0"}
        )

        assert normalized == {}

    def test_leaves_the_database_unchanged(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE TABLE public.test_cck_clean (amount numeric)"))

        canonicalize_check_constraints(
            pg_conn, schema="public", table_name="test_cck_clean", expressions={"ck_test_clean": "amount >= 0"}
        )

        assert inspect_check_constraints(pg_conn, schemas=["public"], table_names=["test_cck_clean"]) == []

    def test_resolves_unqualified_table_through_search_path(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE SCHEMA test_cck_path"))
        pg_conn.execute(text("CREATE TABLE test_cck_path.orders (amount numeric)"))
        pg_conn.execute(text("SET search_path TO test_cck_path"))

        normalized = canonicalize_check_constraints(
            pg_conn, schema=None, table_name="orders", expressions={"ck_test_path": "amount >= 0"}
        )

        assert "ck_test_path" in normalized


@pytest.mark.integration
class TestCanonicalizeIndexesIntegration:
    """The desired-state half of index comparison, round-tripped through a throwaway clone."""

    def _table(self, conn: Connection, schema: str, name: str = "t") -> None:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        conn.execute(
            text(f"CREATE TABLE {schema}.{name} (id integer PRIMARY KEY, a text, b text, status text, data jsonb)")
        )

    def _metadata_table(self, schema: str | None, *indexes: Any) -> Any:
        from sqlalchemy import Column, Integer, MetaData, Table, Text
        from sqlalchemy.dialects.postgresql import JSONB

        return Table(
            "t",
            MetaData(),
            Column("id", Integer, primary_key=True),
            Column("a", Text()),
            Column("b", Text()),
            Column("status", Text()),
            Column("data", JSONB()),
            *indexes,
            schema=schema,
        )

    def test_round_trip_matches_the_catalog(self, pg_conn: Connection):
        from sqlalchemy import Index

        self._table(pg_conn, "test_cix_rt")
        pg_conn.execute(text("CREATE INDEX ix_rt ON test_cix_rt.t (a) INCLUDE (b) WHERE status IS NOT NULL"))
        table = self._metadata_table(
            "test_cix_rt",
            Index("ix_rt", "a", postgresql_include=["b"], postgresql_where=text("status IS NOT NULL")),
        )

        normalized = canonicalize_indexes(
            pg_conn, schema="test_cix_rt", table_name="t", indexes={ix.name: ix for ix in table.indexes}
        )
        (catalog,) = inspect_indexes(pg_conn, schemas=["test_cix_rt"], table_names=["t"])

        assert normalized["ix_rt"].shape == catalog.shape
        assert normalized["ix_rt"].unique == catalog.unique

    def test_predicate_rewrites_are_normalized(self, pg_conn: Connection):
        from sqlalchemy import Index

        self._table(pg_conn, "test_cix_in")
        table = self._metadata_table("test_cix_in", Index("ix_in", "a", postgresql_where=text("status IN ('x','y')")))

        normalized = canonicalize_indexes(
            pg_conn, schema="test_cix_in", table_name="t", indexes={ix.name: ix for ix in table.indexes}
        )

        assert normalized["ix_in"].shape == ("USING btree (a) WHERE (status = ANY (ARRAY['x'::text, 'y'::text]))")

    def test_result_carries_the_callers_identity(self, pg_conn: Connection):
        from sqlalchemy import Index

        self._table(pg_conn, "test_cix_id")
        table = self._metadata_table("test_cix_id", Index("ix_id", "a"))

        normalized = canonicalize_indexes(
            pg_conn, schema="test_cix_id", table_name="t", indexes={ix.name: ix for ix in table.indexes}
        )

        info = normalized["ix_id"]
        assert (info.schema, info.table_name, info.name) == ("test_cix_id", "t", "ix_id")

    def test_unqualified_metadata_resolves_to_the_clone(self, pg_conn: Connection):
        from sqlalchemy import Index

        self._table(pg_conn, "test_cix_path")
        pg_conn.execute(text("SET search_path TO test_cix_path"))
        table = self._metadata_table(None, Index("ix_path", text("lower(a)")))

        normalized = canonicalize_indexes(
            pg_conn, schema=None, table_name="t", indexes={ix.name: ix for ix in table.indexes}
        )

        assert normalized["ix_path"].shape == "USING btree (lower(a))"

    def test_real_table_is_never_touched(self, pg_conn: Connection):
        from sqlalchemy import Index

        self._table(pg_conn, "test_cix_clean")
        table = self._metadata_table("test_cix_clean", Index("ix_clean", "a"))

        canonicalize_indexes(
            pg_conn, schema="test_cix_clean", table_name="t", indexes={ix.name: ix for ix in table.indexes}
        )

        assert inspect_indexes(pg_conn, schemas=["test_cix_clean"], table_names=["t"]) == []
        remaining = pg_conn.execute(
            text("SELECT count(*) FROM pg_class WHERE relnamespace = pg_my_temp_schema()")
        ).scalar()
        assert remaining == 0

    def test_one_unusable_index_does_not_cost_the_others(self, pg_conn: Connection):
        from sqlalchemy import Index

        self._table(pg_conn, "test_cix_bad")
        table = self._metadata_table(
            "test_cix_bad", Index("ix_good", "a"), Index("ix_bad", text("no_such_function(a)"))
        )

        normalized = canonicalize_indexes(
            pg_conn, schema="test_cix_bad", table_name="t", indexes={ix.name: ix for ix in table.indexes}
        )

        assert "ix_good" in normalized
        assert "ix_bad" not in normalized

    def test_every_index_unusable_skips_the_read_back(self, pg_conn: Connection):
        """With nothing successfully probed there is no read-back to do, and the result is empty rather than an error."""
        from sqlalchemy import Index

        self._table(pg_conn, "test_cix_allbad")
        table = self._metadata_table(
            "test_cix_allbad",
            Index("ix_bad_one", text("no_such_function(a)")),
            Index("ix_bad_two", text("also_missing(b)")),
        )

        normalized = canonicalize_indexes(
            pg_conn, schema="test_cix_allbad", table_name="t", indexes={ix.name: ix for ix in table.indexes}
        )

        assert normalized == {}

    def test_uncompilable_index_does_not_abort_the_run(self, pg_conn: Connection, caplog: pytest.LogCaptureFixture):
        """A ``CompileError`` is raised inside ``execute()`` before the server sees anything, and is not a DBAPIError.

        Catching only the server-side error let one unrenderable index abort autogenerate outright, taking every other
        index on the table with it.
        """
        from sqlalchemy import Index, bindparam
        from sqlalchemy.dialects.postgresql import JSONB

        pg_conn.execute(text("CREATE SCHEMA test_cix_compile"))
        pg_conn.execute(text("CREATE TABLE test_cix_compile.t (id integer PRIMARY KEY, a text, payload jsonb)"))
        table = Table(
            "t",
            MetaData(),
            Column("id", Integer, primary_key=True),
            Column("a", Text()),
            Column("payload", JSONB()),
            schema="test_cix_compile",
        )
        # No literal renderer exists for a JSONB bind, so SQLAlchemy cannot compile this index at all.
        uncompilable = Index("ix_uncompilable", table.c.payload == bindparam("v", value={"a": 1}, type_=JSONB()))
        usable = Index("ix_usable", table.c.a)

        with caplog.at_level(logging.WARNING, logger="alembic_pg_autogen.canonicalize"):
            normalized = canonicalize_indexes(
                pg_conn,
                schema="test_cix_compile",
                table_name="t",
                indexes={"ix_uncompilable": uncompilable, "ix_usable": usable},
            )

        assert "ix_usable" in normalized, "one unrenderable index must not cost the others their comparison"
        assert "ix_uncompilable" not in normalized
        assert "Could not canonicalize index 'ix_uncompilable'" in caplog.text

    def test_clone_name_already_taken_reports_the_cause(self, pg_conn: Connection, caplog: pytest.LogCaptureFixture):
        """The clone must take the target's own name for the redirect to reach it, so the name has to be free."""
        from sqlalchemy import Index

        self._table(pg_conn, "test_cix_taken")
        table = self._metadata_table("test_cix_taken", Index("ix_taken", "a"))
        pg_conn.execute(text("CREATE TEMP TABLE t (unrelated integer)"))

        with caplog.at_level(logging.WARNING, logger="alembic_pg_autogen.canonicalize"):
            normalized = canonicalize_indexes(
                pg_conn, schema="test_cix_taken", table_name="t", indexes={ix.name: ix for ix in table.indexes}
            )

        assert normalized == {}
        assert "Could not create a temporary probe clone" in caplog.text
        # The pre-existing temporary table is left exactly as it was.
        columns = (
            pg_conn
            .execute(
                text(
                    "SELECT attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                    "WHERE c.relname = 't' AND c.relnamespace = pg_my_temp_schema() AND a.attnum > 0"
                )
            )
            .scalars()
            .all()
        )
        assert columns == ["unrelated"]

    def test_failing_read_back_is_treated_as_unchanged(
        self, pg_conn: Connection, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        """The probes themselves can succeed and the read-back still fail; that must not propagate either."""
        from sqlalchemy import Index

        canonicalize_module = importlib.import_module("alembic_pg_autogen.canonicalize")

        self._table(pg_conn, "test_cix_readback")
        table = self._metadata_table("test_cix_readback", Index("ix_readback", "a"))
        monkeypatch.setattr(canonicalize_module, "_INDEX_PROBE_QUERY", "SELECT this is not valid sql")

        with caplog.at_level(logging.WARNING, logger="alembic_pg_autogen.canonicalize"):
            normalized = canonicalize_indexes(
                pg_conn, schema="test_cix_readback", table_name="t", indexes={ix.name: ix for ix in table.indexes}
            )

        assert normalized == {}
        assert "Could not canonicalize indexes on" in caplog.text

    def test_empty_input_executes_no_ddl(self, pg_conn: Connection):
        assert canonicalize_indexes(pg_conn, schema="public", table_name="missing", indexes={}) == {}

    def test_missing_table_returns_empty(self, pg_conn: Connection):
        from sqlalchemy import Index

        table = self._metadata_table("public", Index("ix_nope", "a"))

        normalized = canonicalize_indexes(
            pg_conn, schema="public", table_name="test_cix_absent", indexes={ix.name: ix for ix in table.indexes}
        )

        assert normalized == {}

    def test_probe_query_strips_a_postgresql_14_style_table_reference(self, pg_conn: Connection):
        """PostgreSQL 14 prints the backing ``pg_temp_3`` where 15 and newer print the ``pg_temp`` alias.

        CI runs PostgreSQL 14, so the fully-qualified candidate is the branch that fires there, and the branch whose
        absence made every index look unnormalizable. ``pg_get_indexdef()`` always qualifies the table reference, and
        a temp table on this server always qualifies it as the alias, so the qualified branch is exercised the only way
        it can be here: against a table in a named schema, whose rendering is string-for-string what PostgreSQL 14
        emits for a temporary one.
        """
        from alembic_pg_autogen.canonicalize import _INDEX_PROBE_QUERY  # pyright: ignore[reportPrivateUsage]

        # Relax the temp-only filter and neutralise the alias candidate, so a match can only have come from the
        # fully-qualified one.
        variant = _INDEX_PROBE_QUERY.replace("tc.relnamespace = pg_my_temp_schema()", "true").replace(
            "'pg_temp.'", "'not_the_alias_postgresql_uses.'"
        )
        assert variant != _INDEX_PROBE_QUERY, "both the temp filter and the alias candidate should be present"

        pg_conn.execute(text("CREATE SCHEMA test_cix_pg14"))
        pg_conn.execute(text("CREATE TABLE test_cix_pg14.t (a text, status text)"))
        pg_conn.execute(text("CREATE INDEX ix_pg14 ON test_cix_pg14.t (a) WHERE status IS NOT NULL"))

        rows = pg_conn.execute(text(variant), {"names": ["ix_pg14"]}).all()

        assert [(r.name, r.unique, r.shape) for r in rows] == [
            ("ix_pg14", False, "USING btree (a) WHERE (status IS NOT NULL)")
        ]

    def test_unstrippable_probe_definition_is_treated_as_unchanged(
        self, pg_conn: Connection, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        """The branch that hid the PostgreSQL 14 bug: a shape that will not strip must warn, not silently vanish."""
        from sqlalchemy import Index

        # The package re-exports the ``canonicalize`` *function*, which shadows the submodule of the same name:
        # both ``from alembic_pg_autogen import canonicalize`` and ``import alembic_pg_autogen.canonicalize as ...``
        # resolve to it, so reach for the module through the import system instead.
        canonicalize_module = importlib.import_module("alembic_pg_autogen.canonicalize")

        self._table(pg_conn, "test_cix_null")
        table = self._metadata_table("test_cix_null", Index("ix_null", "a"))
        broken = canonicalize_module._INDEX_PROBE_QUERY.replace(  # pyright: ignore[reportPrivateUsage]
            "'CREATE '", "'NOT HOW POSTGRESQL SPELLS IT '"
        )
        monkeypatch.setattr(canonicalize_module, "_INDEX_PROBE_QUERY", broken)

        with caplog.at_level(logging.WARNING, logger="alembic_pg_autogen.canonicalize"):
            normalized = canonicalize_indexes(
                pg_conn, schema="test_cix_null", table_name="t", indexes={ix.name: ix for ix in table.indexes}
            )

        assert normalized == {}
        assert "Could not normalize the probed definition" in caplog.text

    def test_probe_query_yields_null_when_no_candidate_matches(self, pg_conn: Connection):
        """An unrecognised rendering must produce NULL, which the caller treats as unchanged rather than guessing."""
        from alembic_pg_autogen.canonicalize import _INDEX_PROBE_QUERY  # pyright: ignore[reportPrivateUsage]

        broken = _INDEX_PROBE_QUERY.replace("'CREATE '", "'NOT HOW POSTGRESQL SPELLS IT '")
        pg_conn.execute(text("CREATE SCHEMA test_cix_none"))
        pg_conn.execute(text("CREATE TABLE test_cix_none.t (a text)"))
        savepoint = pg_conn.begin_nested()
        try:
            pg_conn.execute(text("CREATE TEMP TABLE t (LIKE test_cix_none.t)"))
            pg_conn.execute(text("CREATE INDEX ix_none ON pg_temp.t (a)"))
            rows = pg_conn.execute(text(broken), {"names": ["ix_none"]}).all()
        finally:
            savepoint.rollback()

        assert [r.shape for r in rows] == [None]
