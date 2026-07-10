"""HDF4/HDF-EOS2 -> netCDF4 conversion fidelity over every committed
fixture: SDS values bit-identical, metadata preserved, geolocation
additive, the source never replaced."""

import numpy as np
import netCDF4 as nc
import pytest
from pyhdf.SD import SD, SDC

from ncarnate import NcarnateError, recompress
from ncarnate.hdf4 import sanitize_name

from conftest import HDFEOS2_FIXTURES, stage, structmetadata_text


def convert(fixture, workdir, **kwargs):
    src = stage(fixture, workdir)
    dst = recompress(str(src), **kwargs)
    return src, dst


def iter_output_variables(dataset):
    stack = [dataset]
    while stack:
        node = stack.pop()
        yield from node.variables.items()
        stack.extend(node.groups.values())


def find_variable(dataset, sanitized_name):
    for name, variable in iter_output_variables(dataset):
        if name == sanitized_name:
            return variable
    raise AssertionError(f"{sanitized_name} not found in output")


@pytest.mark.parametrize(
    "fixture", HDFEOS2_FIXTURES, ids=lambda p: p.stem
)
def test_every_sds_survives_bit_identical(fixture, workdir):
    src, dst = convert(fixture, workdir)
    source = SD(str(src), SDC.READ)
    try:
        with nc.Dataset(dst) as output:
            n_datasets, _ = source.info()
            assert n_datasets > 0
            for index in range(n_datasets):
                sds = source.select(index)
                hdf4_name = sds.info()[0]
                variable = find_variable(output, sanitize_name(hdf4_name))
                variable.set_auto_maskandscale(False)
                assert np.array_equal(variable[...], np.asarray(sds.get())), \
                    hdf4_name
                sds.endaccess()
    finally:
        source.end()


@pytest.mark.parametrize(
    "fixture", HDFEOS2_FIXTURES, ids=lambda p: p.stem
)
def test_source_is_never_replaced(fixture, workdir):
    src = stage(fixture, workdir)
    before = src.read_bytes()
    recompress(str(src), overwrite=True)  # overwrite must be ignored
    assert src.read_bytes() == before
    assert (workdir / f"{fixture.stem}.nc").exists()


def test_structmetadata_preserved_verbatim(workdir):
    fixture = next(f for f in HDFEOS2_FIXTURES if "seaice" in f.stem)
    src, dst = convert(fixture, workdir)
    source = SD(str(src), SDC.READ)
    try:
        expected = structmetadata_text(source.attributes()).rstrip("\x00")
    finally:
        source.end()
    with nc.Dataset(dst) as output:
        info = output.groups["HDFEOS_INFORMATION"]
        actual = structmetadata_text(
            {a: info.getncattr(a) for a in info.ncattrs()}
        )
    assert actual == expected


def test_conversion_refuses_to_clobber_existing_autoderived_output(workdir):
    fixture = next(f for f in HDFEOS2_FIXTURES if "raingrid" in f.stem)
    src = stage(fixture, workdir)
    existing = workdir / f"{fixture.stem}.nc"
    existing.write_text("precious unrelated data")
    with pytest.raises(NcarnateError, match="Refusing to overwrite"):
        recompress(str(src))
    assert existing.read_text() == "precious unrelated data"
    # An explicit dst still overwrites (the user named it).
    out = recompress(str(src), dst=str(workdir / "explicit.nc"))
    assert out == str(workdir / "explicit.nc")


def test_hdf4_conversion_refuses_dst_equal_src(workdir):
    src = stage(HDFEOS2_FIXTURES[0], workdir)
    with pytest.raises(NcarnateError):
        recompress(str(src), dst=str(src))


