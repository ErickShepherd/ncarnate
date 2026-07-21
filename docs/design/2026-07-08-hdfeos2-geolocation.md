# HDF-EOS2 → CF-netCDF4 geolocation subsystem — Design

**Date:** 2026-07-08
**Status:** DRAFT — grounded in the 2026-07-08 fixture survey of three real granules.
**Scope:** This document details the geolocation subsystem: full grid/swath
reconstruction, not HDF4-SDS-only conversion.

## Context / problem

The owner chose (2026-07-08) to make v2 a real **legacy → CF-netCDF4 modernizer**: HDF-EOS2
AMSR-E / MODIS granules must come out the other side with *resolved coordinates* so modern
tools (xarray, QGIS, Panoply, CF-aware anything) can digest them. pyhdf's SD API exposes
HDF-EOS2 files only as raw SDS arrays plus a `StructMetadata.0` text attribute; the EOS
structure layer — grid projections, swath geolocation, dimension maps — has to be parsed and
reconstructed by us. This is the one research-flavored subsystem in v2 and its correctness is
hard to oracle, hence this dedicated design pass before code.

Grounding: the Phase-1 fixture survey (2026-07-08) over three representative granules
(retrieval + provenance in `the local source-granule archive`; survey artifacts in
`a local archive/`):

| Granule | EOS type | Structure found |
|---|---|---|
| `AMSR_E_L3_SeaIce12km_B02_20020619.hdf` (60 MB, NSIDC AE_SI12) | 2× GRID | `NpPolarGrid12km` 608×896 + `SpPolarGrid12km` 632×664, both `GCTP_PS`, Hughes ellipsoid — NH `ProjParams=(6378273,-0.006694,0,0,-45000000,70000000,…)`, SH `(6378273,-0.006694,0,0,0,-70000000,…)` — `UpperLeftPointMtrs`/`LowerRightMtrs`, `GridOrigin=HDFE_GD_UL`; 62 int16 SDS, **zero SDS attributes** (no fill/scale — semantics live in the product doc) |
| `MOD03.A2002299.0710.006.2012261211245.hdf` (29 MB, LAADS MOD03 C6) | SWATH | Full 1-km `Latitude`/`Longitude` float32 (2030×1354, `_FillValue=-999`), dimension maps `nscans*10→nscans*20`, `mframes→mframes*2` (offset 0, increment 2) feeding three int8 offset fields at 4060×2708; SDS names contain spaces and a slash (`Land/SeaMask`) |
| `MYD05_L2.A2020060.1635.061.2020061153519.hdf` (7 MB, LAADS MYD05_L2 C6.1) | SWATH | 5-km `Latitude`/`Longitude` (406×270) with dimension maps `offset=2, increment=5` to 1-km data dims (2030×1354); packed int16 data (`scale_factor`/`add_offset`/`_FillValue`) |

Independent reference: The HDF Group's own conversion of the *same* AMSR-E granule
(`AMSR_E_L3_SeaIce12km_B02_20020619_flatten.nc`, archived) — float64 2-D `lat`/`lon` +
`coordinates="lon lat"`, NH grid only. Also archived: `eos2dump` lat/lon text dumps for the
same *named* grids (`NpPolarGrid12km` etc. are fixed grid definitions across granules).

## Goals & non-goals

**Goals**

- Parse `StructMetadata.0` (concatenated `StructMetadata.X` parts) into a typed model:
  grids (name, dims, projection, params, corners, origin), swaths (dims, dimension maps,
  geolocation fields, data fields).
- **Grid:** emit CF grid mappings + reconstructed coordinates — 1-D projection coordinates
  `x`/`y` in metres (cell centers derived from corner points, dims, `GridOrigin`), a
  `grid_mapping` container variable (e.g. `polar_stereographic` with CF attributes derived
  from GCTP params), and 2-D `lat`/`lon` auxiliary coordinates via inverse projection.
- **Swath:** attach existing geolocation as CF auxiliary coordinates; for data fields on
  dimension-mapped (finer) dims, reconstruct interpolated per-pixel `lat`/`lon` from the
  `DimensionMap` offset/increment — interpolating through ECEF, never raw lat/lon.
- **Nothing is ever lost:** the verbatim `StructMetadata.0` (and `CoreMetadata.0` etc.) is
  preserved in the output; SDS values remain bit-identical raw copies per the parent design's
  fidelity contract. Geolocation reconstruction only *adds* variables/attributes.
- Fail loud and specific: an unsupported projection or swath layout raises a clear error
  naming the unsupported construct (with an escape hatch to convert SDS-only), never a
  silently wrong coordinate.

