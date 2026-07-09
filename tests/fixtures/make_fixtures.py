"""Generate the synthetic netCDF4 test fixtures for ncarnate.

Each fixture is deliberately constructed to exercise one edge of the
fidelity contract (see docs/fidelity-notes.md). Fixtures are committed;
this script is their provenance and the only way they may be regenerated.

Usage:
    python tests/fixtures/make_fixtures.py [output_dir]

Requires: netCDF4, numpy.
"""

from __future__ import annotations

import sys
from pathlib import Path

import netCDF4
import numpy as np

DEFAULT_OUT = Path(__file__).parent / "data" / "netcdf"

# Deterministic content: fixtures must be reproducible bit-for-bit at the
# value level (file bytes may differ across library versions; values not).
RNG_SEED = 20260708


def make_packed(out: Path) -> None:
    """Packed integers with scale_factor/add_offset and _FillValue.

    This is the case v1 corrupted (auto mask-and-scale round-trip) and
    crashed on (_FillValue set after createVariable).
    """
    rng = np.random.default_rng(RNG_SEED)
    with netCDF4.Dataset(out, "w") as nc:
        nc.title = "ncarnate fixture: packed integers + fill"
        nc.createDimension("y", 40)
        nc.createDimension("x", 60)
        v = nc.createVariable(
            "brightness_temp", "i2", ("y", "x"), fill_value=np.int16(-32768)
        )
        v.scale_factor = np.float64(0.01)
        v.add_offset = np.float64(150.0)
        v.units = "K"
        v.long_name = "packed brightness temperature"
        v.set_auto_maskandscale(False)
        raw = rng.integers(-20000, 20000, size=(40, 60), dtype=np.int16)
        raw[::7, ::11] = -32768  # scattered fill values
        v[:] = raw

        # An unpacked float alongside, with its own fill.
        f = nc.createVariable("float_field", "f4", ("y", "x"), fill_value=np.float32(9.96921e36))
        f.units = "1"
        arr = rng.random((40, 60), dtype=np.float32)
        arr[0, 0] = 9.96921e36
        f.set_auto_maskandscale(False)
        f[:] = arr


def make_groups(out: Path) -> None:
    """Nested groups with per-group dimensions, variables, and attributes."""
    rng = np.random.default_rng(RNG_SEED + 1)
    with netCDF4.Dataset(out, "w") as nc:
        nc.title = "ncarnate fixture: nested groups"
        nc.createDimension("t", 5)
        root_v = nc.createVariable("t", "f8", ("t",))
        root_v[:] = np.arange(5, dtype=np.float64)

        g1 = nc.createGroup("instrument_a")
        g1.comment = "first-level group"
        g1.createDimension("channel", 3)
        cv = g1.createVariable("counts", "i4", ("channel", "t"))
        cv.long_name = "raw counts"
        cv[:] = rng.integers(0, 1000, size=(3, 5), dtype=np.int32)

        g2 = g1.createGroup("calibration")
        g2.createDimension("coef", 2)
        gv = g2.createVariable("gain", "f8", ("coef", "channel"))
        gv[:] = rng.random((2, 3))
        # A group variable that uses an ancestor group's dimension.
        tv = g2.createVariable("t_offset", "f4", ("t",))
        tv[:] = rng.random(5, dtype=np.float32)


def make_unlimited(out: Path) -> None:
    """An unlimited (record) dimension with appended records."""
    rng = np.random.default_rng(RNG_SEED + 2)
    with netCDF4.Dataset(out, "w") as nc:
        nc.title = "ncarnate fixture: unlimited dimension"
        nc.createDimension("time", None)  # unlimited
        nc.createDimension("station", 4)
        v = nc.createVariable("obs", "f4", ("time", "station"))
        v.units = "m s-1"
        for step in range(7):
            v[step, :] = rng.random(4, dtype=np.float32)
        tvar = nc.createVariable("time", "i4", ("time",))
        tvar.units = "hours since 2020-01-01"
        tvar[:] = np.arange(7, dtype=np.int32)


def make_endian(out: Path) -> None:
    """Variables stored in explicitly big- and little-endian layouts."""
    rng = np.random.default_rng(RNG_SEED + 3)
    with netCDF4.Dataset(out, "w") as nc:
        nc.title = "ncarnate fixture: non-native endianness"
        nc.createDimension("n", 32)
        big = nc.createVariable("big_endian", "f8", ("n",), endian="big")
        big[:] = rng.random(32)
        little = nc.createVariable("little_endian", "i4", ("n",), endian="little")
        little[:] = rng.integers(-1000, 1000, size=32, dtype=np.int32)


FIXTURES = {
    "packed_fill.nc": make_packed,
    "nested_groups.nc": make_groups,
    "unlimited_dim.nc": make_unlimited,
    "endianness.nc": make_endian,
}


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, maker in FIXTURES.items():
        path = out_dir / name
        maker(path)
        size = path.stat().st_size
        print(f"wrote {path} ({size:,} bytes)")
        if size > 200_000:
            print(f"ERROR: {name} exceeds the 200 KB fixture budget", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
