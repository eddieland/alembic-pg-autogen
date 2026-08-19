# pyright: reportPrivateUsage=false
from __future__ import annotations

from typing import Any

from alembic.operations.ops import UpgradeOps
from alembic.util import PriorityDispatchResult

from alembic_pg_autogen import IGNORED, CanonicalState, FunctionInfo, TriggerInfo, ViewInfo
from alembic_pg_autogen.canonicalize import _declared
from alembic_pg_autogen.compare import _compare_pg_objects, _filter_to_declared, _resolve_ddl
from alembic_pg_autogen.sentinels import _IgnoredSentinel


class TestIgnoredSentinel:
    """The IGNORED sentinel itself."""

    def test_is_singleton(self):
        assert IGNORED is _IgnoredSentinel.IGNORED

    def test_repr_and_str(self):
        assert repr(IGNORED) == "IGNORED"
        assert str(IGNORED) == "IGNORED"
        assert f"{IGNORED}" == "IGNORED"

    def test_not_equal_to_empty_sequence(self):
        """``IGNORED`` must not be confused with "declare nothing"."""
        assert IGNORED != ()
        assert IGNORED != []


class TestResolveDDL:
    """``_resolve_ddl`` passes the sentinel through untouched."""

    def test_ignored_passes_through(self):
        assert _resolve_ddl(IGNORED) is IGNORED

    def test_strings_still_resolved(self):
        assert _resolve_ddl(["CREATE VIEW v AS SELECT 1"]) == ("CREATE VIEW v AS SELECT 1",)


class TestFilterToDeclaredIgnored:
    """``_filter_to_declared`` short-circuits ignored object types.

    The connection is never touched for an ignored type — no DDL is parsed and ``current_schema()`` is not read — so
    this test can pass ``None`` as the connection.
    """

    def _state(self) -> CanonicalState:
        return CanonicalState(
            functions=[FunctionInfo("public", "fn", "", "CREATE FUNCTION public.fn() RETURNS void")],
            triggers=[TriggerInfo("public", "t", "trg", "CREATE TRIGGER trg BEFORE INSERT ON public.t")],
            views=[ViewInfo("public", "v", "CREATE OR REPLACE VIEW public.v AS SELECT 1")],
        )

    def test_all_ignored_yields_empty_desired_state(self):
        no_conn: Any = None
        desired = _filter_to_declared(self._state(), IGNORED, IGNORED, IGNORED, no_conn)

        assert list(desired.functions) == []
        assert list(desired.triggers) == []
        assert list(desired.views) == []


class TestDeclared:
    """``_declared`` maps the sentinel to "no DDL to execute"."""

    def test_ignored_is_empty(self):
        assert list(_declared(IGNORED)) == []

    def test_sequence_passes_through(self):
        ddl = ["CREATE VIEW v AS SELECT 1"]
        assert _declared(ddl) is ddl


class TestComparatorShortCircuit:
    """Every object type ignored means the comparator never touches the database."""

    def test_all_ignored_returns_before_using_the_connection(self):
        opts = {"pg_functions": IGNORED, "pg_triggers": IGNORED, "pg_views": IGNORED}
        # ``connection=None`` would trip the comparator's assert if it got that far.
        autogen_context: Any = _FakeAutogenContext(opts)
        upgrade_ops = UpgradeOps(ops=[])

        result = _compare_pg_objects(autogen_context, upgrade_ops, {None})

        assert result is PriorityDispatchResult.CONTINUE
        assert list(upgrade_ops.ops) == []


class _FakeAutogenContext:
    opts: dict[str, Any]
    connection: None

    def __init__(self, opts: dict[str, Any]) -> None:
        self.opts = opts
        self.connection = None
