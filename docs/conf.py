"""Sphinx configuration for alembic-pg-autogen documentation."""

project = "alembic-pg-autogen"
copyright = "2025, Edward Jones"  # noqa: A001
author = "Edward Jones"

extensions = [
    "autodoc2",
    "sphinx.ext.intersphinx",
    "sphinx_copybutton",
    "sphinx_llms_txt",
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

# -- HTML output ---------------------------------------------------------------
html_theme = "furo"
html_title = "alembic-pg-autogen"
