"""End-to-end integration seam: `convert --manifest` through the REAL CLI
(design §Invocation shape, §Rollout.3).

This is the exact seam the increment-1 loop left unwired — a unit call on
`convert_manifest` passed while the CLI never actually invoked the engine. So
these tests deliberately do **not** call `convert_manifest`; they drive the
whole path through the CLI entry point (a `python -m ncarnate` subprocess for a
true *process* exit code, and an in-process `cli.main` via `sys.argv`) into a
temp `--out-dir`, asserting real outputs land and the exit code is correct.

CLI INTEGRATION SEAM — authored by the loop but deliberately NOT run in-loop:
running it green in the same unit that authored it would self-bless the very
seam increment-1 got wrong (a loop-authored test the loop passes is circular).
There is no `verify:`; an out-of-loop reviewer discharges it with
`python -m pytest tests/convert/test_cli_integration.py -q`, scrutinising that
it genuinely exercises the CLI->engine path.
"""

import json
import shutil
import subprocess
import sys

from conftest import NETCDF_FIXTURES, assert_lossless_netcdf

from ncarnate.audit.codes import RULESET_VERSION
from ncarnate.audit.models import SCHEMA_VERSION
from ncarnate.formats import detect_format
from ncarnate.hashing import sha256_of_file


def _stage_at(fixture, root, relpath):
    dst = root / relpath
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fixture, dst)
    return dst


def _record(root, relpath, staged, *, status, plan, fmt=None):
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
        "format": fmt or detect_format(str(staged)).name,
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


def _run_module(*argv):
    """Drive the installed CLI as a real child process (python -m ncarnate) so
    the assertion is on a genuine process exit code — the strongest possible
    check that the entry point actually invokes the engine."""
    return subprocess.run(
        [sys.executable, "-m", "ncarnate", *argv],
        capture_output=True, text=True,
    )


# --- success: real outputs land in --out-dir, process exit code 0 ------

def test_convert_manifest_subprocess_lands_outputs_and_exits_0(workdir):
    root, out_dir = workdir / "root", workdir / "out"
    relpath = "nested/dir/granule.nc"
    staged = _stage_at(NETCDF_FIXTURES[0], root, relpath)
    manifest = _write_manifest(
        workdir,
        [_record(root, relpath, staged, status="ready",
                 plan={"operation": "recompress"})],
    )

    completed = _run_module("convert", "--manifest", manifest,
                            "--out-dir", str(out_dir))

    assert completed.returncode == 0, completed.stderr
    output = out_dir / relpath                       # mirrored tree, name kept
    assert output.is_file()
    assert_lossless_netcdf(staged, output)
    assert "converted 1" in completed.stdout.lower()  # the run summary printed


# --- failure: a ready record recompress cannot honor -> non-zero exit ---

def test_convert_manifest_subprocess_failure_sets_nonzero_exit(workdir):
    root, out_dir = workdir / "root", workdir / "out"
    relpath = "bad.hdf"
    bad = root / relpath
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"not a real granule, cannot be converted" * 8)
    # A `ready` HDF4 claim over bytes recompress cannot process (faithful
    # sha256 so it clears the integrity gate and fails at conversion, KD4).
    manifest = _write_manifest(
        workdir,
        [_record(root, relpath, bad, status="ready",
                 plan={"operation": "convert"}, fmt="HDF4")],
    )

    completed = _run_module("convert", "--manifest", manifest,
                            "--out-dir", str(out_dir))

    assert completed.returncode != 0                 # the exit code plumbs out
    assert "failed 1" in completed.stdout.lower()


# --- in-process cli.main drives the same seam (belt and suspenders) -----

def test_convert_manifest_through_cli_main_in_process(monkeypatch, workdir):
    from ncarnate.cli import main

    root, out_dir = workdir / "root", workdir / "out"
    relpath = "granule.nc"
    staged = _stage_at(NETCDF_FIXTURES[0], root, relpath)
    manifest = _write_manifest(
        workdir,
        [_record(root, relpath, staged, status="ready",
                 plan={"operation": "recompress"})],
    )

    monkeypatch.setattr(sys, "argv", [
        "ncarnate", "convert", "--manifest", manifest, "--out-dir", str(out_dir),
    ])
    exit_code = main()                               # the real entry point

    assert exit_code == 0
    output = out_dir / relpath
    assert output.is_file()
    assert_lossless_netcdf(staged, output)
