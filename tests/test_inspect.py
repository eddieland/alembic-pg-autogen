from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from alembic_pg_autogen import FunctionInfo, TriggerInfo, inspect_functions, inspect_triggers


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


@pytest.mark.integration
class TestInspectFunctionsIntegration:
    # 3.2  Simple SQL function
    def test_simple_function(self, pg_engine: Engine):
        with pg_engine.connect() as conn:
            conn.execute(
                text(
                    "CREATE OR REPLACE FUNCTION public.test_add(a integer, b integer) "
                    "RETURNS integer LANGUAGE sql AS $$ SELECT a + b $$"
                )
            )
            conn.commit()

            results = inspect_functions(conn)

        funcs = [f for f in results if f.name == "test_add"]
        assert len(funcs) == 1
        f = funcs[0]
        assert f.schema == "public"
        assert f.name == "test_add"
        assert f.identity_args != ""
        assert f.definition != ""
        assert "test_add" in f.definition

    # 3.3  Overloaded functions
    def test_overloaded_functions(self, pg_engine: Engine):
        with pg_engine.connect() as conn:
            conn.execute(
                text(
                    "CREATE OR REPLACE FUNCTION public.test_overload(a integer) "
                    "RETURNS integer LANGUAGE sql AS $$ SELECT a $$"
                )
            )
            conn.execute(
                text(
                    "CREATE OR REPLACE FUNCTION public.test_overload(a text, b integer) "
                    "RETURNS integer LANGUAGE sql AS $$ SELECT b $$"
                )
            )
            conn.commit()

            results = inspect_functions(conn)

        overloads = [f for f in results if f.name == "test_overload"]
        assert len(overloads) == 2
        args_set = {f.identity_args for f in overloads}
        assert len(args_set) == 2  # distinct identity_args

    # 3.4  Aggregate function excluded
    def test_aggregate_excluded(self, pg_engine: Engine):
        with pg_engine.connect() as conn:
            conn.execute(
                text(
                    "CREATE OR REPLACE FUNCTION public.test_agg_sfunc(state integer, val integer) "
                    "RETURNS integer LANGUAGE sql AS $$ SELECT state + val $$"
                )
            )
            conn.execute(
                text(
                    "CREATE AGGREGATE public.test_my_sum(integer) "
                    "(SFUNC = test_agg_sfunc, STYPE = integer, INITCOND = '0')"
                )
            )
            conn.commit()

            results = inspect_functions(conn)

        names = [f.name for f in results]
        assert "test_my_sum" not in names
        # The sfunc should still appear (it's a regular function)
        assert "test_agg_sfunc" in names

    # 3.7  Nonexistent schema returns empty
    def test_nonexistent_schema_empty(self, pg_engine: Engine):
        with pg_engine.connect() as conn:
            results = inspect_functions(conn, schemas=["nonexistent"])
        assert len(results) == 0

    # 3.8  Explicit schemas=["public"]
    def test_explicit_public_schema(self, pg_engine: Engine):
        with pg_engine.connect() as conn:
            results = inspect_functions(conn, schemas=["public"])
        assert all(f.schema == "public" for f in results)
        assert len(results) > 0  # previous tests created public functions


@pytest.mark.integration
class TestInspectTriggersIntegration:
    # 3.5  Simple trigger
    def test_simple_trigger(self, pg_engine: Engine):
        with pg_engine.connect() as conn:
            conn.execute(text("CREATE TABLE IF NOT EXISTS public.test_trigger_tbl (id integer PRIMARY KEY, val text)"))
            conn.execute(
                text(
                    "CREATE OR REPLACE FUNCTION public.test_trigger_fn() "
                    "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$"
                )
            )
            conn.execute(text("DROP TRIGGER IF EXISTS test_trg ON public.test_trigger_tbl"))
            conn.execute(
                text(
                    "CREATE TRIGGER test_trg BEFORE INSERT ON public.test_trigger_tbl "
                    "FOR EACH ROW EXECUTE FUNCTION public.test_trigger_fn()"
                )
            )
            conn.commit()

            results = inspect_triggers(conn)

        trigs = [t for t in results if t.trigger_name == "test_trg"]
        assert len(trigs) == 1
        t = trigs[0]
        assert t.schema == "public"
        assert t.table_name == "test_trigger_tbl"
        assert t.trigger_name == "test_trg"
        assert t.definition != ""
        assert "test_trg" in t.definition

    # 3.6  Internal (constraint) triggers excluded
    def test_internal_triggers_excluded(self, pg_engine: Engine):
        with pg_engine.connect() as conn:
            conn.execute(text("CREATE TABLE IF NOT EXISTS public.test_parent (id integer PRIMARY KEY)"))
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS public.test_child ("
                    "id integer PRIMARY KEY, "
                    "parent_id integer REFERENCES public.test_parent(id))"
                )
            )
            conn.commit()

            results = inspect_triggers(conn)

        # FK constraint creates an internal trigger — it should not appear
        internal = [
            t for t in results if t.table_name in ("test_parent", "test_child") and t.trigger_name != "test_trg"
        ]
        assert len(internal) == 0

    # 3.7  Nonexistent schema returns empty
    def test_nonexistent_schema_empty(self, pg_engine: Engine):
        with pg_engine.connect() as conn:
            results = inspect_triggers(conn, schemas=["nonexistent"])
        assert len(results) == 0

    # 3.8  Explicit schemas=["public"]
    def test_explicit_public_schema(self, pg_engine: Engine):
        with pg_engine.connect() as conn:
            results = inspect_triggers(conn, schemas=["public"])
        assert all(t.schema == "public" for t in results)
        assert len(results) > 0  # previous test created a trigger
