"""Contract-hardening regressions (restoration plan, increment 1).

Each test reproduces one of the review defects the October 2026 proposal
accepted as a contract failure and pins its deliberate refusal:

* handoff gates refuse deep, oversized, cyclic, or non-JSON records with
  ``HandoffError`` (or a reason string from the non-raising helpers) instead
  of leaking ``RecursionError`` / ``TypeError`` — including the standalone
  ``materializability_error`` on a NaN destination size;
* valid existing handoff fixtures still pass unchanged;
* ``json_safe`` refuses undecodable bytes and lone surrogates with a named
  ``ResultEncodingError`` and never alters valid text;
* ``EncodingOptions`` is frozen, so a planned ``Plan`` cannot drift;
* ``AuditOptions`` rejects an unimplemented audit mode at construction;
* the attribute storage-type inquiry refuses an unsynced writer handle and
  only flushes under the explicit ``sync=True`` opt-in.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import netCDF4 as nc
import pytest

from ncarnate import core, handoff, stage
from ncarnate.atttypes import AttTypeInquiryError, string_attributes_of
from ncarnate.audit.codes import HANDOFF_SCHEMA_INVALID
from ncarnate.audit.models import AuditModeError, AuditOptions
from ncarnate.errors import HandoffError, NcarnateError
from ncarnate.handoff import (
    check_materializable,
    materializability_error,
    precheck_handoff,
    schema_errors,
    validate_handoff,
)
from ncarnate.result import EncodingOptions, ResultEncodingError, json_safe

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "operation_result"
_PACKED_FILL = Path(__file__).parent / "fixtures" / "data" / "netcdf" / "packed_fill.nc"
_AMSRE_RESULT = json.loads(
    (_FIXTURE_DIR / "amsre_seaice12km.result.json").read_text(encoding="utf-8")
)


def _record() -> dict:
    return json.loads(json.dumps(_AMSRE_RESULT))


def _empty_group(path: str = "/") -> dict:
    return {"path": path, "dimensions": [], "attributes": [], "variables": [],
            "groups": []}


def _deep_structure(levels: int) -> dict:
    # Built iteratively so the TEST cannot hit the recursion limit itself.
    node = _empty_group("/deep")
    for _ in range(levels):
        node = {**_empty_group("/deep"), "groups": [node]}
    return node


# --- handoff: hostile records are refused, never leak ------------------

def test_valid_fixture_still_passes_every_gate():
    assert precheck_handoff(_AMSRE_RESULT) is None
    assert schema_errors(_AMSRE_RESULT) == []
    validate_handoff(_AMSRE_RESULT)
    check_materializable(_AMSRE_RESULT)
    assert materializability_error(_AMSRE_RESULT) is None


def test_deeply_nested_record_refused_at_every_public_gate():
    record = _record()
    record["structure"] = _deep_structure(5000)   # far past any recursion limit

    with pytest.raises(HandoffError) as excinfo:
        validate_handoff(record)
    assert excinfo.value.code == HANDOFF_SCHEMA_INVALID
    assert "nesting" in str(excinfo.value)

    with pytest.raises(HandoffError):
        check_materializable(record)

    reason = materializability_error(record)       # standalone helper too
    assert reason is not None and "nesting" in reason


def test_node_bound_refuses_oversized_record(monkeypatch):
    monkeypatch.setattr(handoff, "HANDOFF_MAX_NODES", 50)
    record = _record()
    with pytest.raises(HandoffError) as excinfo:
        validate_handoff(record)
    assert "node bound" in str(excinfo.value)
    assert "node bound" in (materializability_error(record) or "")

    # Explicit override on the helper, independent of the module default.
    assert "node bound" in (precheck_handoff(_AMSRE_RESULT, max_nodes=10) or "")


def test_reference_cycle_refused():
    record = _record()
    bag = {"policy": "keep"}
    bag["self"] = bag
    record["retention"] = bag                      # open caller-owned bag

    with pytest.raises(HandoffError) as excinfo:
        validate_handoff(record)
    assert "cycle" in str(excinfo.value)
    assert "cycle" in (materializability_error(record) or "")


def test_shared_subtree_is_not_a_cycle():
    # A DAG (one dict referenced twice) is legal; only a true cycle is refused.
    record = _record()
    shared = {"checks": ["range"]}
    record["retention"] = {"a": shared, "b": shared}
    assert precheck_handoff(record) is None


@pytest.mark.parametrize("value", [b"\xff", float("nan"), float("inf"), {1, 2}, object()])
def test_non_json_values_refused(value):
    record = _record()
    record["retention"] = {"bad": value}
    with pytest.raises(HandoffError):
        validate_handoff(record)
    assert materializability_error(record) is not None


def test_non_string_key_refused():
    record = _record()
    record["retention"] = {1: "x"}
    with pytest.raises(HandoffError) as excinfo:
        validate_handoff(record)
    assert "key" in str(excinfo.value)


def test_nan_destination_size_is_a_reason_for_standalone_helper():
    # The reproduced gap: an empty structure under a NaN size_bytes used to
    # return None ("safe") from materializability_error alone.
    record = _record()
    record["structure"] = _empty_group()
    record["destination"]["size_bytes"] = float("nan")
    reason = materializability_error(record)
    assert reason is not None and "non-finite" in reason


@pytest.mark.parametrize("hostile", [None, "text", 42, [], {}, ()])
def test_precheck_never_raises_on_scalars_or_empties(hostile):
    precheck_handoff(hostile)                       # no raise
    assert materializability_error(hostile) is not None


# --- result: strict text policy at the serialization boundary ----------

def test_json_safe_refuses_undecodable_bytes_with_named_error():
    with pytest.raises(ResultEncodingError) as excinfo:
        json_safe(b"\xff\xfe not utf-8")
    assert isinstance(excinfo.value, NcarnateError)
    assert "UTF-8" in str(excinfo.value)


def test_json_safe_refuses_lone_surrogate_text():
    escaped = b"\xff".decode("utf-8", "surrogateescape")   # '\udcff'
    with pytest.raises(ResultEncodingError):
        json_safe("prefix " + escaped)


def test_json_safe_keeps_valid_text_and_decodes_valid_bytes():
    assert json_safe("units: K") == "units: K"
    assert json_safe("café — °C") == "café — °C"
    assert json_safe("café".encode("utf-8")) == "café"
    assert json_safe([b"ok", "ok"]) == ["ok", "ok"]


# --- result: plan-owned options are frozen -----------------------------

def test_encoding_options_are_frozen():
    options = EncodingOptions(zlib=True, shuffle=True, complevel=7, geolocation=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        options.complevel = 1
    assert options.complevel == 7
    # Deriving a changed request is still possible, explicitly.
    assert dataclasses.replace(options, complevel=1).complevel == 1


def test_planned_options_cannot_drift(tmp_path):
    plan = core._plan_from_path(str(_PACKED_FILL), str(tmp_path / "out.nc"))
    before = plan.options.complevel
    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.options.complevel = before + 1
    assert plan.options.complevel == before


# --- audit: unimplemented modes are refused at construction ------------

def test_audit_options_reject_unknown_mode():
    with pytest.raises(AuditModeError) as excinfo:
        AuditOptions(recursive=False, mode="deep", checksum=None)
    assert isinstance(excinfo.value, NcarnateError)
    assert "deep" in str(excinfo.value)


def test_audit_options_default_and_metadata_still_construct():
    assert AuditOptions().mode == "metadata"
    assert AuditOptions(mode="metadata").mode == "metadata"


def test_stage_inspect_refuses_invented_mode():
    with pytest.raises(NcarnateError):
        stage.inspect(str(_PACKED_FILL), mode="imaginary")


# --- atttypes: unsynced writer inquiry is refused, not silently wrong ----

def _writer_with_pending_string(path):
    ds = nc.Dataset(path, "w", format="NETCDF4")
    ds.setncattr_string("att_pending", "vlen")      # NC_STRING, not yet synced
    return ds


def test_unsynced_writer_inquiry_raises_named_error(workdir):
    ds = _writer_with_pending_string(workdir / "w.nc")
    try:
        with pytest.raises(AttTypeInquiryError) as excinfo:
            string_attributes_of(ds)
        assert "sync=True" in str(excinfo.value)
        assert "att_pending" in str(excinfo.value)
    finally:
        ds.close()


def test_writer_inquiry_with_explicit_sync_opt_in(workdir):
    ds = _writer_with_pending_string(workdir / "w.nc")
    try:
        assert "att_pending" in string_attributes_of(ds, sync=True)
    finally:
        ds.close()


def test_read_handle_inquiry_unchanged(workdir):
    path = workdir / "r.nc"
    ds = _writer_with_pending_string(path)
    ds.setncattr("att_char", "fixed")
    ds.close()
    with nc.Dataset(path, "r") as ds:
        assert string_attributes_of(ds) == frozenset({"att_pending"})


def test_hostile_key_is_refused_without_formatting():
    class Key:
        def __format__(self, _):
            raise TypeError("hostile formatter")
        def __repr__(self):
            raise TypeError("hostile repr")
    assert schema_errors({Key(): 1})
    assert schema_errors({None: 1})
