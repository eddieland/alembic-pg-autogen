"""Prove the assumptions the data table row design depends on, before writing the spec.

Each PROOF block tests one assumption. Run against a throwaway PostgreSQL 16 instance. The script creates its own
objects in the ``public`` schema of the target database and leaves them there for inspection.
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from sqlalchemy import (
    ARRAY,
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    Table,
    Text,
    Uuid,
    create_engine,
    insert,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import CreateTable

URL = "postgresql+psycopg://postgres@/postgres?host=/run/pgs&port=54329"

engine = create_engine(URL)


def proof(title: str) -> None:
    """Print a banner for one proof block."""
    print(f"\n{'=' * 100}\nPROOF: {title}\n{'=' * 100}")


with engine.connect() as conn:
    conn.execute(text("DROP TABLE IF EXISTS order_status, typed, serial_probe, ci CASCADE"))
    conn.execute(text("DROP TYPE IF EXISTS mood"))
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
    conn.execute(text("CREATE TYPE mood AS ENUM ('sad', 'ok', 'happy')"))
    conn.execute(
        text(
            """
            CREATE TABLE typed (
                code text PRIMARY KEY,
                n integer,
                amount numeric(10, 2),
                free numeric,
                f float8,
                b boolean,
                j jsonb,
                j2 json,
                d date,
                ts timestamptz,
                arr text[],
                u uuid,
                by bytea,
                m mood,
                iv interval,
                money money
            )
            """
        )
    )
    conn.commit()

with engine.connect() as conn:
    # ------------------------------------------------------------------
    proof("1. ::text gives one canonical form per value; jsonb normalizes; boolean prints true/false")
    conn.execute(
        text(
            """
            INSERT INTO typed VALUES (
                'a', 30, 1.5, 1.5, 1.5, true, '{"b": 1,   "a": [1,2]}', '{"b": 1,   "a": [1,2]}',
                '2024-01-02', '2024-01-02T03:04:05+02:00', ARRAY['x', 'y z', 'it''s'], '6ba7b810-9dad-11d1-80b4-00c04fd430c8',
                '\\x0102', 'happy', '1 day 2 hours', 12.34
            )
            """
        )
    )
    row = conn.execute(
        text(
            "SELECT n::text, amount::text, free::text, f::text, b::text, j::text, j2::text, d::text, ts::text, "
            "arr::text, u::text, by::text, m::text, iv::text, money::text FROM typed"
        )
    ).one()
    for k, v in row._mapping.items():
        print(f"  {k:>7} -> {v!r}")

    # ------------------------------------------------------------------
    proof("2. json (not jsonb) has no equality operator, but ::text still works for comparison in Python")
    sp = conn.begin_nested()
    try:
        conn.execute(text("SELECT j2 = j2 FROM typed"))
        print("  UNEXPECTED: json equality worked")
    except DBAPIError as e:
        print(f"  json = json raises: {type(e.orig).__name__}: {str(e.orig).splitlines()[0]}")
    finally:
        sp.rollback()
    print("  ROW(...) IS DISTINCT FROM would hit the same error, so the diff compares ::text in Python")

    # ------------------------------------------------------------------
    proof("3. A temp table created via CREATE TABLE pg_temp.x inside a savepoint is gone after rollback")
    sp = conn.begin_nested()
    conn.execute(text("CREATE TABLE pg_temp.probe (x int)"))
    print(
        "  relpersistence =", conn.execute(text("SELECT relpersistence FROM pg_class WHERE relname = 'probe'")).scalar()
    )
    sp.rollback()
    print("  after rollback:", conn.execute(text("SELECT count(*) FROM pg_class WHERE relname = 'probe'")).scalar())

    # ------------------------------------------------------------------
    proof("4. A typed SQLAlchemy Table in schema pg_temp adapts Python values through the driver; ::text reads back")
    meta = MetaData()
    desired = Table(
        "apg_desired_typed",
        meta,
        Column("code", Text),
        Column("n", Integer),
        Column("amount", Numeric(10, 2)),
        Column("free", Numeric),
        Column("b", Boolean),
        Column("j", JSONB),
        Column("d", Date),
        Column("ts", DateTime(timezone=True)),
        Column("arr", ARRAY(Text)),
        Column("u", Uuid),
        Column("by", LargeBinary),
        Column("m", Enum("sad", "ok", "happy", name="mood", create_type=False)),
        schema="pg_temp",
    )
    sp = conn.begin_nested()
    conn.execute(CreateTable(desired))
    conn.execute(
        insert(desired),
        [
            {
                "code": "a",
                "n": 30,
                "amount": decimal.Decimal("1.5"),
                "free": decimal.Decimal("1.50"),
                "b": True,
                "j": {"b": 1, "a": [1, 2]},
                "d": datetime.date(2024, 1, 2),
                "ts": datetime.datetime(2024, 1, 2, 3, 4, 5, tzinfo=datetime.timezone(datetime.timedelta(hours=2))),
                "arr": ["x", "y z", "it's"],
                "u": uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8"),
                "by": b"\x01\x02",
                "m": "happy",
            }
        ],
    )
    row = conn.execute(
        text(
            "SELECT code::text, n::text, amount::text, free::text, b::text, j::text, d::text, ts::text, arr::text, "
            "u::text, by::text, m::text FROM pg_temp.apg_desired_typed"
        )
    ).one()
    for k, v in row._mapping.items():
        print(f"  {k:>7} -> {v!r}")
    sp.rollback()
    print("  CREATE TABLE DDL used:", str(CreateTable(desired).compile(engine)).strip().splitlines()[0])

    # ------------------------------------------------------------------
    proof("5. Quoted literals with no type resolve to the column type in INSERT, UPDATE, and WHERE (no casts needed)")
    sp = conn.begin_nested()
    conn.execute(
        text(
            """
            INSERT INTO typed (code, n, amount, free, f, b, j, j2, d, ts, arr, u, by, m, iv)
            VALUES ('b', '31', '2.50', '2.5', '2.5', 'false', '{"a": [1, 2], "b": 1}', '{"x": 1}',
                    '2024-01-03', '2024-01-02 01:04:05+00', '{x,"y z","it''s"}',
                    '6ba7b810-9dad-11d1-80b4-00c04fd430c9', '\\x0102', 'sad', '1 day 02:00:00')
            """
        )
    )
    conn.execute(text("UPDATE typed SET n = '32', b = 'true' WHERE code = 'b'"))
    print("  rows:", conn.execute(text("SELECT count(*) FROM typed WHERE code IN ('a', 'b')")).scalar())
    print("  n, b after update:", conn.execute(text("SELECT n, b FROM typed WHERE code = 'b'")).one())
    conn.execute(text("DELETE FROM typed WHERE code = 'b'"))
    sp.rollback()

    # ------------------------------------------------------------------
    proof(
        "6. Inserting a value's own ::text form back yields the same ::text (the fixed point that makes runs converge)"
    )
    cols = "n, amount, free, f, b, j, d, ts, arr, u, by, m, iv"
    src = conn.execute(
        text(f"SELECT {', '.join(c + '::text' for c in cols.split(', '))} FROM typed WHERE code = 'a'")
    ).one()
    sp = conn.begin_nested()
    placeholders = ", ".join(f":{c}" for c in cols.split(", "))
    conn.execute(
        text(f"INSERT INTO typed (code, {cols}) VALUES ('c', {placeholders})"),
        dict(zip(cols.split(", "), src, strict=True)),
    )
    dst = conn.execute(
        text(f"SELECT {', '.join(c + '::text' for c in cols.split(', '))} FROM typed WHERE code = 'c'")
    ).one()
    print("  identical after round trip:", tuple(src) == tuple(dst))
    sp.rollback()

    # ------------------------------------------------------------------
    proof("7. Values PostgreSQL calls equal can print differently: numeric scale and citext. One UPDATE then converges")
    print("  1.5 = 1.50 ->", conn.execute(text("SELECT 1.5::numeric = 1.50::numeric")).scalar())
    print(
        "  1.5::numeric::text, 1.50::numeric::text ->",
        conn.execute(text("SELECT 1.5::numeric::text, 1.50::numeric::text")).one(),
    )
    sp = conn.begin_nested()
    conn.execute(text("UPDATE typed SET free = '1.50' WHERE code = 'a'"))
    print(
        "  after UPDATE free = '1.50': ", conn.execute(text("SELECT free::text FROM typed WHERE code = 'a'")).scalar()
    )
    sp.rollback()
    sp = conn.begin_nested()
    conn.execute(text("CREATE TABLE ci (k citext PRIMARY KEY, v text)"))
    conn.execute(text("INSERT INTO ci VALUES ('Foo', 'x')"))
    print("  citext 'Foo' = 'foo' ->", conn.execute(text("SELECT k = 'foo' FROM ci")).scalar())
    print("  citext ::text ->", conn.execute(text("SELECT k::text FROM ci")).scalar())
    sp.rollback()

    # ------------------------------------------------------------------
    proof(
        "8. LIKE ... INCLUDING DEFAULTS copies nextval() of the real sequence; a rolled-back insert still advances it"
    )
    conn.execute(text("CREATE TABLE serial_probe (id serial PRIMARY KEY, code text)"))
    sp = conn.begin_nested()
    conn.execute(text("CREATE TEMP TABLE serial_probe_copy (LIKE serial_probe INCLUDING DEFAULTS)"))
    conn.execute(text("INSERT INTO serial_probe_copy (code) VALUES ('x'), ('y')"))
    sp.rollback()
    print(
        "  serial_probe_id_seq last_value after rollback:",
        conn.execute(text("SELECT last_value FROM serial_probe_id_seq")).scalar(),
    )
    print("  -> the desired-side temp table must carry column types only, never defaults")

    # ------------------------------------------------------------------
    proof("9. Doubling single quotes is the only escaping needed under standard_conforming_strings")
    print("  standard_conforming_strings =", conn.execute(text("SHOW standard_conforming_strings")).scalar())
    sp = conn.begin_nested()
    conn.execute(text("INSERT INTO typed (code) VALUES ('it''s a \\ backslash')"))
    print("  stored:", conn.execute(text("SELECT code FROM typed WHERE code LIKE 'it%'")).scalar())
    sp.rollback()

    # ------------------------------------------------------------------
    proof("10. Text forms that are safe to render without quotes, and ones that are not")
    for expr in ("30::int", "-1.5e-7::float8", "'NaN'::float8", "'Infinity'::float8", "true::boolean", "12.34::money"):
        print(f"  {expr:>18} ::text -> {conn.execute(text(f'SELECT ({expr})::text')).scalar()!r}")

    # ------------------------------------------------------------------
    proof(
        "11. A declared column that the catalog lacks: the current side reads NULL when the SELECT names only catalog columns"
    )
    exists = (
        conn
        .execute(
            text(
                "SELECT attname FROM pg_attribute WHERE attrelid = 'typed'::regclass AND attnum > 0 AND NOT attisdropped "
                "AND attname = ANY(:names)"
            ),
            {"names": ["code", "n", "brand_new"]},
        )
        .scalars()
        .all()
    )
    print("  declared [code, n, brand_new]; present in catalog:", exists)
    print("  -> brand_new compares as NULL on every current row, so an add_column and the UPDATEs share one migration")

    # ------------------------------------------------------------------
    proof("12. DELETE of a referenced row fails on the FK, which is why expand-only tables never emit DELETE")
    conn.execute(text("CREATE TABLE order_status (code text PRIMARY KEY, label text)"))
    conn.execute(text("INSERT INTO order_status VALUES ('new', 'New')"))
    conn.execute(text("CREATE TABLE orders (id int PRIMARY KEY, status text REFERENCES order_status (code))"))
    conn.execute(text("INSERT INTO orders VALUES (1, 'new')"))
    sp = conn.begin_nested()
    try:
        conn.execute(text("DELETE FROM order_status WHERE code = 'new'"))
    except DBAPIError as e:
        print(f"  {type(e.orig).__name__}: {str(e.orig).splitlines()[0]}")
    finally:
        sp.rollback()
    conn.rollback()
