from __future__ import annotations

# pyright: reportPrivateUsage=false
import re
from unittest.mock import MagicMock

import pytest
from alembic.autogenerate.api import AutogenContext
from alembic.autogenerate.render import _render_cmd_body
from alembic.operations.ops import ModifyTableOps, UpgradeOps
from alembic.runtime.migration import MigrationContext
from sqlalchemy import CheckConstraint, Column, MetaData, String, Table
from sqlalchemy.dialects.postgresql.base import PGDialect

from alembic_pg_autogen.inspect import FunctionInfo, TriggerInfo, ViewInfo
from alembic_pg_autogen.ops import (
    CreateCheckConstraintNotValidOp,
    CreateFunctionOp,
    CreateTriggerOp,
    CreateViewOp,
    DropFunctionOp,
    DropTriggerOp,
    DropViewOp,
    NoOp,
    ReplaceFunctionOp,
    ReplaceTriggerOp,
    ReplaceViewOp,
    ValidateConstraintOp,
)
from alembic_pg_autogen.render import (
    _quote_ddl,
    _render_create_check_constraint_not_valid,
    _render_create_function,
    _render_create_trigger,
    _render_create_view,
    _render_drop_function,
    _render_drop_trigger,
    _render_drop_view,
    _render_execute,
    _render_noop,
    _render_replace_function,
    _render_replace_trigger,
    _render_replace_view,
    _render_validate_constraint,
)


def _ctx() -> MagicMock:
    """Return a mock AutogenContext with an imports set and a real PostgreSQL dialect."""
    ctx = MagicMock()
    ctx.imports = set()
    ctx.dialect = PGDialect()
    return ctx


def _autogen_context(**opts: object) -> AutogenContext:
    """Return a real AutogenContext, which Alembic's own check constraint renderer needs for its prefixes."""
    migration_context = MigrationContext.configure(dialect=PGDialect(), opts={"as_sql": True})
    ctx = AutogenContext(migration_context, autogenerate=False)
    ctx.opts.update({"alembic_module_prefix": "op.", "sqlalchemy_module_prefix": "sa.", "user_module_prefix": None})
    ctx.opts.update(opts)
    return ctx


def _render_body(ops: list[object], *, render_as_batch: bool = False) -> str:
    """Render a table's operations the way Alembic renders an upgrade or downgrade body."""
    table_ops = ModifyTableOps("orders", ops)  # pyright: ignore[reportArgumentType]
    return _render_cmd_body(UpgradeOps(ops=[table_ops]), _autogen_context(render_as_batch=render_as_batch))


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


class TestRenderCreateView:
    def test_simple_ddl(self):
        ddl = "CREATE OR REPLACE VIEW public.active_users AS\n SELECT id FROM users WHERE active"
        op = CreateViewOp(ViewInfo("public", "active_users", ddl))
        result = _render_create_view(_ctx(), op)
        assert result.startswith("op.execute(")
        assert "CREATE OR REPLACE VIEW public.active_users AS" in result

    def test_ddl_with_single_quotes(self):
        ddl = "CREATE OR REPLACE VIEW public.v AS\n SELECT id FROM t WHERE status = 'active'"
        op = CreateViewOp(ViewInfo("public", "v", ddl))
        result = _render_create_view(_ctx(), op)
        assert "op.execute(" in result
        assert "'active'" in result
        compiled = compile(result, "<test>", "eval")
        assert compiled is not None


class TestRenderReplaceView:
    def test_uses_desired_definition(self):
        current = ViewInfo("public", "v", "old definition")
        desired = ViewInfo("public", "v", "CREATE OR REPLACE VIEW public.v AS\n SELECT 2 AS new_val")
        op = ReplaceViewOp(current, desired)
        result = _render_replace_view(_ctx(), op)
        assert "new_val" in result
        assert "old definition" not in result

    def test_identical_to_create(self):
        ddl = "CREATE OR REPLACE VIEW public.v AS\n SELECT 1"
        op_create = CreateViewOp(ViewInfo("public", "v", ddl))
        op_replace = ReplaceViewOp(ViewInfo("public", "v", "old"), ViewInfo("public", "v", ddl))
        assert _render_create_view(_ctx(), op_create) == _render_replace_view(_ctx(), op_replace)


