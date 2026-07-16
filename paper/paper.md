---
title: 'ncarnate: Reconstructing CF-annotated netCDF4 from legacy HDF4/HDF-EOS2 Earth-science data'
tags:
  - Python
  - netCDF
  - HDF-EOS
  - HDF4
  - remote sensing
  - Earth observation
  - CF conventions
  - data conversion
authors:
  - name: Erick Edward Shepherd
    orcid: 0000-0002-4750-6100
    affiliation: 1
affiliations:
  - name: Independent researcher
    index: 1
date: 10 July 2026
bibliography: paper.bib
---

# Summary

`ncarnate` is a Python library and command-line tool that converts legacy
Earth-science data in the HDF4 and HDF-EOS2 formats — the distribution formats of
long-running satellite missions such as MODIS and AMSR-E — into modern,
self-describing netCDF4 [@netcdf] that follows the Climate and Forecast (CF)
metadata conventions [@cf]. It reconstructs the geographic coordinate information
that these legacy files encode in mission-specific ways, so a granule that
current tools such as xarray [@xarray], QGIS, or Panoply cannot read correctly
becomes one they can. `ncarnate` also losslessly recompresses netCDF4 and HDF5
files — changing the compression level, shuffle filter, or storage layout
without altering a single stored value.

Every conversion or recompression is governed by a **fidelity contract**: each
variable's stored values are preserved value-identically — bit-for-bit for
integer and packed data, insensitive only to NaN bit-patterns and signed zero
for floating point (packed integers stay packed; `scale_factor`, `add_offset`,
and `_FillValue` are carried across as declarations, never applied; complex
variables are refused rather than guessed), reconstructed geolocation is
strictly additive so the original information always survives, and every output
is verified against its source — values and attribute storage types — before it
replaces anything. Constructs the tool
cannot convert correctly — unverified projections or exotic swath layouts —
**fail loud** with a named error rather than producing a silently wrong result.

# Statement of need

A large fraction of the multi-decadal satellite Earth-observation record is
distributed in HDF4 and HDF-EOS2 [@hdfeos], formats that predate — and are not
readable by — the netCDF4/HDF5-based Python stack that now dominates geoscience
analysis. Modern tools such as xarray [@xarray] read netCDF4 and HDF5, but they
cannot open HDF-EOS2 grids and swaths with usable geolocation: latitude and
longitude are stored implicitly — as GCTP projection parameters for gridded
products, or as separate, sometimes *dimension-mapped*, geolocation arrays for
swaths — rather than as CF coordinate variables. An analyst who simply wants to
load an AMSR-E sea-ice granule or a MODIS product into xarray therefore faces a
conversion-and-georeferencing problem that general-purpose libraries do not
solve, and often ends up writing fragile, per-mission coordinate code.

Existing converters address parts of this gap but leave others. The HDF Group's
`h4tonccf` translates HDF4 structure to netCDF but does not reconstruct CF
geolocation for the full range of grid and swath layouts [@hdfeos]. NASA's
HDF-EOS to GeoTIFF Conversion Tool (HEG) [@heg] reprojects and subsets granules
but targets GeoTIFF and HDF-EOS output through a GUI/scripted workflow rather
than general, coordinate-annotated netCDF4. Command-line utilities such as `nccopy` and GDAL
[@gdal] handle format translation and, for GDAL, some geolocation, but not the
CF-conformant reconstruction of dimension-mapped swath geolocation — and none
couples the conversion to a verified round-trip fidelity guarantee.

`ncarnate` targets this specific need. It reconstructs CF `latitude`/`longitude`
coordinates and `grid_mapping` variables for GCTP polar-stereographic,
geographic, and Lambert-azimuthal (EASE-Grid) projections — inverting the grid
projection with PROJ [@proj] — and reconstructs both direct and dimension-mapped
swath geolocation, interpolating, for example, 5 km geolocation to a 1 km
science grid through Earth-centred, Earth-fixed (ECEF) space. It preserves every
stored value value-identically — bit-for-bit for integer and packed data — and
verifies each output against its source before replacement, refusing with a
named error any construct it cannot convert correctly. For the hemispheric AMSR-E 12.5 km sea-ice grid the project grew up
around, the reconstructed latitudes and longitudes agree with an independent
reference conversion of the same granule to within about $10^{-5}$ degrees
(roughly one metre), the tolerance the project's cross-check against that
reference enforces. The result is that a legacy granule modern
tooling cannot read becomes a CF-annotated netCDF4 file it can, without the
analyst hand-writing per-mission geolocation code or risking a silent coordinate
error. The same verified machinery, run without conversion, provides lossless
recompression for shrinking archives of already-modern netCDF/HDF5 files.

`ncarnate` is implemented on top of the `netCDF4`, `pyhdf`, `pyproj`, and NumPy
[@numpy] libraries, is tested entirely offline against small fixtures trimmed
from real granules, and is distributed on PyPI and conda-forge.

# Acknowledgements

`ncarnate` builds on the netCDF-4 [@netcdf], HDF-EOS [@hdfeos], PROJ [@proj], and
NumPy [@numpy] libraries and their communities. The test fixtures are trimmed
from publicly distributed NASA and HDF Group granules; the independent AMSR-E
reference conversion used to validate reconstructed geolocation was produced by
The HDF Group.

# References
