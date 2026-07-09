"""CLI behavior: exit codes, --version, the escape hatch."""

import sys

import pytest

from ncarnate.cli import main
from ncarnate.constants import __version__

from conftest import HDFEOS2_FIXTURES, NETCDF_FIXTURES, stage


def run_cli(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["ncarnate", *argv])
    return main()


def test_version_works_without_path(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ncarnate", "-V"])
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 0
    assert f"ncarnate {__version__}" in capsys.readouterr().out


def test_missing_path_exits_2(monkeypatch, workdir):
    assert run_cli(monkeypatch, str(workdir / "missing.nc")) == 2


def test_unsupported_extension_exits_2(monkeypatch, workdir):
    path = workdir / "data.txt"
    path.write_text("hello")
    assert run_cli(monkeypatch, str(path)) == 2


def test_successful_run_exits_0(monkeypatch, workdir):
    src = stage(NETCDF_FIXTURES[0], workdir)
    assert run_cli(monkeypatch, "--no-overwrite", str(src)) == 0
    assert (workdir / f"{NETCDF_FIXTURES[0].stem}_recompressed.nc").exists()


def test_failing_file_exits_1(monkeypatch, workdir):
    # A garbage payload with a supported extension fails per-file.
    path = workdir / "garbage.nc"
    path.write_bytes(b"garbage" * 100)
    assert run_cli(monkeypatch, str(path)) == 1


def test_directory_scan_processes_supported_files(monkeypatch, workdir):
    stage(NETCDF_FIXTURES[0], workdir)
    (workdir / "notes.txt").write_text("ignored")
    assert run_cli(monkeypatch, "--no-overwrite", str(workdir)) == 0


def test_no_geolocation_flag_reaches_conversion(monkeypatch, workdir):
    import netCDF4 as nc
    fixture = next(f for f in HDFEOS2_FIXTURES if "raingrid" in f.stem)
    src = stage(fixture, workdir)
    assert run_cli(monkeypatch, "--no-geolocation", str(src)) == 0
    with nc.Dataset(workdir / f"{fixture.stem}.nc") as f:
        group = f.groups["MonthlyRainTotal_GeoGrid"]
        assert "lat" not in group.variables
        assert "lon" not in group.variables
