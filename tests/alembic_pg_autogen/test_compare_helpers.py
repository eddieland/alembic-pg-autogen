"""Unit tests for the comparator's pure helpers — no database required.

The end-to-end tests in ``test_autogenerate.py`` exercise these helpers indirectly, but only through the handful of
scenarios a live PostgreSQL container is set up for.  These tests pin the helpers' contracts directly: the dependency
order ``_order_ops`` guarantees, the schema resolution ``_resolve_schemas`` performs, and the identities the DDL
parsers extract.
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import logging
from typing import Any

import pytest

from alembic_pg_autogen import (
    IGNORED,
    Action,
    CanonicalState,
    CreateFunctionOp,
    CreateTriggerOp,
    CreateViewOp,
    DropFunctionOp,
    DropTriggerOp,
    DropViewOp,
    FunctionInfo,
    FunctionOp,
    ReplaceFunctionOp,
    ReplaceTriggerOp,
    ReplaceViewOp,
    TriggerInfo,
    TriggerOp,
    ViewInfo,
    ViewOp,
)
from alembic_pg_autogen.compare import (
    _filter_to_declared,
    _filter_to_schemas,
    _order_ops,
    _parse_function_names,
    _parse_trigger_identities,
    _parse_view_names,
    _resolve_ddl,
    _resolve_schemas,
)

LOGGER = "alembic_pg_autogen.compare"

FN_DDL = "CREATE FUNCTION {name}() RETURNS void LANGUAGE sql AS $$ SELECT 1 $$"
TRG_DDL = "CREATE TRIGGER {name} AFTER INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION fn()"
VIEW_DDL = "CREATE OR REPLACE VIEW {name} AS SELECT 1"


class _FakeConnection:
    """Stands in for a ``Connection`` for helpers that only read ``current_schema()``."""

    schema: str
    statements: list[str]

    def __init__(self, schema: str = "public") -> None:
        self.schema = schema
        self.statements = []

    def execute(self, statement: Any, *_args: Any, **_kw: Any) -> _FakeConnection:
        self.statements.append(str(statement))
        return self

    def scalar(self) -> str:
        return self.schema


def _conn(schema: str = "public") -> Any:
    """Return a fake connection typed as ``Any`` so it can stand in for ``Connection``."""
    return _FakeConnection(schema)


def _fn(schema: str = "public", name: str = "fn", args: str = "", definition: str = "def") -> FunctionInfo:
    return FunctionInfo(schema, name, args, definition)


def _trg(schema: str = "public", table: str = "t", name: str = "trg", definition: str = "def") -> TriggerInfo:
    return TriggerInfo(schema, table, name, definition)


def _view(schema: str = "public", name: str = "v", definition: str = "def") -> ViewInfo:
    return ViewInfo(schema, name, definition)


class TestOrderOps:
    """``_order_ops`` emits operations in an order PostgreSQL can execute without dependency errors."""

    def test_empty_input_produces_no_ops(self):
        assert _order_ops([], [], []) == []

    def test_full_dependency_order(self):
        """Drops run innermost-first, creates outermost-first, so nothing references a missing object."""
        function_ops = [
            FunctionOp(Action.DROP, _fn(name="old_fn"), None),
            FunctionOp(Action.CREATE, None, _fn(name="new_fn")),
        ]
        trigger_ops = [
            TriggerOp(Action.DROP, _trg(name="old_trg"), None),
            TriggerOp(Action.CREATE, None, _trg(name="new_trg")),
        ]
        view_ops = [
            ViewOp(Action.DROP, _view(name="old_v"), None),
            ViewOp(Action.CREATE, None, _view(name="new_v")),
        ]

        ops = _order_ops(function_ops, trigger_ops, view_ops)

        assert [type(op).__name__ for op in ops] == [
            "DropTriggerOp",
            "DropViewOp",
            "DropFunctionOp",
            "CreateFunctionOp",
            "CreateViewOp",
            "CreateTriggerOp",
        ]

    def test_replace_ops_run_in_the_create_phase(self):
        """A REPLACE is a create for ordering purposes: it must follow the drops it may depend on."""
        function_ops = [FunctionOp(Action.REPLACE, _fn(definition="old"), _fn(definition="new"))]
        trigger_ops = [
            TriggerOp(Action.DROP, _trg(name="gone"), None),
            TriggerOp(Action.REPLACE, _trg(definition="old"), _trg(definition="new")),
        ]
        view_ops = [ViewOp(Action.REPLACE, _view(definition="old"), _view(definition="new"))]

        ops = _order_ops(function_ops, trigger_ops, view_ops)

        assert [type(op).__name__ for op in ops] == [
            "DropTriggerOp",
            "ReplaceFunctionOp",
            "ReplaceViewOp",
            "ReplaceTriggerOp",
        ]

    def test_ops_carry_the_expected_infos(self):
        current_fn = _fn(name="fn", definition="old")
        desired_fn = _fn(name="fn", definition="new")

        ops = _order_ops([FunctionOp(Action.REPLACE, current_fn, desired_fn)], [], [])

        assert len(ops) == 1
        replace = ops[0]
        assert isinstance(replace, ReplaceFunctionOp)
        assert replace.current is current_fn
        assert replace.desired is desired_fn

    def test_relative_order_within_a_phase_is_preserved(self):
        first, second = _view(name="a"), _view(name="b")

        ops = _order_ops([], [], [ViewOp(Action.DROP, first, None), ViewOp(Action.DROP, second, None)])

        assert [op.current for op in ops if isinstance(op, DropViewOp)] == [first, second]

    @pytest.mark.parametrize(
        ("op", "expected_type"),
        [
            (FunctionOp(Action.CREATE, None, _fn()), CreateFunctionOp),
            (FunctionOp(Action.DROP, _fn(), None), DropFunctionOp),
            (FunctionOp(Action.REPLACE, _fn(), _fn()), ReplaceFunctionOp),
        ],
    )
    def test_function_actions_map_to_operations(self, op: FunctionOp, expected_type: type):
        assert [type(o) for o in _order_ops([op], [], [])] == [expected_type]

    @pytest.mark.parametrize(
        ("op", "expected_type"),
        [
            (TriggerOp(Action.CREATE, None, _trg()), CreateTriggerOp),
            (TriggerOp(Action.DROP, _trg(), None), DropTriggerOp),
            (TriggerOp(Action.REPLACE, _trg(), _trg()), ReplaceTriggerOp),
        ],
    )
    def test_trigger_actions_map_to_operations(self, op: TriggerOp, expected_type: type):
        assert [type(o) for o in _order_ops([], [op], [])] == [expected_type]

    @pytest.mark.parametrize(
        ("op", "expected_type"),
        [
            (ViewOp(Action.CREATE, None, _view()), CreateViewOp),
            (ViewOp(Action.DROP, _view(), None), DropViewOp),
            (ViewOp(Action.REPLACE, _view(), _view()), ReplaceViewOp),
        ],
    )
    def test_view_actions_map_to_operations(self, op: ViewOp, expected_type: type):
        assert [type(o) for o in _order_ops([], [], [op])] == [expected_type]


class TestFilterToSchemas:
    """``_filter_to_schemas`` narrows a catalog snapshot to the schemas autogenerate asked about."""

    def _state(self) -> CanonicalState:
        return CanonicalState(
            functions=[_fn(schema="public"), _fn(schema="audit")],
            triggers=[_trg(schema="public"), _trg(schema="audit")],
            views=[_view(schema="public"), _view(schema="audit")],
        )

    def test_none_returns_the_state_unchanged(self):
        state = self._state()

        assert _filter_to_schemas(state, None) is state

    def test_filters_every_object_type(self):
        filtered = _filter_to_schemas(self._state(), ["public"])

        assert [f.schema for f in filtered.functions] == ["public"]
        assert [t.schema for t in filtered.triggers] == ["public"]
        assert [v.schema for v in filtered.views] == ["public"]

    def test_multiple_schemas_are_all_kept(self):
        filtered = _filter_to_schemas(self._state(), ["public", "audit"])

        assert len(filtered.functions) == 2
        assert len(filtered.triggers) == 2
        assert len(filtered.views) == 2

    def test_unmatched_schema_empties_the_state(self):
        filtered = _filter_to_schemas(self._state(), ["reporting"])

        assert list(filtered.functions) == []
        assert list(filtered.triggers) == []
        assert list(filtered.views) == []

    def test_empty_schema_list_empties_the_state(self):
        """An empty list is "no schemas", not "no filter" — that is what *None* means."""
        filtered = _filter_to_schemas(self._state(), [])

        assert list(filtered.functions) == []


class TestResolveSchemas:
    """``_resolve_schemas`` turns Alembic's schema set into concrete names."""

    def test_empty_set_means_no_filter(self):
        assert _resolve_schemas(_conn(), set()) is None

    def test_none_resolves_to_current_schema(self):
        assert _resolve_schemas(_conn("myschema"), {None}) == ["myschema"]

    def test_named_schema_passes_through(self):
        assert _resolve_schemas(_conn(), ["audit"]) == ["audit"]

    def test_mixed_none_and_named_schemas(self):
        resolved = _resolve_schemas(_conn("app"), ["audit", None])

        assert resolved is not None
        assert sorted(resolved) == ["app", "audit"]

    def test_named_schema_does_not_query_the_connection(self):
        conn = _FakeConnection()

        _resolve_schemas(conn, ["audit"])  # pyright: ignore[reportArgumentType]

        assert conn.statements == []