**Non-goals (scope fence)**

- Implementing/validating all 30+ GCTP projections. The dispatch table is generic, but v2
  *claims* only what fixtures prove: `GCTP_PS` (polar stereographic), `GCTP_GEO`
  (geographic), and `GCTP_LAMAZ` (EASE-Grid) if the small archived 5DaySnow fixture is
  adopted. Others raise `UnsupportedProjectionError`.
- MOD03's `Scan Offset`/`Track Offset`/`Height Offset` sub-pixel refinement scheme (int8
  offsets refining 1-km geolocation to 500 m). We convert those SDS faithfully but do not
  *apply* them; applying is MODIS-specific science logic.
- HDF-EOS2 POINT structures; Vgroup/Vdata payloads beyond what the SD API exposes (v2 is
  SDS-scoped, per parent design).
- Terrain/parallax corrections, datum shifts, or any coordinate "improvement" beyond what
  StructMetadata declares.

## Approach

New module boundary inside the package (names indicative):

```
ncarnate/
  eos/
    structmeta.py   # StructMetadata ODL text -> EosGrid / EosSwath dataclasses
    gctp.py         # GCTP (code, ProjParams, SphereCode) -> pyproj.CRS + CF grid-mapping attrs
    grid.py         # EosGrid -> x/y 1-D coords, grid_mapping var, 2-D lat/lon (inverse proj)
    swath.py        # EosSwath -> coordinate attachment + dimension-map interpolation (ECEF)
```

Pipeline position: the pyhdf reader (Phase 3b) produces the SDS payload; when
`structmeta.parse()` finds GRID/SWATH structures, the eos subsystem decorates the output
model with coordinate variables, `grid_mapping`/`coordinates` attributes, and dimension
renames — then the single netCDF4 writer serializes as usual.

**StructMetadata parsing.** The format is ODL (PVL) text. We parse with a small dedicated
ODL-group parser (nested `GROUP=`/`OBJECT=` blocks), not regex spot-lifts: dimension maps,
multi-grid files, and index maps need real structure. Concatenate `StructMetadata.0..N`
before parsing (files >32 KB split the attribute).

**GCTP → CF/pyproj.** GCTP `ProjParams` follow documented conventions (param 0/1 semi-major
/ semi-minor where a negative param 1 encodes eccentricity-squared; angular params in packed
DMS `DDDMMMSSS.SS`). `gctp.py` decodes these into (a) a `pyproj.CRS` for computation and (b)
the CF grid-mapping attribute dict. For the surveyed case:
`ProjParams=(6378273,-0.006694,0,0,-45000000,70000000,…)` →
`+proj=stere +lat_0=90 +lat_ts=70 +lon_0=-45 +a=6378273 +es=0.006694` ↔ CF
`polar_stereographic` (`straight_vertical_longitude_from_pole=-45`,
`standard_parallel=70`, `semi_major_axis=6378273`, …). Hemisphere sign comes from lat_ts's
sign per GCTP PS convention.

**Grid coordinates.** Cell-center `x[i] = UL_x + (i + 0.5) * (LR_x − UL_x)/XDim` (and
similarly `y`, descending for `GridOrigin=HDFE_GD_UL`); 2-D `lat`/`lon` from
`pyproj.Transformer` inverse over the `x`/`y` mesh, stored float64 (zlib-compressed; they
compress well and exactness beats the ~40 % size delta of float32).
`PixelRegistration` absent → HDF-EOS default `HDFE_CENTER` (assumed and asserted; a
non-center registration raises until a fixture proves the variant).

**Multi-grid / multi-swath layout.** One netCDF4 **group per EOS structure**, named after
`GridName`/`SwathName`; single-structure files still get the group (uniformity beats
special-casing; xarray reads groups explicitly). Dimensions live in their group with their
EOS names (`YDim`, `XDim`, `Cell_Along_Swath_1km`, …).

**Swath coordinates.** Geolocation SDS are copied raw (fidelity), then *additionally*
declared CF: `units=degrees_north/degrees_east` set on the lat/lon coordinate variables
(MOD03 ships nonconforming `units="degrees"`; original attributes are preserved under
`original_units` when we normalize), `coordinates="Longitude Latitude"` attached to data
fields sharing the geo dims. For fields on dimension-mapped dims, build interpolated
`lat/lon` at data resolution: geo pixel `g` sits at data index `offset + increment·g`;
interior data pixels interpolate linearly between bracketing geo pixels, edge pixels
extrapolate (MYD05: across-track 1-km columns 0–1 and 1348–1353 lie outside the 5-km
envelope — the last of the 270 geo centers maps to column 2 + 5·269 = 1347) — all in ECEF
(lat/lon → unit-sphere XYZ → interpolate → back), which is antimeridian- and pole-safe where
bilinear on raw degrees is not. Geolocation `_FillValue` pixels propagate as fill in the
interpolated output (never interpolated across).

