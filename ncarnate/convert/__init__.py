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
import argparse
import logging
import os

# Third party imports.
from pyhdf.error import HDF4Error

# Local application imports.
from ncarnate.constants import PACKAGE_NAME
from ncarnate.core import recompress
from ncarnate.discovery import _configure_logging
from ncarnate.errors import NcarnateError
from ncarnate.convert.integrity import resolve_within, verify_sha256
from ncarnate.convert.models import (
    ConvertOptions,
    ConvertRecord,
    ConvertResult,
)
from ncarnate.convert.reader import read_manifest
from ncarnate.convert.report import render_summary

__all__ = [
    "ConvertOptions",
    "ConvertRecord",
    "ConvertResult",
    "convert_manifest",
    "main",
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

            if options.in_place:

                # No mirrored tree: recompress replaces the netCDF source
                # where it sits (after its own verify-lossless step) and
                # writes an HDF4 conversion beside the source. skip_existing
                # is inert here — there is no computed out_dir path to test,
                # so resumability is an out_dir-mode-only guarantee (KD3,
                # §Output destination).
                destination = None

            else:

                destination = resolve_within(
                    options.out_dir, _output_relpath(record)
                )

                # Resumability: a record whose mirrored output already
                # exists is skipped, not re-converted (§Output destination).
                if options.skip_existing and os.path.exists(destination):

                    result.skipped.append(ConvertRecord(
                        record.path,
                        reason="output already exists (--skip-existing)",
                    ))
                    continue

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

        # OSError and pyhdf's HDF4Error (a direct Exception subclass)
        # alongside NcarnateError: a source deleted/unreadable between audit
        # and convert, a full disk, an unwritable out-dir, or a corrupt HDF4
        # container is a per-record failure on a long archive run, never a
        # run abort — the same discipline as `audit._audit_file`.
        except (NcarnateError, OSError, HDF4Error) as error:

            result.failed.append(ConvertRecord(record.path, reason=str(error)))

        except Exception as error:  # noqa: BLE001 — deliberate run-survival belt

            # Belt-and-braces, mirroring `audit._audit_file`'s scan-survival
            # belt: a pathological-but-openable file could raise something
            # unexpected from a reader library. Log the traceback so a genuine
            # bug stays visible, then record the failure so one surprise never
            # aborts a whole-archive migration.
            logging.getLogger(PACKAGE_NAME).exception(
                "Unexpected error converting %s; recording it failed",
                record.path,
            )
            result.failed.append(ConvertRecord(
                record.path, reason=f"Unexpected convert error: {error}",
            ))

    return result


def _build_convert_parser() -> argparse.ArgumentParser:

    '''

    The ``convert`` sub-parser (design §Invocation shape), symmetric with the
    ``audit`` parser (KD7). ``--manifest`` and the legacy positional
    ``path...`` are **mutually exclusive** (KD1); the encoding flags mirror the
    legacy parser so the two forms encode identically.

    '''

    parser = argparse.ArgumentParser(
        prog        = f"{PACKAGE_NAME} convert",
        description = "Execute an audit migration manifest: re-verify each "
                      "granule's recorded sha256, then convert exactly the "
                      "selected statuses into a mirrored output tree.",
    )

    # KD1: a run is driven by a manifest xor the legacy positional paths.
    source = parser.add_mutually_exclusive_group(required = False)

    source.add_argument(
        "--manifest",
        type = str,
        help = "A JSONL migration manifest (from `ncarnate audit`) to execute.",
    )

    source.add_argument(
        "path",
        nargs   = "*",
        default = [],
        help    = "Legacy positional form; mutually exclusive with --manifest.",
    )

    parser.add_argument(
        "--out-dir",
        dest = "out_dir",
        help = "The mirrored output root (required unless --in-place, KD3).",
    )

    parser.add_argument(
        "--status",
        default = "ready",
        help    = "Comma-separated audited statuses to convert "
                  "(default: ready).",
    )

    parser.add_argument(
        "--allow-unverified",
        dest   = "allow_unverified",
        action = "store_true",
        help   = "Convert a record whose recorded sha256 is null, relaxing "
                 "the mandatory integrity gate (KD2).",
    )

    parser.add_argument(
        "--in-place",
        dest   = "in_place",
        action = "store_true",
        help   = "Recompress sources where they sit instead of into a "
                 "mirrored tree (dangerous on an archive; KD3).",
    )

    parser.add_argument(
        "--skip-existing",
        dest   = "skip_existing",
        action = "store_true",
        help   = "Skip a record whose mirrored output already exists, making "
                 "an --out-dir run resumable.",
    )

    parser.add_argument(
        "--root",
        default = None,
        help    = "Override the containment base a source path resolves under "
                  "(defaults to each record's root).",
    )

    parser.add_argument(
        "--complevel",
        type    = int,
        default = 7,
        choices = list(range(10)),
        help    = "The desired gzip deflate compression level.",
    )

    zlib = parser.add_mutually_exclusive_group(required = False)
    zlib.add_argument("--zlib", dest = "zlib", action = "store_true",
                      help = "Enables zlib gzip compression.")
    zlib.add_argument("--no-zlib", dest = "zlib", action = "store_false",
                      help = "Disables zlib gzip compression.")
    parser.set_defaults(zlib = True)

    shuffle = parser.add_mutually_exclusive_group(required = False)
    shuffle.add_argument("--shuffle", dest = "shuffle", action = "store_true",
                         help = "Enables the HDF5 shuffle filter.")
    shuffle.add_argument("--no-shuffle", dest = "shuffle",
                         action = "store_false",
                         help = "Disables the HDF5 shuffle filter.")
    parser.set_defaults(shuffle = True)

    parser.add_argument(
        "--no-geolocation",
        dest    = "geolocation",
        action  = "store_false",
        default = True,
        help    = "Converts HDF-EOS2 files SDS-only, skipping CF geolocation "
                  "reconstruction.",
    )

    return parser


def main(argv : list[str]) -> int:

    '''

    The ``ncarnate convert --manifest`` entry point (design §Invocation
    shape), symmetric with :func:`ncarnate.audit.main`. Parses the convert
    sub-parser, builds a :class:`ConvertOptions` from the flags, executes the
    manifest, prints the run summary, and returns
    :attr:`ConvertResult.exit_code` (non-zero iff a selected record failed).

    Dispatched here by :func:`ncarnate.cli.main` when the ``convert`` verb
    carries ``--manifest``; the bare positional form falls through to the
    legacy flat parser instead, so it is never reached without a manifest.

    '''

    parser = _build_convert_parser()
    args   = parser.parse_args(argv)

    logger = _configure_logging()

    if not args.manifest:

        # Only reached if convert.main is invoked directly without a manifest;
        # cli.main routes the legacy positional form to the flat parser.
        parser.error("--manifest is required")

    if not args.out_dir and not args.in_place:

        parser.error("--out-dir is required in manifest mode (or use --in-place)")

    statuses = {token.strip() for token in args.status.split(",")
                if token.strip()}

    options = ConvertOptions(
        out_dir          = args.out_dir,
        statuses         = statuses,
        allow_unverified = args.allow_unverified,
        in_place         = args.in_place,
        skip_existing    = args.skip_existing,
        root             = args.root,
        zlib             = args.zlib,
        shuffle          = args.shuffle,
        complevel        = args.complevel,
        geolocation      = args.geolocation,
    )

    try:

        result = convert_manifest(args.manifest, options)

    except NcarnateError as error:

        logger.error("%s", error)

        return 2

    print(render_summary(result))

    return result.exit_code