class TestResolveDDL:
    """``_resolve_ddl`` accepts DDL strings and alembic-utils-style entities interchangeably."""

    def test_sql_creatable_is_converted(self):
        entity = _StubSQLCreatable("CREATE VIEW public.v AS SELECT 1")

        assert _resolve_ddl([entity]) == ("CREATE VIEW public.v AS SELECT 1",)

    def test_strings_and_entities_mix(self):
        entity = _StubSQLCreatable("CREATE VIEW public.b AS SELECT 2")

        assert _resolve_ddl(["CREATE VIEW public.a AS SELECT 1", entity]) == (
            "CREATE VIEW public.a AS SELECT 1",
            "CREATE VIEW public.b AS SELECT 2",
        )

    def test_empty_sequence_is_not_the_sentinel(self):
        """An empty declaration means "there should be nothing", which is a real, non-ignored state."""
        resolved = _resolve_ddl([])

        assert resolved == ()
        assert resolved is not IGNORED


class TestParseFunctionNames:
    def test_schema_qualified(self):
        assert _parse_function_names([FN_DDL.format(name="audit.log_event")], _conn()) == {("audit", "log_event")}

    def test_unqualified_uses_current_schema(self):
        assert _parse_function_names([FN_DDL.format(name="hello")], _conn("app")) == {("app", "hello")}

    def test_quoted_identifiers(self):
        ddl = FN_DDL.format(name='"My Schema"."My Func"')

        assert _parse_function_names([ddl], _conn()) == {("My Schema", "My Func")}

    def test_multiple_statements_accumulate(self):
        ddls = [FN_DDL.format(name="a"), FN_DDL.format(name="audit.b")]

        assert _parse_function_names(ddls, _conn("public")) == {("public", "a"), ("audit", "b")}

    def test_non_function_ddl_raises(self):
        with pytest.raises(ValueError, match="Cannot parse function identity"):
            _parse_function_names(["SELECT 1"], _conn())


