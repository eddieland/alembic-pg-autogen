# Sphinx / Read the Docs: Configuration & Plugin Research

Research into what professional Python OSS projects use for documentation, with recommendations for alembic-pg-autogen.

## Current State

alembic-pg-autogen already has a working Sphinx setup:

- **Theme:** Furo
- **Extensions:** `autodoc2`, `sphinx.ext.intersphinx`, `sphinx_copybutton`
- **Hosting:** Read the Docs (`.readthedocs.yaml` with uv-based builds)
- **Markup:** reStructuredText (`.rst`)
- **pyproject.toml `[project.optional-dependencies] docs`:** `sphinx>=8`, `furo>=2024.8`, `sphinx-copybutton>=0.5`,
  `sphinx-autodoc2>=0.5`

______________________________________________________________________

## What Professional OSS Projects Use

### Sphinx-Based Projects

| Project        | Theme                   | Extensions                                                                                        | Markup     |
| -------------- | ----------------------- | ------------------------------------------------------------------------------------------------- | ---------- |
| **Alembic**    | `sphinx_book_theme`     | `autodoc`, `intersphinx`, `changelog`, `sphinx_paramlinks`, `sphinx_copybutton`                   | RST        |
| **SQLAlchemy** | `zzzeeksphinx` (custom) | `autodoc`, `zzzeeksphinx`, `changelog`, `sphinx_paramlinks`, `sphinx_copybutton`                  | RST        |
| **attrs**      | **`furo`**              | `autodoc`, `napoleon`, `intersphinx`, `doctest`, `myst_parser`, `notfound.extension`, `towncrier` | RST + MyST |
| **Nox**        | `alabaster`             | `autodoc`, `napoleon`, `myst_parser`, `sphinx_tabs`                                               | RST + MyST |
| **pip**        | **`furo`**              | (packaging ecosystem standard)                                                                    | RST        |
| **Black**      | **`furo`**              | (packaging ecosystem standard)                                                                    | RST        |
| **setuptools** | **`furo`**              | (packaging ecosystem standard)                                                                    | RST        |

### MkDocs-Based Projects (for context)

| Project      | Theme               | Notable Plugins                                     |
| ------------ | ------------------- | --------------------------------------------------- |
| **Pydantic** | Material for MkDocs | `mkdocstrings`, `mike` (versioning), `llmstxt`      |
| **FastAPI**  | Material for MkDocs | `mkdocstrings`                                      |
| **uv**       | Material for MkDocs | `git-revision-date-localized`, `llmstxt`            |
| **Hatch**    | Material for MkDocs | `mkdocstrings`, `mike`, `mkdocs-click`, `glightbox` |

### Key Takeaway

Sphinx + Furo is the right choice for this project. It aligns with the SQLAlchemy/Alembic ecosystem and the Python
packaging community (pip, Black, setuptools, attrs all use Furo). MkDocs Material dominates the newer Astral/Pydantic
ecosystem but lacks Sphinx's `intersphinx` cross-referencing to Alembic and SQLAlchemy docs.

______________________________________________________________________

## Theme: Furo (Keep It)

Furo is the correct choice. Highlights:

- **Who uses it:** pip (the theme was written for pip), Black, setuptools, virtualenv, urllib3, attrs, Python
  Developer's Guide
- **Community rating:** Scored highest (2.95/4.0) in SymPy's cross-project Sphinx theme survey; sphinx-rtd-theme scored
  lowest (2.47/4.0)
- **Features:** Built-in dark/light toggle, responsive, CSS-variable customization, accessible syntax highlighting,
  right-sidebar TOC
- **Furo config to add** (connects "Edit on GitHub" links):

```python
html_theme_options = {
    "source_repository": "https://github.com/eddieland/alembic-pg-autogen",
    "source_branch": "main",
    "source_directory": "docs/",
}
```

______________________________________________________________________

## Extension Recommendations

### Tier 1 — Essential (add these)

