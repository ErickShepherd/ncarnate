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

    '''


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
