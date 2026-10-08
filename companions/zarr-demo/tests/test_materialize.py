# ruff: noqa: E402
# Local runs use src/; CI copies tests outside the checkout to test the wheel.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import netCDF4 as nc
import numpy as np
import pytest
import ncarnate_zarr_demo as demo
from ncarnate import prepare, execute_prepared


def checkpoint(tmp_path):
    source, output = tmp_path / "input.nc", tmp_path / "checkpoint.nc"
    with nc.Dataset(source, "w") as ds:
        ds.title = "demonstration"
        ds.createDimension("x", 31)
        v = ds.createVariable("packed", "i2", ("x",), fill_value=-999)
        v.set_auto_maskandscale(False)
        v.scale_factor = 0.1
        v.add_offset = 20.0
        v[:] = np.arange(31, dtype="i2")
        g = ds.createGroup("child")
        g.createVariable("scalar", "f4")[()] = 4.0
        g.createVariable("float_fill", "f4", ("x",), fill_value=-99.0)[:] = np.arange(31)
        g.createVariable("inherited", "u4", ("x",))[:] = np.arange(31)
    return execute_prepared(prepare(source, output)), output


def test_roundtrip_raw_packing_groups_scalar_and_tamper(tmp_path):
    record, output = checkpoint(tmp_path)
    target = tmp_path / "store"
    manifest = demo.materialize(record, output, target)
    assert demo.verify_store(target) == manifest
    assert output.exists()
    chunk = next(path for path in target.rglob("*") if path.is_file() and path.name != "COMMIT.json")
    chunk.write_bytes(chunk.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="content mismatch"):
        demo.verify_store(target)


def test_checkpoint_tampering_refused_before_store(tmp_path):
    record, output = checkpoint(tmp_path)
    with output.open("ab") as stream:
        stream.write(b"tamper")
    with pytest.raises(ValueError, match="identity mismatch"):
        demo.materialize(record, output, tmp_path / "store")
    assert not (tmp_path / "store").exists()
    assert not list(tmp_path.glob(".ncarnate-zarr-*"))


def test_failed_reopen_never_publishes(tmp_path, monkeypatch):
    record, output = checkpoint(tmp_path)
    def fail(*args, **kwargs):
        raise OSError("injected independent reopen failure")
    monkeypatch.setattr(demo.xr, "open_zarr", fail)
    with pytest.raises(OSError):
        demo.materialize(record, output, tmp_path / "store")
    assert output.exists()
    assert not (tmp_path / "store").exists()
    assert not list(tmp_path.glob(".ncarnate-zarr-*"))


def test_existing_store_is_never_overwritten(tmp_path):
    record, output = checkpoint(tmp_path)
    target = tmp_path / "store"
    target.mkdir()
    (target / "mine").write_text("keep")
    with pytest.raises(ValueError):
        demo.materialize(record, output, target)
    assert (target / "mine").read_text() == "keep"
