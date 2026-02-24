## 1. Dependency setup

- [x] 1.1 Add `postgast` to `[project.dependencies]` in `pyproject.toml` and run `make install`

## 2. Comparator — identity extraction (`_compare.py`)

- [x] 2.1 Replace `_parse_function_names()` to use `postgast.extract_function_identity()` instead of `_FUNCTION_RE`
- [x] 2.2 Replace `_parse_trigger_identities()` to use `postgast.extract_trigger_identity()` instead of `_TRIGGER_RE`
- [x] 2.3 Remove `_FUNCTION_RE`, `_TRIGGER_RE`, `_IDENT`, `_dequote_ident()`, and the `re` import (if no longer used)

## 3. Canonicalization — OR REPLACE injection (`_canonicalize.py`)

- [x] 3.1 Replace `_ensure_or_replace()` to delegate to `postgast.ensure_or_replace()`
- [x] 3.2 Remove `_CREATE_FUNCTION_RE`, `_CREATE_TRIGGER_RE`, `_CREATE_OR_REPLACE_RE`, and the `re` import

## 4. Render — DROP generation (`_render.py`)

- [x] 4.1 Update `_render_drop_function` to use `postgast.to_drop(op.current.definition)`
- [x] 4.2 Update `_render_drop_trigger` to use `postgast.to_drop(op.current.definition)`
- [x] 4.3 Update the DROP half of `_render_replace_trigger` to use `postgast.to_drop(op.current.definition)`

## 5. Tests

- [x] 5.1 Update identity-extraction tests for postgast-derived output
- [x] 5.2 Update render tests for postgast-derived DROP output
- [x] 5.3 Add edge-case tests: quoted identifiers, multiline DDL, dollar-quoted bodies, `CREATE OR REPLACE` passthrough
- [x] 5.4 Run `make lint` and `make test` — verify clean pass

## 6. Cleanup verification

- [x] 6.1 Grep `src/` for `_FUNCTION_RE`, `_TRIGGER_RE`, `_CREATE_FUNCTION_RE`, `_CREATE_TRIGGER_RE`, `_dequote_ident` —
  confirm no matches
