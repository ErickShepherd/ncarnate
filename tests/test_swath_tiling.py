"""Tiling must retain the old whole-grid interpolation, including tile edges."""
import numpy as np
import pytest

from ncarnate.eos import swath
from ncarnate.streaming import array_budget


def whole_grid_reference(latitude, longitude, mappings, shape, fill):
    xyz = swath._to_unit_xyz(latitude, longitude)
    valid = np.isfinite(latitude) & np.isfinite(longitude)
    if fill is not None:
        valid &= (latitude != fill) & (longitude != fill)
    ok = valid.astype(np.float64)
    for axis, mapping in enumerate(mappings):
        if mapping is not None:
            lower, weight = swath._axis_weights(shape[axis], latitude.shape[axis], *mapping)
            xyz = swath._interpolate_axis(xyz, axis + 1, lower, weight)
            ok = np.minimum(np.take(ok, lower, axis=axis), np.take(ok, lower + 1, axis=axis))
    norm = np.linalg.norm(xyz, axis=0)
    ok = (ok >= 1) & (norm > 0)
    xyz = xyz / np.where(norm == 0, 1, norm)
    lat = np.rad2deg(np.arcsin(np.clip(xyz[2], -1, 1)))
    lon = np.rad2deg(np.arctan2(xyz[1], xyz[0]))
    if fill is not None:
        lat, lon = np.where(ok, lat, fill), np.where(ok, lon, fill)
    return lat.astype('f4'), lon.astype('f4')


@pytest.mark.parametrize('mappings,shape', [
    ([(2, 3), (1, 5)], (79, 141)),
    ([None, (-2, 3)], (25, 89)),
    ([(3, 4), None], (103, 29)),
    ([None, None], (25, 29)),
])
@pytest.mark.parametrize('fill', [None, -999.0])
def test_tiled_ecef_matches_whole_grid_at_edges(mappings, shape, fill, monkeypatch):
    lat = np.broadcast_to(np.linspace(70, 88, 25)[:, None], (25, 29)).copy()
    lon = np.broadcast_to(np.linspace(170, 190, 29)[None, :], (25, 29)).copy()
    lon = (lon + 180) % 360 - 180
    if fill is not None:
        lat[12, 14] = lon[12, 14] = fill
    expected = whole_grid_reference(lat, lon, mappings, shape, fill)
    original = swath._to_unit_xyz
    tile_sizes = []
    def observe(lat, lon):
        tile_sizes.append(lat.size)
        return original(lat, lon)
    monkeypatch.setattr(swath, '_to_unit_xyz', observe)
    with array_budget(4096):
        actual = swath.interpolate_geolocation(lat, lon, mappings, shape, fill)
    for left, right in zip(actual, expected):
        np.testing.assert_array_equal(left, right)
    assert len(tile_sizes) > 1
    assert max(tile_sizes) < lat.size
