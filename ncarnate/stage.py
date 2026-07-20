#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

The public ``inspect → plan → execute`` stage API (step 4B).

Three verbs let a downstream integration assess a file, plan a conversion,
and execute the plan — producing the structured
:class:`~ncarnate.result.OperationResult` (step 4A) directly, without the
CLI, log parsing, or private imports (gate G4):

- :func:`inspect` ``(source) -> AuditResult`` — the audit path's own per-file
  assessor (not a parallel one); the assessment carries the detected format,
  structures, named issues, and the conversion plan.
- :func:`plan` ``(assessment, destination, …) -> Plan`` — an immutable
  :class:`~ncarnate.core.Plan` describing exactly what a conversion will do.
- :func:`~ncarnate.core.execute` ``(plan) -> OperationResult`` — the verified
  write-then-atomic-replace engine, re-exported here for one import surface.
  :func:`~ncarnate.core.execute_batch` streams results lazily over many plans.

The one-shot :func:`~ncarnate.core.recompress` and the manifest
:func:`~ncarnate.convert.convert_manifest` are thin callers of the same
``execute`` engine — this module is the *named* front door to it.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

from __future__ import annotations

# Standard library imports.
import os

# Local application imports.
from ncarnate.audit import AuditOptions, audit_path
from ncarnate.audit.models import AuditResult
from ncarnate.core import Plan, _plan_from_path, execute, execute_batch
from ncarnate.errors import NcarnateError

__all__ = [
    "inspect",
    "plan",
    "execute",
    "execute_batch",
    "Plan",
]


def inspect(source   : str,
            *,
            checksum : str | None = None,
            mode     : str = "metadata") -> AuditResult:

    '''

    Assess a single file read-only and return its
    :class:`~ncarnate.audit.models.AuditResult` — the *assessment* the rest
    of the stage API consumes. This reuses the audit path's per-file
    assessor (``audit_path`` over one file), never opens science arrays, and
    never writes.

    ``checksum="sha256"`` records the source digest in the assessment;
    ``mode`` selects the audit depth (default ``"metadata"``).

    '''

    report = audit_path(
        source, AuditOptions(recursive=False, mode=mode, checksum=checksum)
    )

    if not report.files:

        raise NcarnateError(f"No file to inspect at {source}")

    return report.files[0]


def plan(assessment  : AuditResult,
         destination : str | None = None,
         *,
         zlib        : bool = True,
         shuffle     : bool = True,
         complevel   : int  = 7,
         geolocation : bool = True,
         overwrite   : bool = True) -> Plan:

    '''

    Build an immutable :class:`~ncarnate.core.Plan` from an ``assessment``
    (an :func:`inspect` result) and a ``destination``, following the same
    ``recompress`` destination semantics: ``destination`` given writes there;
    omitted with ``overwrite`` recompresses a netCDF source in place / derives
    a ``<stem>.nc`` sibling for an HDF4 conversion.

    The plan re-detects the format from the source's **bytes** (the source is
    resolved from ``assessment.root``/``assessment.path``), never trusting the
    assessment's *declared* format — the untrusted-input rule. An
    ``assessment.status == "ready_no_geolocation"`` prediction forces
    ``geolocation=False`` (SDS-only), mirroring the manifest's per-status
    override.

    '''

    source = os.path.join(assessment.root, assessment.path)

    if assessment.status == "ready_no_geolocation":

        geolocation = False

    return _plan_from_path(
        source, destination, zlib, shuffle, complevel, overwrite, geolocation,
    )
