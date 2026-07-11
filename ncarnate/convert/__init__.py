#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

The ``ncarnate convert --manifest`` subpackage: executes the read-only
audit's JSONL migration manifest, re-verifying each granule's recorded
sha256 before converting exactly the statuses the operator selected into a
mirrored output tree.

Symmetric with :mod:`ncarnate.audit` (KD7): this ``__init__`` is the public
API and CLI entry, ``models`` holds the option/result dataclasses, and a
manifest reader module parses the JSONL records. The convert loop drives the
existing :func:`ncarnate.core.recompress` and re-verifies hashes with the
shared :func:`ncarnate.hashing.sha256_of_file`; it never duplicates them.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

from __future__ import annotations

# Standard library imports.
import os

# Local application imports.
from ncarnate.core import recompress
from ncarnate.errors import NcarnateError
from ncarnate.convert.integrity import resolve_within, verify_sha256
from ncarnate.convert.models import (
    ConvertOptions,
    ConvertRecord,
    ConvertResult,
)
from ncarnate.convert.reader import read_manifest

__all__ = [
    "ConvertOptions",
    "ConvertRecord",
    "ConvertResult",
    "convert_manifest",
]


def _output_relpath(record) -> str:

    '''

    The mirrored output path for a record: an HDF4/HDF-EOS2 source's
    extension is swapped to ``.nc`` (a conversion), a netCDF source's name is
    kept (a recompressed copy). The sha256 gate has already confirmed the
    bytes, so reading ``record.format`` for the swap is safe (§Output
    destination).

    '''

    if record.format == "HDF4":

        return os.path.splitext(record.path)[0] + ".nc"

    return record.path


def convert_manifest(
    manifest_path : str, options : ConvertOptions
) -> ConvertResult:

    '''

    Execute a migration manifest (design §The per-record loop). For each
    record whose ``status`` is selected, confine and re-verify the source,
    then drive :func:`ncarnate.core.recompress` into a mirrored ``out_dir``
    tree. Per-record failures are isolated — one bad file never aborts the
    run — and tallied into the returned :class:`ConvertResult`; a blocker is
    skipped with a counted reason and never converted (KD6). Sources are
    never mutated.

    '''

    result  = ConvertResult()
    records = read_manifest(manifest_path)

    for record in records:

        if record.status not in options.statuses:

            result.skipped.append(ConvertRecord(
                record.path, reason=f"status {record.status!r} not selected"
            ))
            continue

        # A blocker carries plan: null and is never actionable, even if its
        # status was explicitly named (KD6).
        if record.plan is None:

            result.skipped.append(ConvertRecord(
                record.path,
                reason=f"blocker (status {record.status!r}); never actionable",
            ))
            continue

        try:

            source = resolve_within(options.root or record.root, record.path)
            verify_sha256(
                record, source, allow_unverified=options.allow_unverified
            )

            destination = resolve_within(
                options.out_dir, _output_relpath(record)
            )
            os.makedirs(os.path.dirname(destination), exist_ok=True)

            # ready_no_geolocation forces SDS-only output — the audit
            # predicted geolocation is unsupported (KD4/§Per-status).
            geolocation = (options.geolocation
                           if record.status != "ready_no_geolocation"
                           else False)

            recompress(
                source, dst=destination, zlib=options.zlib,
                shuffle=options.shuffle, complevel=options.complevel,
                geolocation=geolocation,
            )
            result.converted.append(ConvertRecord(record.path))

        except NcarnateError as error:

            result.failed.append(ConvertRecord(record.path, reason=str(error)))

    return result
