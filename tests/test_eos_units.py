"""Unit tests for the eos subsystem: ODL parsing, GCTP decoding, grid
cell-center math + the projection round-trip invariant (verification
lattice #4), and ECEF dimension-map interpolation."""

import dataclasses

import numpy as np
import pyproj
import pytest
from pyhdf.SD import SD, SDC

from ncarnate.eos import structmeta
from ncarnate.eos.gctp import decode_packed_dms, projection_info
from ncarnate.eos.grid import reconstruct
from ncarnate.eos.swath import interpolate_geolocation
from ncarnate.errors import (
    EosParseError,
    UnsupportedGeolocationError,
    UnsupportedProjectionError,
)

from conftest import HDFEOS2_FIXTURES, structmetadata_text


def structmetadata_of(stem: str) -> structmeta.EosStructMetadata:
    fixture = next(f for f in HDFEOS2_FIXTURES if stem in f.stem)
    source = SD(str(fixture), SDC.READ)
    try:
        text = structmetadata_text(source.attributes())
    finally:
        source.end()
    return structmeta.parse(text)


# --- structmeta ---------------------------------------------------------

def test_parses_amsre_grids():
    parsed = structmetadata_of("seaice")
    assert [g.name for g in parsed.grids] \
        == ["NpPolarGrid12km", "SpPolarGrid12km"]
    nh = parsed.grids[0]
    assert (nh.x_dim, nh.y_dim) == (608, 896)
    assert nh.projection == "GCTP_PS"
    assert nh.upper_left == (-3850000.0, 5850000.0)
    assert nh.lower_right == (3750000.0, -5350000.0)
    assert nh.proj_params[:2] == (6378273.0, -0.006694)
    assert len(nh.data_fields) == 31


def test_parses_myd05_dimension_maps():
    parsed = structmetadata_of("myd05")
    swath = parsed.swaths[0]
    assert swath.name == "mod05"
    maps = {m.geo_dimension: m for m in swath.dimension_maps}
    along = maps["Cell_Along_Swath_5km"]
    assert (along.data_dimension, along.offset, along.increment) \
        == ("Cell_Along_Swath_1km", 2, 5)
    assert {f.name for f in swath.geo_fields} == {"Latitude", "Longitude"}
    assert not swath.has_index_maps and not swath.has_merged_fields


@pytest.mark.parametrize("text", [
    "GROUP=GridStructure\nEND_GROUP=SwathStructure\nEND\n",  # mismatched
    "GROUP=GridStructure\nEND\n",                            # unclosed
    "GROUP=A\nDimList=(1,2\nEND_GROUP=A\nEND\n",             # unbalanced
    "GROUP=A\njust some words\nEND_GROUP=A\nEND\n",          # malformed
])
def test_malformed_odl_fails_loud(text):
    with pytest.raises(EosParseError):
        structmeta.parse(text)


def test_structmetadata_parts_ordered_numerically():
    # A >=11-part granule must concatenate .10 AFTER .2, not lexicographically.
    from ncarnate.hdf4 import _structmetadata_text
    attrs = {f"StructMetadata.{i}": f"[{i}]" for i in range(12)}
    text = _structmetadata_text(attrs)
    assert text == "".join(f"[{i}]" for i in range(12))


def test_unbalanced_parentheses_bounded():
    # A never-closing parenthesis must fail loud without accumulating the
    # whole (hostile) body — O(n) with a continuation bound, not O(n^2).
    text = "GROUP=A\nDimList=(\n" + "x\n" * 10000 + "END\n"
    with pytest.raises(EosParseError):
        structmeta.parse(text)


@pytest.mark.parametrize("token", ["nan", "inf", "-inf", "1e999"])
def test_non_finite_numbers_fail_loud(token):
    # Untrusted StructMetadata numbers must not reach pyproj/numpy as
    # nan/inf; the parser rejects them at the boundary.
    text = (
        "GROUP=GridStructure\n\tGROUP=GRID_1\n"
        f"\t\tXDim=608\n\t\tYDim=896\n\t\tProjParams=({token},0,0)\n"
        "\tEND_GROUP=GRID_1\nEND_GROUP=GridStructure\nEND\n"
    )
    with pytest.raises(EosParseError):
        structmeta.parse(text)


# --- gctp ---------------------------------------------------------------

def test_packed_dms_decoding():
    assert decode_packed_dms(-45000000.0) == pytest.approx(-45.0)
    assert decode_packed_dms(70000000.0) == pytest.approx(70.0)
    assert decode_packed_dms(45030000.0) == pytest.approx(45.5)
    assert decode_packed_dms(120030030.0) == pytest.approx(
        120 + 30 / 60 + 30 / 3600
    )


