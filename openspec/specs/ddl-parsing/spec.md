## ADDED Requirements

### Requirement: postgast as required runtime dependency

The package SHALL declare `postgast` as a required runtime dependency in `pyproject.toml`, alongside `alembic` and
`sqlalchemy>=2`. All DDL parsing, identity extraction, syntactic rewriting, and DROP generation SHALL be delegated to
postgast rather than implemented via regex or string manipulation within this package.

#### Scenario: Package installs postgast transitively

- **WHEN** a user runs `uv add alembic-pg-autogen`
- **THEN** `postgast` is installed as a transitive dependency
- **AND** `postgast.parse`, `postgast.ensure_or_replace`, `postgast.to_drop`, `postgast.extract_function_identity`, and
  `postgast.extract_trigger_identity` are all importable

#### Scenario: No fallback to regex parsing

- **WHEN** postgast is available (it always is, as a required dependency)
- **THEN** the package SHALL NOT maintain regex-based parsing as an alternative code path
- **AND** no `re.compile` patterns for DDL identity extraction SHALL exist in the codebase

### Requirement: Extract function identity from user-provided DDL

The comparator pipeline SHALL use `postgast.parse()` and `postgast.extract_function_identity()` to extract
`(schema, name)` pairs from user-provided function DDL strings. This replaces regex-based identity extraction.

#### Scenario: Schema-qualified function

- **WHEN** the user provides `"CREATE FUNCTION public.add(a int, b int) RETURNS int LANGUAGE sql AS $$ SELECT a+b $$"`
- **THEN** identity extraction produces `schema="public"`, `name="add"`

#### Scenario: Unqualified function falls back to connection default schema

- **WHEN** the user provides `"CREATE FUNCTION my_func() RETURNS void LANGUAGE sql AS $$ SELECT 1 $$"`
- **THEN** postgast returns `schema=None`, `name="my_func"`
- **AND** the comparator resolves `None` to the connection's `current_schema()`

#### Scenario: CREATE OR REPLACE function

- **WHEN** the user provides `"CREATE OR REPLACE FUNCTION audit.log_event() ..."`
- **THEN** identity extraction produces `schema="audit"`, `name="log_event"`

#### Scenario: Quoted identifiers

- **WHEN** the user provides `'CREATE FUNCTION "My Schema"."My Func"() ...'`
- **THEN** identity extraction produces `schema="My Schema"`, `name="My Func"` (quotes stripped, case preserved)

#### Scenario: Invalid DDL raises an error

- **WHEN** the user provides a string that is not a valid CREATE FUNCTION statement
- **THEN** a `postgast.PgQueryError` or `ValueError` is raised during identity extraction

### Requirement: Extract trigger identity from user-provided DDL

The comparator pipeline SHALL use `postgast.parse()` and `postgast.extract_trigger_identity()` to extract
`(schema, table_name, trigger_name)` triples from user-provided trigger DDL strings. This replaces regex-based identity
extraction.

#### Scenario: Schema-qualified trigger

- **WHEN** the user provides
  `"CREATE TRIGGER audit_trg AFTER INSERT ON public.orders FOR EACH ROW EXECUTE FUNCTION audit_fn()"`
- **THEN** identity extraction produces `schema="public"`, `table_name="orders"`, `trigger_name="audit_trg"`

#### Scenario: Unqualified trigger table falls back to connection default schema

- **WHEN** the user provides `"CREATE TRIGGER trg BEFORE UPDATE ON t FOR EACH ROW EXECUTE FUNCTION fn()"`
- **THEN** postgast returns `schema=None`, `table="t"`, `trigger="trg"`
- **AND** the comparator resolves `None` to the connection's `current_schema()`

#### Scenario: Trigger with multiline DDL

- **WHEN** the user provides a trigger DDL string spanning multiple lines with the `ON table` clause on a different line
  than `CREATE TRIGGER`
- **THEN** identity extraction succeeds (postgast parses the full SQL grammar, not line-by-line)

### Requirement: Inject OR REPLACE into DDL before savepoint execution

The canonicalization layer SHALL use `postgast.ensure_or_replace()` to rewrite `CREATE FUNCTION`, `CREATE TRIGGER`, and
`CREATE VIEW` statements to their `CREATE OR REPLACE` equivalents before executing them in the canonicalization
savepoint. This replaces regex-based rewriting.

#### Scenario: CREATE FUNCTION rewritten to CREATE OR REPLACE FUNCTION

- **WHEN** `postgast.ensure_or_replace("CREATE FUNCTION public.f() ...")` is called
- **THEN** it returns `"CREATE OR REPLACE FUNCTION public.f() ..."`

