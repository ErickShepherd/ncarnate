"""Structured operation result (step 4A): JSON coercion, the deterministic
canonical serialization, the golden canonical-hash pin (step 5 freezes this),
and the read-back result over real fixtures.

Design: docs/design/ncarnate-operation-result.md. These are white-box tests —
they exercise the `ncarnate.core` execute engine and `ncarnate.result`
internals. The *public*-surface G4 gate lives in test_stage_api_g4.py.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from conftest import HDFEOS2_FIXTURES, stage

from ncarnate import core
from ncarnate.result import (
    OPERATION_RESULT_SCHEMA_VERSION,
    canonical_json,
    json_safe,
)

_GOLDEN_DIR = Path(__file__).parent / "fixtures" / "operation_result"
_PACKED_FILL = Path(__file__).parent / "fixtures" / "data" / "netcdf" / "packed_fill.nc"


# --- JSON coercion (json_safe) --------------------------------------------

def test_json_safe_numpy_scalars_become_python_scalars():
    assert json_safe(np.int16(5)) == 5 and isinstance(json_safe(np.int16(5)), int)
    assert json_safe(np.float32(1.5)) == 1.5
    # bool must not collapse to int (it is an int subclass).
    assert json_safe(np.bool_(True)) is True


def test_json_safe_tokenises_non_finite_floats():
    assert json_safe(float("nan")) == "NaN"
    assert json_safe(np.float64("inf")) == "Infinity"
    assert json_safe(-np.float64("inf")) == "-Infinity"


def test_json_safe_walks_arrays_including_non_finite():
    got = json_safe(np.array([1.0, float("inf"), float("-inf")]))
    assert got == [1.0, "Infinity", "-Infinity"]
    # uint8 arrays (the embedded-NUL companion) coerce to plain ints.
    assert json_safe(np.array([0, 255], dtype="u1")) == [0, 255]


def test_json_safe_leaves_text_and_none():
    assert json_safe("units: K") == "units: K"
    assert json_safe(None) is None


# --- canonical_form / canonical_json determinism --------------------------

def _recompress(tmp_path: Path, dst_name: str = "out.nc"):
    return core.execute(
        core._plan_from_path(str(_PACKED_FILL), str(tmp_path / dst_name))
    )


def test_canonical_form_excludes_nondeterministic_fields(tmp_path):
    res = _recompress(tmp_path)
    form = res.canonical_form()

    # per-run / per-machine / per-library-version fields are dropped
    assert "ncarnate_version" not in form
    assert "elapsed_seconds" not in form
    assert "environment" not in form
    assert "path" not in form["source"]                 # absolute path excluded
    assert set(form["destination"]) == {"container_format"}   # digest+size excluded
    assert "verifier_version" not in form["verification"]

    # the shape identity is kept
    assert form["schema_version"] == OPERATION_RESULT_SCHEMA_VERSION
    assert form["source"]["sha256"] == res.source.sha256   # deterministic digest kept


def test_canonical_json_is_stable_across_runs_and_dirs(tmp_path):
    a = canonical_json(_recompress(tmp_path, "a.nc"))
    b = canonical_json(_recompress(tmp_path, "b.nc"))
    assert a == b


def test_canonical_json_is_strict_json(tmp_path):
    # allow_nan=False round-trip: non-finite floats are already tokens.
    json.loads(canonical_json(_recompress(tmp_path)))


# --- golden pin (the artifact step 5 freezes) -----------------------------

def test_canonical_json_matches_golden(tmp_path):
    """The golden pins the exact serialized shape+content for an
    explicitly-chunked fixture. A diff means the result schema drifted —
    intended to be a loud tripwire (design KD3). Regenerate the golden only
    on a deliberate OPERATION_RESULT_SCHEMA_VERSION bump."""
    golden = (_GOLDEN_DIR / "packed_fill.canonical.json").read_text(encoding="utf-8")
    assert canonical_json(_recompress(tmp_path)) == golden


def test_golden_hash_is_pinned(tmp_path):
    golden = (_GOLDEN_DIR / "packed_fill.canonical.json").read_text(encoding="utf-8")
    digest = hashlib.sha256(golden.encode("utf-8")).hexdigest()
    assert digest == (
        "841933cfbb27ba16fb2651f2b4697a74f6421fae3362785405604c196e3d801a"
    )


# --- read-back result over the netCDF recompression path ------------------

def test_recompress_result_records_packing_and_effective_encoding(tmp_path):
    res = _recompress(tmp_path)
    assert res.operation == "recompress"
    assert res.verification.status == "verified"
    assert res.verification.verifier == "ncarnate._verify_lossless"
    # no coordinate reconstruction / renames on the storage-only path
    assert res.coordinates.generated == [] and res.coordinates.skipped == []
    assert res.name_mappings == []

    rec = res.to_record()
    root_vars = {v["name"]: v for v in rec["structure"]["variables"]}
    packed = root_vars["brightness_temp"]
    attr_names = {a["name"] for a in packed["attributes"]}
    # the central fidelity promise: packing declarations are carried
    assert {"scale_factor", "add_offset", "_FillValue"} <= attr_names
    # effective encoding read back from the committed output
    assert packed["encoding"]["zlib"] is True
    assert packed["encoding"]["complevel"] == 7
    assert packed["encoding"]["chunksizes"] == [40, 60]
    assert packed["dimensions"] == ["y", "x"]


# --- read-back result over the HDF-EOS2 conversion path -------------------

_AMSRE = next(f for f in HDFEOS2_FIXTURES if f.stem == "amsre_seaice12km_trim")
_SNOW = next(f for f in HDFEOS2_FIXTURES if f.stem == "amsre_5daysnow_trim")


def test_convert_result_reports_geolocation_and_preserves_structmetadata(tmp_path):
    staged = stage(_AMSRE, tmp_path)
    res = core.execute(core._plan_from_path(str(staged), str(tmp_path / "amsre.nc")))
    assert res.operation == "convert"
    assert res.verification.verifier == "ncarnate.hdf4.verify_conversion"

    rec = res.to_record()
    # reconstructed coordinates are reported as generated
    assert any(name.endswith("/lat") for name in res.coordinates.generated)
    assert any(name.endswith("/polar_stereographic") for name in res.coordinates.generated)

    # the HDFEOS_INFORMATION group + verbatim StructMetadata.0 survive into
    # the handoff (R2 MUST-FIX 3): an empty-variable, attribute-only group.
    info = next(
        g for g in rec["structure"]["groups"] if g["path"] == "/HDFEOS_INFORMATION"
    )
    assert "StructMetadata.0" in {a["name"] for a in info["attributes"]}


def test_convert_result_reports_name_mappings_with_parent_path(tmp_path):
    staged = stage(_SNOW, tmp_path)          # grid names contain spaces
    res = core.execute(core._plan_from_path(str(staged), str(tmp_path / "snow.nc")))
    groups = {m.original_name: m for m in res.name_mappings if m.kind == "group"}
    assert "Northern Hemisphere" in groups
    mapping = groups["Northern Hemisphere"]
    assert mapping.netcdf_name == "Northern_Hemisphere"
    assert mapping.parent_path == "/Northern_Hemisphere"


def test_convert_geolocation_off_records_a_skip(tmp_path):
    staged = stage(_AMSRE, tmp_path)
    res = core.execute(core._plan_from_path(
        str(staged), str(tmp_path / "sds.nc"), geolocation=False
    ))
    assert res.coordinates.generated == []
    assert [s.name for s in res.coordinates.skipped] == ["geolocation"]
