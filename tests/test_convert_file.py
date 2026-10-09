"""The public one-file API preserves inputs and requires replacement consent."""
import importlib
import os
import subprocess
import sys

import netCDF4 as nc
import numpy as np
import pytest

from ncarnate import convert_file, recompress, NcarnateError, VerificationError
from ncarnate import core
from conftest import FIXTURE_ROOT, assert_lossless_netcdf


@pytest.fixture(params=["NETCDF3_CLASSIC", "NETCDF3_64BIT_OFFSET", "NETCDF3_64BIT_DATA", "NETCDF4"])
def source(tmp_path, request):
    path = tmp_path / "source.nc"
    with nc.Dataset(path, "w", format=request.param) as dataset:
        dataset.createDimension("sample", 3)
        dataset.createVariable("value", "i2", ("sample",))[:] = [1, 2, 3]
    return path


def test_convert_netcdf_preserves_source(source, tmp_path):
    before = source.read_bytes()
    output = tmp_path / "modern.nc"
    assert convert_file(source, output, complevel=3) == str(output)
    assert source.read_bytes() == before
    assert_lossless_netcdf(source, output)
    with nc.Dataset(output) as dataset:
        assert dataset.data_model == "NETCDF4"
        assert dataset["value"].filters()["complevel"] == 3
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("overwrite", [False, True])
def test_existing_output_requires_consent(source, tmp_path, overwrite):
    before = source.read_bytes()
    output = tmp_path / "modern.nc"
    output.write_bytes(b"existing output")
    if overwrite:
        convert_file(source, output, overwrite=True)
        assert_lossless_netcdf(source, output)
    else:
        with pytest.raises(NcarnateError, match="already exists"):
            convert_file(source, output)
        assert output.read_bytes() == b"existing output"
    assert source.read_bytes() == before


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("alias", ["same", "hardlink", "symlink"])
def test_source_alias_is_never_replaced(source, tmp_path, overwrite, alias):
    before = source.read_bytes()
    output = source if alias == "same" else tmp_path / "alias.nc"
    if alias == "hardlink":
        os.link(source, output)
    elif alias == "symlink":
        try:
            output.symlink_to(source)
        except OSError:
            pytest.skip("symlink creation unavailable")
    with pytest.raises(NcarnateError):
        convert_file(source, output, overwrite=overwrite)
    assert source.read_bytes() == before


def test_destination_is_required(source):
    with pytest.raises(TypeError):
        convert_file(source)
    with pytest.raises(NcarnateError, match="separate output"):
        convert_file(source, None)


@pytest.mark.parametrize("overwrite", [False, True])
def test_verification_failure_preserves_files(source, tmp_path, monkeypatch, overwrite):
    output = tmp_path / "modern.nc"
    before = source.read_bytes()
    if overwrite:
        output.write_bytes(b"previous")

    def fail(*args):
        raise VerificationError("injected verification failure")

    monkeypatch.setattr(core, "_verify_lossless", fail)
    with pytest.raises(VerificationError):
        convert_file(source, output, overwrite=overwrite)
    assert source.read_bytes() == before
    assert output.read_bytes() == b"previous" if overwrite else not output.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_output_appearing_during_conversion_is_preserved(source, tmp_path, monkeypatch):
    output = tmp_path / "modern.nc"
    verify = core._verify_lossless

    def racing_verify(src, dst):
        verify(src, dst)
        output.write_bytes(b"another writer")

    monkeypatch.setattr(core, "_verify_lossless", racing_verify)
    with pytest.raises(NcarnateError, match="appeared during conversion"):
        convert_file(source, output)
    assert output.read_bytes() == b"another writer"
    assert not list(tmp_path.glob("*.tmp"))


def test_unsupported_hard_links_fail_without_output(source, tmp_path, monkeypatch):
    output = tmp_path / "modern.nc"

    def unavailable(*args):
        raise OSError("hard links unsupported")

    monkeypatch.setattr(os, "link", unavailable)
    monkeypatch.setattr(core, "_execute_core", lambda *a, **k: pytest.fail("conversion started"))
    with pytest.raises(NcarnateError, match="cannot publish output") as failure:
        convert_file(source, output)
    assert failure.value.code == "OUTPUT_PUBLISH_FAILED"
    assert not output.exists()
    assert not list(tmp_path.glob("*.tmp"))
    assert not list(tmp_path.glob(".ncarnate-probe-*"))


