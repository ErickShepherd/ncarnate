# Fidelity notes — what ncarnate guarantees lossless, and how it's proven

**Date:** 2026-07-08 (Phase 1). Living document; the Phase-4 test suite pins everything
stated here.

## The contract

ncarnate's correctness contract is **data fidelity**: converting or recompressing a file
changes *storage* (compression level, shuffle, chunk/endian layout, container format), never
*science data*.

### netCDF/HDF5 → netCDF4 (recompression)

Guaranteed preserved when read raw (`set_auto_maskandscale(False)`), value-identical —
bit-for-bit for integer/packed data, NaN- and signed-zero-insensitive for floating point:

- every variable's stored values (packed integers stay packed — no mask-and-scale
  round-trip, which silently re-quantizes);
- `_FillValue` (declared at `createVariable` time, never set after creation),
  `scale_factor`, `add_offset` — carried across as *declarations*, not applied;
- all dimensions (including unlimited-ness), all group hierarchy, all variable and
  global attributes;
- explicit variable endianness (stored layout may change only when the user asks).

Deliberately changed: compression (`zlib`/`complevel`), `shuffle`, chunking when
requested. Output must be smaller or equal at higher `complevel`, and the source is never
replaced until the new file has been written and re-opened successfully.

### HDF4 / HDF-EOS2 → netCDF4 (conversion)

- every SDS's values bit-identical (char8 SDS map to netCDF `NC_CHAR` and round-trip
  byte-for-byte); dimensions (with their HDF4 names) and attributes preserved; fill/scale
  carried as declarations;
- `StructMetadata.0` (and other EOS metadata attributes) preserved **verbatim** as
  attributes of an `HDFEOS_INFORMATION` group — reconstruction never becomes the only copy
  of the truth. Two storage-driven exceptions, both loss-free in information terms:
  trailing NUL *padding* of HDF4 character attributes is stripped (C-string semantics —
  netCDF cannot store it), and character attributes containing *embedded* NULs (a MODIS
  PGE record-separator quirk) are preserved byte-exact as `uint8` arrays with a
  self-describing `<name>__hdf4_encoding` companion attribute;
