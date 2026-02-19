def test_package_importable():
    import alembic_pg_autogen

    assert hasattr(alembic_pg_autogen, "__all__")


def test_extension_modules_importable():
    import alembic_pg_autogen._compare
    import alembic_pg_autogen._ops
    import alembic_pg_autogen._render

    assert alembic_pg_autogen._compare is not None
    assert alembic_pg_autogen._ops is not None
    assert alembic_pg_autogen._render is not None
