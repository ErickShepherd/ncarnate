"""Create a small packed dataset and convert it into an explicit new output.

Run from an installed ncarnate environment:
    python examples/verified_conversion.py NEW_DEMO_DIRECTORY
"""
import argparse
import json
from pathlib import Path

import netCDF4 as nc
import numpy as np

from ncarnate import prepare, execute_prepared


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    root = parser.parse_args().directory
    root.mkdir(parents=True, exist_ok=False)
    source, output = root / "source.nc", root / "verified.nc"
    with nc.Dataset(source, "w") as dataset:
        dataset.createDimension("time", 8)
        variable = dataset.createVariable("temperature", "i2", ("time",), fill_value=-999)
        variable.set_auto_maskandscale(False)
        variable.scale_factor = 0.1
        variable.add_offset = 273.15
        variable.units = "K"
        variable[:] = np.arange(8, dtype="i2")
    record = execute_prepared(prepare(source, output), journal=root / "resume.json")
    (root / "handoff.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"Source preserved: {source}")
    print(f"Verified output and handoff: {output}, {root / 'handoff.json'}")


if __name__ == "__main__":
    main()
