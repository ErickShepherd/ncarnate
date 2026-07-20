#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

The structured operation result (stage API step 4A): a versioned,
JSON-safe description of one executed conversion — source and destination
identities, the output's group/dimension/attribute tree with effective
per-variable encoding, the sanitized-name companions, the coordinate
actions, and the conversion-verification status (kept structurally
separate from any scientific-validation status).

Design: docs/design/ncarnate-operation-result.md. This module is **pure
data** — dataclasses plus JSON coercion, no file I/O. The read-back that
turns a committed netCDF file into an :class:`OperationResult` lives in
:mod:`ncarnate.core` (it needs the netCDF runtime); this module only
defines the shape and its two serializations:

- :meth:`OperationResult.to_record` — the full JSON-safe payload for the
  manifest journal (paths, digests, elapsed time, adapter versions).
- :func:`canonical_json` — a deterministic, nondeterminism-excluded byte
  serialization for the golden-hash tests step 5 will freeze against.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

from __future__ import annotations

# Standard library imports.
import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any

# Third party imports.
import numpy as np

# Local application imports.
from ncarnate.constants import __version__ as _NCARNATE_VERSION

# The operation-result schema version. Independent of the audit record's
# SCHEMA_VERSION and the classifier's RULESET_VERSION — it versions a
# different artifact (design KD3) and is the number step 5 freezes. Bumped
# only on a breaking change to the result shape; the canonical-hash golden
# test is its drift tripwire.
#
# v2 (step 5, the freeze): adds the caller-owned ``retention`` slot (ncarnate
# always emits ``null``) and the computed ``plan_hash`` (a stable
# executed-plan identity). Both additive; the shipped JSON Schema
# (ncarnate/schemas/handoff.schema.json, loaded via ncarnate.handoff) freezes
# this number.
OPERATION_RESULT_SCHEMA_VERSION = 2


# ---------------------------------------------------------------------------
# JSON coercion (design §JSON): numpy scalars/arrays -> Python scalars/lists,
# and non-finite floats -> the strict-JSON string tokens a schema validator
# can round-trip (Python's bare NaN/Infinity are not valid JSON).
# ---------------------------------------------------------------------------

def _finite_or_token(value : float) -> "float | str":

    '''

    A finite float unchanged; a non-finite one as its RFC-8259-safe string
    token so ``allow_nan=False`` serialization (and a JSON Schema validator)
    accepts it. A real ``_FillValue`` of NaN must survive the round-trip.

    '''

    if math.isnan(value):

        return "NaN"

    if math.isinf(value):

        return "Infinity" if value > 0 else "-Infinity"

    return value


def json_safe(value : Any) -> Any:

    '''

    Coerce an attribute value (a Python scalar, a numpy scalar, or a numpy
    array — including the ``uint8`` arrays the embedded-NUL companion uses)
    into a JSON-safe form. Recursively walks arrays/sequences so a nested
    non-finite float is tokenized too (:func:`_finite_or_token`).

    '''

    # str/None/bool before int (bool is an int subclass) so a text scalar or
    # a flag is never mis-coerced.
    if value is None or isinstance(value, (str, bool)):

        return value

    if isinstance(value, int):

        return value

    if isinstance(value, float):

        return _finite_or_token(value)

    if isinstance(value, np.generic):

        return json_safe(value.item())

    if isinstance(value, np.ndarray):

        return [json_safe(item) for item in value.tolist()]

    if isinstance(value, (list, tuple)):

        return [json_safe(item) for item in value]

    if isinstance(value, bytes):

        return value.decode("utf-8", "surrogateescape")

    return value


# ---------------------------------------------------------------------------
# The result tree. Every type carries a JSON-safe ``to_record()`` (the
# audit-model convention); attribute values are coerced at build time, so
# ``to_record()`` returns them directly.
# ---------------------------------------------------------------------------

@dataclass
class SourceIdentity:

    '''

    The converted file's input identity, captured **before** the write (so
    an in-place recompression records the original bytes, not the
    replacement). ``sha256`` is the digest over the bytes execute read; it
    is ``None`` only where hashing was relaxed (``--allow-unverified``, KD10).

    '''

    path            : str
    detected_format : str
    size_bytes      : int
    sha256          : str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "path"           : self.path,
            "detected_format": self.detected_format,
            "size_bytes"     : self.size_bytes,
            "sha256"         : self.sha256,
        }


@dataclass
class OutputIdentity:

    '''

    The committed output's identity, read back after the atomic replace.
    ``container_format`` is the written container (``"NETCDF4"``); ``sha256``
    is the output digest (HDF5-library-version-dependent, so excluded from
    the canonical hash — KD8).

    '''

    path             : str
    container_format : str
    size_bytes       : int
    sha256           : str

    def to_record(self) -> dict[str, Any]:
        return {
            "path"            : self.path,
            "container_format": self.container_format,
            "size_bytes"      : self.size_bytes,
            "sha256"          : self.sha256,
        }


