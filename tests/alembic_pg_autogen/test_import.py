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
