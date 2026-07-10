# Configuration file for the Sphinx documentation builder.
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import pathlib
import re
import sys

# Make the package importable for autodoc without installing it.
_repo_root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root))

# -- Project information ------------------------------------------------------

project   = "ncarnate"
author    = "Erick Edward Shepherd"
copyright = "2020-2026, Erick Edward Shepherd"

# Single-source the version from the package constants without importing the
# package (its C-extension dependencies are not installed in the docs build).
_constants = (_repo_root / "ncarnate" / "constants.py").read_text(encoding="utf-8")
release = re.search(r'__version__\s*=\s*"([^"]+)"', _constants).group(1)
version = release

# -- General configuration ----------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "myst_parser",
]

# The heavy C-extension dependencies are mocked so the docs build (in CI or
# locally) needs no system HDF4/netCDF/PROJ libraries — autodoc only has
# to import the package to read its docstrings.
autodoc_mock_imports = ["netCDF4", "pyhdf", "pyproj", "numpy", "tqdm"]

autodoc_member_order  = "bysource"
autodoc_typehints     = "description"

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

templates_path   = ["_templates"]
exclude_patterns = [
    "_build",
    "Thumbs.db",
    ".DS_Store",
    "design",
    "audits",
    "plans",
    "owner-checklist-v2.0.0.md",
    # Internal "living document" with links to the excluded design/plans docs;
    # the README (the docs landing page) already summarizes the fidelity contract.
    "fidelity-notes.md",
]

# -- HTML output --------------------------------------------------------------

html_theme = "furo"
html_title = f"ncarnate {release}"
