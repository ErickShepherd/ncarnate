"""Agreement test — the audit -> convert credibility oracle (design
§Rollout.3, KD4).

`ncarnate audit --output m.jsonl --checksum sha256` over a real fixture tree,
then `ncarnate convert --manifest m.jsonl` must AGREE: convert executes exactly
the `ready` set the audit predicted, every output is faithful, and a blocker
the audit refused is never silently converted. This keeps the taxonomy honest —
any real-world mismatch becomes a new fixture and a public issue (the same role
`tests/audit/test_agreement.py` plays on the audit side).

The manifest is produced by the real audit CLI (a subprocess), so the ready set
is DERIVED from the audit's own classification, never hardcoded. Convert is
driven through the `convert_manifest` engine for an introspectable
`ConvertResult`; the CLI->engine seam itself is proven separately by
`tests/convert/test_cli_integration.py`.

CREDIBILITY ORACLE — authored by the loop but deliberately NOT run in-loop:
running it green in the unit that authored it would self-bless the very
audit<->convert agreement it exists to check (circular). There is no `verify:`;
an out-of-loop reviewer discharges it with
`python -m pytest tests/convert/test_agreement.py -q`.
"""

import os
import shutil
import subprocess
import sys

import netCDF4 as nc

from conftest import (
    BLOCKER_FIXTURES,
    HDFEOS2_FIXTURES,
    NETCDF_FIXTURES,
    assert_lossless_netcdf,
)

from ncarnate.convert import ConvertOptions, convert_manifest
from ncarnate.convert.reader import read_manifest


def _stage_tree(root):
    """Stage the convertible and blocker fixtures into a mirrored tree; return
    {relpath: source_fixture}."""
    staged = {}
    for sub, fixtures in (
        ("netcdf", NETCDF_FIXTURES),
        ("hdfeos2", HDFEOS2_FIXTURES),
        ("blockers", BLOCKER_FIXTURES),
    ):
        for fixture in fixtures:
            relpath = f"{sub}/{fixture.name}"
            dst = root / relpath
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(fixture, dst)
            staged[relpath] = fixture
    return staged


def _audit_to_manifest(root, workdir):
    """Run the REAL audit CLI to produce a sha256 manifest over the tree."""
    manifest = workdir / "m.jsonl"
    completed = subprocess.run(
        [sys.executable, "-m", "ncarnate", "audit", str(root),
         "--checksum", "sha256", "--output", str(manifest)],
        capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return str(manifest)


def _mirror_path(out_dir, record):
    """The output path convert computes for a record (HDF4 -> .nc swap)."""
    relpath = record.path
    if record.format == "HDF4":
        relpath = os.path.splitext(relpath)[0] + ".nc"
    return out_dir / relpath


# --- convert executes EXACTLY the ready set the audit predicted --------

def test_convert_executes_exactly_the_audited_ready_set(workdir):
    root, out_dir = workdir / "root", workdir / "out"
    _stage_tree(root)
    manifest = _audit_to_manifest(root, workdir)

    records = read_manifest(manifest)
    ready = {r.path for r in records if r.status == "ready"}
    assert ready, "audit produced no ready records — fixture tree is wrong"

    result = convert_manifest(manifest, ConvertOptions(out_dir=str(out_dir), allow_manifest_root=True))

    # Agreement: convert's converted set == the audit's ready set, and no
    # ready prediction failed (ready ⇒ recompress honors it).
    assert {r.path for r in result.converted} == ready
    assert not result.failed, [r.reason for r in result.failed]

    by_path = {r.path: r for r in records}
    for relpath, record in by_path.items():
        output = _mirror_path(out_dir, record)
        if relpath in ready:
            # Each ready (HDF4) output is a valid, non-empty netCDF; deep
            # HDF4->nc fidelity is proven by test_hdf4_conversion.
            assert output.is_file()
            with nc.Dataset(output) as dataset:
                assert len(dataset.variables) or len(dataset.groups)
        else:
            # Nothing outside the ready set was converted (KD8 default; a
            # blocker is never actionable, KD6) — no silent extra output.
            assert not output.exists()


# --- recompressed netCDF outputs verify lossless (assert_lossless_netcdf) --

def test_recompressed_modern_outputs_are_lossless(workdir):
    root, out_dir = workdir / "root", workdir / "out"
    staged = _stage_tree(root)
    manifest = _audit_to_manifest(root, workdir)

    records = read_manifest(manifest)
    modern = {r.path for r in records if r.status == "already_modern"}
    assert modern, "audit produced no already_modern records"

    # Widen the selection so the netCDF sources produce same-format outputs
    # that assert_lossless_netcdf can compare bit-for-bit.
    result = convert_manifest(
        manifest, ConvertOptions(out_dir=str(out_dir), statuses={"already_modern"}, allow_manifest_root=True)
    )

    assert {r.path for r in result.converted} == modern
    assert not result.failed, [r.reason for r in result.failed]
    for relpath in modern:
        output = out_dir / relpath               # netCDF name kept
        assert_lossless_netcdf(staged[relpath], output)


# --- a blocker prediction is never converted (KD4 / KD6) ---------------

def test_blocker_prediction_never_silently_converts(workdir):
    root, out_dir = workdir / "root", workdir / "out"
    _stage_tree(root)
    manifest = _audit_to_manifest(root, workdir)

    records = read_manifest(manifest)
    blockers = {r.path for r in records if r.plan is None}
    assert blockers, "audit produced no blocker records"

    # Even naming the blocker's status, a blocker is never actionable (KD6):
    # it is skipped with a reason, never converted, and writes no output.
    statuses = {record.status for record in records if record.plan is None}
    result = convert_manifest(
        manifest, ConvertOptions(out_dir=str(out_dir), statuses=statuses, allow_manifest_root=True)
    )

    converted = {r.path for r in result.converted}
    assert converted.isdisjoint(blockers)
    skipped = {r.path for r in result.skipped}
    assert blockers <= skipped
    by_path = {r.path: r for r in records}
    for relpath in blockers:
        assert not _mirror_path(out_dir, by_path[relpath]).exists()
