"""Tests for postgast-based DDL parsing: identity extraction and ensure_or_replace."""

from __future__ import annotations

import postgast
import pytest


class TestExtractFunctionIdentity:
    def test_schema_qualified(self):
        tree = postgast.parse("CREATE FUNCTION public.add(a int, b int) RETURNS int LANGUAGE sql AS $$ SELECT a+b $$")
        identity = postgast.extract_function_identity(tree)
        assert identity is not None
        assert identity.schema == "public"
        assert identity.name == "add"

    def test_unqualified(self):
        tree = postgast.parse("CREATE FUNCTION my_func() RETURNS void LANGUAGE sql AS $$ SELECT 1 $$")
        identity = postgast.extract_function_identity(tree)
        assert identity is not None
        assert identity.schema is None
        assert identity.name == "my_func"

    def test_create_or_replace(self):
        tree = postgast.parse(
            "CREATE OR REPLACE FUNCTION audit.log_event() RETURNS void LANGUAGE sql AS $$ SELECT 1 $$"
        )
        identity = postgast.extract_function_identity(tree)
        assert identity is not None
        assert identity.schema == "audit"
        assert identity.name == "log_event"

    def test_quoted_identifiers(self):
        tree = postgast.parse('CREATE FUNCTION "My Schema"."My Func"() RETURNS void LANGUAGE sql AS $$ SELECT 1 $$')
        identity = postgast.extract_function_identity(tree)
        assert identity is not None
        assert identity.schema == "My Schema"
        assert identity.name == "My Func"

    def test_invalid_ddl_returns_none(self):
        tree = postgast.parse("SELECT 1")
        identity = postgast.extract_function_identity(tree)
        assert identity is None

    def test_invalid_sql_raises(self):
        with pytest.raises(postgast.PgQueryError):
            postgast.parse("NOT VALID SQL AT ALL !!!")


class TestExtractTriggerIdentity:
    def test_schema_qualified(self):
        tree = postgast.parse(
            "CREATE TRIGGER audit_trg AFTER INSERT ON public.orders FOR EACH ROW EXECUTE FUNCTION audit_fn()"
        )
        identity = postgast.extract_trigger_identity(tree)
        assert identity is not None
        assert identity.schema == "public"
        assert identity.table == "orders"
        assert identity.trigger == "audit_trg"

    def test_unqualified(self):
        tree = postgast.parse("CREATE TRIGGER trg BEFORE UPDATE ON t FOR EACH ROW EXECUTE FUNCTION fn()")
        identity = postgast.extract_trigger_identity(tree)
        assert identity is not None
        assert identity.schema is None
        assert identity.table == "t"
        assert identity.trigger == "trg"

    def test_multiline_ddl(self):
        ddl = "CREATE TRIGGER trg\nAFTER INSERT\nON public.orders\nFOR EACH ROW\nEXECUTE FUNCTION notify()"
        tree = postgast.parse(ddl)
        identity = postgast.extract_trigger_identity(tree)
        assert identity is not None
        assert identity.schema == "public"
        assert identity.table == "orders"
        assert identity.trigger == "trg"


class TestEnsureOrReplace:
    def test_create_function_rewritten(self):
        result = postgast.ensure_or_replace("CREATE FUNCTION public.f() RETURNS void LANGUAGE sql AS $$ SELECT 1 $$")
        assert "CREATE OR REPLACE FUNCTION" in result

    def test_create_trigger_rewritten(self):
        result = postgast.ensure_or_replace("CREATE TRIGGER t AFTER INSERT ON tbl FOR EACH ROW EXECUTE FUNCTION fn()")
        assert "CREATE OR REPLACE TRIGGER" in result

    def test_already_or_replace_unchanged(self):
        original = "CREATE OR REPLACE FUNCTION public.f() RETURNS void LANGUAGE sql AS $$ SELECT 1 $$"
        result = postgast.ensure_or_replace(original)
        assert "CREATE OR REPLACE FUNCTION" in result

    def test_dollar_quoted_body_with_create_keyword(self):
        ddl = (
            "CREATE FUNCTION public.tricky() RETURNS text LANGUAGE sql AS $body$\n"
            "  SELECT 'CREATE FUNCTION inside_body'\n"
            "$body$"
        )
        result = postgast.ensure_or_replace(ddl)
        assert result.startswith("CREATE OR REPLACE")


class TestToDrop:
    def test_function(self):
        result = postgast.to_drop(
            "CREATE FUNCTION public.add(a integer, b integer) RETURNS integer LANGUAGE sql AS $$ SELECT a + b $$"
        )
        assert result == "DROP FUNCTION public.add(int, int)"

    def test_trigger(self):
        result = postgast.to_drop(
            "CREATE TRIGGER audit_trg AFTER INSERT ON public.orders FOR EACH ROW EXECUTE FUNCTION audit_fn()"
        )
        assert result == "DROP TRIGGER audit_trg ON public.orders"

    def test_quoted_identifiers(self):
        result = postgast.to_drop('CREATE FUNCTION "My Schema"."My Func"() RETURNS void LANGUAGE sql AS $$ SELECT 1 $$')
        assert result == 'DROP FUNCTION "My Schema"."My Func"()'
