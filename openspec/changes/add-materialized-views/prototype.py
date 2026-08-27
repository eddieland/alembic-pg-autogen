"""Prove out high-level concepts for materialized view support before writing the spec.

Each PROOF block tests one assumption the spec will depend on. Run against the throwaway
PostgreSQL 16 instance at /tmp/pgmv port 55432.
"""

from __future__ import annotations

import traceback

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

URL = "postgresql+psycopg://postgres@/postgres?host=/tmp/pgmv&port=55432"

engine = create_engine(URL)

MATVIEW_QUERY = """\
SELECT
    n.nspname AS schema,
    c.relname AS name,
    c.relispopulated AS is_populated,
    'CREATE MATERIALIZED VIEW ' || quote_ident(n.nspname) || '.' || quote_ident(c.relname)
        || ' AS' || chr(10) || pg_get_viewdef(c.oid, true) AS definition
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'm'
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
ORDER BY n.nspname, c.relname
"""

INDEX_QUERY = """\
SELECT pg_get_indexdef(i.indexrelid) AS indexdef
FROM pg_catalog.pg_index i
JOIN pg_catalog.pg_class c ON c.oid = i.indrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'm' AND n.nspname = 'public' AND c.relname = :name
ORDER BY pg_get_indexdef(i.indexrelid)
"""


def proof(title: str) -> None:
    print(f"\n{'=' * 80}\nPROOF: {title}\n{'=' * 80}")


with engine.connect() as conn:
    conn.execute(text("CREATE TABLE t (id int primary key, amount numeric, category text)"))
    conn.commit()

