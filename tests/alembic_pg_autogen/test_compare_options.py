# pyright: reportPrivateUsage=false
from __future__ import annotations

import logging
from typing import Any

import pytest
from alembic.operations.ops import UpgradeOps
from alembic.util import PriorityDispatchResult

from alembic_pg_autogen.compare import _compare_pg_objects, _warn_unrecognized_options

LOGGER = "alembic_pg_autogen.compare"


class TestAbsentKeysDefaultToIgnored:
    """An absent desired-state key leaves its object type unmanaged."""

    def test_empty_opts_short_circuits(self):
        """No keys at all means nothing is managed, so the connection is never used."""
        # ``connection=None`` would trip the comparator's assert if it got that far.
        autogen_context: Any = _FakeAutogenContext({})
        upgrade_ops = UpgradeOps(ops=[])

        result = _compare_pg_objects(autogen_context, upgrade_ops, {None})

        assert result is PriorityDispatchResult.CONTINUE
        assert list(upgrade_ops.ops) == []

    def test_unrelated_opts_short_circuit(self):
        """Other ``context.configure()`` options do not make the comparator run."""
        autogen_context: Any = _FakeAutogenContext({"target_metadata": None, "compare_type": True})
        upgrade_ops = UpgradeOps(ops=[])

        assert _compare_pg_objects(autogen_context, upgrade_ops, {None}) is PriorityDispatchResult.CONTINUE


class TestWarnUnrecognizedOptions:
    """``pg_*`` options that look like a misspelled desired-state key are reported."""

    @pytest.mark.parametrize(
        ("typo", "intended"),
        [
            ("pg_view", "pg_views"),
            ("pg_function", "pg_functions"),
            ("pg_trigger", "pg_triggers"),
            ("pg_functons", "pg_functions"),
            ("pg_veiws", "pg_views"),
        ],
    )
    def test_close_match_warns_with_intended_key(self, typo: str, intended: str, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _warn_unrecognized_options({typo: []})

        assert len(caplog.records) == 1
        assert typo in caplog.text
        assert intended in caplog.text

    @pytest.mark.parametrize("key", ["pg_extensions", "pg_indexes", "pg_materialized_views", "pg_func"])
    def test_unrelated_pg_option_is_silent(self, key: str, caplog: pytest.LogCaptureFixture):
        """Another plugin's ``pg_*`` option is not a typo — the namespace is not ours to police."""
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _warn_unrecognized_options({key: object()})

        assert caplog.records == []

    def test_recognized_keys_are_silent(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _warn_unrecognized_options({"pg_functions": [], "pg_triggers": [], "pg_views": []})

        assert caplog.records == []

    def test_non_pg_options_are_silent(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _warn_unrecognized_options({"target_metadata": None, "compare_type": True, "render_as_batch": False})

        assert caplog.records == []

    def test_typo_warns_even_when_nothing_is_managed(self, caplog: pytest.LogCaptureFixture):
        """The misspelling is the reason nothing is managed, so the warning must precede the short-circuit."""
        autogen_context: Any = _FakeAutogenContext({"pg_view": ["CREATE VIEW v AS SELECT 1"]})
        upgrade_ops = UpgradeOps(ops=[])

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = _compare_pg_objects(autogen_context, upgrade_ops, {None})

        assert result is PriorityDispatchResult.CONTINUE
        assert "pg_view" in caplog.text
        assert "pg_views" in caplog.text


class _FakeAutogenContext:
    opts: dict[str, Any]
    connection: None

    def __init__(self, opts: dict[str, Any]) -> None:
        self.opts = opts
        self.connection = None
