# Changelog

All notable changes to this project are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/).

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
