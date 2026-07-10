"""Unit tests for the audit issue-code registry (ncarnate.audit.codes).

The eight v1 codes and RULESET_VERSION are the frozen, append-only
contract archive managers script against (design §Classification). These
tests pin that surface. ``ncarnate.audit.codes`` does not exist yet, so
they fail until the paired [impl] unit lands it.
"""

import ncarnate.audit.codes as codes

# The v1 registry, verbatim from design §Classification's table. Codes are
# append-only: a code may be added (bump RULESET_VERSION) but never renamed
# or removed. This set is the v1 snapshot the append-only contract test
# (increment 3) guards.
V1_CODES = {
    "EOS_UNSUPPORTED_PROJECTION",
    "EOS_STRUCTMETADATA_MALFORMED",
    "SWATH_DIMMAP_UNRESOLVED",
    "SWATH_GEOLOCATION_UNSUPPORTED",
    "NETCDF_NAME_COLLISION",
    "UNSUPPORTED_TYPE",
    "DECLARED_ALLOCATION_TOO_LARGE",
    "FORMAT_UNRECOGNIZED",
}


def test_ruleset_version_is_a_positive_int():
    assert isinstance(codes.RULESET_VERSION, int)
    # A bool is an int subclass; the version is a genuine integer.
    assert not isinstance(codes.RULESET_VERSION, bool)
    assert codes.RULESET_VERSION >= 1


def test_registry_exposes_the_eight_v1_codes():
    # Append-only floor: these codes must always be present.
    assert V1_CODES <= set(codes.ALL_CODES)


def test_v1_registry_is_exactly_these_codes():
    # v1 freezes exactly these eight; adding a ninth is a ruleset bump.
    assert set(codes.ALL_CODES) == V1_CODES


def test_each_code_is_exposed_as_a_named_string_constant():
    # e.g. codes.FORMAT_UNRECOGNIZED == "FORMAT_UNRECOGNIZED".
    for code in V1_CODES:
        assert getattr(codes, code) == code


def test_codes_are_unique():
    all_codes = list(codes.ALL_CODES)
    assert len(all_codes) == len(set(all_codes))