| Extension                      | Purpose                                | Who Uses It                                   | Notes                                                                                                                                                                                 |
| ------------------------------ | -------------------------------------- | --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`sphinx.ext.napoleon`**      | Parse Google-style docstrings          | attrs, Nox, Scientific Python Guide           | **Must-have.** Our Ruff config uses `convention = "google"`. Napoleon translates Google-style docstrings to rST for Sphinx. Without it, autodoc can't render our docstrings properly. |
| **`sphinx.ext.viewcode`**      | "[source]" links to highlighted source | Widely used                                   | Lets readers jump from API docs to the actual implementation.                                                                                                                         |
| **`sphinx_autodoc_typehints`** | Render type annotations in docs        | Scientific Python Guide, many modern projects | Since we use BasedPyright and extensive type hints, this renders them in param descriptions automatically, avoiding duplication in docstrings.                                        |

### Tier 2 — Strongly Recommended

| Extension                 | Purpose                                | Who Uses It                | Notes                                                                                                                                        |
| ------------------------- | -------------------------------------- | -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| **`sphinxext-opengraph`** | OpenGraph meta tags for social sharing | Many RTD projects          | Trivial to add, significant impact when docs links are shared on GitHub, Slack, Discord. Generates `og:title`, `og:description`, `og:image`. |
| **`sphinx.ext.extlinks`** | Shorthand for external links           | Alembic, many projects     | Define `:issue:\`123\``and`:pr:\`456\`\` shortcuts for GitHub links.                                                                         |
| **`sphinx-design`**       | Cards, tabs, grids, badges, dropdowns  | Executable Books ecosystem | Modern UI components. Useful for installation tabs (pip vs uv), feature cards, admonition-like layouts. Replaces the older `sphinx-panels`.  |
| **`sphinx.ext.doctest`**  | Test code examples in docs             | attrs                      | Ensures documentation code examples actually work. Run with `sphinx -b doctest`.                                                             |

### Tier 3 — Nice-to-Have

| Extension                   | Purpose                                 | Notes                                                                                                              |
| --------------------------- | --------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| **`sphinx-notfound-page`**  | Custom 404 page with working CSS/JS     | Used by attrs. Prevents broken styling on 404 pages hosted on RTD.                                                 |
| **`sphinx-tippy`**          | Hover tooltips on cross-references      | Shows rich previews when hovering over intersphinx links. Small community but genuinely useful for API-heavy docs. |
| **`sphinxcontrib-mermaid`** | Mermaid.js diagrams in docs             | Active development. Good for architecture/flow diagrams if/when needed. Currently seeking new maintainers.         |
| **`sphinx_paramlinks`**     | Deep-link to individual function params | Used by Alembic and SQLAlchemy. Allows linking directly to a specific parameter in an API doc page.                |
| **`sphinx.ext.todo`**       | Track TODO items in docs                | Useful during development; can hide in production builds.                                                          |
| **`sphinx.ext.coverage`**   | Report documentation coverage           | CI integration to catch undocumented public API.                                                                   |

### Skip / Not Relevant

| Extension                              | Reason to Skip                                                                                             |
| -------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| **`myst-parser`**                      | Our docs are RST and work fine. MyST is worth considering long-term (see section below) but is not urgent. |
| **`sphinx-inline-tabs`**               | Overlaps with `sphinx-design` tabs. Pick one; `sphinx-design` gives more (cards, grids, badges too).       |
| **`sphinx-immaterial`**                | Material Design theme for Sphinx. Still beta, smaller community than Furo.                                 |
| **`sphinx-click` / `sphinx-argparse`** | No CLI to document (yet). Add if/when a CLI is introduced.                                                 |
| **`autodoc_pydantic`**                 | No Pydantic models in this project.                                                                        |

______________________________________________________________________

## autodoc2 vs autodoc + napoleon: Key Decision

We currently use **`sphinx-autodoc2`**. This is worth evaluating:

### sphinx-autodoc2

- **Pros:** Does NOT require the package to be importable (parses source statically); correctly handles
  `if TYPE_CHECKING:` blocks natively; supports MyST output; caches analysis.
- **Cons:** Only ~96 GitHub stars, ~7,300 weekly PyPI downloads. Snyk classifies it as **inactive**. Latest release
  v0.5.0. Small community means less ecosystem support.

### sphinx.ext.autodoc + napoleon + sphinx_autodoc_typehints

- **Pros:** Built into Sphinx (autodoc); universally supported; massive ecosystem of complementary extensions
  (autosummary, viewcode, typehints, paramlinks). Used by Alembic, SQLAlchemy, attrs, Nox — the exact ecosystem we're
  in.
- **Cons:** Requires the package to be importable at doc build time (not a problem for us — RTD build already installs
  the package). Type hints in docstrings need `sphinx_autodoc_typehints` to render properly.

### Recommendation

**Consider migrating to `autodoc` + `napoleon` + `sphinx_autodoc_typehints`** if autodoc2's inactivity becomes a
concern. The standard stack is battle-tested and supported by every Sphinx extension. However, autodoc2's static
analysis (no import required) and native `TYPE_CHECKING` handling are genuine advantages for a project that uses those
patterns heavily. **Either is viable; monitor autodoc2's maintenance status.**

______________________________________________________________________

## MyST-Parser: RST vs Markdown

### Current: RST

Our docs are currently all `.rst`. This works and aligns with Alembic/SQLAlchemy.

### Case for MyST (Markdown)

| Factor                | MyST-Parser                     | reStructuredText |
| --------------------- | ------------------------------- | ---------------- |
| Developer familiarity | Very high                       | Niche            |
| GitHub rendering      | Renders natively                | Poor rendering   |
| Editor tooling        | 795+ VS Code extensions         | ~21 extensions   |
| Sphinx feature access | Full (via `{directive}` syntax) | Native           |
| Community trend       | Growing rapidly                 | Stable/legacy    |

**attrs and Nox both use MyST alongside RST** — you can mix `.md` and `.rst` files in the same project. This is a
non-breaking change: add `myst_parser` to extensions and new pages can be `.md` while existing `.rst` pages continue
working.

### If adopted

```python
extensions = [
    "myst_parser",
    # ...
]
myst_enable_extensions = [
    "colon_fence",  # ::: directive syntax (better editor highlighting)
    "fieldlist",  # Field lists
    "deflist",  # Definition lists
]
```

### Recommendation

**Not urgent, but worth considering for new pages.** The hybrid approach (keep existing RST, write new pages in
Markdown) is low-risk and used by real projects (attrs, Nox).

______________________________________________________________________

## Read the Docs Configuration

### Current `.readthedocs.yaml`

```yaml
version: 2
build:
  os: ubuntu-24.04
  tools:
    python: "3.12"
  jobs:
    post_install:
      - pip install uv
      - UV_PROJECT_ENVIRONMENT=$READTHEDOCS_VIRTUALENV_PATH uv sync --extra docs --no-dev --link-mode=copy
