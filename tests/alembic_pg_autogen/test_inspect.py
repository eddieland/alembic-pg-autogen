# pyright: reportPrivateUsage=false
from __future__ import annotations

import logging
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
    inspect_indexes,
    inspect_triggers,
    inspect_views,
)
from alembic_pg_autogen.inspect import _build_schema_filter


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


class TestBuildSchemaFilterUnit:
    """``_build_schema_filter`` decides which schemas a catalog query covers.

    Every inspect helper interpolates the returned fragment into its SQL, so the fragment must never carry a schema
    name — the names always travel as bind parameters.
    """

    def test_none_excludes_system_schemas(self):
        fragment, params = _build_schema_filter(None)

        assert fragment == "n.nspname != ALL(:excluded_schemas)"
        assert params == {"excluded_schemas": ["pg_catalog", "information_schema"]}

    def test_explicit_schemas_become_an_allow_list(self):
        fragment, params = _build_schema_filter(["public", "audit"])

        assert fragment == "n.nspname = ANY(:schemas)"
        assert params == {"schemas": ["public", "audit"]}

    def test_empty_sequence_matches_nothing(self):
        """An empty list is "no schemas", not "every schema" — that is what *None* means."""
        fragment, params = _build_schema_filter([])

        assert fragment == "n.nspname = ANY(:schemas)"
        assert params == {"schemas": []}

    def test_schema_names_are_never_interpolated_into_sql(self):
        hostile = "public'; DROP TABLE users; --"

        fragment, params = _build_schema_filter([hostile])

        assert hostile not in fragment
        assert params["schemas"] == [hostile]

    def test_params_are_a_fresh_mutable_copy(self):
        """``inspect_check_constraints`` adds ``table_names`` to the returned mapping, so it must not be shared."""
        _, first = _build_schema_filter(None)
        first["table_names"] = ["orders"]
        _, second = _build_schema_filter(None)

        assert "table_names" not in second

    def test_sequence_is_copied_not_aliased(self):
        schemas = ["public"]

        _, params = _build_schema_filter(schemas)
        schemas.append("audit")

        assert params["schemas"] == ["public"]


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


