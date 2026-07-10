"""Unit tests for the audit data models (ncarnate.audit.models).

Every model is a stdlib dataclass with a ``to_record() -> dict`` that is
JSON-safe. The per-file ``AuditResult`` record *is* the migration-manifest
contract (design §One record schema), so these tests freeze its key set and
the JSON round-trip. ``ncarnate.audit.models`` does not exist yet; these
fail until the paired [impl] unit lands it.
"""

import json

from ncarnate.audit.models import (
    AuditIssue,
    AuditOptions,
    AuditReport,
    AuditResult,
    ConversionPlan,
    StructureAudit,
)


def _roundtrips(record):
    """A dict is JSON-safe iff dumps-then-loads reproduces it exactly.

    This is what catches a raw enum or Path leaking into a record: it
    serialises but does not round-trip back to the same object.
    """
    assert isinstance(record, dict)
    return json.loads(json.dumps(record)) == record


# --- sample instances (spec-faithful values) --------------------------

def _sample_issue():
    return AuditIssue(
        code="EOS_UNSUPPORTED_PROJECTION",
        severity="blocker",
        message="GCTP projection 22 is not verified against a fixture.",
        context={"projection": 22},
    )


def _sample_structure():
    return StructureAudit(
        type="GRID",
        name="MOD_Grid_Snow_500m",
        projection={"gctp_code": 16, "name": "Sinusoidal"},
        geolocation_plan={"method": "grid_affine"},
    )


def _sample_plan():
    return ConversionPlan(
        operation="convert",
        geolocation_method="grid_affine",
        output_format="NETCDF4",
    )


def _sample_result():
    return AuditResult(
        root="/archive",
        path="granules/snow.hdf",
        size_bytes=63124218,
        sha256=None,
        format="HDF4",
        status="ready",
        mode="metadata",
        audited_at="2026-07-10T18:30:00Z",
        structures=[_sample_structure()],
        issues=[_sample_issue()],
        plan=_sample_plan(),
    )


# --- construction -----------------------------------------------------

def test_audit_options_constructs():
    opts = AuditOptions(recursive=True, mode="metadata", checksum=None)
    assert opts.recursive is True
    assert opts.mode == "metadata"
    assert opts.checksum is None


# --- child records ----------------------------------------------------

def test_issue_constructs_and_records():
    rec = _sample_issue().to_record()
    assert _roundtrips(rec)
    assert set(rec) == {"code", "severity", "message", "context"}


def test_structure_constructs_and_records():
    rec = _sample_structure().to_record()
    assert _roundtrips(rec)
    # HDF-specific richness (projection, geolocation_plan) is present for a
    # GRID; KD10 lets it be absent where it does not apply, so this is a
    # floor, not an equality.
    assert {"type", "name", "projection", "geolocation_plan"} <= set(rec)


def test_plan_constructs_and_records():
    rec = _sample_plan().to_record()
    assert _roundtrips(rec)
    assert set(rec) == {"operation", "geolocation_method", "output_format"}


# --- the manifest record contract (design §One record schema) ---------

MANIFEST_KEYS = {
    "schema_version", "ncarnate_version", "ruleset_version", "mode",
    "audited_at", "root", "path", "size_bytes", "sha256", "format",
    "status", "structures", "issues", "plan",
}


def test_result_record_has_exactly_the_v1_manifest_keys():
    rec = _sample_result().to_record()
    assert set(rec) == MANIFEST_KEYS


def test_result_record_is_json_safe():
    # The nested issues/structures/plan must serialise too, and `format`
    # must be a plain string, not a FileFormat enum.
    assert _roundtrips(_sample_result().to_record())


def test_result_record_versions_are_scalars():
    rec = _sample_result().to_record()
    assert rec["schema_version"] == 1
    assert isinstance(rec["ncarnate_version"], str)
    assert isinstance(rec["ruleset_version"], int)


def test_result_nests_child_records():
    rec = _sample_result().to_record()
    assert rec["issues"] == [_sample_issue().to_record()]
    assert rec["structures"] == [_sample_structure().to_record()]
    assert rec["plan"] == _sample_plan().to_record()


# --- report aggregate -------------------------------------------------

def test_report_constructs_and_holds_its_files():
    result = _sample_result()
    report = AuditReport(root="/archive", mode="metadata", files=[result])
    assert report.files == [result]


def test_report_record_is_json_safe():
    report = AuditReport(
        root="/archive", mode="metadata", files=[_sample_result()]
    )
    assert _roundtrips(report.to_record())