class TestRenderDropView:
    def test_drop_view(self):
        op = DropViewOp(ViewInfo("public", "old_view", "CREATE OR REPLACE VIEW …"))
        result = _render_drop_view(_ctx(), op)
        assert result == "op.execute('DROP VIEW public.old_view')"

    def test_drop_view_non_default_schema(self):
        op = DropViewOp(ViewInfo("reporting", "monthly_summary", "CREATE OR REPLACE VIEW …"))
        result = _render_drop_view(_ctx(), op)
        assert result == "op.execute('DROP VIEW reporting.monthly_summary')"

    @pytest.mark.parametrize(
        ("schema", "name", "expected_sql"),
        [
            ("public", "orderView", 'DROP VIEW public."orderView"'),
            ("public", "Order Summary", 'DROP VIEW public."Order Summary"'),
            ("public", "select", 'DROP VIEW public."select"'),
            ("Reporting", "monthly_summary", 'DROP VIEW "Reporting".monthly_summary'),
            ("My Schema", "My View", 'DROP VIEW "My Schema"."My View"'),
            ("public", 'weird"name', 'DROP VIEW public."weird""name"'),
        ],
    )
    def test_drop_view_quotes_identifiers(self, schema: str, name: str, expected_sql: str):
        """Identifiers that are not lowercase-safe must be double-quoted, or the migration fails at runtime."""
        op = DropViewOp(ViewInfo(schema, name, "CREATE OR REPLACE VIEW …"))

        result = _render_drop_view(_ctx(), op)

        recorded = _execute_rendered(result)
        assert recorded == [expected_sql]


class TestQuoteDDL:
    """``_quote_ddl`` must produce a Python literal that survives Alembic's re-indentation."""

    @pytest.mark.parametrize(
        "ddl",
        [
            "CREATE OR REPLACE VIEW public.v AS\n SELECT 1",
            "CREATE FUNCTION f() RETURNS text LANGUAGE sql AS $$\n  SELECT 'hi'\n$$",
            "line1\nline2\\nstill",
            "has '''triple''' quotes\nand a newline",
        ],
    )
    def test_multiline_ddl_renders_on_a_single_line(self, ddl: str):
        """Alembic indents every line of a rendered op, so a multi-line literal would absorb the indentation."""
        literal = _quote_ddl(ddl)

        assert "\n" not in literal
        assert eval(literal) == ddl  # noqa: S307

    @pytest.mark.parametrize(
        "ddl",
        [
            "CREATE VIEW public.v AS SELECT 1",
            "SELECT 'quoted'",
            r"SELECT E'back\slash'",
            "mixes ' and \\ together",
            "contains '''triple''' quotes",
            'contains """double triple""" quotes',
            "contains both ''' and \"\"\" quotes",
        ],
    )
    def test_single_line_ddl_round_trips(self, ddl: str):
        literal = _quote_ddl(ddl)

        assert eval(literal) == ddl  # noqa: S307

    def test_multiline_ddl_survives_alembic_indentation(self):
        """Reproduces what ``alembic.autogenerate.render._indent`` does to the rendered op text."""
        ddl = "CREATE OR REPLACE VIEW public.v AS\n SELECT id\n   FROM users"

        indented = re.sub(r"^", "    ", _render_execute(ddl), flags=re.M)

        namespace: dict[str, object] = {"op": _RecordingOp()}
        exec(indented.strip(), namespace)  # noqa: S102
        recorded = namespace["op"]
        assert isinstance(recorded, _RecordingOp)
        assert recorded.executed == [ddl]

    def test_plain_single_line_ddl_uses_repr(self):
        assert _quote_ddl("CREATE VIEW public.v AS SELECT 1") == "'CREATE VIEW public.v AS SELECT 1'"

    def test_single_line_with_quotes_uses_triple_quoting(self):
        assert _quote_ddl("SELECT 'hi' AS greeting") == """'''SELECT 'hi' AS greeting'''"""

    def test_trailing_quote_falls_back_to_repr(self):
        """``'''SELECT 'hi''''`` is not a valid literal: the trailing quote runs into the closing delimiter."""
        ddl = "SELECT 'hi'"

        literal = _quote_ddl(ddl)

        assert not literal.startswith("'''")
        assert eval(literal) == ddl  # noqa: S307

    def test_trailing_backslash_falls_back_to_repr(self):
        r"""A raw literal cannot end in a backslash, so ``r'''…\'''`` would not terminate."""
        ddl = "SELECT 'a' " + "\\"

        literal = _quote_ddl(ddl)

        assert not literal.startswith("r'''")
        assert eval(literal) == ddl  # noqa: S307

    def test_single_line_with_backslash_uses_a_raw_literal(self):
        ddl = r"SELECT E'a\nb' AS lines"

        literal = _quote_ddl(ddl)

        assert literal.startswith("r'''")
        assert eval(literal) == ddl  # noqa: S307

    def test_triple_quoted_content_falls_back_to_repr(self):
        """Triple-quoting cannot nest, so DDL already containing ``'''`` has to be escaped by ``repr()``."""
        ddl = "SELECT '''x'''"

        literal = _quote_ddl(ddl)

        assert not literal.startswith("'''")
        assert eval(literal) == ddl  # noqa: S307


