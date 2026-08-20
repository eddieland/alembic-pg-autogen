def test_package_importable():
    import alembic_pg_autogen

    assert hasattr(alembic_pg_autogen, "__all__")


def test_extension_modules_importable():
    import alembic_pg_autogen.compare
    import alembic_pg_autogen.compare_check_constraints
    import alembic_pg_autogen.ops
    import alembic_pg_autogen.render

    assert alembic_pg_autogen.compare is not None
    assert alembic_pg_autogen.compare_check_constraints is not None
    assert alembic_pg_autogen.ops is not None
    assert alembic_pg_autogen.render is not None


def test_check_constraint_exports_present():
    import alembic_pg_autogen

    assert "CheckConstraintInfo" in alembic_pg_autogen.__all__
    assert "inspect_check_constraints" in alembic_pg_autogen.__all__
    assert "canonicalize_check_constraints" in alembic_pg_autogen.__all__
    assert "current_schema" in alembic_pg_autogen.__all__


def test_check_constraint_exports_importable():
    from alembic_pg_autogen import (
        CheckConstraintInfo,
        canonicalize_check_constraints,
        current_schema,
        inspect_check_constraints,
    )

    assert CheckConstraintInfo is not None
    assert inspect_check_constraints is not None
    assert canonicalize_check_constraints is not None
    assert current_schema is not None


def test_check_constraint_plugin_registered():
    from alembic.runtime.plugins import _all_plugins  # pyright: ignore[reportPrivateUsage]

    import alembic_pg_autogen

    assert alembic_pg_autogen.__all__  # importing the package is what registers the plugins
    assert "alembic_pg_autogen.checkconstraints" in _all_plugins


def test_view_exports_present():
    import alembic_pg_autogen

    assert "ViewInfo" in alembic_pg_autogen.__all__
    assert "ViewOp" in alembic_pg_autogen.__all__
    assert "CreateViewOp" in alembic_pg_autogen.__all__
    assert "ReplaceViewOp" in alembic_pg_autogen.__all__
    assert "DropViewOp" in alembic_pg_autogen.__all__
    assert "inspect_views" in alembic_pg_autogen.__all__
    assert "canonicalize_views" in alembic_pg_autogen.__all__


def test_view_exports_importable():
    from alembic_pg_autogen import (
        CreateViewOp,
        DropViewOp,
        ReplaceViewOp,
        ViewInfo,
        ViewOp,
        canonicalize_views,
        inspect_views,
    )

    assert ViewInfo is not None
    assert ViewOp is not None
    assert CreateViewOp is not None
    assert ReplaceViewOp is not None
    assert DropViewOp is not None
    assert inspect_views is not None
    assert canonicalize_views is not None


def test_importing_package_registers_renderers():
    """Importing the package must register renderers for every op it emits.

    Regression test: the render module was never imported, so ``renderers.dispatch()`` raised
    ``ValueError: no dispatch function for object`` when Alembic wrote the migration script.
    """
    import subprocess
    import sys
    import textwrap

    code = textwrap.dedent("""
        import alembic_pg_autogen
        from alembic.autogenerate.render import renderers
        from alembic_pg_autogen import (
            CreateFunctionOp,
            CreateTriggerOp,
            CreateViewOp,
            DropFunctionOp,
            DropTriggerOp,
            DropViewOp,
            FunctionInfo,
            ReplaceFunctionOp,
            ReplaceTriggerOp,
            ReplaceViewOp,
            TriggerInfo,
            ViewInfo,
        )

        fn = FunctionInfo("public", "fn", "", "CREATE FUNCTION public.fn() RETURNS void LANGUAGE sql AS $$ $$")
        trg = TriggerInfo("public", "t", "trg", "CREATE TRIGGER trg BEFORE INSERT ON public.t EXECUTE FUNCTION f()")
        view = ViewInfo("public", "v", "CREATE OR REPLACE VIEW public.v AS SELECT 1")

        ops = [
            CreateFunctionOp(fn),
            ReplaceFunctionOp(fn, fn),
            DropFunctionOp(fn),
            CreateTriggerOp(trg),
            ReplaceTriggerOp(trg, trg),
            DropTriggerOp(trg),
            CreateViewOp(view),
            ReplaceViewOp(view, view),
            DropViewOp(view),
        ]
        for op in ops:
            assert renderers.dispatch(op) is not None, type(op).__name__
    """)
    subprocess.run([sys.executable, "-c", code], check=True)
