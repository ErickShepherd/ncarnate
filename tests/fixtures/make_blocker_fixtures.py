"""Generate the synthetic *blocker* fixtures for ncarnate's audit tests.

Unlike the fixtures in ``make_fixtures.py`` (all deliberately convertible),
these exercise the *blocker* direction of the audit/convert agreement oracle:
the audit predicts a blocker code and ``recompress`` must raise the mapped
exception. They live under ``data/blockers/`` so the ``NETCDF_FIXTURES`` /
``HDFEOS2_FIXTURES`` globs — which the round-trip tests assume are all
convertible — never pick them up.

Fixtures are committed; this script is their provenance and the only way they
may be regenerated.

Usage:
    python tests/fixtures/make_blocker_fixtures.py [output_dir]

Requires: netCDF4, numpy.
"""

from __future__ import annotations

import sys
from pathlib import Path

import netCDF4
import numpy as np

DEFAULT_OUT = Path(__file__).parent / "data" / "blockers"

RNG_SEED = 20260710


def make_compound_type(out: Path) -> None:
    """A netCDF4 file with a user-defined compound-type variable.

    ``core._copy_variables`` (core.py:320) refuses any variable whose
    ``datatype`` is not a plain ``np.dtype`` — compound, VLen, enum, and
    opaque types are outside the v2 fidelity guarantee — so ``recompress``
    raises ``UnsupportedTypeError``. The audit's metadata inspection sees the
    same user-defined type and classifies the file ``unsupported``
    (``UNSUPPORTED_TYPE``), keeping the blocker branch of the agreement oracle
    honest.
    """
    rng = np.random.default_rng(RNG_SEED)
    with netCDF4.Dataset(out, "w") as nc:
        nc.title = "ncarnate blocker fixture: user-defined compound type"
        nc.createDimension("sample", 8)

        compound = np.dtype([("value", "f4"), ("flag", "i1")])
        obs_type = nc.createCompoundType(compound, "observation")

        var = nc.createVariable("observations", obs_type, ("sample",))
        data = np.empty(8, dtype=compound)
        data["value"] = rng.random(8, dtype=np.float32)
        data["flag"] = rng.integers(0, 2, size=8, dtype=np.int8)
        var[:] = data


FIXTURES = {
    "compound_type.nc": make_compound_type,
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
