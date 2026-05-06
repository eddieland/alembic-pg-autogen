from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.engine import Engine

from alembic_pg_autogen import FunctionInfo, TriggerInfo, ViewInfo, inspect_functions, inspect_triggers, inspect_views


class TestFunctionInfoUnit:
    def test_construction_and_fields(self):
        info = FunctionInfo(
            schema="public", name="add", identity_args="integer, integer", definition="CREATE FUNCTION …"
        )
        assert info.schema == "public"
        assert info.name == "add"
        assert info.identity_args == "integer, integer"
        assert info.definition == "CREATE FUNCTION …"

    def test_is_tuple(self):
        info = FunctionInfo("s", "n", "a", "d")
        assert isinstance(info, tuple)
        assert info[0] == "s"


class TestTriggerInfoUnit:
    def test_construction_and_fields(self):
        info = TriggerInfo(
            schema="public", table_name="orders", trigger_name="trg_audit", definition="CREATE TRIGGER …"
        )
        assert info.schema == "public"
        assert info.table_name == "orders"
        assert info.trigger_name == "trg_audit"
        assert info.definition == "CREATE TRIGGER …"

    def test_is_tuple(self):
        info = TriggerInfo("s", "t", "n", "d")
        assert isinstance(info, tuple)
        assert info[0] == "s"


class TestViewInfoUnit:
    def test_construction_and_fields(self):
        info = ViewInfo(
            schema="public", name="active_users", definition="CREATE OR REPLACE VIEW public.active_users AS\n SELECT 1"
        )
        assert info.schema == "public"
        assert info.name == "active_users"
        assert "CREATE OR REPLACE VIEW" in info.definition

    def test_is_tuple(self):
        info = ViewInfo("s", "n", "d")
        assert isinstance(info, tuple)
        assert info[0] == "s"

    def test_identity_is_schema_and_name(self):
        info = ViewInfo("myschema", "myview", "definition text")
        assert info[:-1] == ("myschema", "myview")


@pytest.fixture
def pg_conn(pg_engine: Engine) -> Generator[Connection]:
    """Provide an isolated connection that rolls back all DDL after each test."""
    with pg_engine.connect() as conn:
        txn = conn.begin()
        yield conn
        txn.rollback()


