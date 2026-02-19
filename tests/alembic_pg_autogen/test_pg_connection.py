import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, insert, select
from sqlalchemy.engine import Engine


@pytest.mark.integration
def test_pg_create_and_query(pg_engine: Engine):
    metadata = MetaData()
    test_table = Table(
        "test_table",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String(50)),
    )
    metadata.create_all(pg_engine)

    with pg_engine.connect() as conn:
        conn.execute(insert(test_table).values(id=1, name="hello"))
        conn.commit()
        result = conn.execute(select(test_table)).fetchone()

    assert result is not None
    assert result.id == 1
    assert result.name == "hello"

    metadata.drop_all(pg_engine)
