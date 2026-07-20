#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

Defines the exception hierarchy for the ncarnate package.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''


class NcarnateError(Exception):

    '''

    The base class for all errors raised deliberately by ncarnate.

    Carries an optional structured ``code`` (a ``ncarnate.audit.codes``
    registry string) so the audit can disambiguate raise sites that share
    one exception type — e.g. ``UnsupportedGeolocationError`` is both a
    packed-geolocation blocker and a name collision. The code is set **at
    the raise site**, never intrinsic to the type; ``code`` defaults to
    ``None`` and the message is unchanged.

    '''

    def __init__(self, *args, code : "str | None" = None):

        super().__init__(*args)

        self.code = code


class UnsupportedFormatError(NcarnateError):

    '''

    Raised when an input file is not a format ncarnate can read.

    '''


class UnsupportedTypeError(NcarnateError):

    '''

    Raised when a variable uses a netCDF4 user-defined type (compound,
    VLen, enum, or opaque) that is outside the v2 fidelity guarantee.
    ncarnate fails loud rather than guessing at a lossy copy.

    '''


class EosParseError(NcarnateError):

    '''

    Raised when a file's ``StructMetadata`` text cannot be parsed as the
    ODL structure HDF-EOS2 defines.

    '''


class UnsupportedProjectionError(NcarnateError):

    '''

    Raised when an HDF-EOS2 grid uses a GCTP projection ncarnate has not
    verified against a fixture. A wrong coordinate is worse than a
    refused conversion; ``--no-geolocation`` converts the SDS payload
    without reconstruction.

    '''


class UnsupportedGeolocationError(NcarnateError):

    '''

    Raised when an HDF-EOS2 structure uses a geolocation construct
    outside the v2 scope (index dimension maps, merged fields, missing
    geolocation fields). ``--no-geolocation`` converts the SDS payload
    without reconstruction.

    '''


class VerificationError(NcarnateError):

    '''

    Raised when the post-write verification pass finds any difference
    between the source file and the recompressed copy. The source file
    is never replaced when this is raised.

    '''


class AllocationTooLargeError(NcarnateError):

    '''

    Raised when a file's declared array (from untrusted metadata) would
    exceed the allocation safety ceiling — a tiny-file-declares-terabytes
    size bomb. A dedicated type lets the audit's classifier map it to the
    ``unsafe`` status by type as well as by ``code``.

    '''


class HandoffError(NcarnateError):

    '''

    Raised when a received handoff record is not a well-formed, materializable
    ``OperationResult.to_record()`` per the frozen schema. A downstream
    consumer (e.g. the Zarr tail) raises this to refuse a record before it
    reads the destination or materializes a store — either a schema violation
    (:func:`ncarnate.handoff.validate_handoff`) or a materializability refusal
    (:func:`ncarnate.handoff.check_materializable` — a schema-valid but
    empty/degraded record that would yield a silently-empty store).

    '''


def render_refusal(error : NcarnateError) -> str:

    '''

    Renders a refusal as operator-facing text: the stable registry code in
    a ``[CODE]`` prefix, then the message. The code is rendered *textually*
    — not just carried on the exception — because the CLI surface is what
    operators script against (grep stderr, branch on the exit code). An
    error with no ``code`` renders as its message alone, unchanged. Lives
    here, not in a CLI module, so both the flat CLI and the manifest CLI
    render codes identically without pulling in each other's stacks.

    '''

    if getattr(error, "code", None):

        return f"[{error.code}] {error}"

    return str(error)
