## Context

The library handles functions, triggers, and views through one pipeline that it owns completely. It handles check
constraints through a second pattern. In that pattern, Alembic decides if an object exists. This library decides if two
objects with the same name have the same definition. Indexes are a third case. The difference affects each decision
below.

Alembic compares some parts of an index, but not all parts. `_ix_constraint_sig` compares columns, expressions, and
uniqueness. Through `PostgresqlImpl._dialect_options`, it also compares `nulls_not_distinct`. The signature does not
contain the `WHERE` predicate, the access method, `INCLUDE`, or operator classes. Alembic compares expressions through
`_cleanup_index_expr`. That function lowercases the text. It also removes quotes, casts, sort modifiers, and spaces
before it compares the strings.

For check constraints, `compare_check_constraint()` always returns `Equal()`. Thus the two comparators never examine the
same constraint. For indexes, Alembic sometimes finds a change and emits operations. The design must say what happens
when both comparators examine the same index.

## Goals / Non-Goals

**Goals:**

- Find changed predicates, access methods, operator classes, `INCLUDE` lists, and expressions on indexes in
  `target_metadata`. Emit a drop/create pair for each changed index.
- Normalize definitions through PostgreSQL, not through string rules.
- Work together with the Alembic index comparator. Never emit two pairs for one index.
- Keep the round trip fast enough to run on each autogenerate against a development database.
- Let the generated migration use `CREATE INDEX CONCURRENTLY`.

**Non-Goals:**

- A `pg_indexes` channel for DDL strings
- Index existence, unnamed indexes, and indexes that implement constraints
- A second decision on an index that Alembic already changed
- A concurrent index build during autogenerate

## Decisions

### D1: Read the desired state from `target_metadata`, not from DDL strings

The reason is the same as for check constraints. Indexes already exist in SQLAlchemy models. Alembic already manages
their existence from the models. A separate DDL channel needs an ownership model: table scope, exclusion of metadata
names, and warnings for indexes declared twice. Each rule in such a model can drop an index that the user wants.

`target_metadata` needs no ownership model. Alembic owns existence. This library owns the definition.

**Trade-off:** This library does not support indexes on tables outside `target_metadata`. Alembic also does not see
those indexes.

### D2: Compare a *shape*, not the full `pg_get_indexdef()` output

`pg_get_indexdef()` emits
`CREATE [UNIQUE] INDEX <name> ON <schema>.<table> USING <am> (<keys>) [INCLUDE (...)] [NULLS NOT DISTINCT] [WITH (...)] [WHERE ...]`.
The start of the statement is the identity. The comparator matches identity separately, by name. The rest of the
statement is the definition that this comparator compares. `IndexInfo` therefore holds `(unique, shape)` as its payload.
The *shape* is the text from `USING` to the end.

The comparator reads the desired definition from a different table (D4). A comparison that includes the table reference
can therefore never match. For this reason, the identity must be removed.

`IndexInfo` holds `UNIQUE` in a separate field. `UNIQUE` is in the start of the statement, before the name, so the shape
does not contain it. The other catalog types use this rule: the identity is every field except the last. `IndexInfo` is
the one exception, and its docstring says so.

**SQL removes the prefix, and SQL checks the prefix first.** The query builds the prefix with `quote_ident()`. It
compares the prefix with the first characters of the definition through `left()`. The query removes the prefix only when
the two match. A split on the first `" USING "` fails in a rare but real case. An index named `"ix USING y"` on a table
named `"tbl USING x"` has its first `" USING "` inside a quoted identifier. If the prefix does not match, the query
omits the index and logs a warning. An unstripped shape can never equal a canonical shape, so it causes a permanent
false difference.

### D3: Emit Alembic operations on the default path

The comparator appends `ops.DropIndexOp.from_index(reflected)` and `ops.CreateIndexOp.from_index(metadata_index)`.

SQLAlchemy reflection for PostgreSQL returns `postgresql_ops`, `postgresql_using`, `postgresql_where`,
`postgresql_include`, and `postgresql_with`. Thus the reflected index gives a correct `op.create_index()` for the
downgrade. The generated output is ordinary Alembic:

```python
def upgrade() -> None:
    op.drop_index(op.f("ix_t_a"), table_name="t", postgresql_include=[])
    op.create_index("ix_t_a", "t", ["a"], unique=False, postgresql_where=sa.text("deleted_at IS NULL"))
```

**Alternative considered:** Custom operations that render `op.execute()` with the canonical DDL. This library uses that
pattern for functions, triggers, and views. We rejected it for the same reasons as for check constraints. It duplicates
operations that Alembic already has. It gives raw SQL where users expect `op.create_index()`. It also ignores
`include_object` filters. The `op.execute()` pattern exists because Alembic has no `CreateFunctionOp`. Alembic has a
`CreateIndexOp`.

### D4: Test the index on an empty `TEMP` copy, not on the real table

For each candidate index, the canonicalizer does these steps:

1. Open a savepoint.
1. Create `TEMP TABLE <table> (LIKE <real table>)`.
1. Run the compiled `CREATE INDEX` of the metadata index against the copy.
1. Read the definition of the test index back.
1. Roll back the savepoint.

Here the check constraint design does not apply. For check constraints, a test on the real table cost nothing, because
`NOT VALID` skips the validation scan. That design doc rejected a copy of the table for this reason. `CREATE INDEX` has
no `NOT VALID` option, so it builds the index. We measured a table with 500,000 rows. An expression index took 1218 ms.
A GIN index took 2138 ms. On an empty copy, each index took 0.8 ms, and the copy took 1.4 ms to create. On the real
table, autogenerate stops for seconds for each index.

