from __future__ import annotations

from alembic_pg_autogen import (
    Action,
    CanonicalState,
    DiffResult,
    FunctionInfo,
    FunctionOp,
    TriggerInfo,
    TriggerOp,
    diff,
)


class TestActionEnum:
    """3.1 — Action enum members and values."""

    def test_exactly_three_members(self):
        assert len(Action) == 3

    def test_string_values(self):
        assert Action.CREATE.value == "create"
        assert Action.REPLACE.value == "replace"
        assert Action.DROP.value == "drop"


class TestFunctionOp:
    """3.2 — FunctionOp construction and field access."""

    def test_create(self):
        desired = FunctionInfo("public", "fn", "", "CREATE FUNCTION …")
        op = FunctionOp(action=Action.CREATE, current=None, desired=desired)
        assert op.action is Action.CREATE
        assert op.current is None
        assert op.desired is desired

    def test_replace(self):
        cur = FunctionInfo("public", "fn", "", "old def")
        des = FunctionInfo("public", "fn", "", "new def")
        op = FunctionOp(action=Action.REPLACE, current=cur, desired=des)
        assert op.action is Action.REPLACE
        assert op.current is cur
        assert op.desired is des

    def test_drop(self):
        cur = FunctionInfo("public", "fn", "", "CREATE FUNCTION …")
        op = FunctionOp(action=Action.DROP, current=cur, desired=None)
        assert op.action is Action.DROP
        assert op.current is cur
        assert op.desired is None


class TestTriggerOp:
    """3.2 — TriggerOp construction and field access."""

    def test_create(self):
        desired = TriggerInfo("public", "events", "trg", "CREATE TRIGGER …")
        op = TriggerOp(action=Action.CREATE, current=None, desired=desired)
        assert op.action is Action.CREATE
        assert op.current is None
        assert op.desired is desired

    def test_replace(self):
        cur = TriggerInfo("public", "events", "trg", "old def")
        des = TriggerInfo("public", "events", "trg", "new def")
        op = TriggerOp(action=Action.REPLACE, current=cur, desired=des)
        assert op.action is Action.REPLACE
        assert op.current is cur
        assert op.desired is des

    def test_drop(self):
        cur = TriggerInfo("public", "events", "trg", "CREATE TRIGGER …")
        op = TriggerOp(action=Action.DROP, current=cur, desired=None)
        assert op.action is Action.DROP
        assert op.current is cur
        assert op.desired is None


class TestDiffResult:
    """3.3 — DiffResult construction and field access."""

    def test_construction(self):
        result = DiffResult(function_ops=[], trigger_ops=[])
        assert result.function_ops == []
        assert result.trigger_ops == []

    def test_is_tuple(self):
        result = DiffResult(function_ops=[], trigger_ops=[])
        assert isinstance(result, tuple)
        assert result[0] == []
        assert result[1] == []