@dataclass
class EncodingOptions:

    '''

    The **requested** run encoding (mirrors ``recompress``'s flags). The
    *effective* per-variable encoding is recorded separately on each
    :class:`Variable`, read back from the output (KD5).

    '''

    zlib        : bool
    shuffle     : bool
    complevel   : int
    geolocation : bool

    def to_record(self) -> dict[str, Any]:
        return {
            "zlib"       : self.zlib,
            "shuffle"    : self.shuffle,
            "complevel"  : self.complevel,
            "geolocation": self.geolocation,
        }


@dataclass
class Attribute:

    '''

    One netCDF attribute (group/global or per-variable). ``storage_type``
    preserves the ``NC_STRING`` vs ``NC_CHAR`` distinction the converter
    works to keep (KD-L6), or the numpy dtype string for a numeric
    attribute. ``value`` is already :func:`json_safe`-coerced at build time.

    '''

    name         : str
    storage_type : str
    value        : Any

    def to_record(self) -> dict[str, Any]:
        return {
            "name"        : self.name,
            "storage_type": self.storage_type,
            "value"       : self.value,
        }


@dataclass
class Dimension:

    '''

    One dimension of a group: its name, size, and whether it is unlimited
    (an appendable record dimension). Gives a consuming Zarr tail the array
    shape a :class:`Variable`'s ``dimensions`` names resolve against.

    '''

    name      : str
    size      : int
    unlimited : bool

    def to_record(self) -> dict[str, Any]:
        return {
            "name"     : self.name,
            "size"     : self.size,
            "unlimited": self.unlimited,
        }


@dataclass
class Variable:

    '''

    One variable's ground truth, read back from the committed output:
    dtype, explicit endianness, the dimension **names** it spans (shape
    resolves against the group's :class:`Dimension` list), the **effective**
    encoding the library actually wrote, and the full attribute set —
    including the ``scale_factor`` / ``add_offset`` / ``_FillValue`` packing
    declarations the fidelity contract preserves.

    '''

    name       : str
    dtype      : str
    endian     : str
    dimensions : list[str]
    zlib       : bool
    shuffle    : bool
    complevel  : int
    chunksizes : list[int] | None
    attributes : list[Attribute] = field(default_factory=list)

    def to_record(self) -> dict[str, Any]:
        return {
            "name"      : self.name,
            "dtype"     : self.dtype,
            "endian"    : self.endian,
            "dimensions": list(self.dimensions),
            "encoding"  : {
                "zlib"      : self.zlib,
                "shuffle"   : self.shuffle,
                "complevel" : self.complevel,
                "chunksizes": self.chunksizes,
            },
            "attributes": [a.to_record() for a in self.attributes],
        }


@dataclass
class GroupNode:

    '''

    One group of the output, recursively. The root's ``path`` is ``"/"``.
    Empty / metadata-only groups are included (so the ``HDFEOS_INFORMATION``
    group holding verbatim ``StructMetadata.0`` survives into the handoff).
    Mirrors the tree :func:`ncarnate.core._verify_group` walks.

    '''

    path       : str
    dimensions : list[Dimension] = field(default_factory=list)
    attributes : list[Attribute] = field(default_factory=list)
    variables  : list[Variable] = field(default_factory=list)
    groups     : list["GroupNode"] = field(default_factory=list)

    def to_record(self) -> dict[str, Any]:
        return {
            "path"      : self.path,
            "dimensions": [d.to_record() for d in self.dimensions],
            "attributes": [a.to_record() for a in self.attributes],
            "variables" : [v.to_record() for v in self.variables],
            "groups"    : [g.to_record() for g in self.groups],
        }


@dataclass
class NameMapping:

    '''

    A sanitized-name companion: an output name and the original HDF4/HDF-EOS2
    name it was derived from, its ``kind`` (variable/dimension/attribute/
    group/grid), and the ``parent_path`` of its owner — which disambiguates
    an attribute rename (e.g. ``Scan Offset``) that can recur on many
    variables.

    '''

    netcdf_name   : str
    original_name : str
    kind          : str
    parent_path   : str

    def to_record(self) -> dict[str, Any]:
        return {
            "netcdf_name"  : self.netcdf_name,
            "original_name": self.original_name,
            "kind"         : self.kind,
            "parent_path"  : self.parent_path,
        }


