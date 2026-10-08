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
# v3 (2026-07-16): added DESTINATION_COLLISION (the convert-side manifest
# destination preflight's whole-run refusal — a registry code so operators
# script against one stable namespace, though it never appears in an audit
# record's issues).
# v4 (2026-07-16): added HDF4_RUNTIME_UNAVAILABLE (the degraded-capability
# refusal when pyhdf cannot be imported — a Windows pip install; KD-L4).
# v5 (2026-07-20): added RESULT_READBACK_INCOMPLETE (a non-fatal warning on a
# structured operation result whose conversion verified and committed but whose
# post-commit read-back could not assemble the full result; stage API step 4B).
# v6 (2026-07-20): added HANDOFF_SCHEMA_INVALID + HANDOFF_NOT_MATERIALIZABLE
# (the consumer-side handoff gates in `ncarnate.handoff`, refusing a received
# record that is not well-formed / not safe to materialize a store from; the
# handoff-contract hardening before the step-6 Zarr tail).
# v7: prepared source/resume checks, journal failures, strict text and audit mode.
# v8: mandatory OS-enforced isolated conversion worker limits.
# v9: explicit publication refusal and non-fatal worker cleanup warning.
RULESET_VERSION = 9
OUTPUT_PUBLISH_FAILED = "OUTPUT_PUBLISH_FAILED"
WORKER_CLEANUP_INCOMPLETE = "WORKER_CLEANUP_INCOMPLETE"
MEMORY_LIMIT_UNAVAILABLE = "MEMORY_LIMIT_UNAVAILABLE"
BOUNDED_WORKER_FAILED = "BOUNDED_WORKER_FAILED"
WORKER_TIMEOUT = "WORKER_TIMEOUT"
SOURCE_CHANGED = "SOURCE_CHANGED"
RESUME_MISMATCH = "RESUME_MISMATCH"
JOURNAL_UNAVAILABLE = "JOURNAL_UNAVAILABLE"
JOURNAL_WRITE_FAILED = "JOURNAL_WRITE_FAILED"
RESULT_ENCODING_INVALID = "RESULT_ENCODING_INVALID"
AUDIT_MODE_UNSUPPORTED = "AUDIT_MODE_UNSUPPORTED"

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

# DESTINATION_COLLISION: the whole-manifest destination preflight found two
# selected records claiming one output path (or a destination aliasing a
# source/tree, or a pre-existing output without --skip-existing) and refused
# the entire convert run before anything was written (KD-L1/KD-L2). Raised
# by `ncarnate.convert.preflight`, never emitted in an audit record's
# issues — it lives here because this registry is the single stable code
# namespace archive managers script against.
DESTINATION_COLLISION         = "DESTINATION_COLLISION"

# HDF4_RUNTIME_UNAVAILABLE: an HDF4/HDF-EOS2 operation was attempted on an
# install whose HDF4 runtime (pyhdf) cannot be imported — e.g. a Windows
# pip install, which has no pyhdf wheel (KD-L4). Raised by
# `ncarnate.hdf4_runtime.require_hdf4_runtime` before any output is
# created; in an audit record it appears as a blocker issue folding to the
# `unsupported` status (this install cannot convert the file — the file
# itself may be fine).
HDF4_RUNTIME_UNAVAILABLE      = "HDF4_RUNTIME_UNAVAILABLE"

# RESULT_READBACK_INCOMPLETE: a non-fatal warning on a structured operation
# result (stage API step 4B). The conversion's verified output was written and
# atomically committed (never deleted), but the post-commit read-back that
# assembles the full OperationResult failed — so `execute` returns a minimal
# verified result carrying this warning rather than misreporting a completed
# conversion as a failure. Raised nowhere; only ever a `ResultWarning.code`.
RESULT_READBACK_INCOMPLETE    = "RESULT_READBACK_INCOMPLETE"

# HANDOFF_SCHEMA_INVALID / HANDOFF_NOT_MATERIALIZABLE: the consumer-side gates
# a downstream (the step-6 Zarr tail) runs a *received* handoff record through
# before materializing a store. SCHEMA_INVALID = the record is not well-formed
# per the frozen schema; NOT_MATERIALIZABLE = it is schema-valid but unsafe to
# build a store from (an unknown schema_version, a degraded read-back record
# still bearing RESULT_READBACK_INCOMPLETE, or an empty structure over a
# non-empty destination — the silent-empty-store trap). Raised by
# `ncarnate.handoff.validate_handoff` / `check_materializable`; like
# DESTINATION_COLLISION they never appear in an audit record's issues — they
# live here because this registry is the single stable code namespace.
HANDOFF_SCHEMA_INVALID        = "HANDOFF_SCHEMA_INVALID"
HANDOFF_NOT_MATERIALIZABLE    = "HANDOFF_NOT_MATERIALIZABLE"

# The registry: the single source of truth the append-only contract test
# iterates. Adding a code means adding it here (and bumping RULESET_VERSION).
ALL_CODES = frozenset({
    OUTPUT_PUBLISH_FAILED, WORKER_CLEANUP_INCOMPLETE,
    MEMORY_LIMIT_UNAVAILABLE, BOUNDED_WORKER_FAILED, WORKER_TIMEOUT,
    SOURCE_CHANGED, RESUME_MISMATCH, JOURNAL_UNAVAILABLE, JOURNAL_WRITE_FAILED,
    RESULT_ENCODING_INVALID, AUDIT_MODE_UNSUPPORTED,
    EOS_UNSUPPORTED_PROJECTION,
    EOS_STRUCTMETADATA_MALFORMED,
    SWATH_DIMMAP_UNRESOLVED,
    SWATH_GEOLOCATION_UNSUPPORTED,
    NETCDF_NAME_COLLISION,
    UNSUPPORTED_TYPE,
    DECLARED_ALLOCATION_TOO_LARGE,
    FORMAT_UNRECOGNIZED,
    MALFORMED_CONTAINER,
    DESTINATION_COLLISION,
    HDF4_RUNTIME_UNAVAILABLE,
    RESULT_READBACK_INCOMPLETE,
    HANDOFF_SCHEMA_INVALID,
    HANDOFF_NOT_MATERIALIZABLE,
})
