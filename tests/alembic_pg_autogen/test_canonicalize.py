from __future__ import annotations

from collections.abc import Generator
from typing import cast

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.engine import Engine

from alembic_pg_autogen import (
    CanonicalState,
    canonicalize,
    canonicalize_check_constraints,
    canonicalize_functions,
    canonicalize_views,
    inspect_check_constraints,
)


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