**Name sanitization.** SDS names like `Land/SeaMask` are illegal in netCDF (`/` is the group
separator) and names with spaces are hostile downstream. Policy: replace `/` and whitespace
with `_`, record the exact original in an `hdf4_name` variable attribute. Applied uniformly
by the writer, not just in the eos subsystem.

**Verification (the oracle problem).** No frozen exit-code oracle exists for reconstructed
coordinates; the test strategy is a lattice of independent cross-checks, each cheap and
committed to CI:

1. **Same-granule external reference:** our AMSR-E NH `lat`/`lon` vs The HDF Group's own
   `_flatten.nc` conversion of the identical granule (tolerance ≤ 1e-5°, ~1 m).
2. **Same-grid external reference:** `eos2dump` text dumps for `NpPolarGrid12km` (archived
   alongside the zoo) — grid definitions are granule-independent.
3. **Internal decimation oracle for dimension maps:** take MOD03's true 1-km lat/lon,
   decimate to a synthetic 5-km geolocation with `offset=2, increment=5`, run our
   interpolation back to 1 km, and compare against the withheld truth (assert interior
   error ≪ 1 km and bounded tails). This tests the interpolator on real swath geometry
   with a real answer key, no network.
4. **Round-trip invariant:** forward-project our 2-D lat/lon back through the CRS and
   recover the 1-D x/y mesh to numerical precision (catches CRS/axis-order/hemisphere
   errors independent of any reference file).
5. **Spot anchors:** known published grid corners (NSIDC documents AE_SI12 corner
   coordinates) as literal assertions.

CI fixtures are *trimmed* derivatives (a few SDS + full StructMetadata, < 200 KB) generated
by a committed script from the raw granules; raw granules stay outside the repo.

## Key decisions

- **pyproj (PROJ) for all projection math; never hand-rolled Snyder formulas.** The GCTP
  translation layer is small and testable; the numerics come from the most battle-tested
  geodesy library in existence. Cost: a real dependency (~wheels available on all targets),
  accepted — correctness of coordinates is the product here. (Alternative rejected below.)
- **Reconstruction is additive; raw fidelity is untouched.** Every SDS still round-trips
  bit-identically; StructMetadata is preserved verbatim in the output (under a
  `HDFEOS_INFORMATION` group attribute, mirroring HDF-EOS5 practice). If our geolocation were
  ever found wrong, the original information is still all there — the conversion never
  becomes the only copy of the truth.
- **Group-per-EOS-structure output layout.** The AMSR-E fixture has two grids that reuse the
  same dimension names at different sizes (`YDim` 896 vs 664), colliding in a flat namespace; THG's reference dodged this by
  emitting one file per grid. Groups keep one-input→one-output, keep EOS dim names, and are
  first-class in netCDF4/xarray. Cost: strict-classic-model consumers need `--flatten`-style
  handling, deferred as a non-goal until asked for.
