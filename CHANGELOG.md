# Changelog

All notable changes to this project are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/).

## [2.0.3] - 2026-07-10

Discoverability + review-driven fixes: ships the Read the Docs site
configuration in a tag (fixing RTD's tag-based "stable" build), the
Markdown README with DOI badge and Documentation URL on PyPI, and a
small set of fixes adopted from an independent multi-model review.

### Added
- Sphinx documentation site hosted on Read the Docs
  (<https://ncarnate.readthedocs.io/>), with the API reference generated
  from the docstrings; `Documentation` URL in the PyPI project links.
- Warning when a swath variable's geolocation axes are not its first two
  dimensions (e.g. band-first MOD02, byte-segment-first MOD35): the
  variable converts intact but gets no `coordinates` attribute, and the
  skip is now said out loud. The attachment rule is documented in the
  README.
- JOSS paper draft and community health files (`CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`) in the repository (not part of the wheel).

### Fixed
- Swath geolocation interpolation now fails loud when Latitude and
  Longitude declare different `_FillValue`s (previously Longitude's fill
  could be silently interpolated into neighboring pixels); NaN fills that
  match on both fields are accepted, and native-resolution attachment —
  where the fills never interact — is unaffected.
- `ncarnate --help` now describes the headline HDF4/HDF-EOS2 → netCDF4
  conversion, not just recompression, and scopes `--overwrite`/
  `--no-overwrite` to recompression.
- README: Markdown (renders on PyPI as `text/markdown`), shields.io DOI
  badge (the zenodo.org badge 502s behind GitHub's image proxy),
  absolute `docs/fidelity-notes.md` link, `erickshepherd.com` backlink.

### Changed
- StructMetadata is parsed once per file instead of twice.
- Default branch renamed `master` → `main`; in-repo links updated.
- conda recipe's documentation URL points at Read the Docs.

## [2.0.2] - 2026-07-10

Citation metadata only — no code changes.

### Added
- `.zenodo.json` and an author ORCID in `CITATION.cff`, so a Zenodo release
  archive registers a citable DOI with correct software metadata.

## [2.0.1] - 2026-07-09

Catches the PyPI release up to `master`. Bundles the 2026-07-09 read-only
audit remediation (security + correctness hardening), the conda-forge install
path, and the discoverability docs/metadata. There is no on-disk format change;
the only behavior changes are defensive — an auto-derived destination now
refuses to clobber an existing file, and an allocation cap rejects implausibly
large arrays.

### Security
- Cap attacker-declared allocation sizes so a crafted or corrupt granule cannot
  exhaust memory before validation (new `ncarnate/limits.py`).
- Reject non-finite numbers and non-positive grid dimensions in HDF-EOS
  metadata, and guard GCTP `ProjParams` arity and ellipsoid domain — fail loud
  rather than compute wrong geolocation.
- Bound the ODL/StructMetadata parser (O(1) parenthesis tracking and a
  continuation limit) against pathological input.

### Fixed
- Auto-derived output destinations now refuse to clobber an existing file.
- Symlinked sources are resolved so an in-place replace targets the real file,
  not the link.
- Cleanup after a failed run no longer masks the original error.
- HDF4: fail loud on SDS names that collide after sanitization; guard the
  companion-attribute namespace against collision; order StructMetadata parts by
  numeric suffix.
- CLI: de-duplicate the input file worklist.

### Changed
- Added a "Problems this solves" section to the README mapping common
  natural-language queries (converting HDF4/HDF-EOS2 MODIS/AMSR-E granules to
  netCDF4, reconstructing CF lat/lon, recompressing netCDF/HDF5) to the tool;
  led the package description with the HDF4/HDF-EOS2→netCDF4 conversion; and
  broadened discovery keywords (MODIS, AMSR-E, xarray, CF conventions).
- Packaging: added trove classifiers, pinned the hatchling build floor, and
  excluded internal planning/audit docs from the sdist.
- Supply chain: pinned the PyPI publish action to an immutable commit SHA.
- Dropped Windows from CI and documented the `pyhdf`-on-Windows limitation
  (recompression works from PyPI wheels; HDF4 conversion needs conda-forge or
  WSL).

### Added
- conda-forge install path (`conda install -c conda-forge ncarnate`) and an
  in-repo reference recipe.

## [2.0.0] - 2026-07-08

A ground-up rebuild of the 2020 utility, renamed from
`netcdf_recompressor` to **ncarnate**. Everything below is breaking
relative to the notional 1.x behavior; there are no downstream users
(1.x was never published).

### Fixed

- The CLI recompresses again: a debugging `raise ValueError` committed
  in 2020 made the shipped tool process nothing.
- Variables carrying `_FillValue` no longer crash the copy (`fill_value`
  is declared at variable creation, as netCDF4 requires).
- Packed-integer data is no longer silently re-quantized through float:
  values are copied raw (`set_auto_maskandscale(False)`), with
  `scale_factor`/`add_offset`/`_FillValue` re-declared, never applied.
- Overwrite is safe: output is written to a temp file, verified
  value-for-value against the source, and only then atomically moved
  into place. A failed run never destroys the source.
- `-V`/`--version` works without a path; errors exit non-zero with
  clear messages instead of vanishing into a log file.

### Added

- **HDF4/HDF-EOS2 ingest** (read-only; output is always netCDF4):
  SDS payloads convert bit-identically, one netCDF4 group per EOS
  structure, EOS metadata preserved verbatim, hostile names sanitized
  with originals recorded in companion attributes.
- **CF geolocation reconstruction** for HDF-EOS2: GCTP
  polar-stereographic, geographic, and Lambert-azimuthal (EASE-Grid)
  grids become CF grid mappings + `x`/`y` + 2-D `lat`/`lon`; swath
  geolocation is CF-attached; dimension-mapped geolocation (5 km →
  1 km) is interpolated through ECEF space with fill propagation.
  `--no-geolocation` converts payload-only. Unsupported constructs fail
  loud with named errors.
- File-type dispatch by magic bytes (netCDF3 / netCDF4-HDF5 / HDF4),
  independent of file extension.
- pytest suite (66 tests) pinning the fidelity contract over committed
  fixtures trimmed from real granules, plus the geolocation verification
  lattice (four of the design's five checks; the `eos2dump` external
  reference is unimplemented — the same-granule THG reference covers
  that grid); CI across CPython 3.10–3.13.
- Modern packaging: hatchling `pyproject.toml`, console entry point
  `ncarnate`, single-sourced version, `LICENSE` file.

### Changed

- Project renamed to `ncarnate`; import package `ncarnate`; CLI
  `ncarnate`.
- Python 3.10+ (was 3.7).
- Logging goes to stderr only when the CLI runs — importing the library
  no longer creates log files as a side effect.

### Removed

- `setup.py`, `MANIFEST.in`, `version.json` (and its malformed
  `"1.0.0."` version string), committed `__pycache__`, and the stray
  `LOG_FILE`.

## [1.0.0] - 2020-05-28

Initial private version (`netcdf_recompressor`), never published:
a single-purpose netCDF4/HDF5 recompression script with a `setup.py`
package skeleton.