def test_amsre_grids_get_cf_geolocation(workdir):
    fixture = next(f for f in HDFEOS2_FIXTURES if "seaice" in f.stem)
    _, dst = convert(fixture, workdir)
    with nc.Dataset(dst) as output:
        for grid_name in ("NpPolarGrid12km", "SpPolarGrid12km"):
            group = output.groups[grid_name]
            for name in ("x", "y", "lat", "lon", "polar_stereographic"):
                assert name in group.variables, (grid_name, name)
            mapping = group["polar_stereographic"]
            assert mapping.getncattr("grid_mapping_name") \
                == "polar_stereographic"
            data = group["SI_12km_NH_ICECON_DAY" if grid_name.startswith("Np")
                          else "SI_12km_SH_ICECON_DAY"]
            assert data.getncattr("coordinates") == "lon lat"
            assert data.getncattr("grid_mapping") == "polar_stereographic"


def test_geographic_grid_gets_1d_lat_lon(workdir):
    fixture = next(f for f in HDFEOS2_FIXTURES if "raingrid" in f.stem)
    _, dst = convert(fixture, workdir)
    with nc.Dataset(dst) as output:
        group = output.groups["MonthlyRainTotal_GeoGrid"]
        lat, lon = group["lat"][:], group["lon"][:]
        assert lat.shape == (28,) and lon.shape == (72,)
        # RainGrid: 5-degree cells, native 0..360 convention, north-down.
        assert lat[0] == pytest.approx(67.5) and lat[-1] == pytest.approx(-67.5)
        assert lon[0] == pytest.approx(2.5) and lon[-1] == pytest.approx(357.5)
        assert "grid_mapping" not in group["TbOceanRain"].ncattrs()


def test_lamaz_grids_sanitized_and_pole_centered(workdir):
    fixture = next(f for f in HDFEOS2_FIXTURES if "5daysnow" in f.stem)
    _, dst = convert(fixture, workdir)
    with nc.Dataset(dst) as output:
        group = output.groups["Northern_Hemisphere"]
        assert group.getncattr("hdf4_eos_name") == "Northern Hemisphere"
        lat = group["lat"]
        lat.set_auto_maskandscale(False)
        # EASE-Grid NL 721x721: the North Pole sits exactly on the center cell.
        assert float(lat[360, 360]) == pytest.approx(90.0, abs=1e-9)
        # Off-Earth corner cells are declared fill, never inf.
        fill = lat.getncattr("_FillValue")
        values = lat[...]
        assert np.isfinite(values[values != fill]).all()
        assert (values == fill).sum() > 0


def test_swath_units_normalized_reversibly(workdir):
    fixture = next(f for f in HDFEOS2_FIXTURES if "mod03" in f.stem)
    _, dst = convert(fixture, workdir)
    with nc.Dataset(dst) as output:
        group = output.groups["MODIS_Swath_Type_GEO"]
        lat = group["Latitude"]
        assert lat.getncattr("units") == "degrees_north"
        assert lat.getncattr("original_units") == "degrees"
        assert lat.getncattr("standard_name") == "latitude"
        assert group["SensorZenith"].getncattr("coordinates") \
            == "Longitude Latitude"


def test_dimension_mapped_swath_gets_interpolated_coordinates(workdir):
    fixture = next(f for f in HDFEOS2_FIXTURES if "myd05" in f.stem)
    _, dst = convert(fixture, workdir)
    with nc.Dataset(dst) as output:
        group = output.groups["mod05"]
        data = group["Water_Vapor_Near_Infrared"]
        assert data.getncattr("coordinates") \
            == "Longitude_interpolated Latitude_interpolated"
        lat_1km = group["Latitude_interpolated"]
        lat_5km = group["Latitude"]
        lat_1km.set_auto_maskandscale(False)
        lat_5km.set_auto_maskandscale(False)
        assert lat_1km.shape == (50, 1354)
        # The dimension map (offset=2, increment=5) places 5-km pixel g at
        # 1-km index 2 + 5g: geolocation must be exact there. The last
        # 5-km across-track center lands at column 2 + 5*269 = 1347.
        assert float(lat_1km[2, 1347]) == pytest.approx(
            float(lat_5km[0, 269]), abs=5e-5
        )
        assert float(lat_1km[7, 2]) == pytest.approx(
            float(lat_5km[1, 0]), abs=5e-5
        )


