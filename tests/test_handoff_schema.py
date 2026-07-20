"""The verified-netCDF4 handoff contract (step 5, the freeze).

Mirrors ``tests/audit/test_contract.py``: the frozen JSON Schema is the
contract a downstream consumer validates a received
``OperationResult.to_record()`` against. Since the handoff-contract hardening
the schema and its validator **ship in the package** (``ncarnate/schemas/
handoff.schema.json`` + :mod:`ncarnate.handoff`) — a single source of truth,
so this test drives the *same* validator a real consumer imports rather than a
test-local copy. It proves three things:

1. every emitted record — over the in-repo netCDF and HDF-EOS2 fixtures —
   validates against the schema;
2. the schema is **tight, not vacuous**: a malformed record, a stray key, and
   a wrong-typed adapter version are all rejected;
3. the schema-version ``const`` tracks ``OPERATION_RESULT_SCHEMA_VERSION`` (so
   a shape bump that forgets the schema fails loudly).

The validator is a tiny stdlib JSON-Schema-subset — no new dependency (the
audit-contract spec constraint) — now living in :mod:`ncarnate.handoff`.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import HDFEOS2_FIXTURES, stage

from ncarnate import core
from ncarnate.handoff import load_handoff_schema, schema_errors
from ncarnate.result import OPERATION_RESULT_SCHEMA_VERSION

SCHEMA = load_handoff_schema()

_PACKED_FILL = Path(__file__).parent / "fixtures" / "data" / "netcdf" / "packed_fill.nc"
_AMSRE = next(f for f in HDFEOS2_FIXTURES if f.stem == "amsre_seaice12km_trim")
_SNOW = next(f for f in HDFEOS2_FIXTURES if f.stem == "amsre_5daysnow_trim")


# The consumer-facing validator is what the tests exercise (single source of
# truth: `ncarnate.handoff`). `_errors`/`_validate` stay as thin local aliases
# so the assertions below (and the G5 gate that imports `_validate`) read the
# same as before the promotion.

def _errors(instance):
    return schema_errors(instance)


def _validate(instance):
    errors = _errors(instance)
    assert not errors, "schema violations: " + "; ".join(errors)


def _record(source: Path, tmp_path: Path, dst: str, **kwargs):
    staged = stage(source, tmp_path)
    plan = core._plan_from_path(str(staged), str(tmp_path / dst), **kwargs)
    return core.execute(plan).to_record()


# --- 1. emitted records validate --------------------------------------

def test_recompress_record_validates(tmp_path):
    plan = core._plan_from_path(str(_PACKED_FILL), str(tmp_path / "out.nc"))
    _validate(core.execute(plan).to_record())


def test_convert_record_validates_with_nested_groups(tmp_path):
    # AMSR-E is the richest handoff: nested HDFEOS_INFORMATION group with
    # verbatim StructMetadata.0, name mappings, reconstructed coordinates.
    record = _record(_AMSRE, tmp_path, "amsre.nc")
    _validate(record)
    # prove the recursion actually exercised depth > 1
    assert any(
        g["path"] == "/HDFEOS_INFORMATION" for g in record["structure"]["groups"]
    )


def test_convert_record_with_name_mappings_validates(tmp_path):
    record = _record(_SNOW, tmp_path, "snow.nc")   # grid names contain spaces
    _validate(record)
    assert record["name_mappings"], "expected sanitized-name companions"


def test_checked_in_real_result_fixture_validates():
    # The committed real AMSR-E handoff record (action 5 step 4) stays
    # schema-valid — the reference the G5 gate consumes.
    real = json.loads(
        (Path(__file__).parent / "fixtures" / "operation_result"
         / "amsre_seaice12km.result.json").read_text(encoding="utf-8")
    )
    _validate(real)
    assert real["schema_version"] == OPERATION_RESULT_SCHEMA_VERSION


# --- 2. the schema is tight, not vacuous ------------------------------

def test_schema_rejects_missing_required_and_stray_key(tmp_path):
    plan = core._plan_from_path(str(_PACKED_FILL), str(tmp_path / "out.nc"))
    record = core.execute(plan).to_record()
    del record["plan_hash"]         # drop a required top-level key
    record["extra"] = 1             # add a key the closed object forbids
    errors = _errors(record)
    assert any("plan_hash" in e for e in errors)
    assert any("extra" in e for e in errors)


def test_schema_rejects_wrong_typed_adapter_version(tmp_path):
    # The open-VALUED adapter_versions map still constrains values to
    # string|null: an integer version must be rejected (proves the
    # additionalProperties-schema branch is enforced, not vacuous).
    plan = core._plan_from_path(str(_PACKED_FILL), str(tmp_path / "out.nc"))
    record = core.execute(plan).to_record()
    record["environment"]["adapter_versions"]["numpy"] = 123
    assert any("adapter_versions" in e for e in _errors(record))


def test_schema_rejects_bare_object_attribute_value(tmp_path):
    # attributes[].value is an open union of scalar/array/token — never a bare
    # object. A dict value must be rejected.
    plan = core._plan_from_path(str(_PACKED_FILL), str(tmp_path / "out.nc"))
    record = core.execute(plan).to_record()
    record["structure"]["variables"][0]["attributes"][0]["value"] = {"nope": 1}
    # specific, not just non-empty: the error must concern the mutated value.
    assert any(".value" in e for e in _errors(record))


# --- 3. schema-version const tracks the code --------------------------

def test_schema_version_const_tracks_code():
    const = SCHEMA["properties"]["schema_version"]["const"]
    assert const == OPERATION_RESULT_SCHEMA_VERSION == 2


# --- 4. the open caller-owned bags accept caller data -----------------

def test_open_bags_accept_caller_attachments(tmp_path):
    # A pipeline fills the retention slot and the validation record on the SAME
    # record it received; the frozen schema must still accept it (KD-S1/KD-S4).
    plan = core._plan_from_path(str(_PACKED_FILL), str(tmp_path / "out.nc"))
    record = core.execute(plan).to_record()
    record["retention"] = {"policy": "keep-30d", "tier": "cold"}
    record["validation"] = {
        "status": "passed",
        "validator": "my-pipeline@1.2",
        "method": "range + CF check",
        "record": {"checks": ["range", "cf"], "passed": True},
    }
    _validate(record)
