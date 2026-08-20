from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.engine import Engine

from alembic_pg_autogen import (
    CheckConstraintInfo,
    FunctionInfo,
    TriggerInfo,
    ViewInfo,
    current_schema,
    inspect_check_constraints,
    inspect_functions,
    inspect_triggers,
    inspect_views,
)


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


class TestCheckConstraintInfoUnit:
    def test_construction_and_fields(self):
        info = CheckConstraintInfo(
            schema="public", table_name="orders", name="ck_orders_amount", expression="(amount >= (0)::numeric)"
        )
        assert info.schema == "public"
        assert info.table_name == "orders"
        assert info.name == "ck_orders_amount"
        assert info.expression == "(amount >= (0)::numeric)"

    def test_is_tuple(self):
        info = CheckConstraintInfo("s", "t", "n", "e")
        assert isinstance(info, tuple)
        assert info[0] == "s"

    def test_identity_is_schema_table_and_name(self):
        info = CheckConstraintInfo("myschema", "orders", "ck_orders_amount", "expression text")
        assert info[:-1] == ("myschema", "orders", "ck_orders_amount")


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


@pytest.mark.integration
class TestInspectCheckConstraintsIntegration:
    def test_named_constraint(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE TABLE public.test_ck_orders (id integer, amount numeric)"))
        pg_conn.execute(text("ALTER TABLE public.test_ck_orders ADD CONSTRAINT ck_test_amount CHECK (amount >= 0)"))

        results = inspect_check_constraints(pg_conn)

        found = [c for c in results if c.name == "ck_test_amount"]
        assert len(found) == 1
        assert found[0].schema == "public"
        assert found[0].table_name == "test_ck_orders"
        assert "amount" in found[0].expression

    def test_expression_is_the_catalogs_normalized_form(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE TABLE public.test_ck_norm (status varchar(16))"))
        pg_conn.execute(
            text("ALTER TABLE public.test_ck_norm ADD CONSTRAINT ck_test_status CHECK (status IN ('new', 'done'))")
        )

        results = inspect_check_constraints(pg_conn, table_names=["test_ck_norm"])

        assert len(results) == 1
        # PostgreSQL rewrites IN (...) as = ANY (ARRAY[...]) — that rewrite is exactly what makes text comparison
        # of check constraints unreliable, and why the expression is read back from the catalog.
        assert "ANY" in results[0].expression

    def test_table_filtering(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE TABLE public.test_ck_a (x integer CONSTRAINT ck_test_a CHECK (x > 0))"))
        pg_conn.execute(text("CREATE TABLE public.test_ck_b (y integer CONSTRAINT ck_test_b CHECK (y > 0))"))

        results = inspect_check_constraints(pg_conn, table_names=["test_ck_a"])

        assert [c.name for c in results] == ["ck_test_a"]

    def test_schema_filtering(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE SCHEMA test_ck_schema"))
        pg_conn.execute(text("CREATE TABLE test_ck_schema.t (x integer CONSTRAINT ck_test_scoped CHECK (x > 0))"))

        results = inspect_check_constraints(pg_conn, schemas=["test_ck_schema"])

        assert [c.name for c in results] == ["ck_test_scoped"]
        assert results[0].schema == "test_ck_schema"

    def test_not_null_and_primary_key_excluded(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE TABLE public.test_ck_nn (id integer PRIMARY KEY, name text NOT NULL)"))

        results = inspect_check_constraints(pg_conn, table_names=["test_ck_nn"])

        assert results == []

    def test_domain_constraints_excluded(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE DOMAIN public.test_ck_domain AS integer CHECK (VALUE > 0)"))

        results = inspect_check_constraints(pg_conn, schemas=["public"])

        assert all(c.table_name != "test_ck_domain" for c in results)

    def test_no_constraints_returns_empty(self, pg_conn: Connection):
        assert inspect_check_constraints(pg_conn, schemas=["nonexistent"]) == []


@pytest.mark.integration
class TestCurrentSchemaIntegration:
    def test_returns_search_path_head(self, pg_conn: Connection):
        assert current_schema(pg_conn) == "public"

    def test_follows_search_path(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE SCHEMA test_current_schema"))
        pg_conn.execute(text("SET search_path TO test_current_schema"))

        assert current_schema(pg_conn) == "test_current_schema"