def test_sanitized_attribute_names_carry_companions(workdir):
    fixture = next(f for f in HDFEOS2_FIXTURES if "mod03" in f.stem)
    _, dst = convert(fixture, workdir)
    with nc.Dataset(dst) as output:
        attrs = set(output.ncattrs())
        assert "Ephemeris_Attitude_Source" in attrs
        assert output.getncattr(
            "Ephemeris_Attitude_Source__hdf4_name"
        ) == "Ephemeris/Attitude Source"


def test_embedded_nul_attribute_preserved_as_uint8(workdir):
    # MODIS ships globals with embedded NUL record separators (e.g.
    # 'Ephemeris Input Files.1'); they cannot survive netCDF's C-string
    # attributes, so the reader keeps the exact bytes as uint8 with a
    # self-describing __hdf4_encoding companion. The mod03 fixture carries
    # these (its generator preserves them via the typed accessor).
    fixture = next(f for f in HDFEOS2_FIXTURES if "mod03" in f.stem)
    _, dst = convert(fixture, workdir)
    with nc.Dataset(dst) as output:
        encoded = [a for a in output.ncattrs() if a.endswith("__hdf4_encoding")]
        assert encoded, "expected at least one embedded-NUL uint8 attribute"
        base = encoded[0][: -len("__hdf4_encoding")]
        assert output.getncattr(base).dtype == np.uint8


def test_packed_geolocation_fails_loud():
    from ncarnate.errors import UnsupportedGeolocationError
    from ncarnate.hdf4 import TreeVariable, _normalize_coordinate

    packed = TreeVariable(
        name="Latitude",
        dimensions=("y", "x"),
        values=np.zeros((2, 2), dtype=np.int16),
        attributes={"scale_factor": np.float64(0.01),
                    "add_offset": np.float64(0.0)},
    )
    with pytest.raises(UnsupportedGeolocationError, match="packed"):
        _normalize_coordinate(packed, "degrees_north")


@pytest.mark.parametrize("raw,expected", [
    ("Land/SeaMask", "Land_SeaMask"),
    ("Scan Offset", "Scan_Offset"),
    ("Ephemeris/Attitude Source", "Ephemeris_Attitude_Source"),
    ("a  b\tc", "a_b_c"),
    ("already_ok", "already_ok"),
    ("nscans*10", "nscans*10"),  # '*' is legal in netCDF, preserved
])
def test_sanitize_name_direct(raw, expected):
    assert sanitize_name(raw) == expected


def test_hostile_sds_name_converts_with_companion(workdir):
    # A single SDS whose name is illegal in netCDF converts to the
    # sanitized name, values intact, original recorded under hdf4_name.
    from pyhdf.SD import SD, SDC

    path = workdir / "hostile.hdf"
    source = SD(str(path), SDC.WRITE | SDC.CREATE | SDC.TRUNC)
    data = np.arange(6, dtype=np.int16).reshape(2, 3)
    sds = source.create("Sea Ice/Snow", SDC.INT16, (2, 3))
    sds[:] = data
    sds.endaccess()
    source.end()

    dst = recompress(str(path), geolocation=False)
    with nc.Dataset(dst) as output:
        assert "Sea Ice/Snow" not in output.variables
        variable = output.variables["Sea_Ice_Snow"]
        variable.set_auto_maskandscale(False)
        assert np.array_equal(variable[...], data)
        assert variable.getncattr("hdf4_name") == "Sea Ice/Snow"


