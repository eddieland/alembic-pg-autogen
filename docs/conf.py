"""Sphinx configuration for alembic-pg-autogen documentation."""

project = "alembic-pg-autogen"
copyright = "2026, eddie.land"  # noqa: A001
author = "Edward Jones"

extensions = [
    "autodoc2",
    "sphinx.ext.intersphinx",
    "sphinx_copybutton",
    "sphinx_llms_txt",
    "sphinxext.opengraph",
]

# -- Open Graph ----------------------------------------------------------------
ogp_site_url = "https://alembic-pg-autogen.readthedocs.io/"

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
html_theme_options = {
    "source_repository": "https://github.com/eddieland/alembic-pg-autogen",
    "source_branch": "main",
    "source_directory": "docs/",
}