@pytest.mark.integration
class TestInspectIndexesIntegration:
    def _table(self, conn: Connection, name: str) -> None:
        conn.execute(
            text(f"CREATE TABLE public.{name} (id integer PRIMARY KEY, a text, b text, status text, data jsonb)")
        )

    def test_shape_excludes_identity_and_covers_every_feature(self, pg_conn: Connection):
        self._table(pg_conn, "test_ix_feat")
        pg_conn.execute(
            text(
                "CREATE INDEX test_ix_all ON public.test_ix_feat (a, lower(b)) INCLUDE (status) WHERE data IS NOT NULL"
            )
        )

        results = inspect_indexes(pg_conn, schemas=["public"], table_names=["test_ix_feat"])

        assert len(results) == 1
        info = results[0]
        assert (info.schema, info.table_name, info.name) == ("public", "test_ix_feat", "test_ix_all")
        assert info.unique is False
        assert info.shape == "USING btree (a, lower(b)) INCLUDE (status) WHERE (data IS NOT NULL)"

    def test_operator_class_and_access_method_are_part_of_the_shape(self, pg_conn: Connection):
        self._table(pg_conn, "test_ix_ops")
        pg_conn.execute(text("CREATE INDEX test_ix_gin ON public.test_ix_ops USING gin (data jsonb_path_ops)"))

        (info,) = inspect_indexes(pg_conn, schemas=["public"], table_names=["test_ix_ops"])

        assert info.shape == "USING gin (data jsonb_path_ops)"

    def test_unique_is_carried_separately_from_the_shape(self, pg_conn: Connection):
        self._table(pg_conn, "test_ix_uniq")
        pg_conn.execute(text("CREATE UNIQUE INDEX test_ix_u ON public.test_ix_uniq (a)"))

        (info,) = inspect_indexes(pg_conn, schemas=["public"], table_names=["test_ix_uniq"])

        # ``UNIQUE`` sits in the statement head, ahead of the name, so it cannot ride along in the shape.
        assert info.unique is True
        assert info.shape == "USING btree (a)"
        assert "UNIQUE" not in info.shape

    def test_nulls_not_distinct_is_part_of_the_shape(self, pg_conn: Connection):
        if pg_conn.dialect.server_version_info is None or pg_conn.dialect.server_version_info < (15,):
            pytest.skip("NULLS NOT DISTINCT requires PostgreSQL 15 or newer")
        self._table(pg_conn, "test_ix_nnd")
        pg_conn.execute(text("CREATE UNIQUE INDEX test_ix_n ON public.test_ix_nnd (a) NULLS NOT DISTINCT"))

        (info,) = inspect_indexes(pg_conn, schemas=["public"], table_names=["test_ix_nnd"])

        assert info.unique is True
        assert info.shape == "USING btree (a) NULLS NOT DISTINCT"

    def test_constraint_backed_indexes_excluded(self, pg_conn: Connection):
        pg_conn.execute(
            text(
                "CREATE TABLE public.test_ix_con (id integer PRIMARY KEY, a text UNIQUE, b int4range, EXCLUDE USING gist (b WITH &&))"
            )
        )

        results = inspect_indexes(pg_conn, schemas=["public"], table_names=["test_ix_con"])

        assert results == []

    def test_identifiers_containing_using_are_stripped_correctly(self, pg_conn: Connection):
        """A naive split on the first `` USING `` would cut inside the quoted identifiers."""
        pg_conn.execute(text('CREATE TABLE public."tbl USING x" (a text)'))
        pg_conn.execute(text('CREATE INDEX "ix USING y" ON public."tbl USING x" (a)'))

        (info,) = inspect_indexes(pg_conn, schemas=["public"], table_names=["tbl USING x"])

        assert info.name == "ix USING y"
        assert info.shape == "USING btree (a)"

    def test_schema_filtering(self, pg_conn: Connection):
        pg_conn.execute(text("CREATE SCHEMA test_ix_schema"))
        pg_conn.execute(text("CREATE TABLE test_ix_schema.t (a text)"))
        pg_conn.execute(text("CREATE INDEX test_ix_scoped ON test_ix_schema.t (a)"))

        results = inspect_indexes(pg_conn, schemas=["test_ix_schema"])

        assert [i.name for i in results] == ["test_ix_scoped"]

    def test_table_filtering(self, pg_conn: Connection):
        self._table(pg_conn, "test_ix_one")
        self._table(pg_conn, "test_ix_two")
        pg_conn.execute(text("CREATE INDEX test_ix_only_one ON public.test_ix_one (a)"))
        pg_conn.execute(text("CREATE INDEX test_ix_only_two ON public.test_ix_two (a)"))

        results = inspect_indexes(pg_conn, schemas=["public"], table_names=["test_ix_one"])

        assert [i.name for i in results] == ["test_ix_only_one"]

    def test_no_indexes_returns_empty(self, pg_conn: Connection):
        assert inspect_indexes(pg_conn, schemas=["nonexistent"]) == []

    def test_unstrippable_definition_is_skipped(
        self, pg_conn: Connection, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        """An index whose definition does not start with the expected prefix is dropped, not returned unstripped."""
        from alembic_pg_autogen import inspect as inspect_module

        self._table(pg_conn, "test_ix_unstrippable")
        pg_conn.execute(text("CREATE INDEX test_ix_odd ON public.test_ix_unstrippable (a)"))
        broken = inspect_module._INDEXES_QUERY.replace("'CREATE '", "'NOT HOW POSTGRESQL SPELLS IT '")
        monkeypatch.setattr(inspect_module, "_INDEXES_QUERY", broken)

        with caplog.at_level(logging.WARNING, logger="alembic_pg_autogen.inspect"):
            results = inspect_indexes(pg_conn, schemas=["public"], table_names=["test_ix_unstrippable"])

        assert results == []
        assert "Could not normalize the definition" in caplog.text
