import json
import shutil

import netCDF4 as nc
import numpy as np
import pytest

from ncarnate import prepare, prepare_batch, execute_prepared, execute_prepared_batch
from ncarnate.errors import NcarnateError
from ncarnate.streaming import array_budget, slices
from conftest import NETCDF_FIXTURES, assert_lossless_netcdf


def source(tmp_path, name="source.nc"):
    path = tmp_path / name
    shutil.copyfile(NETCDF_FIXTURES[0], path)
    return path


def test_changed_source_refused_before_output(tmp_path):
    src = source(tmp_path)
    dst = tmp_path / "new.nc"
    item = prepare(src, dst)
    with src.open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(NcarnateError, match="source changed"):
        execute_prepared(item)
    assert not dst.exists()


def test_batch_collision_precedes_all_writes(tmp_path):
    a, b = source(tmp_path, "a.nc"), source(tmp_path, "b.nc")
    out = tmp_path / "out.nc"
    with pytest.raises(NcarnateError, match="claimed by"):
        prepare_batch([(a, out), (b, out)])
    assert not out.exists()


def test_verified_resume_and_tampering(tmp_path):
    src, dst, journal = source(tmp_path), tmp_path / "out.nc", tmp_path / "resume.json"
    item = prepare(src, dst)
    first = execute_prepared(item, journal=journal)
    before = dst.stat().st_mtime_ns
    second = execute_prepared(item, journal=journal, resume=True)
    assert first == second and dst.stat().st_mtime_ns == before
    with dst.open("ab") as stream:
        stream.write(b"tamper")
    with pytest.raises(NcarnateError, match="does not match"):
        execute_prepared(item, journal=journal, resume=True)


def test_resume_rejects_options_change(tmp_path):
    src, dst, journal = source(tmp_path), tmp_path / "out.nc", tmp_path / "resume.json"
    execute_prepared(prepare(src, dst, complevel=1), journal=journal)
    with pytest.raises(NcarnateError, match="does not match"):
        execute_prepared(prepare(src, dst, complevel=2), journal=journal, resume=True)


def test_existing_without_evidence_and_unavailable_journal(tmp_path):
    src, dst = source(tmp_path), tmp_path / "out.nc"
    item = prepare(src, dst)
    with pytest.raises(NcarnateError, match="reserve result journal"):
        execute_prepared(item, journal=tmp_path / "missing" / "journal")
    assert not dst.exists()
    shutil.copyfile(src, dst)
    with pytest.raises(NcarnateError):
        execute_prepared(item, journal=tmp_path / "journal", resume=True)


def test_partial_batch_can_resume(tmp_path, monkeypatch):
    import ncarnate.prepared as module
    a, b = source(tmp_path, "a.nc"), source(tmp_path, "b.nc")
    x, y, journal = tmp_path / "x.nc", tmp_path / "y.nc", tmp_path / "journal"
    plans = prepare_batch([(a, x), (b, y)])
    original = module.execute
    def fail_second(plan):
        if plan.source == str(b):
            raise OSError("injected conversion failure")
        return original(plan)
    monkeypatch.setattr(module, "execute", fail_second)
    with pytest.raises(OSError):
        execute_prepared_batch(plans, journal=journal)
    assert x.exists() and not y.exists()
    before = x.stat().st_mtime_ns
    monkeypatch.setattr(module, "execute", original)
    assert len(execute_prepared_batch(plans, journal=journal, resume=True)) == 2
    assert x.stat().st_mtime_ns == before


def test_journal_failure_preserves_completed_output(tmp_path, monkeypatch):
    from ncarnate.journal import ResultJournal
    src, dst = source(tmp_path), tmp_path / "out.nc"
    original = ResultJournal.publish
    def fail(writer, text):
        if writer.target.endswith('.json'):
            raise OSError("injected rename failure")
        return original(writer, text)
    monkeypatch.setattr(ResultJournal, "publish", fail)
    with pytest.raises(NcarnateError) as error:
        execute_prepared(prepare(src, dst), journal=tmp_path / "journal")
    assert error.value.code == "JOURNAL_WRITE_FAILED"
    assert_lossless_netcdf(src, dst)