class _RecordingOp:
    """Stands in for Alembic's ``op`` module while exec'ing rendered migration source."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, ddl: str) -> None:
        self.executed.append(ddl)


def _execute_rendered(rendered: str) -> list[str]:
    """Execute rendered migration source against a recording ``op`` and return the DDL it ran."""
    namespace: dict[str, object] = {"op": _RecordingOp()}
    exec(rendered, namespace)  # noqa: S102
    recorded = namespace["op"]
    assert isinstance(recorded, _RecordingOp)
    return recorded.executed


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

    def test_create_view_no_imports(self):
        ctx = _ctx()
        op = CreateViewOp(ViewInfo("public", "v", "CREATE OR REPLACE VIEW …"))
        _render_create_view(ctx, op)
        assert len(ctx.imports) == 0

    def test_drop_view_no_imports(self):
        ctx = _ctx()
        op = DropViewOp(ViewInfo("public", "v", "CREATE OR REPLACE VIEW …"))
        _render_drop_view(ctx, op)
        assert len(ctx.imports) == 0


class TestRenderValidateConstraint:
    def test_with_schema(self):
        op = ValidateConstraintOp("ck_orders_status", "orders", schema="sales")
        assert (
            _render_validate_constraint(_ctx(), op)
            == "op.execute('ALTER TABLE sales.orders VALIDATE CONSTRAINT ck_orders_status')"
        )

    def test_without_schema(self):
        op = ValidateConstraintOp("ck_orders_status", "orders")
        assert (
            _render_validate_constraint(_ctx(), op)
            == "op.execute('ALTER TABLE orders VALIDATE CONSTRAINT ck_orders_status')"
        )

    def test_quotes_identifiers_that_need_it(self):
        op = ValidateConstraintOp("CK Status", "Order Items", schema="Sales")
        result = _render_validate_constraint(_ctx(), op)
        assert _execute_rendered(result) == ['ALTER TABLE "Sales"."Order Items" VALIDATE CONSTRAINT "CK Status"']

    def test_no_imports(self):
        ctx = _ctx()
        _render_validate_constraint(ctx, ValidateConstraintOp("ck", "orders"))
        assert len(ctx.imports) == 0


class TestRenderCreateCheckConstraintNotValid:
    def test_widening_renders_one_line_with_the_keyword(self):
        op = CreateCheckConstraintNotValidOp("ck_orders_status", "orders", "status IN ('a', 'b')")

        lines = _render_create_check_constraint_not_valid(_autogen_context(), op)

        assert lines == [
            """op.create_check_constraint('ck_orders_status', 'orders', "status IN ('a', 'b')", postgresql_not_valid=True)"""
        ]

    def test_schema_precedes_the_keyword(self):
        op = CreateCheckConstraintNotValidOp("ck", "orders", "status IN ('a')", schema="sales")

        (line,) = _render_create_check_constraint_not_valid(_autogen_context(), op)

        assert line.endswith("schema='sales', postgresql_not_valid=True)")

    def test_narrowing_renders_a_backfill_comment(self):
        op = CreateCheckConstraintNotValidOp(
            "ck_orders_status", "orders", "status IN ('a')", column="status", removed_values=("b", "c")
        )

        lines = _render_create_check_constraint_not_valid(_autogen_context(), op)

        assert lines[-1].startswith("op.create_check_constraint(")
        comments = lines[:-1]
        assert comments and all(line.startswith("# ") for line in comments)
        joined = " ".join(comments)
        assert "status" in joined
        assert "'b', 'c'" in joined
        assert "ck_orders_status" in joined
        assert "Backfill" in joined

    def test_naming_convention_name_renders_through_op_f(self):
        metadata = MetaData(naming_convention={"ck": "ck_%(table_name)s_%(constraint_name)s"})
        table = Table("orders", metadata, Column("status", String(16)))
        constraint = CheckConstraint("status IN ('a')", name="status", table=table)

        (line,) = _render_create_check_constraint_not_valid(
            _autogen_context(), CreateCheckConstraintNotValidOp.from_constraint(constraint)
        )

        assert line.startswith("op.create_check_constraint(op.f('ck_orders_status'), 'orders'")
        assert line.endswith("postgresql_not_valid=True)")

    def test_batch_mode_keeps_the_batch_prefix(self):
        op = CreateCheckConstraintNotValidOp("ck", "orders", "status IN ('a')")

        body = _render_body([op], render_as_batch=True)

        assert "with op.batch_alter_table('orders', schema=None) as batch_op:" in body
        assert "batch_op.create_check_constraint('ck', \"status IN ('a')\", postgresql_not_valid=True)" in body

    def test_rendered_body_is_valid_python(self):
        op = CreateCheckConstraintNotValidOp("ck", "orders", "status IN ('a')", column="status", removed_values=("b",))

        body = _render_body([op])

        compile(body, "<migration>", "exec")


class TestRenderNoOp:
    def test_renders_pass_with_the_reason(self):
        assert _render_noop(_ctx(), NoOp("ck_orders_status on orders stays validated")) == (
            "pass  # ck_orders_status on orders stays validated"
        )

    def test_downgrade_body_holding_only_a_noop_compiles(self):
        body = _render_body([ValidateConstraintOp("ck", "orders").reverse()])

        assert "pass  # Constraint ck on orders stays validated." in body
        compile(f"def downgrade() -> None:\n{_indent(body)}", "<migration>", "exec")


def _indent(body: str) -> str:
    return "".join(f"    {line}\n" for line in body.splitlines())
