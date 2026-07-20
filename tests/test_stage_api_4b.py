"""Stage API step 4B: the public inspect -> plan -> execute primitives, the
immutable Plan, lazy batch iteration, and the two hard invariants (never start
a nested worker pool; never delete a successful output — a post-commit
read-back error degrades to a verified-with-warning result, not a failure).

Design: docs/design/ncarnate-stage-api.md.
"""

from __future__ import annotations

import dataclasses
import inspect as _pyinspect
import shutil
from pathlib import Path

import pytest

from conftest import HDFEOS2_FIXTURES, assert_lossless_netcdf, stage

# Public stage-API surface only (plus core for the invariant monkeypatch).
import ncarnate
from ncarnate import Plan, execute, execute_batch, inspect, plan
from ncarnate import core
from ncarnate.audit.codes import RESULT_READBACK_INCOMPLETE

_NETCDF = Path(__file__).parent / "fixtures" / "data" / "netcdf"
_PACKED_FILL = _NETCDF / "packed_fill.nc"
_UNLIMITED = _NETCDF / "unlimited_dim.nc"


# --- inspect -> plan -> execute -------------------------------------------

def test_inspect_returns_an_assessment(tmp_path):
    src = shutil.copyfile(_PACKED_FILL, tmp_path / "g.nc")
    assessment = inspect(str(src), checksum="sha256")
    assert assessment.format == "HDF5"           # netCDF4 is an HDF5 container
    assert assessment.sha256                      # checksum recorded
    assert assessment.plan is not None            # a convertible file


def test_plan_execute_produces_a_full_result(tmp_path):
    src = shutil.copyfile(_PACKED_FILL, tmp_path / "g.nc")
    out = tmp_path / "out.nc"
    the_plan = plan(inspect(str(src)), str(out))

    assert isinstance(the_plan, Plan)
    assert the_plan.operation == "recompress"
    assert the_plan.destination == str(out)

    result = execute(the_plan)
    assert result.verification.status == "verified"
    assert result.destination.path == str(out)
    assert out.is_file()
    assert_lossless_netcdf(src, out)             # the primitive is lossless


def test_plan_is_immutable(tmp_path):
    src = shutil.copyfile(_PACKED_FILL, tmp_path / "g.nc")
    the_plan = plan(inspect(str(src)), str(tmp_path / "o.nc"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        the_plan.destination = "/elsewhere.nc"   # frozen: a plan cannot mutate


def test_plan_in_place_property(tmp_path):
    # recompress-in-place: no destination, overwrite -> destination == source.
    src = str(shutil.copyfile(_PACKED_FILL, tmp_path / "g.nc"))
    in_place = core._plan_from_path(src)          # dst omitted, overwrite default
    assert in_place.in_place is True
    out_of_place = core._plan_from_path(src, str(tmp_path / "o.nc"))
    assert out_of_place.in_place is False


# --- lazy batch iteration -------------------------------------------------

def test_execute_batch_is_a_lazy_generator_in_order(tmp_path):
    srcs = [shutil.copyfile(_PACKED_FILL, tmp_path / f"g{i}.nc") for i in range(3)]
    plans = [core._plan_from_path(str(s), str(tmp_path / f"o{i}.nc"))
             for i, s in enumerate(srcs)]

    stream = execute_batch(plans)
    assert _pyinspect.isgenerator(stream)         # lazy — nothing executed yet
    assert not (tmp_path / "o0.nc").exists()

    results = list(stream)
    assert [r.destination.path for r in results] == [p.destination for p in plans]
    assert all(r.verification.status == "verified" for r in results)


# --- INVARIANT 1: never start a nested worker pool ------------------------

def test_execute_never_starts_a_worker_pool(tmp_path, monkeypatch):
    import concurrent.futures as cf
    import multiprocessing as mp

    def _forbidden(*args, **kwargs):
        raise AssertionError("stage-API operation started a worker pool")

    monkeypatch.setattr(mp, "Pool", _forbidden)
    monkeypatch.setattr(cf, "ProcessPoolExecutor", _forbidden)
    monkeypatch.setattr(cf, "ThreadPoolExecutor", _forbidden)

    src = shutil.copyfile(_PACKED_FILL, tmp_path / "g.nc")
    hdf = stage(next(f for f in HDFEOS2_FIXTURES if f.stem == "mod03_trim"), tmp_path)

    # Both read paths (netCDF recompress + HDF4 convert), one-shot and batch.
    execute(core._plan_from_path(str(src), str(tmp_path / "o.nc")))
    list(execute_batch([
        core._plan_from_path(str(hdf), str(tmp_path / "h.nc")),
    ]))
    # If any of the above constructed a pool, _forbidden would have fired.


# --- INVARIANT 2: never delete a good output; read-back error degrades ----

def test_readback_error_yields_verified_result_and_keeps_output(tmp_path, monkeypatch):
    src = shutil.copyfile(_PACKED_FILL, tmp_path / "g.nc")
    out = tmp_path / "out.nc"

    # Force the post-commit read-back to fail AFTER the output is written and
    # verified: the conversion succeeded, so this must degrade, not misreport.
    def _boom(*args, **kwargs):
        raise RuntimeError("simulated read-back failure")

    monkeypatch.setattr(core, "_build_operation_result", _boom)

    result = execute(core._plan_from_path(str(src), str(out)))

    # the good output is committed and intact — never deleted
    assert out.is_file()
    assert_lossless_netcdf(src, out)
    # a completed conversion is reported verified, with an honest warning
    assert result.verification.status == "verified"
    assert [w.code for w in result.warnings] == [RESULT_READBACK_INCOMPLETE]
    assert result.destination.sha256                    # best-effort digest still recorded


# --- G4: perform AND understand a single conversion, public API only ------

def test_g4_single_call_via_public_stage_api(tmp_path):
    # No CLI, no log parsing, no private imports — inspect/plan/execute only.
    src = shutil.copyfile(_UNLIMITED, tmp_path / "granule.nc")
    result = execute(plan(inspect(str(src), checksum="sha256"), str(tmp_path / "o.nc")))

    # Everything a Zarr tail needs, from the result object alone.
    root = result.structure
    dims = {d.name: (d.size, d.unlimited) for d in root.dimensions}
    assert any(unlimited for _, unlimited in dims.values())   # unlimited dim preserved
    assert result.source.sha256 and result.destination.sha256
    assert result.verification.status == "verified"
    assert result.operation == "recompress"

    # the stage-API contract is entirely on the public surface
    for name in ("inspect", "plan", "execute", "execute_batch", "Plan"):
        assert name in ncarnate.__all__
