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


class VerificationError(NcarnateError):

    '''

    Raised when the post-write verification pass finds any difference
    between the source file and the recompressed copy. The source file
    is never replaced when this is raised.

    '''
