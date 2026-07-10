#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

The audit issue-code registry: stable string codes archive managers
script against, plus ``RULESET_VERSION``.

Every v1 code mirrors an already-exercised converter site (design
§Classification), so the audit predicts exactly what the converter does.
Codes are **append-only**: a code may be added but never renamed or
repurposed. ``RULESET_VERSION`` is bumped whenever classification
*semantics* change (a code added, a predicate tightened or loosened).

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

# The ruleset version. Bump on any classification-semantics change.
# v2 (2026-07-10): added MALFORMED_CONTAINER (a recognized container whose
# structure is unreadable — a corrupt/truncated file).
RULESET_VERSION = 2

# The v1 issue codes, each mirroring the converter site named in the
# design §Classification registry table. Value == name by construction so
# the string is discoverable both as a module constant and in ALL_CODES.
EOS_UNSUPPORTED_PROJECTION    = "EOS_UNSUPPORTED_PROJECTION"
EOS_STRUCTMETADATA_MALFORMED  = "EOS_STRUCTMETADATA_MALFORMED"
SWATH_DIMMAP_UNRESOLVED       = "SWATH_DIMMAP_UNRESOLVED"
SWATH_GEOLOCATION_UNSUPPORTED = "SWATH_GEOLOCATION_UNSUPPORTED"
NETCDF_NAME_COLLISION         = "NETCDF_NAME_COLLISION"
UNSUPPORTED_TYPE              = "UNSUPPORTED_TYPE"
DECLARED_ALLOCATION_TOO_LARGE = "DECLARED_ALLOCATION_TOO_LARGE"
FORMAT_UNRECOGNIZED           = "FORMAT_UNRECOGNIZED"

# Post-v1, append-only additions (each bumped RULESET_VERSION).
# MALFORMED_CONTAINER: the magic bytes matched a science container but its
# structure could not be read (truncated/corrupt file, or an I/O error) — the
# converter likewise fails to open it, so the audit records it `malformed`
# rather than letting the exception abort a whole-archive scan.
MALFORMED_CONTAINER           = "MALFORMED_CONTAINER"

# The registry: the single source of truth the append-only contract test
# iterates. Adding a code means adding it here (and bumping RULESET_VERSION).
ALL_CODES = frozenset({
    EOS_UNSUPPORTED_PROJECTION,
    EOS_STRUCTMETADATA_MALFORMED,
    SWATH_DIMMAP_UNRESOLVED,
    SWATH_GEOLOCATION_UNSUPPORTED,
    NETCDF_NAME_COLLISION,
    UNSUPPORTED_TYPE,
    DECLARED_ALLOCATION_TOO_LARGE,
    FORMAT_UNRECOGNIZED,
    MALFORMED_CONTAINER,
})
