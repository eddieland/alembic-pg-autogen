## ADDED Requirements

### Requirement: Ignoring object types during canonicalization

`canonicalize()` SHALL accept `IGNORED` in place of a DDL sequence for `function_ddl`, `view_ddl`, or `trigger_ddl`. For
an ignored object type it SHALL execute no DDL and SHALL NOT query that type's catalog during readback, leaving the
corresponding `CanonicalState` field empty.

#### Scenario: Ignored object type is not read back

- **WHEN** `canonicalize(conn, function_ddl=[...], view_ddl=IGNORED, trigger_ddl=IGNORED)` is called against a database
  that already contains views and triggers
- **THEN** the returned `CanonicalState` contains the canonical form of the declared functions
- **AND** its `views` and `triggers` fields are empty
- **AND** no `inspect_views` or `inspect_triggers` query is issued

#### Scenario: Empty sequence still reads back

- **WHEN** `canonicalize(conn, view_ddl=())` is called against a database containing views
- **THEN** the returned `CanonicalState.views` contains the existing views
- **AND** the behavior is unchanged from before the sentinel existed

#### Scenario: Warnings account for ignored types

- **WHEN** an object type is ignored
- **THEN** no "canonicalization produced no <type>" warning is logged for it

### Requirement: Convenience wrappers ignore unrelated object types

`canonicalize_functions()`, `canonicalize_views()`, and `canonicalize_triggers()` SHALL ignore the two object types they
do not return, so each wrapper issues a single catalog readback query.

#### Scenario: Single readback per wrapper

- **WHEN** `canonicalize_views(conn, ddl)` is called
- **THEN** only the view catalog is read back
- **AND** the returned `ViewInfo` sequence is identical to what the wrapper returned before the sentinel existed
