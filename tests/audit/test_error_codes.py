"""Structured exception identity: the exc.code sweep (design §Classification).

The exception taxonomy is coarser than the issue-code registry — one type
(e.g. UnsupportedGeolocationError) is raised at several sites that must map
to *different* codes. So NcarnateError grows an optional ``code`` keyword and
each mapped raise site passes its registry code; classify.py reads exc.code
rather than scraping the (unchanged) message.

These tests fix that contract: the base mechanism, one clean trigger per
code, and that the messages at those sites are unchanged. RED until the
paired [impl] sweep lands (NcarnateError has no ``.code`` yet).

The four converter suites (run by the impl's verify) are the real guarantee
that no message changed; the substring checks here pin a representative few.
"""

import pytest

from ncarnate.audit import codes
from ncarnate.eos import structmeta
from ncarnate.eos.gctp import decode_packed_dms
from ncarnate.errors import (
    EosParseError,
    NcarnateError,
    UnsupportedGeolocationError,
    UnsupportedProjectionError,
    UnsupportedTypeError,
)
from ncarnate.hdf4 import (
    TreeGroup,
    TreeVariable,
    _attribute_value,
    _normalize_coordinate,
    _reserve_names,
)
from ncarnate.limits import check_array_size


# --- the base mechanism -----------------------------------------------

def test_ncarnate_error_carries_optional_code():
    assert NcarnateError("boom").code is None
    assert NcarnateError("boom", code="X").code == "X"


def test_subclasses_carry_code():
    for exc_cls in (
        EosParseError,
        UnsupportedProjectionError,
        UnsupportedGeolocationError,
        UnsupportedTypeError,
    ):
        assert exc_cls("m").code is None
        assert exc_cls("m", code="Y").code == "Y"


# --- one clean trigger per mapped code --------------------------------

def test_check_array_size_carries_allocation_code():
    with pytest.raises(NcarnateError) as exc:
        check_array_size((10 ** 6, 10 ** 6), 8, "bomb")
    assert exc.value.code == codes.DECLARED_ALLOCATION_TOO_LARGE
    assert "safety ceiling" in str(exc.value)   # message unchanged


def test_structmetadata_parse_carries_malformed_code():
    with pytest.raises(EosParseError) as exc:
        structmeta.parse("GROUP=GridStructure\nEND\n")   # unclosed
    assert exc.value.code == codes.EOS_STRUCTMETADATA_MALFORMED


def test_gctp_unsupported_projection_carries_projection_code():
    with pytest.raises(UnsupportedProjectionError) as exc:
        decode_packed_dms(45099000.0)   # 99 minutes: invalid packed DMS
    assert exc.value.code == codes.EOS_UNSUPPORTED_PROJECTION


def test_attribute_value_carries_unsupported_type_code():
    with pytest.raises(UnsupportedTypeError) as exc:
        _attribute_value(9999, b"x")    # 9999 is not a known HDF4 type code
    assert exc.value.code == codes.UNSUPPORTED_TYPE
    assert "unsupported HDF4 type code" in str(exc.value)   # message unchanged


def test_packed_geolocation_carries_geolocation_code():
    # A packed (scaled) coordinate is unsupported geolocation.
    variable = TreeVariable(
        name="Latitude", dimensions=("y", "x"), values=None,
        attributes={"scale_factor": 0.5},
    )
    with pytest.raises(UnsupportedGeolocationError) as exc:
        _normalize_coordinate(variable, "degrees_north")
    assert exc.value.code == codes.SWATH_GEOLOCATION_UNSUPPORTED
    assert "packed" in str(exc.value)   # message unchanged


def test_reserved_name_collision_carries_collision_code():
    # _reserve_names raises UnsupportedGeolocationError (the coarse type) but
    # its code is NETCDF_NAME_COLLISION — the exact case exc.code exists for.
    group = TreeGroup.empty("swath")
    group.variables.append(
        TreeVariable(name="lat", dimensions=(), values=None, attributes={})
    )
    with pytest.raises(UnsupportedGeolocationError) as exc:
        _reserve_names(group, ("lat",), "Swath X")
    assert exc.value.code == codes.NETCDF_NAME_COLLISION
    assert "already exists" in str(exc.value)   # message unchanged
