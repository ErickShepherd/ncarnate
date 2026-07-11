"""Per-record isolation, end-of-run summary, and exit-code tests for
convert_manifest (design §The per-record loop, final paragraph).

Three properties of a partial-failure run:

- **Isolation** — one record the converter cannot process (a `ready` line whose
  bytes `recompress` genuinely refuses) becomes a counted `failed` outcome and
  does *not* abort the run; every other selected record still converts.
- **Summary** — `ncarnate.convert.report.render_summary(result)` renders the
  converted / skipped / failed tallies with each failure's reason (audit-
  symmetric with `ncarnate.audit.report.render_summary`, KD7).
- **Exit code** — `result.exit_code` is non-zero **iff any selected record
  failed**; a skip (a blocker or a non-selected status) never sets it.

`render_summary` and `ConvertResult.exit_code` do not exist yet; the summary
and exit-code tests fail until the paired [impl] unit lands them. The isolation
test is a live positive control (per-record isolation already holds). Authored
ATDD — do not implement here.
"""

import json
import shutil

from conftest import NETCDF_FIXTURES, BLOCKER_FIXTURES

from ncarnate.audit.codes import RULESET_VERSION
from ncarnate.audit.models import SCHEMA_VERSION
from ncarnate.formats import detect_format
from ncarnate.hashing import sha256_of_file

from ncarnate.convert import ConvertOptions, convert_manifest


def _stage_at(fixture, root, relpath):
    dst = root / relpath
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fixture, dst)
    return dst


def _record(root, relpath, staged, *, status, plan, fmt=None):
    """A record for a staged file; `fmt` overrides the detected format (used to
    forge a `ready` HDF4 claim over bytes recompress cannot actually convert)."""
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


def _stage_unprocessable(root, relpath):
    """Stage a file whose bytes recompress cannot process, recorded as a
    `ready` HDF4 line — the audit-said-ready / recompress-can't disagreement
    (§step 4): recompress raises and it is reported as a failure, not
    mis-converted. Its sha256 is faithful, so it clears the integrity gate and
    fails only at conversion."""
    dst = root / relpath
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(b"not a real granule, cannot be converted" * 8)
    return dst


# --- isolation: one bad record does not abort the run (positive control) --

def test_one_failing_record_does_not_abort_run(workdir):
    root, out_dir = workdir / "root", workdir / "out"
    good_rel, bad_rel = "good.nc", "bad.hdf"
    good = _stage_at(NETCDF_FIXTURES[0], root, good_rel)
    bad = _stage_unprocessable(root, bad_rel)

    manifest = _write_manifest(workdir, [
        _record(root, bad_rel, bad, status="ready",
                plan={"operation": "convert"}, fmt="HDF4"),   # fails first
        _record(root, good_rel, good, status="ready",
                plan={"operation": "recompress"}),            # must still run
    ])

    result = convert_manifest(manifest, ConvertOptions(out_dir=str(out_dir)))

    # The bad record failed with a reason; the good one converted regardless
    # of ordering — the run was not aborted by the earlier failure.
    assert good_rel in [r.path for r in result.converted]
    assert bad_rel in [r.path for r in result.failed]
    assert [r.reason for r in result.failed if r.path == bad_rel][0]  # a reason
    assert (out_dir / good_rel).is_file()


# --- exit code: non-zero iff a *selected* record failed -------------------

def test_exit_code_nonzero_iff_selected_record_failed(workdir):
    root, out_dir = workdir / "root", workdir / "out"
    good = _stage_at(NETCDF_FIXTURES[0], root, "good.nc")
    bad = _stage_unprocessable(root, "bad.hdf")

    # (a) a failing selected record -> non-zero.
    failing = _write_manifest(workdir, [
        _record(root, "bad.hdf", bad, status="ready",
                plan={"operation": "convert"}, fmt="HDF4"),
    ])
    result = convert_manifest(failing, ConvertOptions(out_dir=str(out_dir)))
    assert result.failed and result.exit_code != 0

    # (b) all selected records converted -> zero.
    clean = _write_manifest(workdir, [
        _record(root, "good.nc", good, status="ready",
                plan={"operation": "recompress"}),
    ])
    result = convert_manifest(clean, ConvertOptions(out_dir=str(workdir / "o2")))
    assert not result.failed and result.exit_code == 0

    # (c) a run whose only non-conversions are *skips* (a blocker) -> zero;
    #     a skip is not a failure, so it never sets the exit code.
    blocker = _stage_at(BLOCKER_FIXTURES[0], root, "blk.hdf")
    skips = _write_manifest(workdir, [
        _record(root, "good.nc", good, status="ready",
                plan={"operation": "recompress"}),
        _record(root, "blk.hdf", blocker, status="unsupported", plan=None),
    ])
    result = convert_manifest(
        skips,
        ConvertOptions(out_dir=str(workdir / "o3"), statuses={"ready", "unsupported"}),
    )
    assert result.skipped and not result.failed and result.exit_code == 0


# --- summary: converted / skipped / failed counts, with reasons -----------

def test_summary_reports_all_three_tallies_with_reasons(workdir):
    from ncarnate.convert.report import render_summary  # lazy: red until [impl]

    root, out_dir = workdir / "root", workdir / "out"
    good = _stage_at(NETCDF_FIXTURES[0], root, "good.nc")
    bad = _stage_unprocessable(root, "bad.hdf")
    blocker = _stage_at(BLOCKER_FIXTURES[0], root, "blk.hdf")

    manifest = _write_manifest(workdir, [
        _record(root, "good.nc", good, status="ready",
                plan={"operation": "recompress"}),                 # converted
        _record(root, "blk.hdf", blocker, status="unsupported", plan=None),  # skipped
        _record(root, "bad.hdf", bad, status="ready",
                plan={"operation": "convert"}, fmt="HDF4"),        # failed
    ])

    result = convert_manifest(
        manifest,
        ConvertOptions(out_dir=str(out_dir), statuses={"ready", "unsupported"}),
    )
    assert len(result.converted) == 1
    assert len(result.skipped) == 1
    assert len(result.failed) == 1

    summary = render_summary(result)
    assert isinstance(summary, str)

    lowered = summary.lower()
    # Each tally category is named.
    assert "converted" in lowered
    assert "skipped" in lowered
    assert "failed" in lowered
    # The failed record surfaces with its reason (not just a bare count).
    assert "bad.hdf" in summary
    assert result.failed[0].reason in summary
