def test_package_importable():
    import alembic_pg_autogen

    assert hasattr(alembic_pg_autogen, "__all__")


def test_extension_modules_importable():
    import alembic_pg_autogen.compare
    import alembic_pg_autogen.ops
    import alembic_pg_autogen.render

    assert alembic_pg_autogen.compare is not None
    assert alembic_pg_autogen.ops is not None
    assert alembic_pg_autogen.render is not None


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