@dataclass
class SkippedCoordinate:

    '''

    A coordinate reconstruction that did **not** happen, with a reason and,
    where one applies, a stable :mod:`ncarnate.audit.codes` registry code.

    '''

    name   : str
    reason : str
    code   : str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "name"  : self.name,
            "reason": self.reason,
            "code"  : self.code,
        }


@dataclass
class CoordinateActions:

    '''

    What the conversion did to coordinates: the reconstructed coordinate /
    grid-mapping variables ``generated`` (empty on the storage-only netCDF
    recompression path), and any ``skipped`` reconstructions.

    '''

    generated : list[str] = field(default_factory=list)
    skipped   : list[SkippedCoordinate] = field(default_factory=list)

    def to_record(self) -> dict[str, Any]:
        return {
            "generated": list(self.generated),
            "skipped"  : [s.to_record() for s in self.skipped],
        }


@dataclass
class VerificationRecord:

    '''

    The conversion-**integrity** status — always populated by ncarnate. A
    failed verification raises and leaves no output (KD6), so ``status`` has
    no failure value here. ``method`` is worded **per verifier**, scoped to
    the fidelity contract and never beyond it (overclaim-guard).

    '''

    status           : str
    verifier         : str
    verifier_version : str
    method           : str

    def to_record(self) -> dict[str, Any]:
        return {
            "status"          : self.status,
            "verifier"        : self.verifier,
            "verifier_version": self.verifier_version,
            "method"          : self.method,
        }


@dataclass
class ValidationRecord:

    '''

    The scientific-**validation** status — kept structurally separate from
    conversion integrity (design KD4). ncarnate never performs scientific
    validation, so it sets ``status="not_performed"`` and leaves the
    ``validator`` / ``method`` / ``record`` slots for a downstream pipeline
    to fill (step 5's validation record) without a schema change.

    '''

    status    : str = "not_performed"
    validator : str | None = None
    method    : str | None = None
    record    : dict[str, Any] | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "status"   : self.status,
            "validator": self.validator,
            "method"   : self.method,
            "record"   : self.record,
        }


@dataclass
class Environment:

    '''

    The native-library versions that actually produced the output bytes.
    Any adapter absent from this install maps to ``None`` (e.g. ``pyhdf`` /
    ``libhdf4`` on a Windows pip install). Excluded from the canonical hash
    (KD8) — it is environment-dependent, not part of the result shape.

    '''

    adapter_versions : dict[str, str | None] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "adapter_versions": dict(self.adapter_versions),
        }


@dataclass
class ResultWarning:

    '''

    A non-fatal note about a successful conversion, carrying a stable
    :mod:`ncarnate.audit.codes` registry code — the same namespace archive
    managers already script against.

    '''

    code    : str
    message : str
    context : dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "code"   : self.code,
            "message": self.message,
            "context": self.context,
        }