def test_sanitized_sds_name_collision_fails_loud(workdir):
    # Two SDS whose names sanitize to the same string must be refused with
    # a clean NcarnateError, not crash the writer with a raw netCDF error.
    from pyhdf.SD import SD, SDC

    path = workdir / "collide.hdf"
    source = SD(str(path), SDC.WRITE | SDC.CREATE | SDC.TRUNC)
    for name in ("A B", "A/B"):
        sds = source.create(name, SDC.INT16, (2, 2))
        sds[:] = np.ones((2, 2), np.int16)
        sds.endaccess()
    source.end()

    with pytest.raises(NcarnateError, match="collides"):
        recompress(str(path), geolocation=False)


def test_companion_attribute_collision_fails_loud():
    # A real attribute whose name equals a generated companion
    # (`foo/x` -> `foo_x__hdf4_name`) must be caught, not silently
    # overwritten (verify_conversion re-reads through the same code, so an
    # overwrite would be invisible).
    from ncarnate.errors import NcarnateError
    from ncarnate.hdf4 import _read_attributes

    class _FakeAttr:
        def __init__(self, name, value):
            self._name, self._value = name, value

        def info(self):
            from pyhdf.SD import SDC
            return self._name, SDC.CHAR, len(self._value)

        def get(self):
            return self._value

    class _FakeObj:
        def __init__(self, attrs):
            self._attrs = attrs

        def attr(self, index):
            name, value = self._attrs[index]
            return _FakeAttr(name, value)

    hostile = _FakeObj([("foo/x", "a"), ("foo_x__hdf4_name", "b")])
    with pytest.raises(NcarnateError, match="collides"):
        _read_attributes(hostile, 2)


def test_no_geolocation_is_sds_only(workdir):
    fixture = next(f for f in HDFEOS2_FIXTURES if "seaice" in f.stem)
    _, dst = convert(fixture, workdir, geolocation=False)
    with nc.Dataset(dst) as output:
        group = output.groups["NpPolarGrid12km"]
        assert set(group.variables) == {"SI_12km_NH_ICECON_DAY"}


def _swath_scaffold(lat_attrs, lon_attrs, data_dims, dimension_maps=None):
    from ncarnate.eos.structmeta import EosSwath
    from ncarnate.hdf4 import TreeGroup, TreeVariable

    group = TreeGroup.empty("MySwath")
    group.dimensions = {"along": 4, "across": 3, "band": 2,
                        "along_hi": 8, "across_hi": 3}

    latitude = TreeVariable(
        name="Latitude", dimensions=("along", "across"),
        values=np.zeros((4, 3), dtype=np.float32), attributes=dict(lat_attrs),
    )
    longitude = TreeVariable(
        name="Longitude", dimensions=("along", "across"),
        values=np.zeros((4, 3), dtype=np.float32), attributes=dict(lon_attrs),
    )
    data = TreeVariable(
        name="Data", dimensions=data_dims,
        values=np.zeros(tuple(group.dimensions[d] for d in data_dims),
                        dtype=np.int16),
        attributes={},
    )
    group.variables.extend([latitude, longitude, data])

    swath = EosSwath(
        name="MySwath", dimensions=dict(group.dimensions),
        dimension_maps=list(dimension_maps or []), geo_fields=[],
        data_fields=[], has_index_maps=False, has_merged_fields=False,
    )
    return group, swath, latitude, longitude, data


def _hi_res_maps():
    from ncarnate.eos.structmeta import EosDimensionMap

    return [
        EosDimensionMap(geo_dimension="along", data_dimension="along_hi",
                        offset=0, increment=2),
        EosDimensionMap(geo_dimension="across", data_dimension="across_hi",
                        offset=0, increment=1),
    ]


