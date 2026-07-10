#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

Classification: raw facts -> status + issues (design §Classification).

The audit's core move is to **call the converter's own predicates and
catch**, rather than re-implement the rules — so the audit cannot disagree
with the converter about *rules*, only about *depth*. A caught exception's
``code`` (with a type-level fallback) becomes the issue code; the two
return-sentinel predicates are mapped explicitly; and severity is folded so
any blocker yields a non-ready status.

At this (metadata) depth classify runs the metadata-visible predicates
(projection supportability, declared type); the array-entangled swath
geolocation checks wait for ``--mode sample`` (a stated depth limitation,
not a rule fork).

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

from __future__ import annotations

# Third party imports.
import numpy as np

# Local application imports.
from ncarnate.audit import codes
from ncarnate.audit.models import AuditIssue
from ncarnate.eos.gctp import projection_info
from ncarnate.errors import (
    EosParseError,
    NcarnateError,
    UnsupportedGeolocationError,
    UnsupportedProjectionError,
    UnsupportedTypeError,
)

# Type-level fallback code for an exception raised at an un-annotated site
# (exc.code is None). The ambiguous UnsupportedGeolocationError defaults to
# the geolocation code; _reserve_names overrides it via exc.code.
_TYPE_DEFAULT_CODE = {
    EosParseError               : codes.EOS_STRUCTMETADATA_MALFORMED,
    UnsupportedProjectionError  : codes.EOS_UNSUPPORTED_PROJECTION,
    UnsupportedTypeError        : codes.UNSUPPORTED_TYPE,
    UnsupportedGeolocationError : codes.SWATH_GEOLOCATION_UNSUPPORTED,
}

# Each issue code's status bucket. Geolocation blockers leave the SDS
# payload convertible (--no-geolocation), so they fold to
# ready_no_geolocation; the rest name their own non-ready status.
_CODE_STATUS = {
    codes.EOS_STRUCTMETADATA_MALFORMED : "malformed",
    codes.MALFORMED_CONTAINER          : "malformed",
    codes.DECLARED_ALLOCATION_TOO_LARGE: "unsafe",
    codes.UNSUPPORTED_TYPE             : "unsupported",
    codes.FORMAT_UNRECOGNIZED          : "unknown",
    codes.EOS_UNSUPPORTED_PROJECTION   : "ready_no_geolocation",
    codes.SWATH_GEOLOCATION_UNSUPPORTED: "ready_no_geolocation",
    codes.SWATH_DIMMAP_UNRESOLVED      : "ready_no_geolocation",
    codes.NETCDF_NAME_COLLISION        : "ready_no_geolocation",
}

# Most-severe first: a dominating blocker outranks a geolocation-only one.
_STATUS_PRIORITY = [
    "malformed", "unsafe", "unsupported", "unknown", "ready_no_geolocation",
]


def _is_unsupported_type(dtype) -> bool:

    '''

    Mirrors the converter's type predicate (``hdf4._read_dataset`` /
    ``core._copy_variables`` at ``core.py:320``): a supported variable has a
    concrete ``numpy`` dtype. An HDF4 type code outside the v2 set (``dtype``
    is ``None``) or a netCDF4 user-defined type (compound / VLen / enum /
    opaque — its ``datatype`` is not an ``np.dtype``) is exactly what
    ``recompress`` rejects as ``UNSUPPORTED_TYPE``.

    '''

    return not isinstance(dtype, np.dtype)


def _type_default_code(exception : NcarnateError) -> "str | None":

    for exception_type, code in _TYPE_DEFAULT_CODE.items():

        if isinstance(exception, exception_type):

            return code

    return None


def issue_for_exception(exception : NcarnateError) -> AuditIssue:

    '''

    Maps a caught converter exception to an :class:`AuditIssue`. The issue
    code is ``exception.code`` when the raise site set one (the disambiguated
    case), else the exception's type-level default. All mapped codes are
    blockers.

    '''

    code = getattr(exception, "code", None) or _type_default_code(exception)

    return AuditIssue(
        code     = code,
        severity = "blocker",
        message  = str(exception),
        context  = {},
    )


def status_for(facts, issues : "list[AuditIssue]") -> str:

    '''

    The status decision: fold blocker severity into the taxonomy. Pure — it
    reads only the facts' base kind and the issues' codes/severities.

    '''

    blocker_statuses = {
        _CODE_STATUS[issue.code]
        for issue in issues
        if issue.severity == "blocker" and issue.code in _CODE_STATUS
    }

    for status in _STATUS_PRIORITY:

        if status in blocker_statuses:

            return status

    # No blockers: the base status is the file's kind.
    if facts.format == "UNKNOWN":

        return "unknown"

    if facts.already_modern:

        return "already_modern"

    return "ready"


def classify(facts) -> "tuple[str, list[AuditIssue]]":

    '''

    Classifies one file's facts into a status and its issues, by running the
    converter's metadata-visible predicates and catching.

    '''

    issues : list[AuditIssue] = []

    if facts.format == "UNKNOWN":

        # detect_format returning UNKNOWN is a return-sentinel, mapped here.
        issues.append(AuditIssue(
            code     = codes.FORMAT_UNRECOGNIZED,
            severity = "blocker",
            message  = "Unrecognized file format.",
            context  = {},
        ))

        return status_for(facts, issues), issues

    # A variable whose declared type is outside the v2 set — an HDF4 code
    # with no numpy mapping, or a netCDF4 user-defined type — is what the
    # converter rejects as UNSUPPORTED_TYPE.
    for variable in facts.variables:

        if _is_unsupported_type(variable.dtype):

            issues.append(AuditIssue(
                code     = codes.UNSUPPORTED_TYPE,
                severity = "blocker",
                message  = f"Variable {variable.name!r} uses an unsupported "
                           f"type.",
                context  = {"variable": variable.name},
            ))

    # Grid projection supportability is a metadata-visible converter
    # predicate: call it and catch, so the audit agrees by construction.
    if facts.eos_metadata is not None:

        for eos_grid_ in facts.eos_metadata.grids:

            try:

                projection_info(eos_grid_)

            except NcarnateError as error:

                issues.append(issue_for_exception(error))

    return status_for(facts, issues), issues
