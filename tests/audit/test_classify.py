"""Classification: facts -> status + issues (design §Classification).

classify calls the converter's own predicates and catches, reads ``exc.code``
(with a type-level fallback), maps the two return-sentinel predicates
explicitly, and folds severity (any blocker ⇒ a non-ready status). One source
of truth — no re-implemented rules.

This [test] item fixes the classify contract before the paired [impl] lands.
Tested surface (duck-typed / reversible-internal, recorded in LOOP_LEARNINGS):
  issue_for_exception(exc)        -> AuditIssue   (exc.code, else type default)
  status_for(facts, issues)       -> str          (pure: folding + code->status)
  classify(facts)                 -> (status, list[AuditIssue])

The code->status mapping derives from the spec: geolocation blockers leave the
SDS payload convertible (--no-geolocation), so they fold to ready_no_geolocation;
a bad SDS type -> unsupported; malformed StructMetadata -> malformed; an
allocation bomb -> unsafe; an unrecognized format -> unknown. The agreement
tests (next unit) are the end-to-end oracle over real fixtures.
"""

from ncarnate.audit import codes
from ncarnate.audit.classify import classify, issue_for_exception, status_for
from ncarnate.audit.inspect import FileFacts, inspect_file
from ncarnate.audit.models import AuditIssue
from ncarnate.errors import (
    EosParseError,
    NcarnateError,
    UnsupportedGeolocationError,
    UnsupportedProjectionError,
    UnsupportedTypeError,
)

from conftest import HDFEOS2_FIXTURES, NETCDF_FIXTURES

TAXONOMY = {
    "ready", "ready_no_geolocation", "already_modern",
    "unsupported", "malformed", "unsafe", "unknown",
}


def _hdf4_facts():
    return FileFacts(format="HDF4", already_modern=False)


def _modern_facts():
    return FileFacts(format="HDF5", already_modern=True)


def _unknown_facts():
    return FileFacts(format="UNKNOWN", already_modern=False)


def _blocker(code):
    return AuditIssue(code=code, severity="blocker", message="m", context={})


# --- exc.code -> issue, with type-level fallback -----------------------

def test_issue_uses_explicit_exc_code():
    exc = UnsupportedGeolocationError("m", code=codes.NETCDF_NAME_COLLISION)
    issue = issue_for_exception(exc)
    assert issue.code == codes.NETCDF_NAME_COLLISION   # code wins over type
    assert issue.severity == "blocker"


def test_issue_falls_back_to_type_default_code():
    # An exception raised at an un-annotated site (code is None) maps by type.
    assert issue_for_exception(EosParseError("m")).code == \
        codes.EOS_STRUCTMETADATA_MALFORMED
    assert issue_for_exception(UnsupportedProjectionError("m")).code == \
        codes.EOS_UNSUPPORTED_PROJECTION
    assert issue_for_exception(UnsupportedTypeError("m")).code == \
        codes.UNSUPPORTED_TYPE
    # The ambiguous type defaults to the geolocation code; _reserve_names
    # overrides it to NETCDF_NAME_COLLISION via exc.code (test above).
    assert issue_for_exception(UnsupportedGeolocationError("m")).code == \
        codes.SWATH_GEOLOCATION_UNSUPPORTED


# --- status_for: the full taxonomy (pure decision) --------------------

def test_status_ready_when_no_issues():
    assert status_for(_hdf4_facts(), []) == "ready"


def test_status_already_modern_for_modern_facts():
    assert status_for(_modern_facts(), []) == "already_modern"


def test_status_unknown_for_unrecognized_format():
    facts, issues = _unknown_facts(), [_blocker(codes.FORMAT_UNRECOGNIZED)]
    assert status_for(facts, issues) == "unknown"


def test_status_malformed_for_structmetadata_blocker():
    assert status_for(
        _hdf4_facts(), [_blocker(codes.EOS_STRUCTMETADATA_MALFORMED)]
    ) == "malformed"


def test_status_unsafe_for_allocation_blocker():
    assert status_for(
        _hdf4_facts(), [_blocker(codes.DECLARED_ALLOCATION_TOO_LARGE)]
    ) == "unsafe"


def test_status_unsupported_for_bad_sds_type():
    assert status_for(
        _hdf4_facts(), [_blocker(codes.UNSUPPORTED_TYPE)]
    ) == "unsupported"


def test_geolocation_blockers_fold_to_ready_no_geolocation():
    # The SDS payload still converts with --no-geolocation.
    for code in (
        codes.EOS_UNSUPPORTED_PROJECTION,
        codes.SWATH_GEOLOCATION_UNSUPPORTED,
        codes.SWATH_DIMMAP_UNRESOLVED,
        codes.NETCDF_NAME_COLLISION,
    ):
        assert status_for(_hdf4_facts(), [_blocker(code)]) == \
            "ready_no_geolocation"


def test_a_dominating_blocker_outranks_a_geolocation_blocker():
    # Malformed structure dominates a geolocation-only issue.
    status = status_for(_hdf4_facts(), [
        _blocker(codes.EOS_UNSUPPORTED_PROJECTION),
        _blocker(codes.EOS_STRUCTMETADATA_MALFORMED),
    ])
    assert status == "malformed"


# --- severity folding --------------------------------------------------

def test_any_blocker_makes_status_non_ready():
    status = status_for(_hdf4_facts(), [_blocker(codes.SWATH_GEOLOCATION_UNSUPPORTED)])
    assert status != "ready"


def test_a_warning_does_not_demote_from_ready():
    warning = AuditIssue(
        code=codes.SWATH_GEOLOCATION_UNSUPPORTED, severity="warning",
        message="m", context={},
    )
    assert status_for(_hdf4_facts(), [warning]) == "ready"


# --- classify(facts): the craftable end-to-end cases ------------------

def test_classify_unknown_format_maps_format_unrecognized():
    status, issues = classify(_unknown_facts())
    assert status == "unknown"
    assert any(i.code == codes.FORMAT_UNRECOGNIZED for i in issues)


def test_classify_modern_file_is_already_modern():
    status, issues = classify(_modern_facts())
    assert status == "already_modern"


# --- classify smoke over real fixtures (not the agreement oracle) -----

def test_classify_over_fixtures_stays_in_taxonomy():
    for fixture in list(HDFEOS2_FIXTURES) + list(NETCDF_FIXTURES):
        status, issues = classify(inspect_file(str(fixture)))
        assert status in TAXONOMY
        for issue in issues:
            assert isinstance(issue, AuditIssue)
            assert issue.code in codes.ALL_CODES