with engine.connect() as conn:
    # ------------------------------------------------------------------
    proof("1. pg_get_viewdef() works for relkind='m'; existing view query pattern extends")
    conn.execute(text("CREATE MATERIALIZED VIEW mv1 AS SELECT category, sum(amount) AS total FROM t GROUP BY category"))
    rows = conn.execute(text(MATVIEW_QUERY)).all()
    for r in rows:
        print(f"schema={r.schema} name={r.name} populated={r.is_populated}")
        print(r.definition)

    # ------------------------------------------------------------------
    proof("2. CREATE OR REPLACE MATERIALIZED VIEW does not exist")
    sp = conn.begin_nested()
    try:
        conn.execute(text("CREATE OR REPLACE MATERIALIZED VIEW mv1 AS SELECT 1 AS x"))
        print("UNEXPECTED: succeeded")
    except ProgrammingError as e:
        print(f"errored as expected: {e.orig}")
    finally:
        sp.rollback()

    # ------------------------------------------------------------------
    proof("3. IF NOT EXISTS silently keeps the OLD definition (wrong for canonicalization)")
    sp = conn.begin_nested()
    conn.execute(text("CREATE MATERIALIZED VIEW IF NOT EXISTS mv1 AS SELECT 42 AS answer"))
    readback = conn.execute(text(MATVIEW_QUERY)).all()
    print("definition after IF NOT EXISTS with different query:")
    print(readback[0].definition)
    print("=> still the old query, so canonicalization must DROP first" if "sum" in readback[0].definition else "??")
    sp.rollback()

    # ------------------------------------------------------------------
    proof("4. DROP + CREATE inside a savepoint canonicalizes and rolls back cleanly")
    conn.execute(text("CREATE UNIQUE INDEX mv1_category_idx ON mv1 (category)"))
    sp = conn.begin_nested()
    conn.execute(text("DROP MATERIALIZED VIEW IF EXISTS mv1"))
    conn.execute(text("CREATE MATERIALIZED VIEW mv1 AS SELECT   category  ,  sum(amount)   AS total FROM t GROUP BY category WITH NO DATA"))
    canonical = conn.execute(text(MATVIEW_QUERY)).all()[0].definition
    sp.rollback()
    print("canonical definition read back inside savepoint:")
    print(canonical)
    after = conn.execute(text(MATVIEW_QUERY)).all()[0]
    idx_after = [r.indexdef for r in conn.execute(text(INDEX_QUERY), {"name": "mv1"})]
    print(f"\nafter rollback: populated={after.is_populated}, indexes restored={idx_after}")

    # ------------------------------------------------------------------
    proof("5. WITH NO DATA skips query execution (side-effect query does not run)")
    conn.execute(
        text(
            "CREATE FUNCTION boom() RETURNS int LANGUAGE plpgsql AS "
            "$$ BEGIN RAISE EXCEPTION 'query was executed'; END $$"
        )
    )
    sp = conn.begin_nested()
    try:
        conn.execute(text("CREATE MATERIALIZED VIEW mv_boom AS SELECT boom() AS b WITH NO DATA"))
        d = conn.execute(text(MATVIEW_QUERY)).all()
        print(f"created WITH NO DATA without running boom(); {len(d)} matviews visible")
        print([r.name for r in d])
    except DBAPIError as e:
        print(f"UNEXPECTED error: {e.orig}")
    finally:
        sp.rollback()
    sp = conn.begin_nested()
    try:
        conn.execute(text("CREATE MATERIALIZED VIEW mv_boom2 AS SELECT boom() AS b"))
        print("UNEXPECTED: WITH DATA create over boom() succeeded")
    except DBAPIError as e:
        print(f"WITH DATA (default) runs the query and fails as expected: {e.orig}")
    finally:
        sp.rollback()

    # ------------------------------------------------------------------
    proof("6. pg_get_viewdef canonical text is identical for WITH DATA vs WITH NO DATA")
    sp = conn.begin_nested()
    conn.execute(text("CREATE MATERIALIZED VIEW mv_d AS SELECT id FROM t"))
    conn.execute(text("CREATE MATERIALIZED VIEW mv_nd AS SELECT id FROM t WITH NO DATA"))
    got = {
        r.name: (r.definition.split("AS\n", 1)[1], r.is_populated)
        for r in conn.execute(text(MATVIEW_QUERY))
        if r.name in ("mv_d", "mv_nd")
    }
    sp.rollback()
    same = got["mv_d"][0] == got["mv_nd"][0]
    print(f"bodies identical={same}; populated flags: mv_d={got['mv_d'][1]}, mv_nd={got['mv_nd'][1]}")

    # ------------------------------------------------------------------
    proof("7. Equivalent but differently formatted DDL canonicalizes to identical text")
    sp = conn.begin_nested()
    conn.execute(text("DROP MATERIALIZED VIEW mv1"))
    conn.execute(
        text("create materialized view MV1 as select CATEGORY, SUM(amount) total from public.t group by category with no data")
    )
    variant = conn.execute(text(MATVIEW_QUERY)).all()[0].definition
    sp.rollback()
    sp = conn.begin_nested()
    conn.execute(text("DROP MATERIALIZED VIEW mv1"))
    conn.execute(
        text("CREATE MATERIALIZED VIEW mv1 AS\n  SELECT category,\n         sum(amount) AS total\n  FROM t\n  GROUP BY category\n  WITH NO DATA")
    )
    variant2 = conn.execute(text(MATVIEW_QUERY)).all()[0].definition
    sp.rollback()
    print(f"identical={variant == variant2}")
    print(variant)

    # ------------------------------------------------------------------
    proof("8. DROP MATERIALIZED VIEW silently drops its indexes; pg_get_indexdef re-creates them")
    idx = [r.indexdef for r in conn.execute(text(INDEX_QUERY), {"name": "mv1"})]
    print(f"indexes before: {idx}")
    sp = conn.begin_nested()
    conn.execute(text("DROP MATERIALIZED VIEW mv1"))
    conn.execute(text("CREATE MATERIALIZED VIEW mv1 AS SELECT category, sum(amount) AS total FROM t GROUP BY category WITH NO DATA"))
    idx_gone = [r.indexdef for r in conn.execute(text(INDEX_QUERY), {"name": "mv1"})]
    print(f"indexes after drop+create: {idx_gone}")
    for stmt in idx:
        conn.execute(text(stmt))
    idx_back = [r.indexdef for r in conn.execute(text(INDEX_QUERY), {"name": "mv1"})]
    print(f"indexes after replaying pg_get_indexdef output: {idx_back}")
    sp.rollback()

    # ------------------------------------------------------------------
    proof("9. REFRESH CONCURRENTLY requires a unique index (motivates index preservation)")
    conn.commit()  # make mv1 + unique index durable for refresh test
    with engine.connect() as c2:
        try:
            c2.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv1"))
            print("refresh concurrently OK with unique index present")
        except DBAPIError as e:
            print(f"refresh concurrently failed: {e.orig}")
    with engine.connect() as c2:
        c2.execute(text("DROP INDEX mv1_category_idx"))
        try:
            c2.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv1"))
            print("UNEXPECTED: refresh concurrently OK without unique index")
        except DBAPIError as e:
            print(f"without unique index, fails as expected: {e.orig}")
        c2.rollback()

    # ------------------------------------------------------------------
    proof("10. A regular view can select FROM a materialized view (ordering concern)")
    sp = conn.begin_nested()
    try:
        conn.execute(text("CREATE VIEW v_on_mv AS SELECT * FROM mv1"))
        print("regular view over matview: OK (so fixed views-before-matviews order is a limitation)")
    except DBAPIError as e:
        print(f"failed: {e.orig}")
    finally:
        sp.rollback()

    # ------------------------------------------------------------------
    proof("11. ALTER MATERIALIZED VIEW cannot change the defining query")
    sp = conn.begin_nested()
    try:
        conn.execute(text("ALTER MATERIALIZED VIEW mv1 AS SELECT 1 AS x"))
        print("UNEXPECTED: succeeded")
    except ProgrammingError as e:
        print(f"errored as expected: {e.orig}")
    finally:
        sp.rollback()

