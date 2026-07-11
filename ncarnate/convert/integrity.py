#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

The convert safety spine (design §The per-record loop step 3, KD2, §Risks):
two independent, load-bearing controls applied before any conversion.

* :func:`verify_sha256` re-hashes the on-disk source and requires it match
  the manifest's recorded ``sha256`` — a file changed since the audit has a
  stale prediction and must not be converted. A ``null`` recorded hash is
  refused unless the operator passes ``allow_unverified`` (the documented
  unsafe mode).
* :func:`resolve_within` treats the manifest as untrusted data whose
  ``path`` becomes a filesystem read/write target: it rejects any absolute
  or ``..``-escaping path (checked with :func:`os.path.realpath`) and
  confines every resolved path under its base. This is the *sole* defense
  against read/write redirection — the sha256 gate does not help, since an
  attacker who authors the manifest also authors ``record.sha256``.

Both raise an :class:`~ncarnate.errors.NcarnateError` subclass so the CLI
catches them uniformly and the convert loop can isolate the failure to one
record.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

from __future__ import annotations

# Standard library imports.
import os

# Local application imports.
from ncarnate.errors import NcarnateError
from ncarnate.hashing import sha256_of_file


class IntegrityError(NcarnateError):

    '''

    Raised when a record's source fails the sha256 re-verify gate — the
    recorded hash is ``null`` (and ``--allow-unverified`` was not given) or
    does not match the file's current bytes (changed since the audit).

    '''


class ContainmentError(NcarnateError):

    '''

    Raised when a manifest ``path`` is absolute or escapes its base directory
    after normalization — a path-traversal attempt from an untrusted
    manifest. The record is rejected; nothing outside the tree is read or
    written.

    '''


def verify_sha256(
    record, source_path : str, *, allow_unverified : bool = False
) -> None:

    '''

    Re-verify ``source_path`` against ``record.sha256`` (design step 3).
    Returns ``None`` when the source is trusted; raises
    :class:`IntegrityError` when it is not. ``source_path`` must already be
    containment-checked (see :func:`resolve_within`).

    '''

    if record.sha256 is None:

        if allow_unverified:

            return None

        raise IntegrityError(
            f"{record.path}: manifest records no sha256; refusing without "
            f"--allow-unverified"
        )

    # sha256 is a manifest field; a non-string (e.g. a hostile int) would make
    # the `[:12]` slice below raise a bare TypeError that only the run-survival
    # belt catches. Refuse it cleanly as an IntegrityError instead.
    if not isinstance(record.sha256, str):

        raise IntegrityError(
            f"{record.path}: manifest sha256 is not a string "
            f"({type(record.sha256).__name__}); the manifest is malformed"
        )

    actual = sha256_of_file(source_path)

    if actual != record.sha256:

        raise IntegrityError(
            f"{record.path}: sha256 mismatch — the file changed since the "
            f"audit (recorded {record.sha256[:12]}…, found {actual[:12]}…); "
            f"its prediction is stale"
        )

    return None


def resolve_within(base : str, relpath : str) -> str:

    '''

    Resolve ``relpath`` under ``base`` and confirm the result stays within
    ``base`` (§Risks path-containment control). Returns the resolved
    absolute path; raises :class:`ContainmentError` if ``relpath`` is
    absolute or escapes ``base`` after ``realpath`` normalization. Fails
    closed — an unprovable containment is a rejection.

    '''

    if os.path.isabs(relpath):

        raise ContainmentError(
            f"{relpath!r}: absolute paths are rejected; the manifest may only "
            f"carry relative paths confined under its root"
        )

    real_base = os.path.realpath(base)
    resolved  = os.path.realpath(os.path.join(real_base, relpath))

    try:

        contained = os.path.commonpath([resolved, real_base]) == real_base

    except ValueError:

        # Different drives / mixed kinds — containment cannot be proven.
        contained = False

    if not contained:

        raise ContainmentError(
            f"{relpath!r}: path escapes its base {base!r} after normalization"
        )

    return resolved
