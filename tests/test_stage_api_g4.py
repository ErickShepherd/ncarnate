"""Gate G4 (step 4A slice): a downstream integration can perform AND fully
understand a conversion using only ncarnate's public API — no CLI invocation,
no log parsing, no private (underscore) imports.

The public path in 4A is `convert_manifest`, which returns a `ConvertResult`
whose converted records carry a structured `OperationResult`. This test *is*
the gate: it acts as that consumer and asserts every fact a Zarr tail (step 6)
would need is reachable from the returned object alone. The single-file
`inspect/plan/execute` primitives (step 4B) formalize the same reachability
for a one-shot call.

Design: docs/design/ncarnate-operation-result.md.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

# ONLY public imports — the whole point of the gate. No ncarnate.core, no
# leading-underscore names, no reading a log or the CLI's stdout.
import ncarnate
from ncarnate import (
    ConvertOptions,
    OperationResult,
    canonical_json,
    convert_manifest,
)
from ncarnate.audit import AuditOptions, audit_path


def _consumer_understands(result: OperationResult) -> dict:
    """A stand-in downstream: derive everything a materialized-Zarr tail needs
    from the structured result alone, without touching the netCDF file."""
    root = result.structure
    variables = {
        var.name: {
            "dtype": var.dtype,
            "dims": list(var.dimensions),
            "chunks": var.chunksizes,
            "zlib": var.zlib,
            "attrs": {a.name for a in var.attributes},
        }
        for var in root.variables
    }
    dims = {d.name: d.size for d in root.dimensions}
    return {
        "source_digest": result.source.sha256,
        "output_digest": result.destination.sha256,
        "verified": result.verification.status,
        "dims": dims,
        "variables": variables,
    }


def test_g4_perform_and_understand_via_public_api_only(tmp_path):
    # 1. Perform an audit (public) to produce the manifest, then convert it
    #    (public) — no CLI, no subprocess.
    root = tmp_path / "archive"
    root.mkdir()
    src = Path(__file__).parent / "fixtures" / "data" / "netcdf" / "packed_fill.nc"
    shutil.copyfile(src, root / "granule.nc")

    report = audit_path(str(root), AuditOptions(checksum="sha256"))
    manifest = tmp_path / "m.jsonl"
    manifest.write_text(
        "\n".join(json.dumps(r.to_record()) for r in report.files) + "\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out"
    result = convert_manifest(
        str(manifest),
        ConvertOptions(out_dir=str(out_dir), root=str(root),
                       statuses={"already_modern"}),
    )

    # 2. Understand it — entirely from the returned structured result.
    assert len(result.converted) == 1
    op = result.converted[0].result
    assert isinstance(op, OperationResult)

    understanding = _consumer_understands(op)
    assert understanding["verified"] == "verified"
    assert understanding["source_digest"] and understanding["output_digest"]
    assert understanding["dims"] == {"x": 60, "y": 40}
    packed = understanding["variables"]["brightness_temp"]
    assert packed["dtype"].endswith("i2")             # int16, endianness-tagged
    assert packed["dims"] == ["y", "x"]
    assert {"scale_factor", "add_offset", "_FillValue"} <= packed["attrs"]

    # 3. The result serializes to JSON and hashes canonically — both public.
    assert isinstance(canonical_json(op), str)
    json.loads(json.dumps(op.to_record(), allow_nan=False))

    # 4. Nothing private was needed: the whole contract is on ncarnate.__all__.
    for name in ("OperationResult", "canonical_json",
                 "OPERATION_RESULT_SCHEMA_VERSION"):
        assert name in ncarnate.__all__


def test_g4_output_digest_matches_the_written_file(tmp_path):
    """The consumer can trust the reported output digest without re-hashing —
    but here we prove it *does* match the bytes on disk (the digest is honest).
    """
    root = tmp_path / "archive"
    root.mkdir()
    src = Path(__file__).parent / "fixtures" / "data" / "netcdf" / "packed_fill.nc"
    shutil.copyfile(src, root / "g.nc")
    report = audit_path(str(root), AuditOptions(checksum="sha256"))
    manifest = tmp_path / "m.jsonl"
    manifest.write_text(
        "\n".join(json.dumps(r.to_record()) for r in report.files) + "\n",
        encoding="utf-8",
    )
    result = convert_manifest(
        str(manifest),
        ConvertOptions(out_dir=str(tmp_path / "out"), root=str(root),
                       statuses={"already_modern"}),
    )
    op = result.converted[0].result
    on_disk = hashlib.sha256(
        (tmp_path / "out" / "g.nc").read_bytes()
    ).hexdigest()
    assert op.destination.sha256 == on_disk
    assert op.destination.container_format == "NETCDF4"
