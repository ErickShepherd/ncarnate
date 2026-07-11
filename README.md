# ncarnate

[![CI status](https://github.com/ErickShepherd/ncarnate/actions/workflows/ci.yml/badge.svg)](https://github.com/ErickShepherd/ncarnate/actions/workflows/ci.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/ErickShepherd/ncarnate/blob/main/LICENSE)
[![Python 3.10–3.13](https://img.shields.io/badge/python-3.10%E2%80%933.13-blue.svg)](https://pypi.org/project/ncarnate/)
[![DOI](https://img.shields.io/badge/DOI-10.5281%2Fzenodo.21288802-blue)](https://doi.org/10.5281/zenodo.21288802)

Reincarnate legacy scientific data as modern netCDF4.

ncarnate reads netCDF3, netCDF4/HDF5, and HDF4/HDF-EOS2 files and writes
recompressed, CF-annotated netCDF4. It does two jobs:

- **Recompress** netCDF/HDF5 files — change the compression level, shuffle
  filter, or storage layout without changing a single stored value.
- **Convert** HDF4 and HDF-EOS2 granules (AMSR-E, MODIS, and kin) to netCDF4,
  reconstructing the CF coordinates that modern tools (xarray, QGIS, Panoply)
  need: grid projections become CF grid mappings with 1-D `x`/`y` and 2-D
  `lat`/`lon` coordinates, swath geolocation is attached as CF coordinates, and
  dimension-mapped (e.g. 5 km → 1 km) geolocation is interpolated through ECEF
  space.

## Problems this solves

Reach for ncarnate if you are trying to:

- **Convert HDF4 / HDF-EOS2 granules (MODIS, AMSR-E, and kin) to netCDF4** so
  they open cleanly in xarray, QGIS, or Panoply.
- **Read an HDF-EOS2 swath or grid that has no usable lat/lon** — ncarnate
  reconstructs CF `lat`/`lon` coordinates and grid mappings so the data is
  actually georeferenced, instead of an unplottable array.
- **Recompress a netCDF4 / HDF5 file** — change the compression level or shuffle
  filter without altering a single stored value.
- **Shrink an archive of scientific files** without risking the science: every
  output is verified value-for-value against its source before it replaces
  anything, and stored values round-trip bit-identically.
- **Batch-convert a directory tree** of legacy granules to modern netCDF4 in one
  command.

## The fidelity contract

Converting or recompressing a file changes *storage*, never *science data*:

- Every variable's stored values are preserved **bit-identically** — packed
  integers stay packed; `scale_factor`/`add_offset`/`_FillValue` are carried
  across as declarations, never applied.
- Every dimension (including unlimited-ness), attribute (including its type), and
  group survives. HDF-EOS2 `StructMetadata` is preserved verbatim; names netCDF
  cannot hold are sanitized with the original recorded in a companion attribute.
- Geolocation reconstruction is strictly **additive**: the original information
  always rides along, so the conversion never becomes the only copy of the
  truth. Swath coordinates are attached to variables whose first two axes are
  the swath axes; a variable with a leading band/byte dimension is converted
  intact but gets no `coordinates` attribute (a warning says so).
- Every output is **verified against the source value-for-value before it
  replaces anything**. A source file is never destroyed by a failed run, and
  HDF4 sources are never replaced at all.
- Unsupported constructs (user-defined netCDF types, unverified GCTP projections,
  exotic swath layouts) **fail loud** with a named error rather than guessing — a
  wrong coordinate is worse than a refused conversion. `--no-geolocation`
  converts the raw payload anyway.

The details, the guarantee boundary, and how the test suite pins each clause live
in [`docs/fidelity-notes.md`](https://github.com/ErickShepherd/ncarnate/blob/main/docs/fidelity-notes.md).

## Installation

**With conda** (from [conda-forge](https://anaconda.org/conda-forge/ncarnate)):

```console
conda install -c conda-forge ncarnate
```

This works on every platform and is the recommended install on **Windows** —
conda-forge's `pyhdf` is built against a proper HDF4 library everywhere, so the
full HDF4/HDF-EOS2 converter runs on Windows, macOS, and Linux alike.

**With pip** (from [PyPI](https://pypi.org/project/ncarnate/)):

```console
pip install ncarnate
```

On **Linux (x86_64)** and **macOS (arm64)**, every dependency — including
`pyhdf` — installs as a self-contained binary wheel with no system libraries
required. On platforms without a repaired `pyhdf` wheel (e.g. Linux aarch64),
building from sdist requires the system HDF4 library first (Debian/Ubuntu:
`apt install libhdf4-dev`).

**Windows via pip:** the netCDF/HDF5 *recompression* path works from PyPI wheels
out of the box, but the HDF4/HDF-EOS2 *conversion* path does **not** — `pyhdf`'s
Windows wheel ships no HDF4 runtime, so `import pyhdf` fails with a DLL-load
error. Use the conda-forge install above for HDF4 on Windows (or **WSL** with the
pip instructions).

## Command line usage

```console
# Recompress a netCDF4 file in place (verified before replacement).
ncarnate observations.nc --complevel 9

# Keep the original; write observations_recompressed.nc beside it.
ncarnate --no-overwrite observations.nc

# Convert an HDF-EOS2 granule -> granule.nc with CF geolocation.
ncarnate AMSR_E_L3_SeaIce12km_B02_20020619.hdf

# Convert the raw SDS payload only (unsupported-projection escape hatch).
ncarnate --no-geolocation granule.hdf

# Recurse over a directory tree.
ncarnate -r /data/archive
```

Exit codes: `0` success, `1` one or more files failed, `2` bad input paths or
arguments.

## Audit an archive in 5 minutes

Before converting a terabyte archive, run a **read-only audit**: it never opens
science arrays, never touches the network, and never writes to the files it
inspects. It discovers files, detects formats, inspects metadata, classifies
each file into a readiness taxonomy, and prints a summary by files *and* bytes.

```console
# Assess an archive (recursive, read-only) and print a readiness summary.
ncarnate audit /data/archive

# Write the per-file migration manifest (JSONL is the contract; .csv gives a
# flat spreadsheet view). Add --checksum sha256 for a manifest you intend to
# execute later.
ncarnate audit /data/archive --output manifest.jsonl --checksum sha256
```

Each JSONL line is one versioned, schema-validated file record — path,
checksum, status, blockers, and the conversion plan — designed so a later
`ncarnate convert --manifest` (and every downstream tool) consumes it unchanged.
The bare `ncarnate <path>` and `ncarnate convert <path>` forms are unchanged.

## Convert exactly what the audit blessed

The golden path for an archive migration is two steps: **audit an archive, then
convert exactly what it blessed.** `convert --manifest` executes the audit's
manifest — it re-verifies each granule's recorded `sha256` before touching it,
converts only the statuses you select (`ready` by default), writes a mirrored
output tree, and **never modifies a source** unless you pass `--in-place`.

```bash
# 1. Audit the archive, recording a per-file sha256 in the manifest.
ncarnate audit /data/archive --output manifest.jsonl --checksum sha256

# 2. Convert exactly the `ready` granules into a mirrored ./modern tree.
#    --root anchors reads to a directory you control (the manifest is untrusted
#    input, so its recorded root is not trusted as the read base by default;
#    pass --allow-manifest-root to opt into trusting it instead). A record whose
#    bytes changed since the audit (sha256 mismatch) is skipped with an error;
#    a blocker is never converted; sources are left untouched.
ncarnate convert --manifest manifest.jsonl --out-dir ./modern --root /archive

# Widen the selection once you've read the report; resume an interrupted run.
ncarnate convert --manifest manifest.jsonl --out-dir ./modern --root /archive \
    --status ready,already_modern --skip-existing
```

The end-of-run summary counts converted / skipped / failed with reasons, and the
exit code is non-zero **iff** a selected record failed — so a partial failure on
a terabyte run surfaces loudly instead of silently mis-converting.

## Library usage

```python
from ncarnate import (
    recompress, audit_path, AuditOptions, convert_manifest, ConvertOptions,
)

# Lossless recompression; returns the output path.
recompress("observations.nc", complevel=9)

# HDF-EOS2 conversion; the .hdf source is never replaced.
recompress("granule.hdf", dst="granule.nc")

# Read-only archive audit; returns an AuditReport (report.summary, report.files).
report = audit_path("/data/archive", AuditOptions(recursive=True))

# Execute an audit manifest; returns a ConvertResult (converted/skipped/failed).
result = convert_manifest("manifest.jsonl", ConvertOptions(out_dir="./modern"))
```

## Example

The AMSR-E daily 12.5 km sea-ice granule this project grew up around:

| File | Input | Output |
| --- | --- | --- |
| netCDF4 recompression (`--complevel 9`) | 42.6 MB | 19.9 MB |
| HDF-EOS2 → netCDF4 (+ reconstructed lat/lon) | 60.2 MB | 35.5 MB |

Both outputs re-read bit-identically to their sources; the conversion
additionally carries CF `polar_stereographic` grid mappings and coordinates for
both hemispheric grids. The northern grid's reconstructed latitudes/longitudes
agree with The HDF Group's independent conversion of the same granule to within
10⁻⁵ degrees (about a metre), the tolerance the test suite enforces.

## Supported inputs

- **netCDF4 / HDF5** and **netCDF3** — recompressed via the netCDF4 library.
- **HDF4 / HDF-EOS2** — read via the pyhdf SD API. GRID structures with GCTP
  polar-stereographic, geographic, and Lambert-azimuthal (EASE-Grid)
  projections; SWATH structures with direct or dimension-mapped geolocation.
  Output is always netCDF4 — HDF4 is never written.

## Development

```console
pip install -e ".[test]"
ruff check .
pytest
```

The test suite runs entirely offline against small committed fixtures trimmed
from real granules (provenance sidecars included); cross-checks against the raw
multi-MB granules self-skip where the local granule store is absent.

## License

MIT — see [LICENSE](https://github.com/ErickShepherd/ncarnate/blob/main/LICENSE).
Built by [Erick Shepherd](https://erickshepherd.com).
