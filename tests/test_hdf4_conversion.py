"""HDF4/HDF-EOS2 -> netCDF4 conversion fidelity over every committed
fixture: SDS values bit-identical, metadata preserved, geolocation
additive, the source never replaced."""

import numpy as np
import netCDF4 as nc
import pytest
from pyhdf.SD import SD, SDC

from ncarnate import NcarnateError, recompress
from ncarnate.hdf4 import sanitize_name

from conftest import HDFEOS2_FIXTURES, stage


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
        parts = sorted(
            name for name in source.attributes()
            if name.startswith("StructMetadata")
        )
        expected = "".join(
            source.attributes()[name] for name in parts
        ).rstrip("\x00")
    finally:
        source.end()
    with nc.Dataset(dst) as output:
        info = output.groups["HDFEOS_INFORMATION"]
        actual = "".join(
            info.getncattr(name) for name in sorted(
                a for a in info.ncattrs()
                if a.startswith("StructMetadata")
            )
        )
    assert actual == expected


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


def test_no_geolocation_is_sds_only(workdir):
    fixture = next(f for f in HDFEOS2_FIXTURES if "seaice" in f.stem)
    _, dst = convert(fixture, workdir, geolocation=False)
    with nc.Dataset(dst) as output:
        group = output.groups["NpPolarGrid12km"]
        assert set(group.variables) == {"SI_12km_NH_ICECON_DAY"}
