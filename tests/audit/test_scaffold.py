"""Increment-1 scaffold: discovery, format detection, per-status summary.

``audit_path`` walks a tree (reusing ``cli._get_files`` enumeration +
magic-byte ``detect_format``) and returns an ``AuditReport``. At scaffold
depth the only statuses emitted are ``already_modern`` / ``unknown`` /
``unsafe`` (design §Rollout.1); the full taxonomy arrives in increment 2.
The terminal summary reports readiness by files *and* bytes (§CLI
integration).

RED until the paired [impl] scaffold unit lands ``audit_path`` + the
``report.py`` summary. ``ncarnate.audit`` exposes ``audit_path`` /
``AuditOptions`` per §Python API.
"""

from pathlib import Path

from ncarnate.audit import AuditOptions, audit_path

from conftest import FIXTURE_ROOT, HDFEOS2_FIXTURES, NETCDF_FIXTURES

# The only statuses the scaffold classifier emits at this depth.
SCAFFOLD_STATUSES = {"already_modern", "unknown", "unsafe"}


def _metadata_opts(recursive):
    return AuditOptions(recursive=recursive, mode="metadata", checksum=None)


# --- discovery + classification over the committed fixture tree -------

def test_fixture_tree_discovery_and_statuses():
    report = audit_path(str(FIXTURE_ROOT), _metadata_opts(recursive=True))
    by_name = {Path(f.path).name: f for f in report.files}

    # All 9 committed fixtures (5 HDF-EOS2 + 4 netCDF) are discovered.
    for fixture in list(NETCDF_FIXTURES) + list(HDFEOS2_FIXTURES):
        assert fixture.name in by_name, f"{fixture.name} not discovered"

    # The modern netCDF/HDF5 fixtures classify already_modern.
    for fixture in NETCDF_FIXTURES:
        assert by_name[fixture.name].status == "already_modern"

    # Legacy HDF4/HDF-EOS2 is never "already modern"; at scaffold depth its
    # readiness is one of the three scaffold statuses (the full taxonomy,
    # which distinguishes ready/unsupported/malformed, is increment 2).
    for fixture in HDFEOS2_FIXTURES:
        assert by_name[fixture.name].status != "already_modern"

    # The scaffold emits only its three statuses.
    assert {f.status for f in report.files} <= SCAFFOLD_STATUSES


def test_records_carry_root_and_relative_path():
    report = audit_path(str(FIXTURE_ROOT), _metadata_opts(recursive=True))
    assert report.files, "expected a non-empty report"
    for f in report.files:
        # `path` is relative to `root`, and rejoining recovers a real file
        # (schema: absolute `root` + relative `path`).
        assert Path(f.root).is_absolute()
        assert not Path(f.path).is_absolute()
        assert (Path(f.root) / f.path).is_file()


# --- unrecognized inputs are counted as `unknown`, never skipped ------

def test_unknown_magic_bytes_classified_unknown(workdir):
    # A supported extension (.nc) but garbage content -> detect UNKNOWN.
    garbage = workdir / "garbage.nc"
    garbage.write_bytes(b"not a science file at all" * 10)
    report = audit_path(str(garbage), _metadata_opts(recursive=False))
    assert len(report.files) == 1
    assert report.files[0].status == "unknown"


def test_nonscience_file_explicitly_targeted_is_unknown(workdir):
    # Unsupported extension: the legacy _get_files raises; audit must
    # instead count it and classify unknown, not skip it (§CLI integration).
    notes = workdir / "notes.txt"
    notes.write_text("hello")
    report = audit_path(str(notes), _metadata_opts(recursive=False))
    assert len(report.files) == 1
    assert report.files[0].status == "unknown"


# --- terminal summary: readiness by files AND bytes -------------------

def test_summary_reports_by_files_and_bytes():
    report = audit_path(str(FIXTURE_ROOT), _metadata_opts(recursive=True))
    summary = report.summary

    assert summary.total_files == len(report.files)
    assert summary.total_bytes == sum(f.size_bytes for f in report.files)

    # Both a per-status file census and a per-status byte census exist, and
    # each partitions its total (this is the "by files *and* bytes" shape).
    assert sum(summary.files_by_status.values()) == summary.total_files
    assert sum(summary.bytes_by_status.values()) == summary.total_bytes

    # The modern fixtures appear in both censuses.
    assert summary.files_by_status["already_modern"] == len(NETCDF_FIXTURES)
    modern_bytes = sum(f.stat().st_size for f in NETCDF_FIXTURES)
    assert summary.bytes_by_status["already_modern"] == modern_bytes
