# conda-forge recipe

This is the maintained candidate recipe for ncarnate 2.3.0 in conda-forge's
v1 recipe format. The package already has a
[feedstock](https://github.com/conda-forge/ncarnate-feedstock); submit updates
there. Its published recipe was 2.2.2 when checked on October 8, 2026.

The candidate version, dependencies and Python floor are synchronized with
the main package. The source digest records the locally built candidate
archive. Before submitting the feedstock update, confirm that the published
PyPI source archive has exactly that digest. Rebuild and refresh the digest
after any changes to source-distribution contents. The recipe is excluded
from the sdist to avoid a self-referential digest.

conda-forge provides the native HDF4/netCDF/PROJ stack as dependencies. Windows
pip HDF4 support depends on the pyhdf build; pyhdf 0.11.7 worked in the tested
Windows environment. The older blanket statement that all Windows wheels
lack HDF4 is no longer accurate.

For a new release, keep `context.version`, `context.python_min`, dependency
floors and entry points aligned with `pyproject.toml`; reset `build.number` to
zero. Build and test with `rattler-build`, lint with `conda-smithy`, and verify
the feedstock's platform matrix before submission. Earlier successful builds
of older recipes do not qualify this new candidate. The main repository CI
installs dependencies from conda-forge and tests the source on Linux and Windows;
it does not build this recipe or replace the feedstock build matrix.

Use `python tools/validate_release_metadata.py --sdist PATH_TO_SDIST` to check
the local metadata and recipe digest. See the published maintainer guide for
the release order and external service checks.
