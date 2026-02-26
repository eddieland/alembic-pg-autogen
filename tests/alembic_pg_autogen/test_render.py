from __future__ import annotations

# pyright: reportPrivateUsage=false
from unittest.mock import MagicMock

from alembic_pg_autogen.inspect import FunctionInfo, TriggerInfo
from alembic_pg_autogen.ops import (
    CreateFunctionOp,
    CreateTriggerOp,
    DropFunctionOp,
    DropTriggerOp,
    ReplaceFunctionOp,
    ReplaceTriggerOp,
)
from alembic_pg_autogen.render import (
    _render_create_function,
    _render_create_trigger,
    _render_drop_function,
    _render_drop_trigger,
    _render_replace_function,
    _render_replace_trigger,
)


def _ctx() -> MagicMock:
    """Return a mock AutogenContext with an imports set."""
    ctx = MagicMock()
    ctx.imports = set()
    return ctx


class TestRenderCreateFunction:
    def test_simple_ddl(self):
        op = CreateFunctionOp(FunctionInfo("public", "my_fn", "", "CREATE FUNCTION public.my_fn() RETURNS void"))
        result = _render_create_function(_ctx(), op)
        assert result.startswith("op.execute(")
        assert "CREATE FUNCTION public.my_fn() RETURNS void" in result

    def test_ddl_with_single_quotes(self):
        ddl = "CREATE FUNCTION public.fn() RETURNS text AS $$ SELECT 'hello' $$ LANGUAGE sql"
        op = CreateFunctionOp(FunctionInfo("public", "fn", "", ddl))
        result = _render_create_function(_ctx(), op)
        assert "op.execute(" in result
        assert "'hello'" in result
        # Verify it's valid Python
        compiled = compile(result, "<test>", "eval")
        assert compiled is not None

    def test_ddl_with_backslashes(self):
        ddl = r"CREATE FUNCTION public.fn() RETURNS text AS $$ SELECT E'line1\nline2' $$ LANGUAGE sql"
        op = CreateFunctionOp(FunctionInfo("public", "fn", "", ddl))
        result = _render_create_function(_ctx(), op)
        compiled = compile(result, "<test>", "eval")
        assert compiled is not None


class TestRenderReplaceFunction:
    def test_uses_desired_definition(self):
        current = FunctionInfo("public", "fn", "", "old def")
        desired = FunctionInfo("public", "fn", "", "CREATE FUNCTION public.fn() RETURNS void AS $$ new $$ LANGUAGE sql")
        op = ReplaceFunctionOp(current, desired)
        result = _render_replace_function(_ctx(), op)
        assert "new" in result
        assert "old def" not in result


class TestRenderDropFunction:
    def test_with_args(self):
        ddl = "CREATE FUNCTION public.old_fn(a integer, b text) RETURNS void LANGUAGE sql AS $$ SELECT 1 $$"
        op = DropFunctionOp(FunctionInfo("public", "old_fn", "integer, text", ddl))
        result = _render_drop_function(_ctx(), op)
        assert result == "op.execute('DROP FUNCTION public.old_fn(int, text)')"

    def test_no_args(self):
        ddl = "CREATE FUNCTION audit.cleanup() RETURNS void LANGUAGE sql AS $$ SELECT 1 $$"
        op = DropFunctionOp(FunctionInfo("audit", "cleanup", "", ddl))
        result = _render_drop_function(_ctx(), op)
        assert result == "op.execute('DROP FUNCTION audit.cleanup()')"

    def test_quoted_identifiers(self):
        ddl = 'CREATE FUNCTION "My Schema"."My Func"() RETURNS void LANGUAGE sql AS $$ SELECT 1 $$'
        op = DropFunctionOp(FunctionInfo("My Schema", "My Func", "", ddl))
        result = _render_drop_function(_ctx(), op)
        assert result == """op.execute('DROP FUNCTION "My Schema"."My Func"()')"""


class TestRenderCreateTrigger:
    def test_simple_ddl(self):
        ddl = "CREATE TRIGGER audit_trg AFTER INSERT ON public.orders FOR EACH ROW EXECUTE FUNCTION audit.log()"
        op = CreateTriggerOp(TriggerInfo("public", "orders", "audit_trg", ddl))
        result = _render_create_trigger(_ctx(), op)
        assert result.startswith("op.execute(")
        assert "CREATE TRIGGER audit_trg" in result


class TestRenderReplaceTrigger:
    def test_emits_two_statements(self):
        current_ddl = "CREATE TRIGGER audit_trg AFTER INSERT ON public.orders FOR EACH ROW EXECUTE FUNCTION audit.log()"
        current = TriggerInfo("public", "orders", "audit_trg", current_ddl)
        desired_ddl = (
            "CREATE TRIGGER audit_trg AFTER INSERT OR UPDATE ON public.orders FOR EACH ROW EXECUTE FUNCTION audit.log()"
        )
        desired = TriggerInfo("public", "orders", "audit_trg", desired_ddl)
        op = ReplaceTriggerOp(current, desired)
        result = _render_replace_trigger(_ctx(), op)
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0] == "op.execute('DROP TRIGGER audit_trg ON public.orders')"
        assert "CREATE TRIGGER audit_trg" in result[1]


class TestRenderDropTrigger:
    def test_drop_trigger(self):
        ddl = "CREATE TRIGGER notify_trg AFTER INSERT ON public.events FOR EACH ROW EXECUTE FUNCTION fn()"
        op = DropTriggerOp(TriggerInfo("public", "events", "notify_trg", ddl))
        result = _render_drop_trigger(_ctx(), op)
        assert result == "op.execute('DROP TRIGGER notify_trg ON public.events')"


class TestNoImportsInjected:
    def test_create_function_no_imports(self):
        ctx = _ctx()
        op = CreateFunctionOp(FunctionInfo("public", "fn", "", "CREATE FUNCTION …"))
        _render_create_function(ctx, op)
        assert len(ctx.imports) == 0

    def test_drop_trigger_no_imports(self):
        ctx = _ctx()
        ddl = "CREATE TRIGGER trg AFTER INSERT ON public.t FOR EACH ROW EXECUTE FUNCTION fn()"
        op = DropTriggerOp(TriggerInfo("public", "t", "trg", ddl))
        _render_drop_trigger(ctx, op)
        assert len(ctx.imports) == 0
