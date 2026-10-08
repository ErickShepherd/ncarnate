import netCDF4 as nc
import numpy as np

from ncarnate import execute_prepared, prepare
from ncarnate.streaming import limit_cache


def test_multidimensional_variable_named_like_dimension(tmp_path):
    source, output = tmp_path / "source.nc", tmp_path / "out.nc"
    values = np.full((203, 10), b"X", dtype="S1")
    with nc.Dataset(source, "w") as dataset:
        dataset.createDimension("nscans", 203)
        dataset.createDimension("Scan_Type", 10)
        variable = dataset.createVariable("Scan_Type", "S1", ("nscans", "Scan_Type"), zlib=True)
        variable[:] = values
    with nc.Dataset(source) as dataset:
        variable = dataset["Scan_Type"]
        limit_cache(variable)
        np.testing.assert_array_equal(variable[:], values)
    execute_prepared(prepare(source, output))
    with nc.Dataset(output) as dataset:
        np.testing.assert_array_equal(dataset["Scan_Type"][:], values)
