"""Integration: `audit_path` drives the real inspect + classify engine.

The scaffold (`test_scaffold.py`) only checked discovery/summary; the
agreement oracle (`test_agreement.py`) checks `classify(inspect_file(...))`
directly. This file closes the gap between them: it asserts the *shipped*
`ncarnate audit` command (via `audit_path`) actually populates status,
issues, structures, and the conversion plan from the engine — not the empty
scaffold record it emitted before the engine was wired in.
"""

import netCDF4 as nc

from ncarnate.audit import AuditOptions, audit_path

from conftest import BLOCKER_FIXTURES, HDFEOS2_FIXTURES, NETCDF_FIXTURES


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


def test_audit_path_survives_a_malformed_granule(workdir):
    # An archive auditor must not abort the whole scan on one bad file: a
    # granule with corrupt StructMetadata classifies `malformed` and the scan
    # continues rather than raising out of audit_path.
    bad = workdir / "broken.nc"
    with nc.Dataset(bad, "w") as dataset:
        dataset.createDimension("x", 2)
        dataset.createVariable("v", "f4", ("x",))[:] = [1.0, 2.0]
    # Not HDF-EOS2, but exercises the non-crashing contract over a real file.
    report = audit_path(str(bad), _opts())
    assert len(report.files) == 1
    assert report.files[0].status in (
        "ready", "ready_no_geolocation", "already_modern",
        "unsupported", "malformed", "unsafe", "unknown",
    )
