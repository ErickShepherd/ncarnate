# conda-forge recipe

`meta.yaml` here is the source recipe for publishing ncarnate to
[conda-forge](https://conda-forge.org/). It is kept in-repo as the maintained
starting point; the canonical copy, once accepted, lives in the auto-created
`conda-forge/ncarnate-feedstock`.

Why conda-forge in addition to PyPI: ncarnate's HDF4 path depends on `pyhdf`,
whose **Windows wheel ships no HDF4 runtime**, so `pip install ncarnate` cannot
give Windows users a working HDF4 converter. conda-forge's `pyhdf` is built
against a proper HDF4 library on every platform (including win-64), so a
conda-forge `ncarnate` — pure-Python `noarch`, delegating the compiled work to
its dependencies — makes `conda install -c conda-forge ncarnate` a one-command
working install everywhere.

## To submit (owner-gated; runs under the maintainer's GitHub account)

1. Fork [`conda-forge/staged-recipes`](https://github.com/conda-forge/staged-recipes).
2. Copy this file to `recipes/ncarnate/meta.yaml` in the fork.
3. Open a pull request. conda-forge CI builds it on Linux/macOS/Windows and runs
   the import/command tests; a reviewer and the linting bots check it.
4. On merge, conda-forge auto-creates `conda-forge/ncarnate-feedstock`, lists
   the `recipe-maintainers` (here, `ErickShepherd`), builds, and uploads to the
   conda-forge channel.
5. Thereafter the autotick bot opens a version-bump PR automatically on each new
   PyPI release; keep this file in sync with those bumps (update `version` and
   the sdist `sha256`).

## Updating on a new release

- Bump `{% set version = "…" %}`.
- Replace `sha256` with the new sdist's hash:
  `curl -sSL <sdist-url> | sha256sum` (the sdist URL and its digest are on the
  release's PyPI JSON: `https://pypi.org/pypi/ncarnate/<version>/json`).
- Reset `build.number` to `0` for a new version.
