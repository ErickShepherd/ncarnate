# conda-forge recipe

`recipe.yaml` here is the source recipe for publishing ncarnate to
[conda-forge](https://conda-forge.org/). It is in the **v1 (rattler-build)
recipe format** that conda-forge's `staged-recipes` now leads with. It is kept
in-repo as the maintained starting point; the canonical copy, once accepted,
lives in the auto-created `conda-forge/ncarnate-feedstock`.

Why conda-forge in addition to PyPI: ncarnate's HDF4 path depends on `pyhdf`,
whose **Windows wheel ships no HDF4 runtime**, so `pip install ncarnate` cannot
give Windows users a working HDF4 converter. conda-forge's `pyhdf` is built
against a proper HDF4 library on every platform (including win-64), so a
conda-forge `ncarnate` — pure-Python `noarch`, delegating the compiled work to
its dependencies — makes `conda install -c conda-forge ncarnate` a one-command
working install everywhere.

## Verified locally

The recipe was generated with `grayskull` (the tool `staged-recipes` recommends),
enhanced with an `ncarnate.eos` import test / `--version` command / fuller
description, then **built and tested with `rattler-build` and linted with
`conda-smithy`**:

- `conda-smithy recipe-lint conda-recipe/` → *"in fine form"*.
- `rattler-build build --recipe conda-recipe/recipe.yaml -c conda-forge` → built
  `ncarnate-2.0.0-*.conda` (noarch), then in a clean environment resolved every
  run dependency from conda-forge and passed all recipe tests (imports +
  `pip check` + `ncarnate --help` / `--version`). This is the same check
  conda-forge CI runs on the PR.

Tooling lives in the `recipe-tools` conda env
(`mamba create -n recipe-tools -c conda-forge grayskull conda-recipe-manager rattler-build conda-smithy`).

## To submit (owner-gated; runs under the maintainer's GitHub account)

1. Fork [`conda-forge/staged-recipes`](https://github.com/conda-forge/staged-recipes).
2. Copy `recipe.yaml` to `recipes/ncarnate/recipe.yaml` in the fork.
3. Open a pull request. conda-forge CI builds it on Linux/macOS/Windows and runs
   the import/command tests; a reviewer and the linting bots check it.
4. On merge, conda-forge auto-creates `conda-forge/ncarnate-feedstock`, lists
   the `recipe-maintainers` (here, `ErickShepherd`), builds, and uploads to the
   conda-forge channel.
5. Thereafter the autotick bot opens a version-bump PR automatically on each new
   PyPI release; keep this file in sync with those bumps (update `version` and
   the sdist `sha256`).

## Updating on a new release

- Bump `version:` in the `context:` block.
- Replace `sha256` with the new sdist's hash:
  `curl -sSL <sdist-url> | sha256sum` (the sdist URL and its digest are on the
  release's PyPI JSON: `https://pypi.org/pypi/ncarnate/<version>/json`).
- Reset `build.number` to `0` for a new version.
- Re-run the local build/test/lint above before submitting the bump.