sphinx:
  configuration: docs/conf.py
```

### Recommended Improvements

1. **Add `fail_on_warning: true`** — catches broken cross-references and other doc issues at build time.
1. **Use `asdf` for uv** — attrs and Nox use `asdf plugin add uv && asdf install uv latest` on RTD for a cleaner
   install.
1. **Use `-W` flag** — treats Sphinx warnings as errors (same effect as `fail_on_warning` but explicit in the build
   command).
1. **Consider `dirhtml` builder** — produces cleaner URLs (`/quickstart/` instead of `/quickstart.html`).

### Suggested Config

```yaml
version: 2

build:
  os: ubuntu-24.04
  tools:
    python: "3.12"
  commands:
    - asdf plugin add uv
    - asdf install uv latest
    - asdf global uv latest
    - uv sync --extra docs --no-dev
    - uv run python -m sphinx -W --keep-going -b dirhtml docs/ $READTHEDOCS_OUTPUT/html

sphinx:
  fail_on_warning: true
```

______________________________________________________________________

## Recommended `conf.py` Additions

Based on this research, here are the additions to consider for `docs/conf.py`:

```python
"""Sphinx configuration for alembic-pg-autogen."""

from datetime import date

project = "alembic-pg-autogen"
copyright = f"{date.today().year}, Edward Jones"  # noqa: A001
author = "Edward Jones"

