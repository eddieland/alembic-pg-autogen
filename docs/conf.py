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
    "footer_icons": [
        {
            "name": "GitHub",
            "url": "https://github.com/eddieland/alembic-pg-autogen",
            "html": (
                '<svg stroke="currentColor" fill="currentColor" stroke-width="0" '
                'viewBox="0 0 16 16"><path fill-rule="evenodd" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 '
                "2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49"
                "-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23"
                ".82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 "
                "0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32"
                "-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 "
                "2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 "
                "1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8"
                '-8z"></path></svg>'
            ),
            "class": "",
        },
        {
            "name": "PyPI",
            "url": "https://pypi.org/project/alembic-pg-autogen/",
            "html": (
                '<svg stroke="currentColor" fill="currentColor" stroke-width="0" '
                'viewBox="0 0 17 20" xmlns="http://www.w3.org/2000/svg"><path '
                'd="M12.5 7.3V4l-4.2-2.4L4 4.1v3.2l4.2-2.5L12.5 7.3zm-8.4 '
                "3.8V7.8l4.2-2.5v3.2L4.1 11.1zm0 4.3V12l4.2-2.5v3.3L4.1 15.4zm4.8 "
                "2.8V15l4.2-2.5v3.2L8.9 18.2zm4.8-2.8V12l-4.2 2.5v-3.3L13.7 "
                '8.7v6.6zm0-8L8.5 5.5 4.1 3 8.5.5l4.2 2.5z"></path></svg>'
            ),
            "class": "",
        },
    ],
}