class TestParseTriggerIdentities:
    def test_schema_qualified(self):
        ddl = TRG_DDL.format(name="audit_trg", table="public.orders")

        assert _parse_trigger_identities([ddl], _conn()) == {("public", "orders", "audit_trg")}

    def test_unqualified_uses_current_schema(self):
        ddl = TRG_DDL.format(name="trg", table="orders")

        assert _parse_trigger_identities([ddl], _conn("app")) == {("app", "orders", "trg")}

    def test_quoted_identifiers(self):
        ddl = TRG_DDL.format(name='"My Trigger"', table='"My Schema"."My Table"')

        assert _parse_trigger_identities([ddl], _conn()) == {("My Schema", "My Table", "My Trigger")}

    def test_non_trigger_ddl_raises(self):
        with pytest.raises(ValueError, match="Cannot parse trigger identity"):
            _parse_trigger_identities(["SELECT 1"], _conn())


class TestParseViewNames:
    def test_schema_qualified(self):
        assert _parse_view_names([VIEW_DDL.format(name="reporting.summary")], _conn()) == {("reporting", "summary")}

    def test_unqualified_uses_current_schema(self):
        assert _parse_view_names([VIEW_DDL.format(name="active_users")], _conn("app")) == {("app", "active_users")}

    def test_plain_create_view(self):
        assert _parse_view_names(["CREATE VIEW public.v AS SELECT 1"], _conn()) == {("public", "v")}

    def test_quoted_view_name(self):
        """A quoted identifier must not be mistaken for the schema — that would silently drop the view."""
        ddl = 'CREATE OR REPLACE VIEW public."My View" AS SELECT 1'

        assert _parse_view_names([ddl], _conn()) == {("public", "My View")}

    def test_quoted_schema_and_view_name(self):
        ddl = 'CREATE VIEW "My Schema"."My View" AS SELECT 1'

        assert _parse_view_names([ddl], _conn()) == {("My Schema", "My View")}

    def test_unqualified_quoted_view_name(self):
        assert _parse_view_names(['CREATE VIEW "weird name" AS SELECT 1'], _conn("app")) == {("app", "weird name")}

    def test_leading_comment_is_not_mistaken_for_the_statement(self):
        ddl = "-- CREATE VIEW public.decoy AS SELECT 1\nCREATE VIEW public.real_view AS SELECT 1"

        assert _parse_view_names([ddl], _conn()) == {("public", "real_view")}

    def test_multiple_statements_accumulate(self):
        ddls = [VIEW_DDL.format(name="a"), VIEW_DDL.format(name="reporting.b")]

        assert _parse_view_names(ddls, _conn("public")) == {("public", "a"), ("reporting", "b")}

    def test_non_view_ddl_raises(self):
        with pytest.raises(ValueError, match="Cannot parse view identity"):
            _parse_view_names(["SELECT 1"], _conn())

    def test_materialized_view_raises(self):
        """Materialized views are not a ``ViewStmt`` and are outside what ``pg_views`` manages."""
        with pytest.raises(ValueError, match="Cannot parse view identity"):
            _parse_view_names(["CREATE MATERIALIZED VIEW public.mv AS SELECT 1"], _conn())


