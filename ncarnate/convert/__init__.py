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
try:

    from pyhdf.error import HDF4Error

except ImportError:

    # pyhdf absent (no Windows pip wheel — KD-L3): the convert stack must
    # still import for netCDF-only manifests. Without pyhdf no code can
    # *raise* HDF4Error, so a never-raised placeholder keeps the except
    # clause below verbatim.
    class HDF4Error(Exception):

        '''

        Placeholder for :class:`pyhdf.error.HDF4Error` when the HDF4
        runtime is unavailable; never raised.

        '''

# Local application imports.
from ncarnate.constants import PACKAGE_NAME
from ncarnate.core import recompress
from ncarnate.discovery import _configure_logging
from ncarnate.errors import NcarnateError, render_refusal
from ncarnate.formats import FileFormat
from ncarnate.hdf4_runtime import require_hdf4_runtime
from ncarnate.convert.integrity import ContainmentError
from ncarnate.convert.models import (
    ConvertOptions,
    ConvertRecord,
    ConvertResult,
)
from ncarnate.convert.preflight import (
    DestinationCollisionError,
    preflight_destinations,
)
from ncarnate.convert.reader import read_manifest
from ncarnate.convert.report import render_summary

__all__ = [
    "ConvertOptions",
    "ConvertRecord",
    "ConvertResult",
    "DestinationCollisionError",
    "convert_manifest",
    "main",
]


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

    Before anything is written, the whole selected run passes the
    destination preflight (:mod:`ncarnate.convert.preflight`, KD-L1/KD-L2):
    every destination is computed up front from the source's detected bytes,
    and any collision raises :class:`DestinationCollisionError` — refusing
    the entire run before any directory or output is created.

    '''

    # The read containment base must be operator-controlled. The manifest is
    # untrusted input, so its own recorded `root` is only used as the base when
    # the operator explicitly opts in (`allow_manifest_root`); otherwise
    # `--root` supplies the base. With neither, refuse the run rather than
    # silently trust an attacker-controllable base (a crafted `record.root`
    # could otherwise redirect reads outside the intended archive — the sha256
    # gate is no defense, since the attacker also authors the recorded hash;
    # design §Risks path-containment). Consistent with the tool's rule never to
    # auto-pick a security-critical default the operator didn't name.
    #
    # `not options.root` (not `is None`) so an *empty* --root fails closed too:
    # the base resolution below is `options.root or record.root`, which also
    # treats "" as falsy — guarding on `is None` would let `--root ""` (e.g. an
    # unset shell var) pass the guard and then silently fall back to the
    # untrusted record.root. The two checks must agree.
    if not options.root and not options.allow_manifest_root:

        raise ContainmentError(
            "manifest mode will not trust the manifest's own recorded root as "
            "the read base: pass --root <archive-root> to anchor reads to a "
            "directory you control, or --allow-manifest-root to explicitly "
            "trust the manifest's recorded root."
        )

    result  = ConvertResult()
    records = read_manifest(manifest_path)

    actionable = []

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

        actionable.append(record)

    # The whole-manifest destination preflight (KD-L1/KD-L2): resolve,
    # verify, and byte-detect every actionable source and compute every
    # destination BEFORE any directory or output exists; any collision
    # raises DestinationCollisionError, refusing the entire run. A record
    # that merely fails resolution/verification stays a per-record failure.
    # NB: verify_sha256 (in the preflight) and recompress (below) open
    # `source` by path in two non-atomic steps — a TOCTOU residual risk
    # under a hostile archive filesystem that can race the tree between the
    # two opens (design §Risks "TOCTOU"). Accepted for now; the full fix
    # (single-fd hash + convert) needs an fd-accepting recompress entry
    # point.
    plans, preflight_failed = preflight_destinations(actionable, options)
    result.failed.extend(preflight_failed)

    for record, source, destination, detected in plans:

        try:

            if destination is not None:

                # Resumability: a record whose mirrored output already
                # exists is skipped, not re-converted (§Output destination).
                # skip_existing is inert under in_place — there is no
                # computed out_dir path to test (KD3, §Output destination).
                # Checked before the runtime gate so a resumed run on a
                # runtime-less install still *skips* already-converted HDF4
                # records rather than failing them.
                if options.skip_existing and os.path.exists(destination):

                    result.skipped.append(ConvertRecord(
                        record.path,
                        reason="output already exists (--skip-existing)",
                    ))
                    continue

                # KD-L4 ordering: an HDF4 record on a runtime-less install
                # refuses here, before its mirrored directory is created —
                # still a per-record failure, but one that leaves the
                # output tree untouched (recompress would only raise the
                # same refusal after the makedirs below).
                if detected is FileFormat.HDF4:

                    require_hdf4_runtime()

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
            # %r: record.path is untrusted; keep newlines/escapes out of logs.
            logging.getLogger(PACKAGE_NAME).exception(
                "Unexpected error converting %r; recording it failed",
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
        # No prefix abbreviations: the cli pre-dispatch shim routes to this
        # parser only on the exact `--manifest` token, so allowing argparse to
        # accept `--man=…` here would make the two layers disagree (an
        # abbreviation the shim never routes but this parser would honor).
        allow_abbrev = False,
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
        help    = "The operator-controlled containment base a source path "
                  "resolves under (e.g. the archive's current location if it "
                  "moved since the audit). Required unless --allow-manifest-root.",
    )

    parser.add_argument(
        "--allow-manifest-root",
        dest   = "allow_manifest_root",
        action = "store_true",
        help   = "Trust the manifest's own recorded root as the read base "
                 "(only when --root is not given). Off by default: the manifest "
                 "is untrusted input, so a crafted root could redirect reads.",
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
        allow_manifest_root = args.allow_manifest_root,
        zlib             = args.zlib,
        shuffle          = args.shuffle,
        complevel        = args.complevel,
        geolocation      = args.geolocation,
    )

    try:

        result = convert_manifest(args.manifest, options)

    except NcarnateError as error:

        # A whole-run refusal (destination preflight, containment) renders
        # its stable registry code textually — [DESTINATION_COLLISION] … —
        # so operators can script against stderr, not just the exception
        # attribute the CLI boundary would otherwise swallow (KD-L2).
        logger.error("%s", render_refusal(error))

        return 2

    print(render_summary(result))

    return result.exit_code