class TestDiffFunction:
    """3.4–3.17 — diff() behavior."""

    def test_both_empty(self):
        current = CanonicalState(functions=[], triggers=[])
        desired = CanonicalState(functions=[], triggers=[])
        result = diff(current, desired)
        assert result.function_ops == []
        assert result.trigger_ops == []

    def test_identical_states(self):
        fn = FunctionInfo("public", "fn", "integer", "CREATE FUNCTION …")
        trg = TriggerInfo("public", "events", "trg", "CREATE TRIGGER …")
        current = CanonicalState(functions=[fn], triggers=[trg])
        desired = CanonicalState(functions=[fn], triggers=[trg])
        result = diff(current, desired)
        assert result.function_ops == []
        assert result.trigger_ops == []

    def test_create_function(self):
        fn = FunctionInfo("public", "new_fn", "integer", "CREATE FUNCTION …")
        current = CanonicalState(functions=[], triggers=[])
        desired = CanonicalState(functions=[fn], triggers=[])
        result = diff(current, desired)
        assert len(result.function_ops) == 1
        op = result.function_ops[0]
        assert op.action is Action.CREATE
        assert op.current is None
        assert op.desired is fn

    def test_drop_function(self):
        fn = FunctionInfo("public", "old_fn", "", "CREATE FUNCTION …")
        current = CanonicalState(functions=[fn], triggers=[])
        desired = CanonicalState(functions=[], triggers=[])
        result = diff(current, desired)
        assert len(result.function_ops) == 1
        op = result.function_ops[0]
        assert op.action is Action.DROP
        assert op.current is fn
        assert op.desired is None

    def test_replace_function(self):
        cur = FunctionInfo("public", "my_func", "integer", "old body")
        des = FunctionInfo("public", "my_func", "integer", "new body")
        current = CanonicalState(functions=[cur], triggers=[])
        desired = CanonicalState(functions=[des], triggers=[])
        result = diff(current, desired)
        assert len(result.function_ops) == 1
        op = result.function_ops[0]
        assert op.action is Action.REPLACE
        assert op.current is cur
        assert op.desired is des

    def test_create_trigger(self):
        trg = TriggerInfo("public", "events", "audit_trg", "CREATE TRIGGER …")
        current = CanonicalState(functions=[], triggers=[])
        desired = CanonicalState(functions=[], triggers=[trg])
        result = diff(current, desired)
        assert len(result.trigger_ops) == 1
        op = result.trigger_ops[0]
        assert op.action is Action.CREATE
        assert op.current is None
        assert op.desired is trg

    def test_drop_trigger(self):
        trg = TriggerInfo("public", "events", "old_trg", "CREATE TRIGGER …")
        current = CanonicalState(functions=[], triggers=[trg])
        desired = CanonicalState(functions=[], triggers=[])
        result = diff(current, desired)
        assert len(result.trigger_ops) == 1
        op = result.trigger_ops[0]
        assert op.action is Action.DROP
        assert op.current is trg
        assert op.desired is None

    def test_replace_trigger(self):
        cur = TriggerInfo("public", "events", "audit_trg", "old def")
        des = TriggerInfo("public", "events", "audit_trg", "new def")
        current = CanonicalState(functions=[], triggers=[cur])
        desired = CanonicalState(functions=[], triggers=[des])
        result = diff(current, desired)
        assert len(result.trigger_ops) == 1
        op = result.trigger_ops[0]
        assert op.action is Action.REPLACE
        assert op.current is cur
        assert op.desired is des

    def test_mixed_scenario(self):
        fn_a = FunctionInfo("public", "a_func", "", "def A")
        fn_b_cur = FunctionInfo("public", "b_func", "", "def B old")
        fn_b_des = FunctionInfo("public", "b_func", "", "def B new")
        fn_c = FunctionInfo("public", "c_func", "", "def C")

        current = CanonicalState(functions=[fn_a, fn_b_cur], triggers=[])
        desired = CanonicalState(functions=[fn_b_des, fn_c], triggers=[])
        result = diff(current, desired)

        assert len(result.function_ops) == 3
        ops = {op.action: op for op in result.function_ops}
        assert ops[Action.DROP].current is fn_a
        assert ops[Action.REPLACE].current is fn_b_cur
        assert ops[Action.REPLACE].desired is fn_b_des
        assert ops[Action.CREATE].desired is fn_c

    def test_overloaded_functions(self):
        fn_int_cur = FunctionInfo("public", "my_func", "integer", "def v1")
        fn_txt_cur = FunctionInfo("public", "my_func", "text", "def txt")
        fn_int_des = FunctionInfo("public", "my_func", "integer", "def v2")
        fn_txt_des = FunctionInfo("public", "my_func", "text", "def txt")

        current = CanonicalState(functions=[fn_int_cur, fn_txt_cur], triggers=[])
        desired = CanonicalState(functions=[fn_int_des, fn_txt_des], triggers=[])
        result = diff(current, desired)

        assert len(result.function_ops) == 1
        op = result.function_ops[0]
        assert op.action is Action.REPLACE
        assert op.current is fn_int_cur
        assert op.desired is fn_int_des

    def test_same_name_different_schemas(self):
        fn_pub = FunctionInfo("public", "helper", "", "def pub")
        fn_aud = FunctionInfo("audit", "helper", "", "def aud")

        current = CanonicalState(functions=[fn_pub, fn_aud], triggers=[])
        desired = CanonicalState(functions=[fn_pub, fn_aud], triggers=[])
        result = diff(current, desired)
        assert result.function_ops == []

        # Drop one, keep the other
        desired2 = CanonicalState(functions=[fn_pub], triggers=[])
        result2 = diff(current, desired2)
        assert len(result2.function_ops) == 1
        assert result2.function_ops[0].action is Action.DROP
        assert result2.function_ops[0].current is fn_aud

    def test_same_trigger_name_different_tables(self):
        trg_orders = TriggerInfo("public", "orders", "audit_trg", "def orders")
        trg_users = TriggerInfo("public", "users", "audit_trg", "def users")

        current = CanonicalState(functions=[], triggers=[trg_orders, trg_users])
        desired = CanonicalState(functions=[], triggers=[trg_orders, trg_users])
        result = diff(current, desired)
        assert result.trigger_ops == []

        # Drop one
        desired2 = CanonicalState(functions=[], triggers=[trg_orders])
        result2 = diff(current, desired2)
        assert len(result2.trigger_ops) == 1
        assert result2.trigger_ops[0].action is Action.DROP
        assert result2.trigger_ops[0].current is trg_users

    def test_deterministic_ordering(self):
        fn_z = FunctionInfo("public", "z_func", "", "def z")
        fn_a_aud = FunctionInfo("audit", "a_func", "", "def a_aud")
        fn_a_pub = FunctionInfo("public", "a_func", "integer", "def a_pub")

        # Provide in reverse order — result should still be sorted
        current = CanonicalState(functions=[], triggers=[])
        desired = CanonicalState(functions=[fn_z, fn_a_pub, fn_a_aud], triggers=[])
        result = diff(current, desired)

        assert len(result.function_ops) == 3
        keys = [op.desired[:3] for op in result.function_ops if op.desired is not None]
        assert keys == sorted(keys)
        assert keys[0] == ("audit", "a_func", "")
        assert keys[1] == ("public", "a_func", "integer")
        assert keys[2] == ("public", "z_func", "")

    def test_whitespace_difference_produces_replace(self):
        cur = FunctionInfo("public", "fn", "", "BEGIN\n  RETURN 1;\nEND;")
        des = FunctionInfo("public", "fn", "", "BEGIN\n    RETURN 1;\nEND;")
        current = CanonicalState(functions=[cur], triggers=[])
        desired = CanonicalState(functions=[des], triggers=[])
        result = diff(current, desired)
        assert len(result.function_ops) == 1
        assert result.function_ops[0].action is Action.REPLACE