def test_packed_dms_rejects_invalid():
    with pytest.raises(UnsupportedProjectionError):
        decode_packed_dms(45099000.0)  # 99 minutes


def test_polar_stereographic_decoding():
    nh = structmetadata_of("seaice").grids[0]
    info = projection_info(nh)
    attrs = info.cf_attributes
    assert attrs["grid_mapping_name"] == "polar_stereographic"
    assert attrs["latitude_of_projection_origin"] == 90.0
    assert attrs["standard_parallel"] == pytest.approx(70.0)
    assert attrs["straight_vertical_longitude_from_pole"] \
        == pytest.approx(-45.0)
    assert attrs["semi_major_axis"] == pytest.approx(6378273.0)
    # ProjParams[1] = -e^2: b = a * sqrt(1 - 0.006694)
    assert attrs["semi_minor_axis"] == pytest.approx(6356889.074, abs=1e-3)


def test_southern_hemisphere_from_negative_standard_parallel():
    sh = structmetadata_of("seaice").grids[1]
    attrs = projection_info(sh).cf_attributes
    assert attrs["latitude_of_projection_origin"] == -90.0
    assert attrs["standard_parallel"] == pytest.approx(-70.0)


def test_unsupported_projection_fails_loud():
    grid = dataclasses.replace(
        structmetadata_of("seaice").grids[0], projection="GCTP_SOM"
    )
    with pytest.raises(UnsupportedProjectionError, match="GCTP_SOM"):
        projection_info(grid)


def test_short_ps_projparams_fails_loud():
    grid = dataclasses.replace(
        structmetadata_of("seaice").grids[0],
        proj_params=(6378273.0, -0.006694),
    )
    with pytest.raises(UnsupportedProjectionError, match="at least 6"):
        projection_info(grid)


def test_invalid_eccentricity_fails_loud():
    # ProjParams[1] = -e^2 below -1 means e^2 > 1 — not a valid ellipsoid;
    # must fail loud, not sqrt() a negative and raise a bare ValueError.
    grid = dataclasses.replace(
        structmetadata_of("seaice").grids[0],
        proj_params=(6378273.0, -2.0, 0.0, 0.0, -45000000.0, 70000000.0),
    )
    with pytest.raises(UnsupportedProjectionError, match="eccentricity"):
        projection_info(grid)


def test_sphere_code_table_fails_loud():
    grid = dataclasses.replace(
        structmetadata_of("seaice").grids[0],
        proj_params=(0.0,) * 13,
    )
    with pytest.raises(UnsupportedProjectionError):
        projection_info(grid)


# --- grid ---------------------------------------------------------------

def test_cell_centers_are_half_cell_inside_corners():
    grid = dataclasses.replace(
        structmetadata_of("seaice").grids[0],
        x_dim=10, y_dim=10,
        upper_left=(0.0, 100.0), lower_right=(100.0, 0.0),
    )
    geolocation = reconstruct(grid)
    assert geolocation.x[0] == pytest.approx(5.0)
    assert geolocation.x[-1] == pytest.approx(95.0)
    assert geolocation.y[0] == pytest.approx(95.0)   # north-down (UL origin)
    assert geolocation.y[-1] == pytest.approx(5.0)


def test_ps_round_trip_recovers_grid_mesh():
    # Verification lattice #4: forward-project the reconstructed 2-D
    # lat/lon back through the CRS and recover the 1-D x/y mesh.
    grid = structmetadata_of("seaice").grids[0]
    geolocation = reconstruct(grid)
    transformer = pyproj.Transformer.from_crs(
        "EPSG:4326", geolocation.projection.crs, always_xy=True
    )
    x_mesh, y_mesh = np.meshgrid(geolocation.x, geolocation.y)
    x_back, y_back = transformer.transform(
        geolocation.longitude, geolocation.latitude
    )
    assert np.abs(x_back - x_mesh).max() < 1e-3
    assert np.abs(y_back - y_mesh).max() < 1e-3


def test_ps_corner_anchor():
    # Verification lattice #5: the NH 12.5 km grid's corner cell centers,
    # cross-validated 2026-07-08 against The HDF Group's independent
    # conversion of the same granule (agreement at 1e-13 degrees).
    geolocation = reconstruct(structmetadata_of("seaice").grids[0])
    assert float(geolocation.latitude[0, 0]) == pytest.approx(
        31.0416016, abs=1e-6
    )
    assert float(geolocation.longitude[0, 0]) == pytest.approx(
        168.3350796, abs=1e-6
    )