@pytest.mark.parametrize("error_type", [FileExistsError, OSError])
def test_link_success_with_lost_reply_is_recognized(source, tmp_path, monkeypatch, error_type):
    output = tmp_path / "modern.nc"
    link = os.link

    def lost_reply(src, dst):
        link(src, dst)
        raise error_type("server reply lost")

    monkeypatch.setattr(os, "link", lost_reply)
    assert convert_file(source, output) == str(output)
    assert_lossless_netcdf(source, output)
    assert not list(tmp_path.glob("*.tmp"))
    assert not list(tmp_path.glob(".ncarnate-probe-*"))


def test_missing_parent_refused_before_conversion(source, tmp_path, monkeypatch):
    monkeypatch.setattr(core, "_execute_core", lambda *a, **k: pytest.fail("conversion started"))
    with pytest.raises(NcarnateError, match="existing directory"):
        convert_file(source, tmp_path / "missing" / "modern.nc", overwrite=True)


def test_case_only_source_alias_refused(source):
    with pytest.raises(NcarnateError, match="aliases selected source"):
        convert_file(source, source.with_name("SOURCE.nc"), overwrite=True)


def test_legacy_cli_and_manifest_preserve_netcdf_values(source, tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    source = source.rename(archive / source.name)
    before = source.read_bytes()
    command = [sys.executable, "-m", "ncarnate"]
    subprocess.run(command + [str(source), "--no-overwrite"], check=True, capture_output=True)
    assert_lossless_netcdf(source, archive / "source_recompressed.nc")
    manifest = tmp_path / "manifest.jsonl"
    subprocess.run(command + ["audit", str(source), "--output", str(manifest),
                             "--checksum", "sha256"], check=True, capture_output=True)
    output = tmp_path / "modern"
    result = subprocess.run(command + ["convert", "--manifest", str(manifest),
                            "--root", str(archive), "--out-dir", str(output),
                            "--status", "ready,already_modern"], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
    assert_lossless_netcdf(source, output / "source.nc")
    assert source.read_bytes() == before


def test_hdf4_conversion_matches_legacy(tmp_path):
    from ncarnate.hdf4_runtime import require_hdf4_runtime
    try:
        require_hdf4_runtime()
    except NcarnateError as error:
        if error.code == "HDF4_RUNTIME_UNAVAILABLE":
            pytest.skip("HDF4 runtime unavailable")
        raise
    source = FIXTURE_ROOT / "hdfeos2" / "amsre_seaice12km_trim.hdf"
    before = source.read_bytes()
    output = tmp_path / "modern.nc"
    legacy = tmp_path / "legacy.nc"
    convert_file(source, output)
    recompress(source, legacy)
    assert source.read_bytes() == before
    assert_lossless_netcdf(legacy, output)


def test_legacy_import_and_in_place_behavior_remain(source):
    module = importlib.import_module("ncarnate.convert")
    assert callable(module.convert_manifest)
    assert recompress(source) == str(source)
    with nc.Dataset(source) as dataset:
        assert dataset.data_model == "NETCDF4"
        np.testing.assert_array_equal(dataset["value"][:], [1, 2, 3])


def test_relative_output_returns_absolute_path(source, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert convert_file(source, "modern.nc") == str(tmp_path / "modern.nc")


def test_invalid_input_does_not_create_output(tmp_path):
    source = tmp_path / "invalid.hdf"
    source.write_bytes(b"not a supported scientific file")
    with pytest.raises(NcarnateError):
        convert_file(source, tmp_path / "modern.nc")
    assert sorted(path.name for path in tmp_path.iterdir()) == ["invalid.hdf"]


def test_readonly_source_leaves_no_staging_link(source, tmp_path):
    output = tmp_path / "modern.nc"
    source.chmod(0o444)
    try:
        assert convert_file(source, output) == str(output)
        assert_lossless_netcdf(source, output)
        assert not list(tmp_path.glob("*.tmp"))
        assert not (output.stat().st_mode & 0o222)
    finally:
        source.chmod(0o600)
        if output.exists():
            output.chmod(0o600)


def test_permission_failure_after_publication_is_warning(source, tmp_path, monkeypatch, caplog):
    output = tmp_path / "modern.nc"
    chmod = os.chmod

    def fail_output_mode(path, mode, *args, **kwargs):
        if os.fspath(path) == str(output):
            raise PermissionError("injected mode failure")
        return chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "chmod", fail_output_mode)
    assert convert_file(source, output) == str(output)
    assert_lossless_netcdf(source, output)
    assert "could not copy source permissions" in caplog.text
    assert not list(tmp_path.glob("*.tmp"))