@dataclass
class OperationResult:

    '''

    One executed conversion, fully described (design §Module & type layout).
    ``to_record()`` injects ``schema_version`` and ``ncarnate_version`` from
    package constants (the audit-model convention), so a record is
    self-describing about the shape and the code that produced it.

    '''

    source        : SourceIdentity
    destination   : OutputIdentity
    operation     : str
    options       : EncodingOptions
    structure     : GroupNode
    verification  : VerificationRecord
    environment   : Environment
    elapsed_seconds : float
    validation    : ValidationRecord = field(default_factory=ValidationRecord)
    name_mappings : list[NameMapping] = field(default_factory=list)
    coordinates   : CoordinateActions = field(default_factory=CoordinateActions)
    warnings      : list[ResultWarning] = field(default_factory=list)
    # The caller/pipeline-owned retention slot (step 5, design KD-S1). ncarnate
    # NEVER sets this — it stays ``None`` (serialized ``null``), reserving the
    # slot so a downstream can attach retention metadata to the same record it
    # received without a schema change. The always-null value is kept in the
    # canonical form so the golden pins that ncarnate never fills it (KD-S2).
    retention     : dict[str, Any] | None = None

    def plan_hash(self) -> str:

        '''

        A stable identity of the **executed plan** — the sha256 hex digest of
        a canonical serialization of the conversion *inputs* (operation +
        requested encoding options + source identity), so "same source bytes +
        same request ⇒ same ``plan_hash``" (design KD-S3).

        It dedupes **requests, not artifacts**: it excludes the output
        ``structure`` (that is the *result* of executing the plan, not the
        plan), the absolute source *path* (machine-specific), and every
        ncarnate / native-library version — so the *same* request re-run under
        a different HDF5/ncarnate version can legitimately produce a different
        store (e.g. different effective chunking; the KD-S6 reason outputs are
        not hash-pinned). Artifact identity is therefore the **pair**
        ``(plan_hash, destination.sha256)``: a consumer that keys "already
        materialized" on ``plan_hash`` alone can serve a stale or
        grid-mismatched store, so a step-6 commit manifest must link
        ``destination.sha256``.

        **Null-digest caveat:** ``plan_hash`` is collision-resistant only when
        ``source.sha256`` is non-null. :func:`ncarnate.core.execute` always
        hashes the source, so no current path emits a null digest; the nullable
        branch is reserved for a future digest relaxation, where the projection
        would degrade to ``{operation, options, format, size}`` and a consumer
        must refuse to key idempotency on it.

        '''

        projection = {
            "operation": self.operation,
            "options"  : self.options.to_record(),
            "source"   : {
                "detected_format": self.source.detected_format,
                "size_bytes"     : self.source.size_bytes,
                "sha256"         : self.source.sha256,
            },
        }
        payload = json.dumps(
            projection,
            sort_keys    = True,
            separators   = (",", ":"),
            ensure_ascii = False,
            allow_nan    = False,
        )

        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version"  : OPERATION_RESULT_SCHEMA_VERSION,
            "ncarnate_version": _NCARNATE_VERSION,
            "operation"       : self.operation,
            "plan_hash"       : self.plan_hash(),
            "source"          : self.source.to_record(),
            "destination"     : self.destination.to_record(),
            "options"         : self.options.to_record(),
            "structure"       : self.structure.to_record(),
            "name_mappings"   : [m.to_record() for m in self.name_mappings],
            "coordinates"     : self.coordinates.to_record(),
            "verification"    : self.verification.to_record(),
            "validation"      : self.validation.to_record(),
            "retention"       : self.retention,
            "environment"     : self.environment.to_record(),
            "warnings"        : [w.to_record() for w in self.warnings],
            "elapsed_seconds" : self.elapsed_seconds,
        }

    def canonical_form(self) -> dict[str, Any]:

        '''

        The deterministic, nondeterminism-excluded projection of
        :meth:`to_record` for golden hashing (design KD8). Drops the fields
        that vary per run / per machine / per native-library version —
        ``ncarnate_version``, ``elapsed_seconds``, ``environment``, the
        absolute source/destination **paths**, the output ``size_bytes`` /
        ``sha256`` (HDF5-version-dependent), and ``verification``'s
        ``verifier_version`` (ncarnate's own version, which moves every
        release) — while keeping the structural content that is
        deterministic for a fixture at a fixed schema version. The v2 fields
        are both deterministic and therefore **kept**: ``plan_hash`` (derived
        only from operation + options + source identity — all canonical-form
        fields) and ``retention`` (always ``null`` from ncarnate, so the
        golden pins that ncarnate never fills the caller's slot — KD-S2).
        ``structure``
        is kept in full; a Zarr-relevant residual (library-default chunking
        on a *contiguous* source can vary by HDF5 version) is pinned by
        using an explicitly-chunked golden fixture (design §Risks). Note that
        ``structure`` carries native-byte-order-tagged dtype fields
        (``Variable.dtype`` / ``endian``, numeric ``Attribute.storage_type``),
        so the canonical form is stable on a **fixed-endianness** host (the
        little-endian CI/dev target) — a big-endian host would tag the same
        values differently.

        '''

        record = self.to_record()

        record.pop("ncarnate_version", None)
        record.pop("elapsed_seconds", None)
        record.pop("environment", None)

        # Absolute paths are machine-specific; keep the deterministic
        # identity fields.
        record["source"] = {
            "detected_format": self.source.detected_format,
            "size_bytes"     : self.source.size_bytes,
            "sha256"         : self.source.sha256,
        }

        # The output digest and size depend on the HDF5 library version, not
        # the result shape — keep only the container format.
        record["destination"] = {
            "container_format": self.destination.container_format,
        }

        # verifier_version == ncarnate's version, which moves every release;
        # the shape lives in status / verifier / method.
        record["verification"] = {
            "status"  : self.verification.status,
            "verifier": self.verification.verifier,
            "method"  : self.verification.method,
        }

        return record


def canonical_json(result : OperationResult) -> str:

    '''

    The deterministic byte serialization of ``result.canonical_form()``:
    sorted keys, tight separators, ``ensure_ascii=False``,
    ``allow_nan=False`` (non-finite floats are already tokenized to strings
    by :func:`json_safe`). Stable across runs and across same-endianness
    machines for a fixed fixture + schema version (see
    :meth:`OperationResult.canonical_form` on the byte-order residual) — the
    exact function step 5's golden-hash test pins.

    '''

    return json.dumps(
        result.canonical_form(),
        sort_keys   = True,
        separators  = (",", ":"),
        ensure_ascii = False,
        allow_nan   = False,
    )
