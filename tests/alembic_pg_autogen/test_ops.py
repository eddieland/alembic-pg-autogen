from __future__ import annotations

from alembic.operations.ops import MigrateOperation

from alembic_pg_autogen import (
    CreateFunctionOp,
    CreateTriggerOp,
    DropFunctionOp,
    DropTriggerOp,
    FunctionInfo,
    ReplaceFunctionOp,
    ReplaceTriggerOp,
    TriggerInfo,
)

FN_A = FunctionInfo("public", "my_fn", "integer", "CREATE FUNCTION …")
FN_B = FunctionInfo("audit", "log_change", "", "CREATE FUNCTION … v2")
TRG_A = TriggerInfo("public", "orders", "audit_trg", "CREATE TRIGGER …")
TRG_B = TriggerInfo("public", "events", "notify_trg", "CREATE TRIGGER … v2")


class TestCreateFunctionOp:
    def test_stores_desired(self):
        op = CreateFunctionOp(FN_A)
        assert op.desired is FN_A

    def test_reverse_is_drop(self):
        op = CreateFunctionOp(FN_A)
        rev = op.reverse()
        assert isinstance(rev, DropFunctionOp)
        assert rev.current is FN_A

    def test_to_diff_tuple(self):
        op = CreateFunctionOp(FN_A)
        assert op.to_diff_tuple() == ("create_function", "public", "my_fn", "integer")

    def test_extends_migrate_operation(self):
        assert issubclass(CreateFunctionOp, MigrateOperation)


class TestReplaceFunctionOp:
    def test_stores_current_and_desired(self):
        op = ReplaceFunctionOp(FN_A, FN_B)
        assert op.current is FN_A
        assert op.desired is FN_B

    def test_reverse_swaps(self):
        op = ReplaceFunctionOp(FN_A, FN_B)
        rev = op.reverse()
        assert isinstance(rev, ReplaceFunctionOp)
        assert rev.current is FN_B
        assert rev.desired is FN_A

    def test_to_diff_tuple(self):
        op = ReplaceFunctionOp(FN_A, FN_B)
        assert op.to_diff_tuple() == ("replace_function", "audit", "log_change", "")

    def test_extends_migrate_operation(self):
        assert issubclass(ReplaceFunctionOp, MigrateOperation)


class TestDropFunctionOp:
    def test_stores_current(self):
        op = DropFunctionOp(FN_A)
        assert op.current is FN_A

    def test_reverse_is_create(self):
        op = DropFunctionOp(FN_A)
        rev = op.reverse()
        assert isinstance(rev, CreateFunctionOp)
        assert rev.desired is FN_A

    def test_to_diff_tuple(self):
        op = DropFunctionOp(FunctionInfo("public", "old_fn", "text", "…"))
        assert op.to_diff_tuple() == ("drop_function", "public", "old_fn", "text")

    def test_extends_migrate_operation(self):
        assert issubclass(DropFunctionOp, MigrateOperation)


class TestCreateTriggerOp:
    def test_stores_desired(self):
        op = CreateTriggerOp(TRG_A)
        assert op.desired is TRG_A

    def test_reverse_is_drop(self):
        op = CreateTriggerOp(TRG_A)
        rev = op.reverse()
        assert isinstance(rev, DropTriggerOp)
        assert rev.current is TRG_A

    def test_to_diff_tuple(self):
        op = CreateTriggerOp(TRG_A)
        assert op.to_diff_tuple() == ("create_trigger", "public", "orders", "audit_trg")

    def test_extends_migrate_operation(self):
        assert issubclass(CreateTriggerOp, MigrateOperation)


class TestReplaceTriggerOp:
    def test_stores_current_and_desired(self):
        op = ReplaceTriggerOp(TRG_A, TRG_B)
        assert op.current is TRG_A
        assert op.desired is TRG_B

    def test_reverse_swaps(self):
        op = ReplaceTriggerOp(TRG_A, TRG_B)
        rev = op.reverse()
        assert isinstance(rev, ReplaceTriggerOp)
        assert rev.current is TRG_B
        assert rev.desired is TRG_A

    def test_to_diff_tuple(self):
        op = ReplaceTriggerOp(TRG_A, TRG_B)
        assert op.to_diff_tuple() == ("replace_trigger", "public", "events", "notify_trg")

    def test_extends_migrate_operation(self):
        assert issubclass(ReplaceTriggerOp, MigrateOperation)


class TestDropTriggerOp:
    def test_stores_current(self):
        op = DropTriggerOp(TRG_A)
        assert op.current is TRG_A

    def test_reverse_is_create(self):
        op = DropTriggerOp(TRG_A)
        rev = op.reverse()
        assert isinstance(rev, CreateTriggerOp)
        assert rev.desired is TRG_A

    def test_to_diff_tuple(self):
        op = DropTriggerOp(TriggerInfo("public", "users", "old_trg", "…"))
        assert op.to_diff_tuple() == ("drop_trigger", "public", "users", "old_trg")

    def test_extends_migrate_operation(self):
        assert issubclass(DropTriggerOp, MigrateOperation)


class TestReverseRoundtrip:
    """Verify that reverse().reverse() produces an equivalent op."""

    def test_create_function_roundtrip(self):
        op = CreateFunctionOp(FN_A)
        roundtripped = op.reverse().reverse()
        assert isinstance(roundtripped, CreateFunctionOp)
        assert roundtripped.desired is FN_A

    def test_replace_function_roundtrip(self):
        op = ReplaceFunctionOp(FN_A, FN_B)
        roundtripped = op.reverse().reverse()
        assert isinstance(roundtripped, ReplaceFunctionOp)
        assert roundtripped.current is FN_A
        assert roundtripped.desired is FN_B

    def test_drop_function_roundtrip(self):
        op = DropFunctionOp(FN_A)
        roundtripped = op.reverse().reverse()
        assert isinstance(roundtripped, DropFunctionOp)
        assert roundtripped.current is FN_A

    def test_create_trigger_roundtrip(self):
        op = CreateTriggerOp(TRG_A)
        roundtripped = op.reverse().reverse()
        assert isinstance(roundtripped, CreateTriggerOp)
        assert roundtripped.desired is TRG_A

    def test_replace_trigger_roundtrip(self):
        op = ReplaceTriggerOp(TRG_A, TRG_B)
        roundtripped = op.reverse().reverse()
        assert isinstance(roundtripped, ReplaceTriggerOp)
        assert roundtripped.current is TRG_A
        assert roundtripped.desired is TRG_B

    def test_drop_trigger_roundtrip(self):
        op = DropTriggerOp(TRG_A)
        roundtripped = op.reverse().reverse()
        assert isinstance(roundtripped, DropTriggerOp)
        assert roundtripped.current is TRG_A


class TestNoOperationRegistration:
    """Importing _ops should NOT register op.* methods on Operations."""

    def test_no_create_function_method(self):
        from alembic.operations.base import Operations

        assert not hasattr(Operations, "create_function")

    def test_no_drop_trigger_method(self):
        from alembic.operations.base import Operations

        assert not hasattr(Operations, "drop_trigger")