def test_unsupported_grid_origin_fails_loud():
    grid = dataclasses.replace(
        structmetadata_of("seaice").grids[0], grid_origin="HDFE_GD_LL"
    )
    with pytest.raises(UnsupportedGeolocationError, match="HDFE_GD_LL"):
        reconstruct(grid)


@pytest.mark.parametrize("x_dim,y_dim", [(0, 896), (608, 0), (-1, 896)])
def test_non_positive_dimensions_fail_loud(x_dim, y_dim):
    grid = dataclasses.replace(
        structmetadata_of("seaice").grids[0], x_dim=x_dim, y_dim=y_dim
    )
    with pytest.raises(UnsupportedGeolocationError, match="non-positive"):
        reconstruct(grid)


# --- swath --------------------------------------------------------------

def synthetic_geolocation(shape=(20, 30)):
    rows = np.linspace(10.0, 12.0, shape[0])[:, None]
    cols = np.linspace(40.0, 43.0, shape[1])[None, :]
    latitude = np.broadcast_to(rows, shape).astype(np.float32).copy()
    longitude = np.broadcast_to(cols, shape).astype(np.float32).copy()
    return latitude, longitude


def great_circle_km(lat_a, lon_a, lat_b, lon_b):
    def xyz(lat, lon):
        lat, lon = np.deg2rad(lat.astype(np.float64)), \
            np.deg2rad(lon.astype(np.float64))
        return np.stack((np.cos(lat) * np.cos(lon),
                         np.cos(lat) * np.sin(lon), np.sin(lat)))
    chord = np.linalg.norm(xyz(lat_a, lon_a) - xyz(lat_b, lon_b), axis=0)
    return 6371.0 * 2 * np.arcsin(np.clip(chord / 2, 0, 1))


def test_decimation_oracle_on_smooth_field():
    # Withhold full-resolution truth, decimate offset=2/increment=5,
    # interpolate back, compare (verification lattice #3).
    truth_lat, truth_lon = synthetic_geolocation((52, 102))
    geo_lat = truth_lat[2::5, 2::5]
    geo_lon = truth_lon[2::5, 2::5]
    out_lat, out_lon = interpolate_geolocation(
        geo_lat, geo_lon, [(2, 5), (2, 5)], truth_lat.shape, None
    )
    error_km = great_circle_km(truth_lat, truth_lon, out_lat, out_lon)
    assert error_km.max() < 0.05  # smooth field: everywhere < 50 m


def test_mapped_points_are_exact():
    geo_lat, geo_lon = synthetic_geolocation((10, 10))
    out_lat, _ = interpolate_geolocation(
        geo_lat, geo_lon, [(2, 5), (2, 5)], (48, 48), None
    )
    assert float(out_lat[2 + 5 * 3, 2]) == pytest.approx(
        float(geo_lat[3, 0]), abs=1e-5
    )


def test_fill_propagates_never_interpolated_across():
    geo_lat, geo_lon = synthetic_geolocation((10, 10))
    fill = -999.0
    geo_lat[4, 4] = fill
    geo_lon[4, 4] = fill
    out_lat, out_lon = interpolate_geolocation(
        geo_lat, geo_lon, [(0, 2), (0, 2)], (19, 19), fill
    )
    # Any output pixel bracketing the fill geolocation pixel is fill.
    assert float(out_lat[8, 8]) == fill      # exactly on it
    assert float(out_lat[7, 7]) == fill      # interpolates across it
    assert float(out_lat[4, 4]) != fill      # far from it
    assert float(out_lon[9, 9]) == fill


def test_edge_extrapolation_is_linear():
    geo_lat, geo_lon = synthetic_geolocation((10, 10))
    out_lat, _ = interpolate_geolocation(
        geo_lat, geo_lon, [(2, 5), (2, 5)], (48, 48), None
    )
    # Row 0 sits at fractional index -0.4 off the first segment; for a
    # linear field the extrapolation reproduces the linear trend.
    row_step = (geo_lat[1, 0] - geo_lat[0, 0]) / 5.0
    expected = float(geo_lat[0, 0]) - 2 * row_step
    assert float(out_lat[0, 2]) == pytest.approx(expected, abs=1e-4)


def test_bad_dimension_maps_fail_loud():
    geo_lat, geo_lon = synthetic_geolocation((10, 10))
    with pytest.raises(UnsupportedGeolocationError):
        interpolate_geolocation(
            geo_lat, geo_lon, [(0, 0), (0, 2)], (19, 19), None
        )
    with pytest.raises(UnsupportedGeolocationError):
        interpolate_geolocation(
            geo_lat, geo_lon, [None, (0, 2)], (99, 19), None
        )