extensions = [
    # API documentation
    "autodoc2",  # (or switch to autodoc + napoleon + sphinx_autodoc_typehints)
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",  # NEW: [source] links
    # UX
    "sphinx_copybutton",
    "sphinxext.opengraph",  # NEW: social sharing meta tags
    "sphinx_design",  # NEW: tabs, cards, grids
    # Quality
    "sphinx.ext.doctest",  # NEW: test code examples
    "sphinx.ext.extlinks",  # NEW: :issue:`123` shortcuts
]

# -- autodoc2 ----------------------------------------------------------------
autodoc2_packages = ["../src/alembic_pg_autogen"]
autodoc2_render_plugin = "rst"

# -- Intersphinx --------------------------------------------------------------
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "alembic": ("https://alembic.sqlalchemy.org/en/latest/", None),
    "sqlalchemy": ("https://docs.sqlalchemy.org/en/20/", None),
}

# -- extlinks ----------------------------------------------------------------
extlinks = {
    "issue": ("https://github.com/eddieland/alembic-pg-autogen/issues/%s", "#%s"),
    "pr": ("https://github.com/eddieland/alembic-pg-autogen/pull/%s", "PR #%s"),
}

# -- copybutton ---------------------------------------------------------------
copybutton_prompt_text = r">>> |\.\.\. |\$ "
copybutton_prompt_is_regexp = True

# -- opengraph ----------------------------------------------------------------
ogp_site_url = "https://alembic-pg-autogen.readthedocs.io/"
ogp_site_name = "alembic-pg-autogen"

# -- HTML output ---------------------------------------------------------------
html_theme = "furo"
html_title = "alembic-pg-autogen"
html_theme_options = {
    "source_repository": "https://github.com/eddieland/alembic-pg-autogen",
    "source_branch": "main",
    "source_directory": "docs/",
}
```

### Updated `pyproject.toml` `docs` Extra

```toml
[project.optional-dependencies]
docs = [
    "sphinx>=8",
    "furo>=2024.8",
    "sphinx-copybutton>=0.5",
    "sphinx-autodoc2>=0.5",
    "sphinx-design>=0.6",
    "sphinxext-opengraph>=0.9",
    "sphinx-autodoc-typehints>=2",   # only if switching from autodoc2 to autodoc
]
```

______________________________________________________________________

## Summary: Priority Order

| Priority | Action                             | Effort                                       |
| -------- | ---------------------------------- | -------------------------------------------- |
| 1        | Add `sphinx.ext.viewcode`          | One line in `conf.py`                        |
| 2        | Add `sphinxext-opengraph`          | One dep + 3 lines config                     |
| 3        | Add `sphinx.ext.extlinks`          | One line + shortcut definitions              |
| 4        | Add `sphinx-design`                | One dep + one line in extensions             |
| 5        | Add `sphinx.ext.doctest`           | One line; start writing testable examples    |
| 6        | Configure `copybutton_prompt_text` | Two lines in `conf.py`                       |
| 7        | Add Furo `html_theme_options`      | Source repo/branch/directory links           |
| 8        | Add `sphinx.ext.napoleon`          | One line (needed if switching from autodoc2) |
| 9        | Update `.readthedocs.yaml`         | `fail_on_warning`, `dirhtml` builder         |
| 10       | Evaluate `myst-parser`             | Add for new pages; keep existing RST         |
| 11       | Add `sphinx-notfound-page`         | Nice polish for RTD hosting                  |
| 12       | Add `sphinx-tippy`                 | Nice UX for cross-reference tooltips         |