#### Scenario: CREATE TRIGGER rewritten to CREATE OR REPLACE TRIGGER

- **WHEN** `postgast.ensure_or_replace("CREATE TRIGGER t AFTER INSERT ON tbl ...")` is called
- **THEN** it returns `"CREATE OR REPLACE TRIGGER t AFTER INSERT ON tbl ..."`

#### Scenario: Already contains OR REPLACE

- **WHEN** `postgast.ensure_or_replace("CREATE OR REPLACE FUNCTION public.f() ...")` is called
- **THEN** it returns the input unchanged

#### Scenario: Works via AST round-trip, not text substitution

- **WHEN** the DDL contains comments, unusual whitespace, or dollar-quoted bodies with the word "CREATE" inside
- **THEN** `ensure_or_replace` correctly modifies only the statement's CREATE clause (it parses into an AST, sets the
  `replace` flag, and deparses back to SQL)

### Requirement: Generate DROP statements from CREATE DDL definitions

The render layer SHALL use `postgast.to_drop()` to generate DROP statements from the `definition` field of
`FunctionInfo` and `TriggerInfo` instances. This replaces per-type string interpolation of DROP statements.

#### Scenario: DROP FUNCTION from function definition

- **WHEN** `postgast.to_drop(function_info.definition)` is called with a canonical `pg_get_functiondef()` string
- **THEN** it returns `"DROP FUNCTION schema.name(arg_types)"` with correct argument types and quoting

#### Scenario: DROP TRIGGER from trigger definition

- **WHEN** `postgast.to_drop(trigger_info.definition)` is called with a canonical `pg_get_triggerdef()` string
- **THEN** it returns `"DROP TRIGGER trigger_name ON schema.table_name"` with correct quoting

#### Scenario: Quoting handled by postgast

- **WHEN** identifiers require quoting (reserved words, mixed case, special characters)
- **THEN** `postgast.to_drop()` produces correctly quoted identifiers via AST-level construction (not string
  interpolation)

#### Scenario: Render layer uses to_drop uniformly

- **WHEN** `DropFunctionOp`, `DropTriggerOp`, or the DROP half of `ReplaceTriggerOp` is rendered
- **THEN** the renderer calls `postgast.to_drop(op.current.definition)` to produce the DROP statement
- **AND** no per-type DROP string templates exist in the render module

### Requirement: No regex patterns for DDL parsing in the codebase

After integration, the package SHALL NOT contain `re.compile` patterns targeting DDL statement structure. Specifically,
the following patterns (or equivalents) SHALL be removed:

- `_CREATE_FUNCTION_RE`
- `_CREATE_TRIGGER_RE`
- `_CREATE_OR_REPLACE_RE`
- `_FUNCTION_RE`
- `_TRIGGER_RE`
- `_IDENT`

The `_dequote_ident()` helper SHALL also be removed, as postgast handles identifier normalization internally.

#### Scenario: No regex imports for DDL parsing

- **WHEN** `_canonicalize.py` and `_compare.py` are inspected
- **THEN** neither module imports `re` for the purpose of DDL pattern matching

#### Scenario: Grep for DDL regex finds nothing

- **WHEN** the `src/` directory is searched for `_FUNCTION_RE`, `_TRIGGER_RE`, `_CREATE_FUNCTION_RE`,
  `_CREATE_TRIGGER_RE`, or `_dequote_ident`
- **THEN** no matches are found

## MODIFIED Requirements

### Canonicalization: DDL is parsed before execution (modifies canonicalization spec)

The canonicalization spec previously required: "The module SHALL NOT parse, transform, or template the DDL strings in
any way — they are passed directly to PostgreSQL." This is superseded. DDL strings ARE parsed and transformed by
postgast (to inject OR REPLACE) before execution. The savepoint round-trip pattern is unchanged — DDL is still executed
in a savepoint, canonical forms are still read back from the catalog, and the savepoint is still rolled back.

### Render: DROP generation is DDL-derived (modifies alembic-render spec)

The render spec previously required DROP statements to be "constructed from the function's schema, name, and identity
args" (for functions) or "the trigger's name, schema, and table name" (for triggers) via field interpolation. This is
superseded. DROP statements are now generated by `postgast.to_drop(definition)` from the full CREATE DDL stored in the
`definition` field. The rendered output is functionally equivalent — valid DROP statements wrapped in `op.execute()` —
but the text may differ cosmetically (e.g., type name normalization by the PostgreSQL parser).
