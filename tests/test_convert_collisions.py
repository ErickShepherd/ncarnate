"""Failing-test matrix for the whole-manifest destination-collision preflight
(IMPLEMENTATION_PLAN step 1 / priority-queue step 1.1 / readiness action 1;
KD-L1/KD-L2; gate G1).

`convert_manifest` must compute every selected record's normalized
destination — from the source's *detected bytes*, never its declared
``record.format`` — before any directory or output file is created, and
refuse the **entire selected run** on any collision: no last-writer-wins, no
auto-rename, no partial proceed. The refusal is a stable
``DESTINATION_COLLISION`` code (an `NcarnateError` raise, the
`ContainmentError` whole-run-refusal precedent) listing every involved
source and the contested destination.

The preflight does not exist yet; these fail until the paired [impl] unit
lands it (the pattern test_convert_manifest.py used for `convert_manifest`
itself).
"""

import json
import os
import shutil

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

# The stable whole-run refusal code (KD-L2). The [impl] unit must register
# this string in `ncarnate.audit.codes` (and ALL_CODES); the tests assert
# the string itself because the operator-facing contract is the string.
COLLISION_CODE = "DESTINATION_COLLISION"


def _stage_at(fixture, root, relpath):
    """Copy a committed fixture to root/relpath (nested), returning its Path."""
    dst = root / relpath
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fixture, dst)
    return dst


def _record(root, relpath, staged, *, status="ready", plan, format=None):
    """A faithful record for a staged file with its real format + sha256.

    ``format`` overrides the detected format string — the false-declaration
    cases feed a lie here while the staged bytes (and sha256) stay real.
    """
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
        "format": format or detect_format(str(staged)).name,
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


def _hdf_record(root, relpath, staged, **overrides):
    return _record(root, relpath, staged,
                   plan={"operation": "convert"}, **overrides)


def _nc_record(root, relpath, staged, **overrides):
    return _record(root, relpath, staged,
                   plan={"operation": "recompress"}, **overrides)


def _expect_refusal(manifest, options, *, involved):
    """The run refuses with the stable collision code, naming ``involved``.

    KD-L2: the refusal lists all involved sources and the destination —
    every string in ``involved`` must appear in the message.
    """
    with pytest.raises(NcarnateError) as excinfo:
        convert_manifest(manifest, options)
    error = excinfo.value
    assert getattr(error, "code", None) == COLLISION_CODE
    message = str(error)
    for fragment in involved:
        assert fragment in message
    return error


def _assert_out_dir_untouched(out_dir):
    """Gate G1: refusal happens before any directory or output is created."""
    assert not out_dir.exists()


# --- .hdf/.nc sibling pair: the name-swap collides with a real sibling --

def test_hdf_nc_sibling_pair_refused(workdir):
    root, out_dir = workdir / "root", workdir / "out"
    hdf = _stage_at(HDFEOS2_FIXTURES[0], root, "sub/a.hdf")
    nc = _stage_at(NETCDF_FIXTURES[0], root, "sub/a.nc")
    manifest = _write_manifest(workdir, [
        _hdf_record(root, "sub/a.hdf", hdf),   # converts to sub/a.nc ...
        _nc_record(root, "sub/a.nc", nc),      # ... which this one mirrors
    ])

    _expect_refusal(
        manifest,
        ConvertOptions(out_dir=str(out_dir), allow_manifest_root=True),
        involved=["sub/a.hdf", "sub/a.nc"],
    )
    _assert_out_dir_untouched(out_dir)


# --- .hdf/.he5 variants: detected bytes drive the swap, so a misnamed ---
# --- .he5 holding HDF4 bytes lands on the same .nc destination        ---

def test_hdf_he5_variant_pair_refused(workdir):
    root, out_dir = workdir / "root", workdir / "out"
    hdf = _stage_at(HDFEOS2_FIXTURES[0], root, "g.hdf")
    he5 = _stage_at(HDFEOS2_FIXTURES[0], root, "g.he5")  # HDF4 bytes, .he5 name
    manifest = _write_manifest(workdir, [
        _hdf_record(root, "g.hdf", hdf),
        _hdf_record(root, "g.he5", he5),       # detected HDF4 -> g.nc too
    ])

    _expect_refusal(
        manifest,
        ConvertOptions(out_dir=str(out_dir), allow_manifest_root=True),
        involved=["g.hdf", "g.he5", "g.nc"],
    )
    _assert_out_dir_untouched(out_dir)


# --- duplicate actionable records: one source listed twice -------------

def test_duplicate_manifest_records_refused(workdir):
    root, out_dir = workdir / "root", workdir / "out"
    staged = _stage_at(NETCDF_FIXTURES[0], root, "dup.nc")
    record = _nc_record(root, "dup.nc", staged)
    manifest = _write_manifest(workdir, [record, record])

    _expect_refusal(
        manifest,
        ConvertOptions(out_dir=str(out_dir), allow_manifest_root=True),
        involved=["dup.nc"],
    )
    _assert_out_dir_untouched(out_dir)


