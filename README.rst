ncarnate
========

|ci| |license| |python|

.. |ci| image:: https://github.com/ErickShepherd/ncarnate/actions/workflows/ci.yml/badge.svg
   :target: https://github.com/ErickShepherd/ncarnate/actions/workflows/ci.yml
   :alt: CI status

.. |license| image:: https://img.shields.io/badge/license-MIT-blue.svg
   :target: https://github.com/ErickShepherd/ncarnate/blob/master/LICENSE
   :alt: MIT License

.. |python| image:: https://img.shields.io/badge/python-3.10%E2%80%933.13-blue.svg
   :target: https://pypi.org/project/ncarnate/
   :alt: Python 3.10-3.13

Reincarnate legacy scientific data as modern netCDF4.

ncarnate reads netCDF3, netCDF4/HDF5, and HDF4/HDF-EOS2 files and writes
recompressed, CF-annotated netCDF4. It does two jobs:

- **Recompress** netCDF/HDF5 files — change the compression level,
  shuffle filter, or storage layout without changing a single stored
  value.
- **Convert** HDF4 and HDF-EOS2 granules (AMSR-E, MODIS, and kin) to
  netCDF4, reconstructing the CF coordinates that modern tools (xarray,
  QGIS, Panoply) need: grid projections become CF grid mappings with
  1-D ``x``/``y`` and 2-D ``lat``/``lon`` coordinates, swath geolocation
  is attached as CF coordinates, and dimension-mapped (e.g. 5 km → 1 km)
  geolocation is interpolated through ECEF space.

The fidelity contract
---------------------

Converting or recompressing a file changes *storage*, never *science
data*:

- Every variable's stored values are preserved **bit-identically** —
  packed integers stay packed; ``scale_factor``/``add_offset``/
  ``_FillValue`` are carried across as declarations, never applied.
- Every dimension (including unlimited-ness), attribute (including its
  type), and group survives. HDF-EOS2 ``StructMetadata`` is preserved
  verbatim; names netCDF cannot hold are sanitized with the original
  recorded in a companion attribute.
- Geolocation reconstruction is strictly **additive**: the original
  information always rides along, so the conversion never becomes the
  only copy of the truth.
- Every output is **verified against the source value-for-value before
  it replaces anything**. A source file is never destroyed by a failed
  run, and HDF4 sources are never replaced at all.
- Unsupported constructs (user-defined netCDF types, unverified GCTP
  projections, exotic swath layouts) **fail loud** with a named error
  rather than guessing — a wrong coordinate is worse than a refused
  conversion. ``--no-geolocation`` converts the raw payload anyway.

The details, the guarantee boundary, and how the test suite pins each
clause live in ``docs/fidelity-notes.md``.

Installation
------------

.. code-block:: console

   pip install ncarnate

On **Linux (x86_64)** and **macOS (arm64)**, every dependency — including
``pyhdf`` — installs as a self-contained binary wheel with no system
libraries required (the ``pyhdf`` wheels there bundle the HDF4 C
library). On platforms without a repaired ``pyhdf`` wheel (e.g. Linux
aarch64), building from sdist requires the system HDF4 library first
(Debian/Ubuntu: ``apt install libhdf4-dev``).

**Windows:** the netCDF/HDF5 *recompression* path works from PyPI wheels
out of the box, but the HDF4/HDF-EOS2 *conversion* path does **not** —
``pyhdf``'s Windows wheel ships no HDF4 runtime, so ``import pyhdf`` fails
with a DLL-load error. For HDF4 support on Windows, install
``pyhdf`` from **conda-forge** first (``conda install -c conda-forge
pyhdf``), which provides a properly linked build with the HDF4 runtime,
then ``pip install ncarnate`` into that same environment — or use **WSL**
and follow the Linux instructions.

Command line usage
------------------

.. code-block:: console

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

Exit codes: ``0`` success, ``1`` one or more files failed, ``2`` bad
input paths or arguments.

Library usage
-------------

.. code-block:: python

   from ncarnate import recompress

   # Lossless recompression; returns the output path.
   recompress("observations.nc", complevel=9)

   # HDF-EOS2 conversion; the .hdf source is never replaced.
   recompress("granule.hdf", dst="granule.nc")

Example
-------

The AMSR-E daily 12.5 km sea-ice granule this project grew up around:

===============================================  ==========  ===========
File                                             Input       Output
===============================================  ==========  ===========
netCDF4 recompression (``--complevel 9``)        42.6 MB     19.9 MB
HDF-EOS2 → netCDF4 (+ reconstructed lat/lon)     60.2 MB     35.5 MB
===============================================  ==========  ===========

Both outputs re-read bit-identically to their sources; the conversion
additionally carries CF ``polar_stereographic`` grid mappings and
coordinates for both hemispheric grids. The northern grid's
reconstructed latitudes/longitudes agree with The HDF Group's
independent conversion of the same granule to within 10\ :sup:`-5`
degrees (about a metre), the tolerance the test suite enforces.

Supported inputs
----------------

- **netCDF4 / HDF5** and **netCDF3** — recompressed via the netCDF4
  library.
- **HDF4 / HDF-EOS2** — read via the pyhdf SD API. GRID structures with
  GCTP polar-stereographic, geographic, and Lambert-azimuthal
  (EASE-Grid) projections; SWATH structures with direct or
  dimension-mapped geolocation. Output is always netCDF4 — HDF4 is
  never written.

Development
-----------

.. code-block:: console

   pip install -e ".[test]"
   ruff check .
   pytest

The test suite runs entirely offline against small committed fixtures
trimmed from real granules (provenance sidecars included); cross-checks
against the raw multi-MB granules self-skip where the local granule
store is absent.

License
-------

MIT — see ``LICENSE``.
