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

The prediction is taken from `classify(inspect_file(...))` — the real
increment-2 taxonomy engine. (`audit_path` itself is not yet wired to this
engine; see LOOP_LEARNINGS.)
"""

import pytest

from ncarnate import recompress
from ncarnate.audit import codes
from ncarnate.audit.classify import classify
from ncarnate.audit.inspect import inspect_file
from ncarnate.errors import (
    EosParseError,
    NcarnateError,
    UnsupportedGeolocationError,
    UnsupportedProjectionError,
    UnsupportedTypeError,
)

from conftest import HDFEOS2_FIXTURES, NETCDF_FIXTURES, stage

ALL_FIXTURES = list(HDFEOS2_FIXTURES) + list(NETCDF_FIXTURES)

# The exception each blocker code predicts `recompress` will raise.
_CODE_EXCEPTION = {
    codes.EOS_UNSUPPORTED_PROJECTION   : UnsupportedProjectionError,
    codes.EOS_STRUCTMETADATA_MALFORMED : EosParseError,
    codes.SWATH_GEOLOCATION_UNSUPPORTED: UnsupportedGeolocationError,
    codes.SWATH_DIMMAP_UNRESOLVED      : UnsupportedGeolocationError,
    codes.NETCDF_NAME_COLLISION        : UnsupportedGeolocationError,
    codes.UNSUPPORTED_TYPE             : UnsupportedTypeError,
    codes.DECLARED_ALLOCATION_TOO_LARGE: NcarnateError,
}


def _first_blocker(issues):
    return next(issue for issue in issues if issue.severity == "blocker")


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

        # ...and attempting geolocation raises the predicted blocker.
        expected = _CODE_EXCEPTION[_first_blocker(issues).code]
        with pytest.raises(expected):
            recompress(str(src), overwrite=False, geolocation=True)

    else:

        # unsupported / malformed / unsafe / unknown ⇒ recompress raises the
        # mapped exception regardless of geolocation.
        expected = _CODE_EXCEPTION[_first_blocker(issues).code]
        with pytest.raises(expected):
            recompress(str(src), overwrite=False)
