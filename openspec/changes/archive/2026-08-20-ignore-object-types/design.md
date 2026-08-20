## Context

`_compare_pg_objects()` reads three desired-state keys from `autogen_context.opts` (`pg_functions`, `pg_triggers`,
`pg_views`), inspects the live catalog for all three object types, and diffs. Anything in the catalog that is not in the
desired set becomes a `DROP`. An absent key resolves to `()`, which is indistinguishable from "I declare no objects of
this type" — so adopting the library for one object type silently proposes dropping every object of the others.

We need a value that is distinguishable from an empty sequence and can be passed through the same configuration keys.

## Goals / Non-Goals

**Goals:**

- A per-object-type opt-out that is explicit at the configuration site
- Zero catalog work for an ignored type (this library exists to be fast on large schemas)
- No behavior change for existing configurations
- Type checkers can tell the sentinel apart from a DDL sequence

**Non-Goals:**

- Name- or pattern-based exclusions within an object type
- Changing what an absent configuration key means
- A "drops only" mode

## Decisions

### D1: A single-member enum as the sentinel

```python
class _IgnoredSentinel(enum.Enum):
    IGNORED = "IGNORED"


IGNORED: Final = _IgnoredSentinel.IGNORED
Ignored: TypeAlias = Literal[_IgnoredSentinel.IGNORED]
```

The enum-member idiom is the one pattern type checkers narrow through `is` comparisons, so `Sequence[str] | Ignored`
parameters narrow to `Sequence[str]` inside `if value is not IGNORED:` blocks with no casts. `__repr__` and `__str__`
are overridden so logs read `IGNORED` rather than `_IgnoredSentinel.IGNORED`.

**Alternatives considered:**

- A module-level `object()` — no narrowing, so every call site needs a cast.
- A distinct falsy singleton class — same narrowing problem, plus falsiness invites `if pg_views:` checks that quietly
  treat "ignored" as "empty".
- A separate `pg_ignore={"views"}` option — a second place to configure the same thing, easy to get out of sync with the
  DDL keys, and stringly typed.

### D2: `IGNORED` replaces the DDL sequence, rather than sitting inside it

The sentinel is the whole value of `pg_views`, not an element of the list. "Some views are managed, some are ignored" is
not expressible, and deliberately so: within a managed type, the declared set is the whole truth. That property is what
makes drops correct.

### D3: Ignore is enforced at both ends of the pipeline

For an ignored type the comparator skips `inspect_*` (current state stays empty) and `_filter_to_declared` returns an
empty desired set without parsing DDL or reading `current_schema()`. Empty vs. empty diffs to no operations, so the
diff, ops, and render layers need no changes at all — the sentinel never reaches them.

`canonicalize()` also accepts the sentinel and skips that type's readback query, which is what makes the ignore free
rather than merely correct. It is also why the `canonicalize_*` convenience wrappers can now ignore the two object types
they do not return.

### D4: Short-circuit when everything is ignored

If all three keys are `IGNORED` the comparator returns `CONTINUE` before touching the connection, matching the existing
"no keys configured at all" fast path.

## Risks / Trade-offs

- **`IGNORED` is truthy.** Code that checks `if pg_views:` would treat an ignored type as declared. Mitigated by using
  `is IGNORED` / `is not IGNORED` everywhere internally and by resolving the sentinel to `()` (via `_declared`) before
  any length or truthiness check.
- **The empty-sequence footgun remains.** `pg_views=[]` built from a dynamic source still drops every view. The docs
  call this out and suggest `collect_view_ddl() or IGNORED`; making an absent or empty key mean "ignored" would be a
  behavior change in its own right and is left out of scope.
- **Silent no-op risk.** A user who ignores a type and forgets will wonder why their new view is not detected. The
  comparator logs the ignored types at `INFO` on every autogenerate run.