The copy also removes a risk that the check constraint design documents. A test on the real table takes a lock.
PostgreSQL holds the lock until the transaction ends. Thus the table stays locked for the rest of the autogenerate run.
This design only reads the column definitions of the real table.

`CREATE TABLE ... (LIKE ...)` copies the column names, types, and collations that the deparse uses. We checked the
output for expression, predicate, operator class, `INCLUDE`, GIN, and `NULLS NOT DISTINCT` indexes. The shapes were
identical byte for byte.

**Two mechanisms send the DDL to the copy.** Each mechanism alone misses one case. First, the copy has the same name as
the real table. PostgreSQL searches `pg_temp` first, so an unqualified `ON t` finds the copy. This covers metadata with
no explicit schema. Second, metadata with an explicit schema compiles to `ON myschema.t`, which finds the real table.
Thus the DDL runs under `schema_translate_map={None: "pg_temp", schema: "pg_temp"}`. A map to `None` does not work,
because SQLAlchemy then renders the default schema as `public`.

**Alternative considered:** Drop and create the real index inside the savepoint, with its own name. Then the definitions
compare with no split. We rejected this. It builds the index twice, and it removes a real index for a short time.

**The query that reads the test index accepts two spellings.** `pg_get_indexdef()` always writes the schema of the
table. We confirmed this for temporary and regular tables under each `search_path`. But the spelling of a temporary
schema changed in PostgreSQL 15. PostgreSQL 15 uses `get_namespace_name_or_temp()`, which writes `pg_temp` in place of
the real name `pg_temp_3`. PostgreSQL 14 writes `pg_temp_3`. The query therefore builds both prefixes and removes the
prefix that matches. It does not ask the server for its version. If neither prefix matches, the query returns NULL, and
the comparator reports the index as unchanged (D7).

### D5: Run last, and skip each index that Alembic already changed

The Alembic index comparator registers at `DispatchPriority.MEDIUM`. This comparator registers at `LAST`. Thus it runs
after Alembic fills `modify_table_ops`. Before it compares, it collects the `index_name` of each operation in that list.
It skips those indexes.

The two comparators can examine the same index. This rule is the only way to keep their results separate. Alembic owns
existence and each index that its own comparison rejected. This comparator adds a result only where Alembic found no
change. In our tests, Alembic never reported a difference for two identical indexes. Thus the skip never hides a real
difference.

**Alternative considered:** Register under the Alembic `"indexes"` subgroup with `qualifier="postgresql"` and return
`STOP`. The dispatcher supports this, and it replaces the Alembic comparison. We rejected it.
`_compare_indexes_and_uniques` also owns unique constraints and index existence. A `STOP` requires a new implementation
of both.

### D6: `CONCURRENTLY` is an option for rendering, and it wraps the Alembic call

With `pg_index_concurrently=True`, the comparator wraps each operation in `CreateIndexConcurrentlyOp` or
`DropIndexConcurrentlyOp`. These classes set `postgresql_concurrently=True` on the wrapped operation. They render it
through `render_op()`, the Alembic renderer, inside `op.get_context().autocommit_block()`:

```python
with op.get_context().autocommit_block():
    op.create_index("ix_t_a", "t", ["a"], postgresql_where=sa.text("..."), postgresql_concurrently=True)
```

The block is necessary. PostgreSQL refuses `CREATE INDEX CONCURRENTLY` inside a transaction block, and Alembic runs each
migration in a transaction. The body comes from the Alembic renderer, so it keeps each `postgresql_*` keyword. The
option adds only the block.

The option affects the *migration*, not autogenerate. The canonicalization test always runs an ordinary `CREATE INDEX`
on an empty copy. A concurrent build there only adds time.

### D7: If a step fails, report the index as unchanged

Some indexes do not compile or do not apply. For example, an expression can use a column that does not exist yet. An
access method can refuse an operator class. For such an index, the comparator logs a warning and emits no operation.
Each test runs in its own nested savepoint. Thus one bad index does not stop the comparison of the other indexes on the
table.

The comparator catches `SQLAlchemyError`, not `DBAPIError`. Compilation happens inside `execute()`. Thus an index that
SQLAlchemy cannot render raises `CompileError` before the server receives a statement. When the code caught only the
server error, one such index stopped the whole run.

## Risks / Trade-offs

**[The copy only reproduces the deparse context of the real table]** → `LIKE` copies column names, types, and
collations, but the copy is a reconstruction. **Mitigation:** The output was identical byte for byte for each supported
feature. A mismatch gives an extra drop/create pair. A reviewer can see that pair, so the result is not silent drift.

**[The skip keeps the lossy expression comparison of Alembic]** → When `_cleanup_index_expr` wrongly reports two
expressions as equal, Alembic emits nothing, and this comparator finds the difference. When Alembic emits operations for
the wrong reason, this comparator does nothing. **Mitigation:** Our tests found no such false positive. The failure
gives an extra migration, not drift.

**[One savepoint and one copy for each table]** → A schema with many tables that have indexes runs several statements
for each table. **Mitigation:** The canonicalizer tests only tables with indexes on both sides. The copy is empty, so
the cost is the statements, not the index builds.

**\[The connection must be able to run `CREATE TEMP TABLE`\]** → A connection without permission for temporary tables
cannot run the test. **Mitigation:** The comparator catches the failure and reports each index on the table as unchanged
(D7).

**[Depends on the dispatch order of Alembic]** → D5 requires that `DispatchPriority.LAST` runs after `MEDIUM`. It also
requires that Alembic index operations have `index_name`. **Mitigation:** Both are public API. Users can also disable
this plugin alone.

## Open Questions

_(none; the decisions above resolve all questions)_