@pytest.mark.parametrize("target", ["source", "output"])
def test_journal_cannot_alias_conversion_path(tmp_path, target):
    src, dst = source(tmp_path), tmp_path / "out.nc"
    with pytest.raises(NcarnateError):
        execute_prepared(prepare(src, dst), journal=src if target == "source" else dst)
    assert not dst.exists()


def test_multidimensional_streaming_scalar_empty_packed_and_groups(tmp_path):
    src, dst = tmp_path / "source.nc", tmp_path / "out.nc"
    expected = np.arange(13 * 37 * 23, dtype="i2").reshape(13, 37, 23)
    with nc.Dataset(src, "w") as ds:
        for name, size in zip("xyz", expected.shape):
            ds.createDimension(name, size)
        ds.createDimension("empty", None)
        ds.createVariable("nothing", "i4", ("empty",))
        ds.createVariable("scalar", "f8")[()] = np.nan
        group = ds.createGroup("nested")
        v = group.createVariable("packed", "i2", tuple("xyz"), fill_value=-999)
        v.set_auto_maskandscale(False)
        v.scale_factor = 0.1
        v.add_offset = 3.0
        v[:] = expected
    with array_budget(1024):
        execute_prepared(prepare(src, dst))
    assert_lossless_netcdf(src, dst)
    with nc.Dataset(dst) as ds:
        v = ds.groups["nested"].variables["packed"]
        v.set_auto_maskandscale(False)
        np.testing.assert_array_equal(v[:], expected)


def test_slice_partition_has_bounded_nonoverlapping_complete_coverage():
    coverage = np.zeros((3, 129, 17), dtype="u1")
    with array_budget(1024):
        for selection in slices(coverage.shape, 8):
            assert coverage[selection].size * 8 * 8 <= 1024
            coverage[selection] += 1
        assert list(slices((), 8)) == [()]
        assert list(slices((0, 7), 8)) == []
    assert np.all(coverage == 1)


@pytest.mark.parametrize("invalid", [[], None, "journal", {"records": None}])
def test_hostile_resume_envelope_is_named_refusal(tmp_path, invalid):
    item = prepare(source(tmp_path), tmp_path / "out.nc")
    journal = tmp_path / "journal"
    journal.write_text(json.dumps(invalid))
    with pytest.raises(NcarnateError) as error:
        execute_prepared(item, journal=journal, resume=True)
    assert error.value.code == "RESUME_MISMATCH"
    assert not (tmp_path / "out.nc").exists()


def test_new_batch_cannot_discard_existing_journal(tmp_path):
    journal = tmp_path / "journal"
    journal.write_text("retain existing evidence")
    with pytest.raises(NcarnateError) as error:
        execute_prepared(prepare(source(tmp_path), tmp_path / "out.nc"), journal=journal)
    assert error.value.code == "JOURNAL_UNAVAILABLE"
    assert journal.read_text() == "retain existing evidence"


def test_degraded_readback_can_resume_verified_output(tmp_path, monkeypatch):
    import ncarnate.core as core
    def fail(*args, **kwargs):
        raise ValueError("injected metadata failure")
    monkeypatch.setattr(core, "_build_operation_result", fail)
    item = prepare(source(tmp_path), tmp_path / "out.nc")
    journal = tmp_path / "journal"
    first = execute_prepared(item, journal=journal)
    assert first["warnings"][0]["code"] == "RESULT_READBACK_INCOMPLETE"
    assert execute_prepared(item, journal=journal, resume=True) == first


def test_character_encoding_is_copied_as_raw_bytes_across_slices(tmp_path):
    src, dst = tmp_path / "input.nc", tmp_path / "out.nc"
    expected = np.full((9, 300), b"x", dtype="S1")
    expected[:, 12] = b"\x00"
    with nc.Dataset(src, "w") as ds:
        ds.createDimension("n", 9)
        ds.createDimension("text", 300)
        variable = ds.createVariable("characters", "S1", ("n", "text"))
        variable.setncattr("_Encoding", "ascii")
        variable.set_auto_chartostring(False)
        variable[:] = expected
    with array_budget(1024):
        execute_prepared(prepare(src, dst))
    with nc.Dataset(dst) as ds:
        variable = ds["characters"]
        variable.set_auto_maskandscale(False)
        variable.set_auto_chartostring(False)
        np.testing.assert_array_equal(variable[:], expected)
