"""--in-place and --skip-existing tests for convert_manifest (design §Output
destination, KD3).

Two out-dir-mode-optional flags:

- `--in-place` (`ConvertOptions(in_place=True)`) omits the computed `dst` and
  lets `recompress` replace a netCDF source in place after its verify-lossless
  step. There is no mirrored output tree; the source path *is* the output.
- `--skip-existing` (`ConvertOptions(skip_existing=True)`, out-dir mode) skips
  any record whose computed output path already exists, making a terabyte-scale
  run resumable without re-doing work. It relies on the predictable mirrored
  output path, so it is scoped to `--out-dir` mode and is inert under
  `--in-place`.

The current `convert_manifest` handles neither flag; these fail until the
paired [impl] unit lands them. Authored ATDD (red) — do not implement here.
"""

import json
import os
import shutil

from conftest import NETCDF_FIXTURES, assert_lossless_netcdf

from ncarnate.audit.codes import RULESET_VERSION
from ncarnate.audit.models import SCHEMA_VERSION
from ncarnate.formats import detect_format
from ncarnate.hashing import sha256_of_file

from ncarnate.convert import ConvertOptions, convert_manifest


def _stage_at(fixture, root, relpath):
    """Copy a committed fixture to root/relpath (nested), returning its Path."""
    dst = root / relpath
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fixture, dst)
    return dst


def _record(root, relpath, staged, *, status, plan):
    """A faithful record for a staged file with its real format + sha256."""
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


def _write_manifest(workdir, records):
    manifest = workdir / "m.jsonl"
    with open(manifest, "w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record) + "\n")
    return str(manifest)


def _expected_output(out_dir, relpath, fmt):
    """The mirrored output path: HDF4 sources swap the extension to .nc."""
    if fmt == "HDF4":
        relpath = os.path.splitext(relpath)[0] + ".nc"
    return out_dir / relpath


# --- --in-place: a netCDF source is recompressed in place, no mirror ---

def test_in_place_recompresses_source_and_writes_no_mirror(workdir):
    """`in_place=True` (no out_dir) recompresses the source at its own path;
    the bytes stay lossless against the committed original and no mirrored
    out-dir tree is created."""
    fixture = NETCDF_FIXTURES[0]
    root = workdir / "root"
    relpath = f"nested/dir/{fixture.name}"
    staged = _stage_at(fixture, root, relpath)

    manifest = _write_manifest(
        workdir,
        [_record(root, relpath, staged, status="ready",
                 plan={"operation": "recompress"})],
    )

    result = convert_manifest(manifest, ConvertOptions(in_place=True, allow_manifest_root=True))

    # The record converted in place: the source path still holds a valid,
    # lossless netCDF (recompress verifies losslessness before replacing).
    assert [r.path for r in result.converted] == [relpath]
    assert staged.is_file()
    assert_lossless_netcdf(fixture, staged)
    # No mirrored out-dir tree was invented alongside the source root.
    assert not (workdir / "out").exists()


# --- --skip-existing: a record whose output already exists is skipped ---

def test_skip_existing_skips_record_with_preexisting_output(workdir):
    """`skip_existing=True` (out-dir mode) skips a record whose computed
    output path already exists, leaving that output untouched and never
    re-converting."""
    fixture = NETCDF_FIXTURES[0]
    root, out_dir = workdir / "root", workdir / "out"
    relpath = fixture.name
    staged = _stage_at(fixture, root, relpath)

    # Pre-seed the computed output with a sentinel: a resumed run must leave
    # it exactly as-is (proof the record was skipped, not re-written).
    output = _expected_output(out_dir, relpath, "HDF5")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"pre-existing sentinel")

    manifest = _write_manifest(
        workdir,
        [_record(root, relpath, staged, status="ready",
                 plan={"operation": "recompress"})],
    )

    result = convert_manifest(
        manifest,
        ConvertOptions(out_dir=str(out_dir), skip_existing=True, allow_manifest_root=True),
    )

    assert relpath in [r.path for r in result.skipped]
    assert all(r.path != relpath for r in result.converted)
    assert [r.reason for r in result.skipped if r.path == relpath][0]  # a reason
    # Untouched: the sentinel bytes prove no re-conversion overwrote it.
    assert output.read_bytes() == b"pre-existing sentinel"


def test_skip_existing_converts_when_output_absent(workdir):
    """`skip_existing=True` only skips *existing* outputs — a record whose
    output does not yet exist still converts normally (the resume boundary)."""
    fixture = NETCDF_FIXTURES[0]
    root, out_dir = workdir / "root", workdir / "out"
    relpath = fixture.name
    staged = _stage_at(fixture, root, relpath)

    manifest = _write_manifest(
        workdir,
        [_record(root, relpath, staged, status="ready",
                 plan={"operation": "recompress"})],
    )

    result = convert_manifest(
        manifest,
        ConvertOptions(out_dir=str(out_dir), skip_existing=True, allow_manifest_root=True),
    )

    output = _expected_output(out_dir, relpath, "HDF5")
    assert [r.path for r in result.converted] == [relpath]
    assert output.is_file()
    assert_lossless_netcdf(staged, output)


# --- scope: --skip-existing is an out-dir-mode-only guarantee ----------

def test_skip_existing_is_inert_under_in_place(workdir):
    """`skip_existing` is scoped to out-dir mode: under `--in-place` there is
    no computed mirrored output path to test for existence, so the flag is
    inert and the source is still recompressed in place (the resumability
    guarantee is out-dir-mode-only, per the HDF4 re-run caveat)."""
    fixture = NETCDF_FIXTURES[0]
    root = workdir / "root"
    relpath = fixture.name
    staged = _stage_at(fixture, root, relpath)

    manifest = _write_manifest(
        workdir,
        [_record(root, relpath, staged, status="ready",
                 plan={"operation": "recompress"})],
    )

    result = convert_manifest(
        manifest, ConvertOptions(in_place=True, skip_existing=True, allow_manifest_root=True)
    )

    # Not skipped by skip_existing — recompressed in place despite the flag.
    assert [r.path for r in result.converted] == [relpath]
    assert all(r.path != relpath for r in result.skipped)
    assert_lossless_netcdf(fixture, staged)
