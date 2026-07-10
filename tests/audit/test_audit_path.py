"""Integration: `audit_path` drives the real inspect + classify engine.

The scaffold (`test_scaffold.py`) only checked discovery/summary; the
agreement oracle (`test_agreement.py`) checks `classify(inspect_file(...))`
directly. This file closes the gap between them: it asserts the *shipped*
`ncarnate audit` command (via `audit_path`) actually populates status,
issues, structures, and the conversion plan from the engine — not the empty
scaffold record it emitted before the engine was wired in.
"""

import os

import netCDF4 as nc
import pytest

from ncarnate.audit import AuditOptions, audit_path
from ncarnate.audit import main as audit_main

from conftest import BLOCKER_FIXTURES, HDFEOS2_FIXTURES, NETCDF_FIXTURES

# chmod-based permission tests are meaningless as root (root bypasses them).
_skip_if_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses directory permissions",
)


def _opts():
    return AuditOptions(recursive=True, mode="metadata", checksum=None)


def test_audit_path_populates_hdfeos2_structures_and_plan():
    # A real HDF-EOS2 granule, audited through the public command, must carry
    # a convertible status, at least one structure, and a conversion plan —
    # proving audit_path reaches inspect_file + classify, not the scaffold.
    fixture = HDFEOS2_FIXTURES[0]
    report = audit_path(str(fixture), _opts())

    assert len(report.files) == 1
    result = report.files[0]

    assert result.status in ("ready", "ready_no_geolocation")
    assert result.structures, "expected HDF-EOS2 structures to be populated"
    assert result.plan is not None
    assert result.plan.operation == "convert"


def test_audit_path_populates_blocker_issue_through_command():
    # A user-defined-type file must surface the blocker issue + code through
    # audit_path, with no conversion plan (nothing safe to do).
    fixture = BLOCKER_FIXTURES[0]
    report = audit_path(str(fixture), _opts())

    result = report.files[0]
    assert result.status == "unsupported"
    assert any(
        issue.code == "UNSUPPORTED_TYPE" and issue.severity == "blocker"
        for issue in result.issues
    )
    assert result.plan is None


def test_audit_path_modern_netcdf_plans_recompress():
    fixture = NETCDF_FIXTURES[0]
    report = audit_path(str(fixture), _opts())

    result = report.files[0]
    assert result.status == "already_modern"
    assert result.plan is not None
    assert result.plan.operation == "recompress"


def test_audit_path_survives_a_corrupt_container(workdir):
    # An archive auditor must NOT abort the whole scan on one unreadable file.
    # A file whose HDF5 magic bytes match but whose body is garbage makes
    # netCDF4.Dataset raise OSError; the audit must record it `malformed`
    # (MALFORMED_CONTAINER) and STILL classify a healthy sibling in the same
    # scan — proving the exception did not abort audit_path.
    corrupt = workdir / "corrupt.nc"
    corrupt.write_bytes(b"\x89HDF\r\n\x1a\n" + b"not a real superblock" * 8)

    good = workdir / "good.nc"
    with nc.Dataset(good, "w") as dataset:
        dataset.createDimension("x", 2)
        dataset.createVariable("v", "f4", ("x",))[:] = [1.0, 2.0]

    report = audit_path(str(workdir), _opts())
    by_name = {result.path.rsplit("/", 1)[-1]: result for result in report.files}

    assert by_name["corrupt.nc"].status == "malformed"
    assert any(
        issue.code == "MALFORMED_CONTAINER"
        for issue in by_name["corrupt.nc"].issues
    )
    # The healthy sibling was still reached and classified — no scan abort.
    assert by_name["good.nc"].status == "already_modern"


def test_audit_path_survives_an_unreadable_file(workdir):
    # A filesystem-level failure *before* inspection — here a dangling symlink
    # (also permission denied, a file removed mid-scan) — must not abort the
    # scan either: `detect_format`/`getsize`/`_sha256` all do I/O and sit
    # inside the per-file guard. The bad entry is `malformed`; a healthy
    # sibling in the same scan is still classified.
    (workdir / "dangling.nc").symlink_to(workdir / "does_not_exist.nc")

    good = workdir / "good.nc"
    with nc.Dataset(good, "w") as dataset:
        dataset.createDimension("x", 2)
        dataset.createVariable("v", "f4", ("x",))[:] = [1.0, 2.0]

    report = audit_path(str(workdir), _opts())
    by_name = {result.path.rsplit("/", 1)[-1]: result for result in report.files}

    assert by_name["dangling.nc"].status == "malformed"
    assert any(
        issue.code == "MALFORMED_CONTAINER"
        for issue in by_name["dangling.nc"].issues
    )
    assert by_name["good.nc"].status == "already_modern"


@_skip_if_root
def test_audit_cli_survives_unreadable_directory(workdir):
    # Discovery I/O (os.listdir in --no-recursive) can fail at the root before
    # any per-file guard is reached. The CLI must degrade to a clean error
    # exit (2), never propagate an OSError as a traceback.
    denied = workdir / "denied"
    denied.mkdir()
    (denied / "granule.nc").write_bytes(b"\x89HDF\r\n\x1a\ndata")
    denied.chmod(0o000)
    try:
        rc = audit_main([str(denied), "--no-recursive"])
    finally:
        denied.chmod(0o755)   # restore so tmp cleanup can remove it
    assert rc == 2


@_skip_if_root
def test_recursive_scan_warns_on_unreadable_subtree(workdir, caplog):
    # A permission-denied subdirectory in a recursive scan is skipped, but the
    # omission must be surfaced (an auditor silently dropping a subtree gives
    # false confidence), and healthy siblings elsewhere are still classified.
    good = workdir / "good.nc"
    with nc.Dataset(good, "w") as dataset:
        dataset.createDimension("x", 2)
        dataset.createVariable("v", "f4", ("x",))[:] = [1.0, 2.0]

    denied = workdir / "denied"
    denied.mkdir()
    (denied / "buried.nc").write_bytes(b"\x89HDF\r\n\x1a\ndata")
    denied.chmod(0o000)
    try:
        import logging
        with caplog.at_level(logging.WARNING, logger="ncarnate"):
            report = audit_path(str(workdir), _opts())
    finally:
        denied.chmod(0o755)

    names = {result.path.rsplit("/", 1)[-1] for result in report.files}
    assert "good.nc" in names        # healthy sibling still scanned
    assert "buried.nc" not in names  # unreadable subtree omitted...
    assert any("unreadable directory" in record.message.lower()
               for record in caplog.records)   # ...but surfaced, not silent