class TestFilterToDeclared:
    """``_filter_to_declared`` keeps only the canonical objects the user actually declared."""

    def _state(self) -> CanonicalState:
        return CanonicalState(
            functions=[_fn(name="declared"), _fn(name="preexisting")],
            triggers=[_trg(name="declared"), _trg(name="preexisting")],
            views=[_view(name="declared"), _view(name="preexisting")],
        )

    def test_undeclared_objects_are_dropped_from_the_desired_state(self):
        desired = _filter_to_declared(
            self._state(),
            [FN_DDL.format(name="declared")],
            [TRG_DDL.format(name="declared", table="t")],
            [VIEW_DDL.format(name="declared")],
            _conn(),
        )

        assert [f.name for f in desired.functions] == ["declared"]
        assert [t.trigger_name for t in desired.triggers] == ["declared"]
        assert [v.name for v in desired.views] == ["declared"]

    def test_empty_declaration_yields_an_empty_desired_state(self):
        """Declaring nothing is not the same as ignoring: every existing object becomes a drop candidate."""
        desired = _filter_to_declared(self._state(), [], [], [], _conn())

        assert list(desired.functions) == []
        assert list(desired.triggers) == []
        assert list(desired.views) == []

    def test_schema_mismatch_warns(self, caplog: pytest.LogCaptureFixture):
        """DDL that matches nothing is nearly always a schema-qualifier mistake, so it must not pass silently."""
        state = CanonicalState(functions=[_fn(schema="audit", name="declared")], triggers=[], views=[])

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            desired = _filter_to_declared(state, [FN_DDL.format(name="declared")], IGNORED, IGNORED, _conn("public"))

        assert list(desired.functions) == []
        assert "No canonical functions matched user DDL" in caplog.text

    def test_trigger_schema_mismatch_warns(self, caplog: pytest.LogCaptureFixture):
        state = CanonicalState(functions=[], triggers=[_trg(schema="audit", name="declared")], views=[])
        ddl = TRG_DDL.format(name="declared", table="t")

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            desired = _filter_to_declared(state, IGNORED, [ddl], IGNORED, _conn("public"))

        assert list(desired.triggers) == []
        assert "No canonical triggers matched user DDL" in caplog.text

    def test_view_schema_mismatch_warns(self, caplog: pytest.LogCaptureFixture):
        state = CanonicalState(functions=[], triggers=[], views=[_view(schema="audit", name="declared")])

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            desired = _filter_to_declared(state, IGNORED, IGNORED, [VIEW_DDL.format(name="declared")], _conn("public"))

        assert list(desired.views) == []
        assert "No canonical views matched user DDL" in caplog.text

    def test_matching_declaration_does_not_warn(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _filter_to_declared(self._state(), [FN_DDL.format(name="declared")], IGNORED, IGNORED, _conn())

        assert caplog.records == []

    def test_overloads_of_a_declared_name_are_all_kept(self):
        """Functions match on ``(schema, name)``, so declaring one overload keeps every overload of that name."""
        state = CanonicalState(
            functions=[_fn(name="add", args="integer, integer"), _fn(name="add", args="text, text")],
            triggers=[],
            views=[],
        )

        desired = _filter_to_declared(state, [FN_DDL.format(name="add")], IGNORED, IGNORED, _conn())

        assert len(desired.functions) == 2


class _StubSQLCreatable:
    """Minimal alembic-utils-style entity: ``to_sql_statement_create()`` returning an object with ``.text``."""

    ddl: str

    def __init__(self, ddl: str) -> None:
        self.ddl = ddl

    def to_sql_statement_create(self) -> _StubSQLCreatable:
        return self

    @property
    def text(self) -> str:
        return self.ddl
