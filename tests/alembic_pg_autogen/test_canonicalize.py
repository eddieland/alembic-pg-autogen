from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.engine import Engine

from alembic_pg_autogen import IGNORED, CanonicalState, canonicalize, canonicalize_functions, canonicalize_views


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
