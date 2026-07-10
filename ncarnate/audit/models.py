#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

The audit data models: stdlib dataclasses, each with a
``to_record() -> dict`` that is JSON-safe.

The per-file ``AuditResult`` record *is* the migration-manifest contract
(design §One record schema) — there is no separate report format. Every
record carries the ``schema_version``/``ncarnate_version``/``ruleset_version``
it was produced under; those three are injected by ``to_record()`` from the
package constants, not stored per instance.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

from __future__ import annotations

# Standard library imports.
from dataclasses import dataclass, field
from typing import Any

# Local application imports.
from ncarnate.constants import __version__ as _NCARNATE_VERSION
from ncarnate.audit.codes import RULESET_VERSION

# The record schema version. Bumped only on a breaking schema change; the
# classification ruleset versions independently (codes.RULESET_VERSION).
SCHEMA_VERSION = 1


@dataclass
class AuditOptions:

    '''

    How an audit run behaves: recursion, audit depth, and opt-in hashing.

    '''

    recursive : bool = False
    mode      : str = "metadata"
    checksum  : str | None = None


@dataclass
class AuditIssue:

    '''

    A single named blocker or warning against a file, carrying a stable
    ``code`` (see :mod:`ncarnate.audit.codes`) and a machine-readable
    ``context``.

    '''

    code     : str
    severity : str
    message  : str
    context  : dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "code"    : self.code,
            "severity": self.severity,
            "message" : self.message,
            "context" : self.context,
        }


@dataclass
class StructureAudit:

    '''

    One structure inside a file (an HDF-EOS2 GRID/SWATH, or a netCDF/HDF5
    group) at metadata depth. ``projection`` and ``geolocation_plan`` are
    absent (``None``) where they do not apply (KD10).

    '''

    type             : str
    name             : str
    projection       : dict[str, Any] | None = None
    geolocation_plan : dict[str, Any] | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "type"            : self.type,
            "name"            : self.name,
            "projection"      : self.projection,
            "geolocation_plan": self.geolocation_plan,
        }


@dataclass
class ConversionPlan:

    '''

    What the converter would do with a ``ready`` file: the operation, the
    geolocation reconstruction method, and the output format.

    '''

    operation          : str
    geolocation_method : str | None = None
    output_format      : str = "NETCDF4"

    def to_record(self) -> dict[str, Any]:
        return {
            "operation"         : self.operation,
            "geolocation_method": self.geolocation_method,
            "output_format"     : self.output_format,
        }


@dataclass
class AuditResult:

    '''

    One file's audit outcome — the migration-manifest record. ``to_record()``
    emits the frozen schema-v1 shape (design §One record schema): the three
    version fields are injected from package constants, so a record is
    self-describing about the ruleset that produced it.

    '''

    root        : str
    path        : str
    size_bytes  : int
    format      : str
    status      : str
    mode        : str
    audited_at  : str
    sha256      : str | None = None
    structures  : list[StructureAudit] = field(default_factory=list)
    issues      : list[AuditIssue] = field(default_factory=list)
    plan        : ConversionPlan | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version" : SCHEMA_VERSION,
            "ncarnate_version": _NCARNATE_VERSION,
            "ruleset_version": RULESET_VERSION,
            "mode"           : self.mode,
            "audited_at"     : self.audited_at,
            "root"           : self.root,
            "path"           : self.path,
            "size_bytes"     : self.size_bytes,
            "sha256"         : self.sha256,
            "format"         : self.format,
            "status"         : self.status,
            "structures"     : [s.to_record() for s in self.structures],
            "issues"         : [i.to_record() for i in self.issues],
            "plan"           : self.plan.to_record() if self.plan is not None
                               else None,
        }


@dataclass
class AuditReport:

    '''

    The in-memory aggregate of an audit run: the per-file results plus the
    run context. The JSONL contract is the list of per-file records (no
    header, no trailer); ``to_record()`` is the convenience whole-run dict
    the Python API can serialise.

    '''

    root  : str
    mode  : str
    files : list[AuditResult] = field(default_factory=list)

    @property
    def summary(self) -> "AuditSummary":

        '''

        The per-status census, computed from the files. Both a file count
        and a byte total are tallied per status so readiness can be read
        "by files *and* bytes" (design §CLI integration).

        '''

        files_by_status : dict[str, int] = {}
        bytes_by_status : dict[str, int] = {}
        total_bytes     = 0

        for result in self.files:

            files_by_status[result.status] = (
                files_by_status.get(result.status, 0) + 1
            )
            bytes_by_status[result.status] = (
                bytes_by_status.get(result.status, 0) + result.size_bytes
            )
            total_bytes += result.size_bytes

        return AuditSummary(
            total_files     = len(self.files),
            total_bytes     = total_bytes,
            files_by_status = files_by_status,
            bytes_by_status = bytes_by_status,
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "root" : self.root,
            "mode" : self.mode,
            "files": [f.to_record() for f in self.files],
        }


@dataclass
class AuditSummary:

    '''

    A whole-run readiness census: totals plus a per-status breakdown by
    both file count and byte total. ``ready_bytes`` (design §Python API) is
    simply ``bytes_by_status.get("ready", 0)`` once increment 2 emits it.

    '''

    total_files     : int
    total_bytes     : int
    files_by_status : dict[str, int] = field(default_factory=dict)
    bytes_by_status : dict[str, int] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "total_files"    : self.total_files,
            "total_bytes"    : self.total_bytes,
            "files_by_status": self.files_by_status,
            "bytes_by_status": self.bytes_by_status,
        }
