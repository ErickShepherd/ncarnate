"""Failing-test matrix for the whole-manifest destination preflight under
``--in-place`` (independent-review finding F1; IMPLEMENTATION_PLAN
remediation-hold item 1; KD-L1/KD-L2; gate G1).

``--in-place`` is *not* a uniform "no destination" mode. A netCDF/HDF5
source is genuinely replaced at its own path after a verified write (KD3),
so it has no separate output to collide. But an HDF4/HDF-EOS2 source is a
format *conversion*: ``recompress`` derives a ``<source-stem>.nc`` sibling
**beside the source** and never touches the HDF4 original. That derived
sibling is a real output and must take part in the whole-run collision
preflight exactly as a mirrored ``--out-dir`` destination does — otherwise
two HDF4 sources deriving one ``.nc`` (or an HDF4 source whose derived
``.nc`` aliases a selected ``.nc`` source, or a pre-existing derived
``.nc``) partially executes instead of refusing the whole run, violating
G1's zero-mutation rule.

Before the F1 fix, ``preflight_destinations`` assigned ``destination =
None`` to every ``--in-place`` record and skipped all destination checks;
these regressions fail until the paired impl models the HDF4 derived
sibling.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

from conftest import (
    HDFEOS2_FIXTURES,
    NETCDF_FIXTURES,
)

from ncarnate.audit.codes import RULESET_VERSION
from ncarnate.audit.models import SCHEMA_VERSION
from ncarnate.errors import NcarnateError
from ncarnate.formats import detect_format
from ncarnate.hashing import sha256_of_file

from ncarnate.convert import ConvertOptions, convert_manifest

# The stable whole-run refusal code (KD-L2) — the operator-facing contract.
COLLISION_CODE = "DESTINATION_COLLISION"


def _stage_at(fixture, root, relpath):
    """Copy a committed fixture to root/relpath (nested), returning its Path."""
    dst = root / relpath
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fixture, dst)
    return dst


def _record(root, relpath, staged, *, plan, status="ready"):
    return {
        "schema_version": SCHEMA_VERSION,
        "ncarnate_version": "0.0.0",
        "ruleset_version": RULESET_VERSION,
        "mode": "metadata",
        "audited_at": "2026-01-01T00:00:00Z",
        "root": str(root),
        "path": relpath,
        "size_bytes": staged.stat().st_size,
        "sha256": sha256_of_file(str(staged)),
        "format": detect_format(str(staged)).name,
        "status": status,
        "structures": [],
        "issues": [],
        "plan": plan,
    }


def _hdf_record(root, relpath, staged, **overrides):
    return _record(root, relpath, staged, plan={"operation": "convert"},
                   **overrides)


def _nc_record(root, relpath, staged, **overrides):
    return _record(root, relpath, staged, plan={"operation": "recompress"},
                   **overrides)


def _write_manifest(workdir, records):
    manifest = workdir / "m.jsonl"
    with open(manifest, "w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record) + "\n")
    return str(manifest)


def _tree_snapshot(root):
    """{relative path -> sha256} for every file under ``root`` right now."""
    snapshot = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(dirpath, name)
            snapshot[os.path.relpath(path, root)] = sha256_of_file(path)
    return snapshot


def _expect_refusal(manifest, options, *, involved):
    with pytest.raises(NcarnateError) as excinfo:
        convert_manifest(manifest, options)
    error = excinfo.value
    assert getattr(error, "code", None) == COLLISION_CODE
    message = str(error)
    for fragment in involved:
        assert fragment in message, f"{fragment!r} not in {message!r}"
    return error


# --- same-stem .hdf/.he5 under --in-place both derive same.nc ----------

def test_in_place_hdf_he5_same_stem_derived_collision_refused(workdir):
    root = workdir / "root"
    hdf = _stage_at(HDFEOS2_FIXTURES[0], root, "sub/same.hdf")
    he5 = _stage_at(HDFEOS2_FIXTURES[0], root, "sub/same.he5")  # HDF4 bytes
    before = _tree_snapshot(root)
    manifest = _write_manifest(workdir, [
        _hdf_record(root, "sub/same.hdf", hdf),  # derives sub/same.nc ...
        _hdf_record(root, "sub/same.he5", he5),  # ... and so does this
    ])

    _expect_refusal(
        manifest,
        ConvertOptions(in_place=True, allow_manifest_root=True),
        involved=["sub/same.hdf", "sub/same.he5"],
    )
    # Gate G1: the whole run was refused before any output was written.
    assert _tree_snapshot(root) == before
    assert not (root / "sub" / "same.nc").exists()


# --- an HDF4 source whose derived .nc aliases a selected .nc source ----

def test_in_place_hdf4_derived_nc_aliases_selected_nc_source_refused(workdir):
    root = workdir / "root"
    hdf = _stage_at(HDFEOS2_FIXTURES[0], root, "a.hdf")  # derives a.nc
    nc = _stage_at(NETCDF_FIXTURES[0], root, "a.nc")     # a selected source
    before = _tree_snapshot(root)
    manifest = _write_manifest(workdir, [
        _hdf_record(root, "a.hdf", hdf),
        _nc_record(root, "a.nc", nc),  # in-place recompression of a.nc itself
    ])

    _expect_refusal(
        manifest,
        ConvertOptions(in_place=True, allow_manifest_root=True),
        involved=["a.hdf", "a.nc"],
    )
    assert _tree_snapshot(root) == before  # neither source touched


# --- a pre-existing derived .nc sibling, no resume policy --------------

def test_in_place_preexisting_derived_nc_refused_without_resume(workdir):
    root = workdir / "root"
    hdf = _stage_at(HDFEOS2_FIXTURES[0], root, "g.hdf")  # derives g.nc
    existing = root / "g.nc"
    existing.write_bytes(b"pre-existing sentinel")  # not in the manifest
    manifest = _write_manifest(workdir, [_hdf_record(root, "g.hdf", hdf)])

    _expect_refusal(
        manifest,
        ConvertOptions(in_place=True, allow_manifest_root=True),
        involved=["g.hdf"],
    )
    assert existing.read_bytes() == b"pre-existing sentinel"  # untouched


# --- --skip-existing resumes an in-place HDF4 run past its derived .nc --

def test_in_place_skip_existing_skips_preexisting_derived_nc(workdir):
    # The resume boundary must also hold for in-place HDF4: a derived .nc
    # that already exists is *skipped* (not re-converted, not refused) when
    # the operator selected the resume policy — the same guarantee out-dir
    # mode gives, now that the derived sibling is modeled.
    root = workdir / "root"
    hdf = _stage_at(HDFEOS2_FIXTURES[0], root, "g.hdf")
    existing = root / "g.nc"
    existing.write_bytes(b"already converted sentinel")
    manifest = _write_manifest(workdir, [_hdf_record(root, "g.hdf", hdf)])

    result = convert_manifest(
        manifest,
        ConvertOptions(in_place=True, skip_existing=True,
                       allow_manifest_root=True),
    )

    assert "g.hdf" in [r.path for r in result.skipped]
    assert all(r.path != "g.hdf" for r in result.converted)
    assert existing.read_bytes() == b"already converted sentinel"  # untouched


# --- a non-colliding in-place HDF4 run still converts to its sibling ---

def test_in_place_hdf4_converts_to_derived_sibling_when_clear(workdir):
    root = workdir / "root"
    hdf = _stage_at(HDFEOS2_FIXTURES[0], root, "clean.hdf")
    source_before = sha256_of_file(str(hdf))
    manifest = _write_manifest(workdir, [_hdf_record(root, "clean.hdf", hdf)])

    result = convert_manifest(
        manifest, ConvertOptions(in_place=True, allow_manifest_root=True)
    )

    assert [r.path for r in result.converted] == ["clean.hdf"]
    assert (root / "clean.nc").is_file()               # derived sibling written
    assert sha256_of_file(str(hdf)) == source_before   # HDF4 source untouched


# --- end-to-end through the real CLI (gate G1 wiring evidence) ---------

def test_cli_in_place_derived_collision_refusal_end_to_end(workdir):
    root = workdir / "root"
    hdf = _stage_at(HDFEOS2_FIXTURES[0], root, "same.hdf")
    he5 = _stage_at(HDFEOS2_FIXTURES[0], root, "same.he5")
    before = _tree_snapshot(root)
    manifest = _write_manifest(workdir, [
        _hdf_record(root, "same.hdf", hdf),
        _hdf_record(root, "same.he5", he5),
    ])

    completed = subprocess.run(
        [sys.executable, "-m", "ncarnate", "convert", "--manifest", manifest,
         "--in-place", "--root", str(root)],
        capture_output=True, text=True,
    )

    assert completed.returncode == 2                # whole-run refusal
    assert COLLISION_CODE in completed.stderr
    for fragment in ("same.hdf", "same.he5"):
        assert fragment in completed.stderr
    assert _tree_snapshot(root) == before           # gate G1: nothing written
    assert not (root / "same.nc").exists()