# ----------------------------------------------------------------------
proof("12. postgast: parsing + ensure_or_replace/to_drop behavior on matview DDL")
import postgast
from postgast import pg_query_pb2

ddl = "CREATE MATERIALIZED VIEW analytics.mv1 AS SELECT category FROM t"
tree = postgast.parse(ddl)
stmts = [type(getattr(n, n.WhichOneof("node"))).__name__ for n in (s.stmt for s in tree.stmts)]
print(f"node type for CREATE MATERIALIZED VIEW: {stmts}")
ctas = next(postgast.find_nodes(tree, pg_query_pb2.CreateTableAsStmt), None)
if ctas is not None:
    print(f"objtype={pg_query_pb2.ObjectType.Name(ctas.objtype)}")
    print(f"schema={ctas.into.rel.schemaname!r} name={ctas.into.rel.relname!r}")
    print(f"skip_data (WITH NO DATA)={ctas.into.skip_data}")

try:
    print(f"ensure_or_replace: {postgast.ensure_or_replace(ddl)!r}")
except Exception as e:
    print(f"ensure_or_replace raised: {type(e).__name__}: {e}")
try:
    print(f"to_drop: {postgast.to_drop(ddl)!r}")
except Exception as e:
    print(f"to_drop raised: {type(e).__name__}: {e}")

# unqualified name
tree2 = postgast.parse("CREATE MATERIALIZED VIEW mv2 AS SELECT 1 WITH NO DATA")
ctas2 = next(postgast.find_nodes(tree2, pg_query_pb2.CreateTableAsStmt), None)
print(f"unqualified: schema={ctas2.into.rel.schemaname!r} name={ctas2.into.rel.relname!r} skip_data={ctas2.into.skip_data}")

# make sure plain CREATE TABLE AS is distinguishable from CREATE MATERIALIZED VIEW
tree3 = postgast.parse("CREATE TABLE t2 AS SELECT 1")
ctas3 = next(postgast.find_nodes(tree3, pg_query_pb2.CreateTableAsStmt), None)
print(f"CREATE TABLE AS objtype={pg_query_pb2.ObjectType.Name(ctas3.objtype)} (vs matview above)")

print("\nALL PROOFS DONE")
