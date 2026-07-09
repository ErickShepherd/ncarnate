"""Cross-checks against the raw multi-MB granules kept outside the repo
(the local source-granule archive/). Local-only: marked raw_granules and skipped
wherever the granule directory is absent (always skipped in CI)."""

import numpy as np
import netCDF4 as nc
import pytest

from ncarnate import recompress
from ncarnate.eos.swath import interpolate_geolocation

from conftest import GRANULE_DIR

AMSRE = GRANULE_DIR / "AMSR_E_L3_SeaIce12km_B02_20020619.hdf"
THG_REFERENCE = GRANULE_DIR / "AMSR_E_L3_SeaIce12km_B02_20020619_flatten.nc"
MOD03 = GRANULE_DIR / "MOD03.A2002299.0710.006.2012261211245.hdf"

pytestmark = [
    pytest.mark.raw_granules,
    pytest.mark.skipif(
        not GRANULE_DIR.is_dir(), reason="raw granule store not present"
    ),
]


def test_amsre_conversion_matches_thg_reference(workdir):
    """Verification lattice #1: The HDF Group's independent conversion of
    the SAME granule (NH grid) agrees on every value and coordinate."""
    output = recompress(
        str(AMSRE), dst=str(workdir / "amsre.nc"), complevel=1
    )
    with nc.Dataset(output) as ours, nc.Dataset(THG_REFERENCE) as reference:
        grid = ours.groups["NpPolarGrid12km"]
        assert float(np.abs(grid["lat"][:] - reference["lat"][:]).max()) \
            <= 1e-5
        longitude_delta = np.abs(
            (grid["lon"][:] - reference["lon"][:] + 180) % 360 - 180
        )
        assert float(longitude_delta.max()) <= 1e-5
        compared = 0
        for name, ref_var in reference.variables.items():
            if name in ("lat", "lon") or name not in grid.variables:
                continue
            our_var = grid.variables[name]
            our_var.set_auto_maskandscale(False)
            ref_var.set_auto_maskandscale(False)
            assert np.array_equal(our_var[...], ref_var[...]), name
            compared += 1
        assert compared == 31


def test_mod03_decimation_oracle(workdir):
    """Verification lattice #3 on real swath geometry: decimate the true
    1-km geolocation to synthetic 5-km, interpolate back, compare against
    the withheld truth. Interior nadir error is metres; the documented
    data-limited regions (scan boundaries, near-limb edge columns) carry
    km-scale error inherent to the format."""
    output = recompress(
        str(MOD03), dst=str(workdir / "mod03.nc"), complevel=1
    )
    with nc.Dataset(output) as ours:
        group = ours.groups["MODIS_Swath_Type_GEO"]
        lat_var, lon_var = group["Latitude"], group["Longitude"]
        lat_var.set_auto_maskandscale(False)
        lon_var.set_auto_maskandscale(False)
        lat, lon = lat_var[...], lon_var[...]
        fill = float(lat_var.getncattr("_FillValue"))

    out_lat, out_lon = interpolate_geolocation(
        lat[2::5, 2::5], lon[2::5, 2::5], [(2, 5), (2, 5)],
        lat.shape, fill,
    )
    valid = (lat != fill) & (out_lat != fill)

    def xyz(la, lo):
        la = np.deg2rad(la.astype(np.float64))
        lo = np.deg2rad(lo.astype(np.float64))
        return np.stack((np.cos(la) * np.cos(lo),
                         np.cos(la) * np.sin(lo), np.sin(la)))

    chord = np.linalg.norm(
        xyz(lat, lon) - xyz(out_lat, out_lon), axis=0
    )
    error_km = 6371.0 * 2 * np.arcsin(np.clip(chord / 2, 0, 1))

    assert float(np.median(error_km[valid])) < 0.1        # metres-scale
    # Within-scan nadir strip: essentially exact. Cross-scan rows (the
    # MODIS bowtie overlap, r % 10 in {8, 9, 0, 1}) are excluded here —
    # their ~0.4 km median is inherent to the format, not our code.
    within_scan = np.isin(np.arange(lat.shape[0]) % 10,
                          (2, 3, 4, 5, 6, 7))[:, None]
    nadir = valid[:, 600:750] & within_scan
    assert float(np.percentile(error_km[:, 600:750][nadir], 99)) < 0.05
    # Cross-scan rows stay bounded at the documented km scale.
    cross_scan = valid & ~within_scan
    assert float(np.median(error_km[cross_scan])) < 1.0
