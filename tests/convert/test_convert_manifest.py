"""convert_manifest core-loop tests over the committed fixtures (design §The
per-record loop step 4, §Output destination, §Per-status parameters, KD3/KD4).

The loop reads a manifest and, for each selected record, re-verifies the
sha256 (tested in test_integrity), computes a mirrored output path under
--out-dir (HDF4/HDF-EOS2 → `.nc`, netCDF name kept), and drives the existing
`recompress` with an explicit dst and a per-status geolocation override. A
blocker record is skipped with a counted reason, never converted; sources
are never mutated. Deep HDF4→netCDF fidelity is already proven by
test_hdf4_conversion — here we verify the *loop's* responsibilities:
placement, name-swap, per-status behavior, isolation, and non-destruction.

`ncarnate.convert.convert_manifest` does not exist yet; these fail until the
paired [impl] unit lands it.
"""

import json
import os
import shutil

import netCDF4 as nc
import pytest

from conftest import (
    BLOCKER_FIXTURES,
    HDFEOS2_FIXTURES,
    NETCDF_FIXTURES,
    assert_lossless_netcdf,
)

from ncarnate import recompress
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


# --- ready: netCDF fixtures recompress losslessly into the mirror ------

@pytest.mark.parametrize("fixture", NETCDF_FIXTURES, ids=lambda p: p.stem)
def test_ready_netcdf_converts_lossless_into_mirror(fixture, workdir):
    root, out_dir = workdir / "root", workdir / "out"
    relpath = f"nested/dir/{fixture.name}"
    staged = _stage_at(fixture, root, relpath)
    manifest = _write_manifest(
        workdir,
        [_record(root, relpath, staged, status="ready", plan={"operation": "recompress"})],
    )

    result = convert_manifest(manifest, ConvertOptions(out_dir=str(out_dir)))

    output = _expected_output(out_dir, relpath, "HDF5")
    assert output.is_file()                      # name kept, mirrored tree
    assert [r.path for r in result.converted] == [relpath]
    assert_lossless_netcdf(staged, output)       # bit-for-bit fidelity


# --- ready: HDF4/HDF-EOS2 fixtures convert with the .nc name-swap ------

@pytest.mark.parametrize("fixture", HDFEOS2_FIXTURES, ids=lambda p: p.stem)
def test_ready_hdf4_converts_with_nc_swap_into_mirror(fixture, workdir):
    root, out_dir = workdir / "root", workdir / "out"
    relpath = f"nested/{fixture.name}"
    staged = _stage_at(fixture, root, relpath)
    manifest = _write_manifest(
        workdir,
        [_record(root, relpath, staged, status="ready", plan={"operation": "convert"})],
    )

    result = convert_manifest(manifest, ConvertOptions(out_dir=str(out_dir)))

    output = _expected_output(out_dir, relpath, "HDF4")
    assert output.suffix == ".nc" and output.is_file()   # HDF4 -> .nc swap
    assert [r.path for r in result.converted] == [relpath]
    with nc.Dataset(output) as dataset:                  # a valid, non-empty nc
        assert len(dataset.variables) or len(dataset.groups)


# --- ready_no_geolocation: SDS-only (geolocation forced off, KD4) ------

def test_ready_no_geolocation_forces_geolocation_off(workdir):
    fixture = HDFEOS2_FIXTURES[0]
    root, out_dir = workdir / "root", workdir / "out"
    relpath = fixture.name
    staged = _stage_at(fixture, root, relpath)
    manifest = _write_manifest(
        workdir,
        [_record(root, relpath, staged,
                 status="ready_no_geolocation", plan={"operation": "convert"})],
    )

    result = convert_manifest(
        manifest,
        ConvertOptions(out_dir=str(out_dir), statuses={"ready_no_geolocation"}),
    )

    output = _expected_output(out_dir, relpath, "HDF4")
    assert [r.path for r in result.converted] == [relpath]
    # Independent reference: an SDS-only (geolocation=False) recompress of the
    # same source. If the loop had NOT forced geolocation off, its output would
    # carry extra reconstructed coordinate variables and this would fail.
    reference = recompress(str(_stage_at(fixture, workdir / "ref", relpath)),
                           dst=str(workdir / "ref_out.nc"), geolocation=False)
    assert_lossless_netcdf(output, reference)


# --- already_modern: recompress a netCDF in place of conversion --------

def test_already_modern_recompresses_lossless(workdir):
    fixture = NETCDF_FIXTURES[0]
    root, out_dir = workdir / "root", workdir / "out"
    relpath = fixture.name
    staged = _stage_at(fixture, root, relpath)
    manifest = _write_manifest(
        workdir,
        [_record(root, relpath, staged,
                 status="already_modern", plan={"operation": "recompress"})],
    )

    result = convert_manifest(
        manifest, ConvertOptions(out_dir=str(out_dir), statuses={"already_modern"})
    )

    output = _expected_output(out_dir, relpath, "HDF5")
    assert [r.path for r in result.converted] == [relpath]
    assert_lossless_netcdf(staged, output)


# --- blocker: skipped with a counted reason, never converted (KD6) -----

def test_blocker_record_skipped_never_converted(workdir):
    fixture = BLOCKER_FIXTURES[0]
    root, out_dir = workdir / "root", workdir / "out"
    relpath = fixture.name
    staged = _stage_at(fixture, root, relpath)
    # A blocker carries plan:null and an unactionable status — even if named.
    manifest = _write_manifest(
        workdir,
        [_record(root, relpath, staged, status="unsupported", plan=None)],
    )

    result = convert_manifest(
        manifest, ConvertOptions(out_dir=str(out_dir), statuses={"unsupported"})
    )

    assert relpath in [r.path for r in result.skipped]
    assert all(r.path != relpath for r in result.converted)
    assert [r.reason for r in result.skipped if r.path == relpath][0]  # a reason
    assert not _expected_output(out_dir, relpath, "HDF5").exists()     # no output


# --- non-destruction: sources are never mutated ------------------------

def test_sources_are_left_untouched(workdir):
    fixture = NETCDF_FIXTURES[0]
    root, out_dir = workdir / "root", workdir / "out"
    relpath = fixture.name
    staged = _stage_at(fixture, root, relpath)
    before_hash = sha256_of_file(str(staged))
    before_mtime = staged.stat().st_mtime

    manifest = _write_manifest(
        workdir,
        [_record(root, relpath, staged, status="ready", plan={"operation": "recompress"})],
    )
    convert_manifest(manifest, ConvertOptions(out_dir=str(out_dir)))

    assert sha256_of_file(str(staged)) == before_hash
    assert staged.stat().st_mtime == before_mtime
