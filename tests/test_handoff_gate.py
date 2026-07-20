"""The consumer-side handoff gates (handoff-contract hardening, panel C1/C2).

A downstream consumer (the step-6 Zarr tail) runs a *received* record through
two gates before materializing a store:

* ``validate_handoff`` — well-formed per the frozen schema;
* ``check_materializable`` — schema-valid AND safe to build a store from.

The load-bearing test here is the **trap**: the degraded read-back record
(``core._minimal_result``) is schema-VALID and labelled ``verified``, yet
describes an empty store. A naive consumer following "validate then derive"
would "successfully" materialize *nothing*. ``check_materializable`` must
refuse it. This is the G6 failure class, injected upstream of where G6 can see
it — so it is gated at the contract boundary instead.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ncarnate import core
from ncarnate.audit.codes import (
    HANDOFF_NOT_MATERIALIZABLE,
    HANDOFF_SCHEMA_INVALID,
)
from ncarnate.errors import HandoffError
from ncarnate.handoff import (
    check_materializable,
    materializability_error,
    schema_errors,
    validate_handoff,
)
from ncarnate.result import OPERATION_RESULT_SCHEMA_VERSION

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "operation_result"
_PACKED_FILL = Path(__file__).parent / "fixtures" / "data" / "netcdf" / "packed_fill.nc"
_AMSRE_RESULT = json.loads(
    (_FIXTURE_DIR / "amsre_seaice12km.result.json").read_text(encoding="utf-8")
)


def _good_record(tmp_path) -> dict:
    plan = core._plan_from_path(str(_PACKED_FILL), str(tmp_path / "out.nc"))
    return core.execute(plan).to_record()


# --- validate_handoff: the schema gate --------------------------------

def test_validate_handoff_accepts_a_real_record():
    validate_handoff(_AMSRE_RESULT)                      # no raise


def test_validate_handoff_rejects_schema_invalid(tmp_path):
    record = _good_record(tmp_path)
    del record["plan_hash"]                              # drop a required key
    with pytest.raises(HandoffError) as excinfo:
        validate_handoff(record)
    assert excinfo.value.code == HANDOFF_SCHEMA_INVALID
    assert "plan_hash" in str(excinfo.value)


def test_good_record_is_materializable():
    check_materializable(_AMSRE_RESULT)                  # no raise


# --- check_materializable: the empty-store trap -----------------------

def test_degraded_readback_record_is_a_schema_valid_trap(tmp_path, monkeypatch):
    # Force the real post-commit read-back to fail: `core.execute` then returns
    # the genuine `_minimal_result` degraded record (empty structure, a
    # `verified` status, a RESULT_READBACK_INCOMPLETE warning). This is the
    # real code path, not a hand-built dict.
    def _boom(*args, **kwargs):
        raise RuntimeError("simulated read-back failure")

    monkeypatch.setattr(core, "_build_operation_result", _boom)
    plan = core._plan_from_path(str(_PACKED_FILL), str(tmp_path / "out.nc"))
    degraded = core.execute(plan).to_record()

    # The trap: schema-VALID, `verified`, non-empty destination — a naive
    # consumer would accept it and build an empty store.
    assert not schema_errors(degraded)
    assert degraded["verification"]["status"] == "verified"
    assert degraded["destination"]["size_bytes"] > 0

    # The gate refuses it.
    with pytest.raises(HandoffError) as excinfo:
        check_materializable(degraded)
    assert excinfo.value.code == HANDOFF_NOT_MATERIALIZABLE
    assert "READBACK" in str(excinfo.value).upper()


def test_empty_structure_over_nonempty_destination_refused():
    # The empty-store trap independent of the readback warning: a record whose
    # structure carries no variables while the destination is non-empty.
    record = json.loads(json.dumps(_AMSRE_RESULT))
    record["structure"] = {
        "path": "/", "dimensions": [], "variables": [], "groups": [],
        "attributes": [],
    }
    assert not schema_errors(record)                     # still schema-valid
    reason = materializability_error(record)
    assert reason is not None and "no variables" in reason


def test_unknown_schema_version_refused():
    # A materializability gate a consumer may apply before schema-validating
    # (the schema `const` would also reject it, but the gate must stand alone).
    record = json.loads(json.dumps(_AMSRE_RESULT))
    record["schema_version"] = OPERATION_RESULT_SCHEMA_VERSION + 99
    reason = materializability_error(record)
    assert reason is not None and "schema_version" in reason


@pytest.mark.parametrize("hostile", [None, "not a record", 42, [], {}])
def test_materializability_error_fails_closed_on_hostile_input(hostile):
    # This runs at an untrusted boundary: a non-object record (or a missing /
    # unknown schema_version) must fail CLOSED to a reason string (unsafe),
    # never leak a stdlib exception and never return None ("safe").
    reason = materializability_error(hostile)            # no raise
    assert reason is not None


def test_materializability_error_no_raise_on_malformed_subfields():
    # A well-versioned record whose sub-objects are the wrong shape must not
    # leak a stdlib exception (the reviewer's threat-model finding). The return
    # value is unconstrained here — such a record is rejected upstream by
    # validate_handoff / check_materializable; the guarantee is "no crash".
    record = {
        "schema_version": OPERATION_RESULT_SCHEMA_VERSION,
        "destination": "not-a-dict",
        "warnings": ["not-a-dict"],
        "structure": "not-a-dict",
    }
    materializability_error(record)                      # no raise