- **ECEF interpolation for dimension maps.** Linear interpolation in lat/lon breaks at the
  antimeridian and near poles — precisely where polar-orbiter swaths live. Interpolating in
  3-D Cartesian and renormalizing is the standard robust construction (what MODIS tooling
  itself does), and the decimation oracle (#3) quantifies its error on real geometry.
- **Claim-what-you-test projection support.** Generic GCTP dispatch table, but unsupported
  codes fail loud with the code name and the file's ProjParams in the message plus a
  `--no-geolocation` escape hatch (SDS-only conversion, geolocation skipped, warning
  emitted). A wrong coordinate is worse than a refused conversion.
- **Attribute normalization is confined and reversible.** Only geolocation coordinate
  variables get CF-normalized attributes, and every overwritten original is kept under an
  `original_*` name. Data-variable attributes are copied raw (the AMSR-E fixture proves some
  products have *no* attributes to normalize anyway — we add nothing we can't source from
  StructMetadata or the file itself).

## Alternatives considered

- **HDF4-SDS-only conversion (no geolocation).** Simplest and honest, but the owner rejected
  it deliberately: his granules need resolved coordinates; without them the output is not
  "tool-digestible" and the tool's true purpose is unmet. Kept only as the
  `--no-geolocation` escape hatch / unsupported-projection fallback.
- **Wrap the HDF-EOS2 C library (or pyhdfeos) instead of parsing StructMetadata ourselves.**
  The C library is the reference implementation (its `gdij2ll` is exactly this), but it is
  legacy, packaging-hostile (no wheels, aging toolchain), and drags GCTP with it; pyhdfeos is
  abandoned (~2015). The ODL text + GCTP-param conventions are stable, small, documented
  formats — parsing them with a few hundred lines plus pyproj is the maintainable path, and
  OPeNDAP's `hdf4_handler` (HDFEOS2*.cc) provides a proven open-source map of every corner
  case if we hit one.
- **GDAL as the conversion engine** (it reads HDF4/HDF-EOS2 and understands GCTP). Rejected
  in the parent design already for weight; additionally its HDF-EOS output model (subdatasets,
  GeoTIFF-ish semantics) fights the lossless-SDS + CF-groups contract.
- **Hand-rolled polar-stereographic inverse (Snyder).** Two pages of math, no new dependency
  — but every future projection re-opens the numerics question, ellipsoidal PS variants are
  exactly where sign/series errors hide, and we'd be testing our math against itself. pyproj
  makes verification checks #1/#2/#4 independent of our own code.
- **Emit only 1-D x/y + grid_mapping (no 2-D lat/lon) for grids.** Strictly CF-sufficient
  and smaller, but most of the owner's "modern tools should digest this" bar (quick xarray
  selection by lat/lon, Panoply plots) is met by auxiliary 2-D lat/lon; disk is cheap and
  they compress. Both are emitted; this also matches the THG reference conversion.
- **Interpolate dimension maps in raw lat/lon with antimeridian unwrapping.** Equivalent
  accuracy when done carefully, but the unwrap logic is exactly the bug-prone part; ECEF
  needs no cases.

## Risks

- **GCTP parameter decoding subtleties** (packed DMS, negative-param conventions, sphere vs
  ellipsoid selection via `SphereCode=-1` + params). A misread yields plausible-looking but
  shifted coordinates. Mitigated by verification lattice #1/#2/#5 (external references catch
  constant-offset and hemisphere errors that internal checks can't).
- **Wayback/THG reference artifacts could themselves be imperfect.** The `_flatten.nc` is
  used as a cross-check with tolerance, never as the definition of correct; disagreement
  beyond tolerance fails the build and demands investigation, not auto-trust of either side.
- **Fixture trimming changes structure** (e.g. dropping an SDS a DimensionMap references).
  The trim script must preserve StructMetadata verbatim and every geolocation-referenced
  object; the survey JSONs pin the expected structure and the tests open the trimmed fixture
  through the same parser.
- **Edge extrapolation error at swath borders** (MYD05's first two and last six across-track
  1-km columns lie outside the 5-km envelope). Bounded and measured by oracle #3; documented in
  `docs/fidelity-notes.md` as reconstruction (not measurement) pixels.
- **pyproj/PROJ version drift changing distant-decimal results.** Pin a floor version; the
  tolerance-based tests are deliberately robust to last-ulp drift.

## Open questions

- **Adopt the 108 KB `AMSR_E_L3_RainGrid` + 2.1 MB `5DaySnow` archived granules as
  additional fixtures** (GEO and LAMAZ/EASE-Grid projections) in Phase 3b, or defer LAMAZ to
  a v2.x? Leaning adopt-both (they're tiny and archived complete); implementer's call at
  Phase-3b start.
- **Where `coordinates` attributes point for dimension-mapped fields:** at the interpolated
  full-res lat/lon (max usability) — current plan — or at the native 5-km ones (max honesty)?
  Plan: full-res, with the 5-km originals retained and the interpolated variables flagged
  `comment="interpolated from 5km geolocation via HDF-EOS2 dimension map"`.
- Naming of reconstructed variables when a swath already has `Latitude`/`Longitude` at geo
  resolution (MYD05): `Latitude_1km`/`Longitude_1km` vs EOS-style suffixes. Cosmetic;
  implementer's call.

## Rollout / migration

Lands as **Phase 3b** of the parent plan (after the lossless netCDF→netCDF core in Phase 3):
first `structmeta.py` + `gctp.py` with unit tests (parsing is projection-free and cheap to
oracle), then `grid.py` against verification #1/#2/#4/#5, then `swath.py` against #3.
Stop-anywhere stays safe: until `eos/` lands, HDF-EOS2 files convert SDS-only behind the
same code path as the escape hatch. The verification lattice enters CI in Phase 4 with the
trimmed fixtures; raw-granule cross-checks (60 MB inputs) stay in a local, non-CI test mark.