- reconstructed geolocation (CF grid mappings, coordinates, interpolated swath lat/lon) is
  **additive** and verified by the verification lattice in
  [docs/design/2026-07-08-hdfeos2-geolocation.md](design/2026-07-08-hdfeos2-geolocation.md) —
  four of its five checks are implemented as tests (#1 same-granule THG reference, #3
  decimation oracle, #4 round-trip invariant, #5 corner anchors); the #2 `eos2dump`
  same-grid external reference is **not** implemented (the #1 reference independently
  covers the same grid);
- names illegal or hostile in netCDF (`Land/SeaMask`, `Scan Offset`,
  `Ephemeris/Attitude Source`, grid names with spaces) are sanitized (`/` and whitespace →
  `_`) uniformly across variables, dimensions, attributes, and groups, with each original
  recorded in a companion (`hdf4_name`, `<attr>__hdf4_name`, `hdf4_eos_name`);
- the HDF4 source file is **never** replaced (conversion, not recompression): output goes
  to `dst` or `<stem>.nc` regardless of `--overwrite`.

**Dimension-map interpolation accuracy** (measured against the MOD03 decimation oracle on
real swath geometry, 2026-07-08): interpolated coordinates are exact to ~1–25 m over the
swath interior (nadir p99 < 12 m, overall median 22 m). Two data-limited regions carry
larger error inherent to the format, matching the reference `SWinterpolate` semantics:
scan-boundary rows (MODIS scans overlap — the bowtie effect; median ~0.4 km) and
near-limb edge columns where ground spacing explodes (km-scale tails, worst ~31 km at the
extreme swath edge — the same caveat NASA documents for using 5-km geolocation at swath
edges). Interpolated variables carry a `comment` attribute saying they are interpolated;
edge pixels outside the geolocation envelope are linearly extrapolated; geolocation fill
propagates, never interpolated across.

### Guarantee boundary

- Compound, VLen, enum, and opaque netCDF4 types are **out of scope for v2**: ncarnate
  raises a clear unsupported-type error rather than guessing (none of the target-domain
  files surveyed use them; revisit on demand).
- Already-lossy packing is preserved as-is, never "un-quantized".
- HDF4 inputs are read via the pyhdf SD API: SDS datasets and attributes. Vdata/Vgroup
  payloads beyond SD are out of scope for v2.

## How the round-trip proves it

For every committed fixture: convert/recompress → re-open both files raw → assert
(a) value arrays equal on raw reads (`numpy.array_equal`, with `equal_nan` set for
float/complex — bit-for-bit for integer/packed data, NaN-/±0-insensitive for floats), (b) dimension names,
sizes, and unlimited flags equal, (c) attribute sets equal (fill/scale compared as
declarations), (d) group tree equal, and (e) output ≤ input size at `complevel≥7` for the
compressible fixtures. The packed-integer + `_FillValue` fixture exists precisely because
v1 failed both (a crash and a silent re-quantization).

## Fixture inventory (all committed, all generated by committed scripts)

| Fixture | Generator | Exercises |
|---|---|---|
| `tests/fixtures/data/netcdf/packed_fill.nc` (23 KB) | `make_fixtures.py` | packed int16 + `scale_factor`/`add_offset` + `_FillValue`; unpacked float with fill |
| `tests/fixtures/data/netcdf/nested_groups.nc` (10 KB) | `make_fixtures.py` | nested groups, per-group dims, ancestor-dim use |
| `tests/fixtures/data/netcdf/unlimited_dim.nc` (13 KB) | `make_fixtures.py` | unlimited dimension with 7 records |
| `tests/fixtures/data/netcdf/endianness.nc` (7 KB) | `make_fixtures.py` | explicit big- and little-endian variables |
| `tests/fixtures/data/hdfeos2/amsre_seaice12km_trim.hdf` (167 KB) | `trim_hdfeos2.py` | HDF-EOS2 GRID ×2 (N/S polar stereographic GCTP_PS), verbatim `StructMetadata.0`, attribute-less SDS |
| `tests/fixtures/data/hdfeos2/mod03_trim.hdf` (83 KB) | `trim_hdfeos2.py` | HDF-EOS2 SWATH, full-res 1 km geolocation, dimension maps (inc=2), fill in geolocation |
| `tests/fixtures/data/hdfeos2/myd05_trim.hdf` (188 KB) | `trim_hdfeos2.py` | HDF-EOS2 SWATH, 5 km→1 km dimension maps (offset=2, inc=5), packed int16 data |
| `tests/fixtures/data/hdfeos2/raingrid_trim.hdf` (43 KB) | `trim_hdfeos2.py` | HDF-EOS2 GRID, geographic GCTP_GEO (corners in packed DMS → 1-D lat/lon) |
| `tests/fixtures/data/hdfeos2/amsre_5daysnow_trim.hdf` (124 KB) | `trim_hdfeos2.py` | HDF-EOS2 GRID ×2, EASE-Grid GCTP_LAMAZ (spherical LAEA), grid names with spaces, off-Earth corner cells → fill |

All fixtures respect the plan's < 200 KB Phase-1 budget. Attribute *types* are preserved
exactly (written with each source attribute's true HDF4 type code via `attr.info()`, never
inferred from type-erased Python values — e.g. an INT16 `_FillValue` stays INT16).

HDF-EOS2 fixtures are trimmed from real granules kept outside the repo
(`the local source-granule archive/` + `PROVENANCE.md` there); each fixture has a
`.provenance.json` sidecar (source granule, SHA-256, trim parameters). Trim rules: the
AMSR-E grid fixture keeps `StructMetadata.0` **verbatim** (grid dims unchanged; metadata
lists DataFields not carried over — readers must tolerate that, as with subsetted granules
in the wild); the swath fixtures rewrite only the trimmed dimensions' `Size=` values
(MOD03 along- and across-track: 20×270 at 1 km; MYD05 along-track only: 50/10 lines),
recorded in the sidecars, leaving dimension-map offsets/increments untouched. Generation
was validated against the sources: 15/15 checks (verbatim/structural StructMetadata,
dimension + dimension-map integrity, bit-identical values on kept rows/columns, attribute
values *and* HDF4 type codes preserved) passed 2026-07-08.

Raw-granule cross-checks (the 29–60 MB originals) stay in a local, non-CI test mark.