# --- false declared format: the destination follows detected bytes -----

def test_false_declaration_output_follows_detected_bytes(workdir):
    # A record lying `format: HDF5` over real HDF4 bytes must still land at
    # the detected-bytes destination (x.nc), never keep the .hdf name the
    # declared format implies (readiness action 1 step 2).
    root, out_dir = workdir / "root", workdir / "out"
    staged = _stage_at(HDFEOS2_FIXTURES[0], root, "x.hdf")
    manifest = _write_manifest(workdir, [
        _hdf_record(root, "x.hdf", staged, format="HDF5"),  # a lie
    ])

    result = convert_manifest(
        manifest, ConvertOptions(out_dir=str(out_dir), allow_manifest_root=True)
    )

    assert [r.path for r in result.converted] == ["x.hdf"]
    assert (out_dir / "x.nc").is_file()          # detected bytes won
    assert not (out_dir / "x.hdf").exists()      # declared format did not


def test_false_declaration_cannot_mask_a_collision(workdir):
    # Trusting the declared format would compute x.hdf + x.nc (no collision)
    # and proceed to overwrite; detecting from bytes computes x.nc + x.nc
    # and must refuse.
    root, out_dir = workdir / "root", workdir / "out"
    hdf = _stage_at(HDFEOS2_FIXTURES[0], root, "x.hdf")
    nc = _stage_at(NETCDF_FIXTURES[0], root, "x.nc")
    manifest = _write_manifest(workdir, [
        _hdf_record(root, "x.hdf", hdf, format="HDF5"),  # a lie
        _nc_record(root, "x.nc", nc),
    ])

    _expect_refusal(
        manifest,
        ConvertOptions(out_dir=str(out_dir), allow_manifest_root=True),
        involved=["x.hdf", "x.nc"],
    )
    _assert_out_dir_untouched(out_dir)


# --- case-fold-equivalent names: distinct on ext4, one file on NTFS ----

def test_case_fold_equivalent_destinations_refused(workdir):
    root, out_dir = workdir / "root", workdir / "out"
    upper = _stage_at(HDFEOS2_FIXTURES[0], root, "Granule.hdf")
    lower = _stage_at(HDFEOS2_FIXTURES[0], root, "granule.hdf")
    manifest = _write_manifest(workdir, [
        _hdf_record(root, "Granule.hdf", upper),   # -> Granule.nc
        _hdf_record(root, "granule.hdf", lower),   # -> granule.nc: casefold-equal
    ])

    _expect_refusal(
        manifest,
        ConvertOptions(out_dir=str(out_dir), allow_manifest_root=True),
        involved=["Granule.hdf", "granule.hdf"],
    )
    _assert_out_dir_untouched(out_dir)


# --- source-tree/output-tree overlap ------------------------------------

def test_out_dir_inside_source_root_refused(workdir):
    root = workdir / "root"
    out_dir = root / "converted"                   # output nested in sources
    staged = _stage_at(HDFEOS2_FIXTURES[0], root, "a.hdf")
    manifest = _write_manifest(workdir, [_hdf_record(root, "a.hdf", staged)])

    _expect_refusal(
        manifest,
        ConvertOptions(out_dir=str(out_dir), allow_manifest_root=True),
        involved=["a.hdf"],
    )
    _assert_out_dir_untouched(out_dir)


def test_destination_aliasing_a_selected_source_refused(workdir):
    # out_dir == root makes the netCDF record's mirrored destination the
    # source file itself — the exact data-loss shape KD-L1 forbids.
    root = workdir / "root"
    staged = _stage_at(NETCDF_FIXTURES[0], root, "a.nc")
    before = sha256_of_file(str(staged))
    manifest = _write_manifest(workdir, [_nc_record(root, "a.nc", staged)])

    _expect_refusal(
        manifest,
        ConvertOptions(out_dir=str(root), allow_manifest_root=True),
        involved=["a.nc"],
    )
    assert sha256_of_file(str(staged)) == before   # source never touched


def test_symlinked_out_dir_aliasing_source_tree_refused(workdir):
    # The overlap check must hold after symlink resolution: an out_dir that
    # is a link back into the source tree aliases the sources.
    root = workdir / "root"
    staged = _stage_at(NETCDF_FIXTURES[0], root, "a.nc")
    before = sha256_of_file(str(staged))
    out_link = workdir / "out_link"
    os.symlink(root, out_link)
    manifest = _write_manifest(workdir, [_nc_record(root, "a.nc", staged)])

    _expect_refusal(
        manifest,
        ConvertOptions(out_dir=str(out_link), allow_manifest_root=True),
        involved=["a.nc"],
    )
    assert sha256_of_file(str(staged)) == before   # source never touched
