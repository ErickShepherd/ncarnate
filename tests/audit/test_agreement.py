"""Agreement tests (design §Testing.1): the taxonomy's credibility oracle.

For every fixture the audit's prediction must match what the converter
actually does: predicts `ready` (or `already_modern`) ⇒ `recompress`
succeeds; predicts a blocker code ⇒ `recompress` raises the mapped
exception; predicts `ready_no_geolocation` ⇒ the payload converts with
`--no-geolocation` but attempting geolocation raises the mapped exception.
This single parametrized test keeps the taxonomy honest forever — every
real-world mismatch becomes a new fixture and a public issue.

CREDIBILITY ORACLE — authored by the loop but deliberately NOT run in-loop:
self-blessing the oracle (running it green to certify that the loop's own
classify agrees with recompress) is the circular discharge the loop must
never do. There is no `verify:`; an out-of-loop reviewer discharges it with
`python -m pytest tests/audit/test_agreement.py -q`.

The prediction is taken from `classify(inspect_file(...))` — the same
taxonomy engine `audit_path` drives (see `tests/audit/test_audit_path.py`
for the through-`audit_path` integration check).
"""

import pytest

from ncarnate import recompress
from ncarnate.audit import codes
from ncarnate.audit.classify import _CODE_STATUS, classify, issue_for_exception
from ncarnate.audit.inspect import inspect_file
from ncarnate.errors import (
    AllocationTooLargeError,
    EosParseError,
    UnsupportedGeolocationError,
    UnsupportedProjectionError,
    UnsupportedTypeError,
)

from conftest import BLOCKER_FIXTURES, HDFEOS2_FIXTURES, NETCDF_FIXTURES, stage

# Convertible fixtures + deliberately-unconvertible blocker fixtures, so the
# oracle exercises BOTH directions: the ready/already_modern path AND the
# blocker path (predicts a blocker code ⇒ recompress raises the mapped
# exception). Without a blocker fixture the raise-mapping branch is dead.
ALL_FIXTURES = (
    list(HDFEOS2_FIXTURES) + list(NETCDF_FIXTURES) + list(BLOCKER_FIXTURES)
)

# The exception each blocker code predicts `recompress` will raise.
_CODE_EXCEPTION = {
    codes.EOS_UNSUPPORTED_PROJECTION   : UnsupportedProjectionError,
    codes.EOS_STRUCTMETADATA_MALFORMED : EosParseError,
    codes.SWATH_GEOLOCATION_UNSUPPORTED: UnsupportedGeolocationError,
    codes.SWATH_DIMMAP_UNRESOLVED      : UnsupportedGeolocationError,
    codes.NETCDF_NAME_COLLISION        : UnsupportedGeolocationError,
    codes.UNSUPPORTED_TYPE             : UnsupportedTypeError,
    codes.DECLARED_ALLOCATION_TOO_LARGE: AllocationTooLargeError,
}


def _dominating_blocker(issues, status):
    # The blocker that actually set the folded status — not merely the first
    # in list order (a malformed blocker outranks a geolocation one, so
    # list-order can name the wrong cause). Falls back to the first blocker
    # if none maps to `status` (defensive; shouldn't happen).
    blockers = [issue for issue in issues if issue.severity == "blocker"]
    for issue in blockers:
        if _CODE_STATUS.get(issue.code) == status:
            return issue
    return blockers[0]


@pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda f: f.stem)
def test_audit_predicts_recompression_outcome(fixture, workdir):
    # Stage a writable copy: inspect (read-only) and recompress both act on
    # it, so the committed fixture is never touched.
    src = stage(fixture, workdir)

    status, issues = classify(inspect_file(str(src)))

    if status in ("ready", "already_modern"):

        # Predicted fully convertible ⇒ recompress must succeed.
        recompress(str(src), overwrite=False)

    elif status == "ready_no_geolocation":

        # The SDS payload converts without geolocation reconstruction...
        recompress(str(src), overwrite=False, geolocation=False)

        # ...and attempting geolocation raises the predicted blocker — and
        # for the predicted *reason*, not merely the right type: the exception
        # the converter raises must classify back to the predicted code.
        dominating = _dominating_blocker(issues, status)
        expected   = _CODE_EXCEPTION[dominating.code]
        with pytest.raises(expected) as exc:
            recompress(str(src), overwrite=False, geolocation=True)
        assert issue_for_exception(exc.value).code == dominating.code

    else:

        # unsupported / malformed / unsafe / unknown ⇒ recompress raises the
        # mapped exception regardless of geolocation — and for the predicted
        # cause (the raised exception classifies back to the predicted code).
        dominating = _dominating_blocker(issues, status)
        expected   = _CODE_EXCEPTION[dominating.code]
        with pytest.raises(expected) as exc:
            recompress(str(src), overwrite=False)
        assert issue_for_exception(exc.value).code == dominating.code