def test_mismatched_fill_values_fail_loud_on_interpolation():
    # Interpolation applies one fill mask (Latitude's) to both fields;
    # differing declared fills would interpolate Longitude's finite fill
    # values into neighboring pixels, so the converter must refuse.
    from ncarnate.errors import UnsupportedGeolocationError
    from ncarnate.hdf4 import _attach_swath_coordinates

    for lat_attrs, lon_attrs in (
        ({"_FillValue": np.float32(-999.0)}, {"_FillValue": np.float32(-9999.0)}),
        ({}, {"_FillValue": np.float32(-999.0)}),  # Longitude-only
    ):
        group, swath, latitude, longitude, _ = _swath_scaffold(
            lat_attrs, lon_attrs, ("along_hi", "across_hi"),
            dimension_maps=_hi_res_maps())
        with pytest.raises(UnsupportedGeolocationError, match="_FillValue"):
            _attach_swath_coordinates(group, swath, latitude, longitude)


def test_mismatched_fill_values_tolerated_at_native_resolution():
    # Native-resolution attachment only names the coordinate variables;
    # the fills never interact, so differing fills must NOT refuse the
    # conversion (the guard is scoped to the interpolation path).
    from ncarnate.hdf4 import _attach_swath_coordinates

    group, swath, latitude, longitude, data = _swath_scaffold(
        {"_FillValue": np.float32(-999.0)}, {"_FillValue": np.float32(-9999.0)},
        ("along", "across"))
    _attach_swath_coordinates(group, swath, latitude, longitude)
    assert data.attributes["coordinates"] == "Longitude Latitude"


def test_matching_geolocation_fill_values_accepted():
    from ncarnate.hdf4 import _attach_swath_coordinates

    for lat_attrs, lon_attrs in (
        ({"_FillValue": np.float32(-999.0)}, {"_FillValue": np.float32(-999.0)}),
        ({"_FillValue": np.float32(-999.0)}, {}),  # Latitude-only (status quo)
        ({}, {}),
    ):
        group, swath, latitude, longitude, data = _swath_scaffold(
            lat_attrs, lon_attrs, ("along", "across"))
        _attach_swath_coordinates(group, swath, latitude, longitude)
        assert data.attributes["coordinates"] == "Longitude Latitude"


def test_nan_fill_values_agree_and_interpolate():
    # NaN is a legitimate geolocation fill; both-NaN must count as
    # matching (nan != nan under plain equality) and interpolation must
    # proceed.
    from ncarnate.hdf4 import _attach_swath_coordinates

    group, swath, latitude, longitude, data = _swath_scaffold(
        {"_FillValue": np.float32(np.nan)}, {"_FillValue": np.float32(np.nan)},
        ("along_hi", "across_hi"), dimension_maps=_hi_res_maps())
    _attach_swath_coordinates(group, swath, latitude, longitude)
    assert data.attributes["coordinates"] == \
        "Longitude_interpolated Latitude_interpolated"


def test_nonleading_swath_axes_skip_warns(caplog):
    # A band-first variable is converted intact but gets no coordinates;
    # the skip must be said out loud, not silent.
    import logging as _logging

    from ncarnate.hdf4 import _attach_swath_coordinates

    group, swath, latitude, longitude, data = _swath_scaffold(
        {}, {}, ("band", "along", "across"))
    with caplog.at_level(_logging.WARNING, logger="ncarnate.hdf4"):
        _attach_swath_coordinates(group, swath, latitude, longitude)
    assert "coordinates" not in data.attributes
    assert any("non-leading" in record.getMessage() for record in caplog.records)


def test_unrelated_variable_skip_stays_silent(caplog):
    # A variable with no swath axes at all is not swath-mapped; skipping
    # it is correct and must NOT warn.
    import logging as _logging

    from ncarnate.hdf4 import _attach_swath_coordinates

    group, swath, latitude, longitude, data = _swath_scaffold(
        {}, {}, ("band", "band"))
    data.dimensions = ("band", "band")
    with caplog.at_level(_logging.WARNING, logger="ncarnate.hdf4"):
        _attach_swath_coordinates(group, swath, latitude, longitude)
    assert "coordinates" not in data.attributes
    assert not caplog.records