@pytest.mark.integration
class TestInspectFunctionsIntegration:
    # 3.2  Simple SQL function
    def test_simple_function(self, pg_conn: Connection):
        pg_conn.execute(
            text(
                "CREATE FUNCTION public.test_add(a integer, b integer) "
                "RETURNS integer LANGUAGE sql AS $$ SELECT a + b $$"
            )
        )

        results = inspect_functions(pg_conn)

        funcs = [f for f in results if f.name == "test_add"]
        assert len(funcs) == 1
        f = funcs[0]
        assert f.schema == "public"
        assert f.name == "test_add"
        assert f.identity_args != ""
        assert f.definition != ""
        assert "test_add" in f.definition

    # 3.3  Overloaded functions
    def test_overloaded_functions(self, pg_conn: Connection):
        pg_conn.execute(
            text("CREATE FUNCTION public.test_overload(a integer) RETURNS integer LANGUAGE sql AS $$ SELECT a $$")
        )
        pg_conn.execute(
            text(
                "CREATE FUNCTION public.test_overload(a text, b integer) RETURNS integer LANGUAGE sql AS $$ SELECT b $$"
            )
        )

        results = inspect_functions(pg_conn)

        overloads = [f for f in results if f.name == "test_overload"]
        assert len(overloads) == 2
        args_set = {f.identity_args for f in overloads}
        assert len(args_set) == 2  # distinct identity_args

    # 3.4  Aggregate function excluded
    def test_aggregate_excluded(self, pg_conn: Connection):
        pg_conn.execute(
            text(
                "CREATE FUNCTION public.test_agg_sfunc(state integer, val integer) "
                "RETURNS integer LANGUAGE sql AS $$ SELECT state + val $$"
            )
        )
        pg_conn.execute(
            text(
                "CREATE AGGREGATE public.test_my_sum(integer) (SFUNC = test_agg_sfunc, STYPE = integer, INITCOND = '0')"
            )
        )

        results = inspect_functions(pg_conn)

        names = [f.name for f in results]
        assert "test_my_sum" not in names
        assert "test_agg_sfunc" in names

    # Procedure included (prokind = 'p')
    def test_procedure_included(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE PROCEDURE public.test_noop() LANGUAGE sql AS $$ SELECT 1 $$"))

        results = inspect_functions(pg_conn)

        procs = [f for f in results if f.name == "test_noop"]
        assert len(procs) == 1
        assert "test_noop" in procs[0].definition

    # 3.7  Nonexistent schema returns empty
    def test_nonexistent_schema_empty(self, pg_conn: Connection):
        results = inspect_functions(pg_conn, schemas=["nonexistent"])
        assert len(results) == 0

    # 3.8  Explicit schemas=["public"]
    def test_explicit_public_schema(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE FUNCTION public.test_schema_fn() RETURNS integer LANGUAGE sql AS $$ SELECT 1 $$"))

        results = inspect_functions(pg_conn, schemas=["public"])

        assert all(f.schema == "public" for f in results)
        assert any(f.name == "test_schema_fn" for f in results)


@pytest.mark.integration
class TestInspectTriggersIntegration:
    # 3.5  Simple trigger
    def test_simple_trigger(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE TABLE public.test_trigger_tbl (id integer PRIMARY KEY, val text)"))
        pg_conn.execute(
            text(
                "CREATE FUNCTION public.test_trigger_fn() "
                "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$"
            )
        )
        pg_conn.execute(
            text(
                "CREATE TRIGGER test_trg BEFORE INSERT ON public.test_trigger_tbl "
                "FOR EACH ROW EXECUTE FUNCTION public.test_trigger_fn()"
            )
        )

        results = inspect_triggers(pg_conn)

        trigs = [t for t in results if t.trigger_name == "test_trg"]
        assert len(trigs) == 1
        t = trigs[0]
        assert t.schema == "public"
        assert t.table_name == "test_trigger_tbl"
        assert t.trigger_name == "test_trg"
        assert t.definition != ""
        assert "test_trg" in t.definition

    # 3.6  Internal (constraint) triggers excluded
    def test_internal_triggers_excluded(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE TABLE public.test_parent (id integer PRIMARY KEY)"))
        pg_conn.execute(
            text(
                "CREATE TABLE public.test_child ("
                "id integer PRIMARY KEY, "
                "parent_id integer REFERENCES public.test_parent(id))"
            )
        )

        results = inspect_triggers(pg_conn)

        # FK constraint creates internal triggers — none should appear
        constraint_trigs = [t for t in results if t.table_name in ("test_parent", "test_child")]
        assert len(constraint_trigs) == 0

    # 3.7  Nonexistent schema returns empty
    def test_nonexistent_schema_empty(self, pg_conn: Connection):
        results = inspect_triggers(pg_conn, schemas=["nonexistent"])
        assert len(results) == 0

    # 3.8  Explicit schemas=["public"]
    def test_explicit_public_schema(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE TABLE public.test_tbl_schema (id integer PRIMARY KEY)"))
        pg_conn.execute(
            text(
                "CREATE FUNCTION public.test_trg_schema_fn() "
                "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$"
            )
        )
        pg_conn.execute(
            text(
                "CREATE TRIGGER test_trg_schema BEFORE INSERT ON public.test_tbl_schema "
                "FOR EACH ROW EXECUTE FUNCTION public.test_trg_schema_fn()"
            )
        )

        results = inspect_triggers(pg_conn, schemas=["public"])

        assert all(t.schema == "public" for t in results)
        assert any(t.trigger_name == "test_trg_schema" for t in results)


@pytest.mark.integration
class TestInspectViewsIntegration:
    def test_simple_view(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE VIEW public.test_view_simple AS SELECT 1 AS val"))

        results = inspect_views(pg_conn)

        views = [v for v in results if v.name == "test_view_simple"]
        assert len(views) == 1
        v = views[0]
        assert v.schema == "public"
        assert v.name == "test_view_simple"
        assert "CREATE OR REPLACE VIEW" in v.definition
        assert "test_view_simple" in v.definition
        assert "SELECT" in v.definition

    def test_definition_includes_full_ddl(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE VIEW public.test_view_ddl AS SELECT 42 AS answer"))

        results = inspect_views(pg_conn)

        views = [v for v in results if v.name == "test_view_ddl"]
        assert len(views) == 1
        defn = views[0].definition
        assert defn.startswith("CREATE OR REPLACE VIEW public.test_view_ddl AS")

    def test_no_views_returns_empty(self, pg_conn: Connection):
        results = inspect_views(pg_conn, schemas=["nonexistent"])
        assert len(results) == 0

    def test_schema_filtering(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE VIEW public.test_view_schema AS SELECT 1 AS x"))

        results = inspect_views(pg_conn, schemas=["public"])

        assert all(v.schema == "public" for v in results)
        assert any(v.name == "test_view_schema" for v in results)

    def test_materialized_views_excluded(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE MATERIALIZED VIEW public.test_matview AS SELECT 1 AS val"))

        results = inspect_views(pg_conn)

        # Materialized views should not appear in inspect_views output
        mat_views = [v for v in results if v.name == "test_matview"]
        assert len(mat_views) == 0

    def test_view_referencing_function(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE FUNCTION public.test_view_fn() RETURNS integer LANGUAGE sql AS $$ SELECT 1 $$"))
        pg_conn.execute(text("CREATE VIEW public.test_view_fn_ref AS SELECT public.test_view_fn() AS result"))

        results = inspect_views(pg_conn)

        views = [v for v in results if v.name == "test_view_fn_ref"]
        assert len(views) == 1
        assert "test_view_fn" in views[0].definition
