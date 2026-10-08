<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/ErickShepherd/ncarnate/main/brand/ncarnate-lockup-dark.png">
    <img alt="ncarnate" src="https://raw.githubusercontent.com/ErickShepherd/ncarnate/main/brand/ncarnate-lockup.png" width="460">
  </picture>
</p>

[![CI status](https://github.com/ErickShepherd/ncarnate/actions/workflows/ci.yml/badge.svg)](https://github.com/ErickShepherd/ncarnate/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/ncarnate.svg)](https://pypi.org/project/ncarnate/)
[![conda-forge version](https://img.shields.io/conda/vn/conda-forge/ncarnate.svg)](https://anaconda.org/conda-forge/ncarnate)
[![PyPI Downloads](https://img.shields.io/pypi/dm/ncarnate.svg?label=PyPI%20downloads)](https://pypi.org/project/ncarnate/)
[![Conda Downloads](https://img.shields.io/conda/dn/conda-forge/ncarnate.svg?label=Conda%20downloads)](https://anaconda.org/conda-forge/ncarnate)
[![Docs](https://readthedocs.org/projects/ncarnate/badge/?version=latest)](https://ncarnate.readthedocs.io/en/latest/)
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
  anything, and stored values round-trip value-identically (bit-for-bit for
  integer and packed data; NaN- and signed-zero-insensitive for floating point).
- **Batch-convert a directory tree** of legacy granules to modern netCDF4 in one
  command.

## The fidelity contract

Converting or recompressing a file changes *storage*, never *science data*:

- Every variable's stored values are preserved **value-identically** — bit-for-bit
  for integer and packed data; for floating-point data, distinct NaN
  bit-patterns and `-0.0`/`+0.0` compare equal. Packed integers stay packed;
  `scale_factor`/`add_offset`/`_FillValue` are carried across as declarations,
  never applied.
- Every dimension (including unlimited-ness), attribute (including its exact
  storage type — an `NC_STRING` scalar stays `NC_STRING`, verified via netCDF-C
  type inquiry), and group survives. HDF-EOS2 `StructMetadata` is preserved
  verbatim; names netCDF cannot hold are sanitized with the original recorded in
  a companion attribute.
- **Complex-valued variables (`complex64`/`complex128`) are excluded** from the
  fidelity guarantee: netCDF stores them as compound types, which ncarnate
  **refuses loudly** with the stable `UNSUPPORTED_TYPE` error rather than
  guessing at a lossy copy. Complex support is a later, evidence-backed feature.
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

Conda-forge supplies the native HDF4, netCDF and PROJ dependencies together.
Package availability depends on the platform and Python version. Release CI
checks Linux and Windows conda environments separately from pip installations.

**With pip** (from [PyPI](https://pypi.org/project/ncarnate/)):

```console
pip install ncarnate
```

Where compatible binary wheels are available, no separate native-library
installation is needed. Building `pyhdf` from source requires an HDF4 library
and build tools first (Debian/Ubuntu:
`apt install libhdf4-dev`).

**Windows via pip:** HDF4 availability depends on the installed pyhdf runtime.
The 2.3.0 candidate validation used Python 3.11 and pyhdf 0.11.7 successfully on
Windows, including all five full granules in the checked-in corpus catalog. This does not
qualify every Python or Windows build. If HDF4 cannot be loaded, ncarnate
refuses that conversion before output creation with `HDF4_RUNTIME_UNAVAILABLE`;
netCDF conversion remains usable. The conda-forge installation above is an
alternative when the pip runtime is unavailable.

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
ncarnate convert --manifest manifest.jsonl --out-dir ./modern --root /data/archive

# Widen the selection and skip paths that already exist (without verifying them).
ncarnate convert --manifest manifest.jsonl --out-dir ./modern --root /data/archive \
    --status ready,already_modern --skip-existing
```

The end-of-run summary counts converted / skipped / failed with reasons.
Conversion failures return nonzero. With `--result-journal`, an unavailable
journal refuses the run before conversions (exit 2); a later reporting failure
returns exit 3 while retaining the conversion summary and completed outputs.

**Destination collision preflight.** Before any directory or output file is
created, every selected record's destination is computed up front — from the
source's *detected bytes*, never the manifest's declared format — and any
collision refuses the **entire run** with exit code 2 and a stable
`[DESTINATION_COLLISION]` message on stderr listing every involved source and
the contested destination. No last-writer-wins, no auto-rename, no partial
proceed, and nothing is written. Refused collisions include: two records
landing on one output path (e.g. an `a.hdf` → `a.nc` conversion next to a real
`a.nc` sibling), case-fold-equivalent names (one file on NTFS/APFS), duplicate
records for one source, an output tree overlapping a source tree (symlinks
resolved), and a pre-existing destination unless you pass `--skip-existing` to
skip them. This legacy flag checks existence, not successful completion. Use the
prepared library API below for digest-verified resumption.

## Prepared conversions and verified resume

```python
from ncarnate import prepare, execute_prepared

# The output's parent must already exist. Preparation writes nothing.
job = prepare("input.nc", "output.nc", complevel=4)
record = execute_prepared(job, journal="completion.json", resume=True)
```

`prepare` binds the source digest, size and frozen encoding options to an
explicit, separate output. `prepare_batch([(source, output), ...])` checks all
destinations before execution; `execute_prepared_batch` checkpoints each completed
item in the journal. Existing outputs require a matching journal record and a
matching output digest. Changed sources, changed options, damaged outputs and
missing completion evidence are refused. An existing journal requires
`resume=True`; it is never silently discarded. These APIs return handoff records.

Keep the journal descriptor together with its sibling `<journal>.records`
directory when backing up or moving completion evidence. Each completed output
gets its own atomic checkpoint, so larger batches do not repeatedly rewrite a
growing journal. Each record has a 64 MiB limit; the complete journal has no
aggregate byte limit. Resume loads these records into caller memory, so its
memory use grows with the batch. Earlier 2.3.0 development journals migrate when
resumed. Records without an output digest cannot authorize reuse. Preserve a
completed output for inspection if checkpoint writing fails; retry with a fresh
destination rather than assuming the existing output is resumable. Concurrent
journal writers and power-loss durability are not supported.

Legacy `recompress` still defaults to replacing a netCDF source when no output
is supplied. Legacy `execute_batch` remains a lazy executor of caller-owned
plans; it does not inspect future destinations. The new explicit-output API is
the non-destructive route. Digest checks assume a cooperative filesystem and
do not prevent concurrent path replacement between checking and reading.

netCDF arrays and HDF4 SDS values copy and verify in bounded slices. To change the temporary
array budget, wrap execution in `with ncarnate.streaming.array_budget(bytes):`.
The default is 16 MiB. This is **not a global process-memory limit**: Python,
metadata, compression and native chunk caches add memory. Native caches can hold
at least one storage chunk. Variables whose names collide with a dimension use
the native cache default to avoid a netCDF-C read failure. HDF-EOS geolocation
reconstruction retains whole output coordinate arrays under declared-size limits;
swath interpolation computes its larger temporary arrays in tiles.

For a hard limit across the conversion worker, including native allocations:

```python
from ncarnate import prepare, execute_bounded

report = execute_bounded(prepare("input.hdf", "output.nc"),
                         memory_bytes=512 * 1024**2,
                         array_bytes=4 * 1024**2,
                         timeout_seconds=600)
record = report["result"]
```

This serial worker uses a Windows Job Object committed-memory limit or a Linux
process address-space limit. Neither measures RSS; the calling process is outside
the cap. Only a successful, verified worker output is published, exclusively to a
new destination. Timeout, insufficient memory, or native failure leaves no final
output. Existing outputs are refused; journals and resume remain in the prepared
API. Unsupported platforms, including macOS for this API, fail before conversion
with `MEMORY_LIMIT_UNAVAILABLE`. The ordinary conversion API remains portable.
Publication needs same-filesystem hard-link support; an unavailable link fails
with `OUTPUT_PUBLISH_FAILED`. If cleanup fails after publication, the verified
result still returns with a `WORKER_CLEANUP_INCOMPLETE` warning naming the retained
staging directory. Resource counters are diagnostics; allocation-refusal tests
verify enforcement independently of those counters.
The accepted 64 MiB minimum is a validation floor; imports alone may exceed it.
Start with the default 512 MiB and size the limit for the input and runtime.

Audit mode is metadata-only. Handoff validation bounds nesting and record
complexity. Undecodable byte attributes and lone surrogate text are refused at
serialization; a conversion already committed before result read-back fails
remains an intact output, with a degraded record unsuitable for materialization.

Full-granule provenance and digests are in `tests/fixtures/corpus.json`.
For a complete small worked example, run
`python examples/verified_conversion.py NEW_DEMO_DIRECTORY`.
Run `python tools/corpus.py DATA_DIRECTORY --fetch --include-references`, then set
`NCARNATE_GRANULE_DIR` and run
`python tools/test_full_corpus.py --junitxml full-granules.xml`.
This release check refuses missing or changed inputs, skipped tests and missing
test cases. Ordinary `pytest` still skips unavailable full-size data. GitHub's
manual **Full-granule validation** workflow runs the same strict checks and
retains reports. The separately packaged, local Zarr
demonstration lives under `companions/zarr-demo` and is excluded from ncarnate's
distribution. It is not a production storage service.

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
result = convert_manifest("manifest.jsonl",
                          ConvertOptions(root="/data/archive", out_dir="./modern"))
```

## Example

The AMSR-E daily 12.5 km sea-ice granule this project grew up around:

| File | Input | Output |
| --- | --- | --- |
| netCDF4 recompression (`--complevel 9`) | 42.6 MB | 19.9 MB |
| HDF-EOS2 → netCDF4 (+ reconstructed lat/lon) | 60.2 MB | 35.5 MB |

Both outputs re-read value-identically to their sources; the conversion
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
python -m pip install -e ".[test,release]" "ruff==0.15.*"
ruff check .
python -m pytest -q
python -m pytest -q tools/tests
```

The test suite runs entirely offline against small committed fixtures trimmed
from real granules (provenance sidecars included); cross-checks against the raw
multi-MB granules self-skip where the local granule store is absent.
CI additionally builds documentation, checks release metadata, installs both
package formats outside the checkout, and tests the separate Zarr companion.
PyPI and TestPyPI publication wait for all those jobs and use the same tested
artifacts. Production runs require a matching version tag and release date.
See [the contributor guide](https://github.com/ErickShepherd/ncarnate/blob/main/CONTRIBUTING.md) for local checks and
[the release guide](https://ncarnate.readthedocs.io/en/latest/releasing.html)
for the publication sequence.

## License

MIT — see [LICENSE](https://github.com/ErickShepherd/ncarnate/blob/main/LICENSE).
Built by [Erick Shepherd](https://erickshepherd.com).
