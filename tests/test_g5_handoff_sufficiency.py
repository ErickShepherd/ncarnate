"""Gate G5: the frozen handoff is sufficient for a Zarr tail *without* reading
terminal output or ncarnate internals.

This test **is** the gate. It plays the step-6 consumer: it validates a real
``OperationResult.to_record()`` against the frozen schema, then derives a
Zarr-v3 array spec for every variable **from the record dict alone**. To make
"record alone" mechanical rather than disciplinary, the derivation
(:func:`zarr_array_spec`) takes *only the JSON dict* — no ``OperationResult``,
no ``ncarnate.core``, no netCDF handle in scope — so a field the record fails
to carry surfaces as a ``KeyError``, never a silent re-open. The gate asserts
**concrete** derived values (exact shape / dtype / fill / chunks /
dimension_names) plus both ``None`` fallbacks (scalar grid-mapping var →
``chunksizes: null``; a variable with no ``_FillValue``).

If this derivation succeeds from the record alone, G5 holds.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import HDFEOS2_FIXTURES, stage

from ncarnate import core

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "operation_result"
_PACKED_FILL = Path(__file__).parent / "fixtures" / "data" / "netcdf" / "packed_fill.nc"
_AMSRE = next(f for f in HDFEOS2_FIXTURES if f.stem == "amsre_seaice12km_trim")

# The checked-in real AMSR-E handoff record (action 5 step 4). Consumed by the
# pinned-value gate below so those assertions are stable regardless of the
# host HDF5 version's convert-path chunking (design KD-S6). A separate test
# runs the LIVE emitter for drift-tolerant conformance.
_AMSRE_RESULT = json.loads(
    (_FIXTURE_DIR / "amsre_seaice12km.result.json").read_text(encoding="utf-8")
)


# --------------------------------------------------------------------------
# The step-6 consumer, expressed against the handoff record ALONE. A real Zarr
# tail lives in its own repo (step 6); this is the minimal derivation the gate
# needs to prove sufficiency. It imports nothing from ncarnate and never
# touches a netCDF file — its only input is the plain JSON dict.
# --------------------------------------------------------------------------

def _codecs(encoding: dict) -> list[dict]:
    """A representation-preserving codec chain from the effective encoding.

    NOTE (panel C6 — realizability vs reachability): the tokens here are a
    *sketch* proving the encoding booleans/level are **reachable** from the
    record, NOT a ready-to-open Zarr v3 codec chain. ``{"name": "shuffle"}`` is
    not a Zarr v3 *core* codec — HDF5 byte-shuffle maps to blosc-with-shuffle
    or a numcodecs extension (and needs the dtype's element size). Choosing the
    concrete v3 codec mapping is an explicit **step-6 profile decision**; G5
    proves the inputs are present, not that this exact spec materializes.
    """
    codecs: list[dict] = []
    if encoding["shuffle"]:
        codecs.append({"name": "shuffle"})
    if encoding["zlib"]:
        codecs.append({"name": "gzip", "configuration": {"level": encoding["complevel"]}})
    return codecs


def zarr_array_spec(record: dict) -> dict:
    """Derive a Zarr-v3 array spec per variable path from a handoff record.

    Resolves each variable's shape from the dimension **names** it spans
    against the sizes declared on its group (and inherited ancestors) — a
    missing dimension is a ``KeyError`` (the record is insufficient), never a
    silent netCDF re-open. Byteorder comes from ``dtype`` (concrete), never
    from ``endian`` (``"native"`` is not an order).
    """
    specs: dict[str, dict] = {}

    def walk(group: dict, inherited: dict) -> None:
        dims = dict(inherited)
        for dimension in group["dimensions"]:
            dims[dimension["name"]] = dimension["size"]

        for variable in group["variables"]:
            shape = tuple(dims[name] for name in variable["dimensions"])
            encoding = variable["encoding"]
            chunks = (
                tuple(encoding["chunksizes"])
                if encoding["chunksizes"] is not None
                else shape                      # contiguous -> one chunk == shape
            )
            fill = None
            for attribute in variable["attributes"]:
                if attribute["name"] == "_FillValue":
                    fill = attribute["value"]
                    break

            path = group["path"].rstrip("/") + "/" + variable["name"]
            specs[path] = {
                "shape"          : shape,
                "dimension_names": list(variable["dimensions"]),
                "dtype"          : variable["dtype"],   # byteorder from dtype
                "fill_value"     : fill,
                "chunks"         : chunks,
                "codecs"         : _codecs(encoding),
            }

        for child in group["groups"]:
            walk(child, dims)

    walk(record["structure"], {})
    return specs


# --- schema-validity of the record the gate consumes ----------------------
# (reuse the frozen contract validator so the gate consumes a *schema-valid*
# record, exactly as a downstream would.)

from test_handoff_schema import _validate   # noqa: E402


def _amsre_record(tmp_path) -> dict:
    staged = stage(_AMSRE, tmp_path)
    plan = core._plan_from_path(str(staged), str(tmp_path / "amsre.nc"))
    return core.execute(plan).to_record()


# --- the gate: derive a full Zarr spec from the record alone --------------

def test_g5_zarr_spec_derivable_from_amsre_record_alone():
    # Consume the checked-in real AMSR-E handoff record. It is a schema-valid
    # record a downstream would receive; the pinned values below are stable
    # across hosts (a live convert's chunking could vary by HDF5 version).
    _validate(_AMSRE_RESULT)                 # a downstream validates first

    # Pure-JSON round-trip: proves the derivation needs nothing but the dict.
    specs = zarr_array_spec(json.loads(json.dumps(_AMSRE_RESULT)))

    # A packed data variable: concrete shape / dtype / chunks / dimension names.
    ice = specs["/NpPolarGrid12km/SI_12km_NH_ICECON_DAY"]
    assert ice["shape"] == (896, 608)
    assert ice["dtype"] == "<i2"            # byteorder present (not "native")
    assert ice["chunks"] == (896, 608)
    assert ice["dimension_names"] == ["YDim", "XDim"]

    # The reconstructed 2-D geolocation arrays carry the same grid shape.
    assert specs["/NpPolarGrid12km/lat"]["shape"] == (896, 608)
    assert specs["/SpPolarGrid12km/lon"]["shape"] == (664, 632)

    # None-fallback #1 — a scalar grid-mapping variable: no dims, no chunks.
    grid_mapping = specs["/NpPolarGrid12km/polar_stereographic"]
    assert grid_mapping["shape"] == ()
    assert grid_mapping["chunks"] == ()      # chunksizes was null -> shape

    # None-fallback #2 — these AMSR-E variables declare no _FillValue.
    assert ice["fill_value"] is None
    assert grid_mapping["fill_value"] is None

    # Coordinates are reachable from the record, no re-open.
    generated = set(_AMSRE_RESULT["coordinates"]["generated"])
    assert {"/NpPolarGrid12km/lat", "/NpPolarGrid12km/lon",
            "/NpPolarGrid12km/polar_stereographic"} <= generated


def test_g5_live_emitter_stays_sufficient(tmp_path):
    # Drift-tolerant conformance: the LIVE emitter still produces a
    # schema-valid, spec-derivable record (no chunk-value pins, so a host HDF5
    # difference cannot flake this — it guards the emitter, not the snapshot).
    record = _amsre_record(tmp_path)
    _validate(record)
    specs = zarr_array_spec(json.loads(json.dumps(record)))
    ice = specs["/NpPolarGrid12km/SI_12km_NH_ICECON_DAY"]
    assert ice["shape"] == (896, 608)
    assert ice["dtype"] == "<i2"
    assert ice["chunks"]                     # derivable (value may vary by host)
    assert ice["dimension_names"] == ["YDim", "XDim"]


def test_g5_real_fill_and_chunks_derivable_from_packed_fill(tmp_path):
    # The with-fill / with-explicit-chunks case (AMSR-E covers the null paths).
    plan = core._plan_from_path(str(_PACKED_FILL), str(tmp_path / "out.nc"))
    record = core.execute(plan).to_record()
    _validate(record)

    specs = zarr_array_spec(json.loads(json.dumps(record)))
    packed = specs["/brightness_temp"]
    assert packed["shape"] == (40, 60)
    assert packed["dtype"] == "<i2"
    assert packed["chunks"] == (40, 60)
    assert packed["fill_value"] == -32768               # a real fill value
    assert packed["dimension_names"] == ["y", "x"]
    assert {"name": "gzip", "configuration": {"level": 7}} in packed["codecs"]


def test_g5_endian_field_is_not_a_concrete_byteorder(tmp_path):
    # Guards the derivation choice: `endian` is "native", so a consumer that
    # read byteorder from it (instead of `dtype`) would produce a wrong Zarr
    # dtype. This pins the record's contract, not just our helper.
    record = _amsre_record(tmp_path)
    ice = record["structure"]["groups"]
    npgrid = next(g for g in ice if g["path"] == "/NpPolarGrid12km")
    var = next(v for v in npgrid["variables"] if v["name"] == "SI_12km_NH_ICECON_DAY")
    assert var["endian"] == "native"
    assert var["dtype"].startswith("<")     # the concrete order lives here


def test_g5_missing_dimension_is_a_key_error_not_a_reopen():
    # Insufficiency is loud: strip a dimension the variable references and the
    # derivation raises rather than silently guessing or re-opening.
    record = json.loads(json.dumps(_AMSRE_RESULT))   # deep copy of the fixture
    npgrid = next(
        g for g in record["structure"]["groups"] if g["path"] == "/NpPolarGrid12km"
    )
    npgrid["dimensions"] = []               # remove the shape source
    try:
        zarr_array_spec(record)
    except KeyError:
        return
    raise AssertionError("expected a KeyError from an unresolvable dimension")
