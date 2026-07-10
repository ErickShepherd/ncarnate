#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

The read-only ``ncarnate audit`` subpackage: recursive discovery,
metadata-only inspection, per-file classification into a stable status
taxonomy, and the versioned migration-manifest record schema.

This module is the public API (``audit_path``, ``AuditOptions``) and the
``ncarnate audit`` CLI entry (``main``), which the ``ncarnate/cli.py``
pre-dispatch shim routes to. ``audit_path`` drives the metadata-only
inspection (``inspect.py``) and predicate classification (``classify.py``)
engine, emitting the full status taxonomy, named issues, structures, and a
conversion plan per file.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

from __future__ import annotations

# Standard library imports.
import argparse
import datetime
import hashlib
import logging
import os

# Third party imports.
from pyhdf.error import HDF4Error

# Local application imports.
from ncarnate.cli import _configure_logging, _get_files
from ncarnate.constants import PACKAGE_NAME
from ncarnate.constants import __version__
from ncarnate.errors import NcarnateError
from ncarnate.formats import FileFormat, detect_format
from ncarnate.audit import codes
from ncarnate.audit.classify import classify, issue_for_exception, status_for
from ncarnate.audit.inspect import FileFacts, inspect_file
from ncarnate.audit.models import (
    AuditIssue,
    AuditOptions,
    AuditReport,
    AuditResult,
    ConversionPlan,
)
from ncarnate.audit.report import render_summary, write_csv, write_jsonl

__all__ = ["audit_path", "AuditOptions", "main"]


def _discover(path : str, recursive : bool) -> tuple[str, list[str]]:

    '''

    Resolves ``path`` to an absolute audit root and the files under it.

    A directory reuses ``cli._get_files`` enumeration (recursion + extension
    filtering). An explicitly-named file is **always** included, even with
    an unsupported extension — the audit counts and classifies it (as
    ``unknown``) rather than skipping or rejecting it (design §CLI
    integration).

    '''

    abspath = os.path.abspath(path)

    if os.path.isdir(abspath):

        return abspath, _get_files([abspath], recursive)

    if os.path.isfile(abspath):

        return os.path.dirname(abspath), [abspath]

    raise NcarnateError(f"No such file or directory: {path}")


def _plan_for(status : str) -> "ConversionPlan | None":

    '''

    The manifest's conversion plan, derived from the predicted status. Only
    the operation is fixed at metadata depth; the exact geolocation
    reconstruction method is depth-limited (finalised by ``--mode sample``)
    and left ``None`` except where the status itself says geolocation is
    skipped. A blocking status has no safe operation, so its plan is ``None``.

    '''

    if status == "already_modern":

        return ConversionPlan(operation="recompress")

    if status == "ready":

        return ConversionPlan(operation="convert")

    if status == "ready_no_geolocation":

        return ConversionPlan(operation="convert", geolocation_method="none")

    return None


def _inspect_and_classify(
    file_path : str, file_format : FileFormat
) -> "tuple[str, list, list, ConversionPlan | None]":

    '''

    Runs the metadata inspection + predicate classification engine for one
    file, returning ``(status, structures, issues, plan)``. Inspection can
    fail two ways, both caught per-file so one bad granule never aborts a
    whole-archive scan (an auditor's core job is surveying messy archives):

    * a converter *predicate* raises (e.g. a malformed ``StructMetadata`` →
      ``EosParseError``) — mapped to its registry code; and
    * the container itself is unreadable — the magic bytes matched a science
      format but the file is truncated/corrupt (``OSError`` from
      ``netCDF4.Dataset``, ``HDF4Error`` from ``pyhdf``) — mapped to
      ``MALFORMED_CONTAINER`` (status ``malformed``).

    '''

    try:

        facts = inspect_file(file_path)

    except NcarnateError as error:

        return _blocked_record(file_format, issue_for_exception(error))

    except (OSError, HDF4Error) as error:

        return _blocked_record(
            file_format,
            _unreadable_issue(f"Unreadable {file_format.name} container: {error}"),
        )

    status, issues = classify(facts)

    return status, facts.structures, issues, _plan_for(status)


def _unreadable_issue(message : str) -> AuditIssue:

    '''

    A ``MALFORMED_CONTAINER`` blocker issue for a file that could not be read
    (a corrupt/unreadable container, an I/O error, or an unexpected reader
    failure). The status folds to ``malformed``.

    '''

    return AuditIssue(
        code     = codes.MALFORMED_CONTAINER,
        severity = "blocker",
        message  = message,
        context  = {},
    )


def _blocked_record(
    file_format : FileFormat, issue : AuditIssue
) -> "tuple[str, list, list, ConversionPlan | None]":

    '''

    The ``(status, structures, issues, plan)`` tuple for a file that could not
    be inspected: a single blocker issue, no structures, no conversion plan.
    The status is folded from the issue's code (e.g. ``malformed``).

    '''

    facts = FileFacts(format=file_format.name, already_modern=False)

    return status_for(facts, [issue]), [], [issue], None


def _sha256(file_path : str) -> str:

    '''

    The file's SHA-256, read in chunks so a terabyte granule never lands in
    memory. This reads the raw file bytes (for the manifest's integrity
    check), never the decoded science arrays.

    '''

    hasher = hashlib.sha256()

    with open(file_path, "rb") as stream:

        for chunk in iter(lambda: stream.read(1 << 20), b""):

            hasher.update(chunk)

    return hasher.hexdigest()


def _audit_file(
    file_path : str, root : str, options : AuditOptions, audited_at : str
) -> AuditResult:

    relpath = os.path.relpath(file_path, root)

    # Every filesystem touch for this file is guarded, so a single unreadable
    # granule — permission denied, removed mid-scan, a dangling symlink, a
    # corrupt container, or any I/O error — becomes a `malformed` record and
    # the whole-archive scan continues instead of aborting. `detect_format`,
    # `getsize`, and `_sha256` all do file I/O that can raise `OSError` before
    # inspection is even reached, so they sit inside the guard too (not only
    # `_inspect_and_classify`, which guards the inspection itself).
    try:

        file_format = detect_format(file_path)
        size_bytes  = os.path.getsize(file_path)
        sha256      = (_sha256(file_path) if options.checksum == "sha256"
                       else None)
        status, structures, issues, plan = _inspect_and_classify(
            file_path, file_format
        )
        format_name = file_format.name

    except (OSError, HDF4Error) as error:

        status, structures, issues, plan = _blocked_record(
            FileFormat.UNKNOWN, _unreadable_issue(f"Unreadable file: {error}")
        )
        format_name = FileFormat.UNKNOWN.name
        size_bytes  = 0
        sha256      = None

    except Exception as error:  # noqa: BLE001 — deliberate scan-survival belt

        # Belt-and-braces, mirroring the convert path's outer guard
        # (`cli.main`): a pathological-but-openable file could raise something
        # unexpected from a reader library. Log the traceback so a genuine bug
        # stays visible, then record the file `malformed` so one surprise never
        # aborts a whole-archive scan.
        logging.getLogger(PACKAGE_NAME).exception(
            "Unexpected error auditing %s; recording it malformed", file_path
        )
        status, structures, issues, plan = _blocked_record(
            FileFormat.UNKNOWN,
            _unreadable_issue(f"Unexpected audit error: {error}"),
        )
        format_name = FileFormat.UNKNOWN.name
        size_bytes  = 0
        sha256      = None

    return AuditResult(
        root       = root,
        path       = relpath,
        size_bytes = size_bytes,
        format     = format_name,
        status     = status,
        mode       = options.mode,
        audited_at = audited_at,
        sha256     = sha256,
        structures = structures,
        issues     = issues,
        plan       = plan,
    )


def audit_path(path : str, options : AuditOptions) -> AuditReport:

    '''

    Audits ``path`` (a file or an archive root) read-only and returns an
    :class:`~ncarnate.audit.models.AuditReport`. Never opens science arrays,
    never touches the network, never writes to audited files.

    '''

    root, files = _discover(path, options.recursive)

    audited_at = datetime.datetime.now(
        datetime.timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    results = [_audit_file(f, root, options, audited_at) for f in files]

    return AuditReport(root=root, mode=options.mode, files=results)


def _build_audit_parser() -> argparse.ArgumentParser:

    parser = argparse.ArgumentParser(
        prog        = f"{PACKAGE_NAME} audit",
        description = "Read-only assessment of an archive: discovery, format "
                      "detection, per-file readiness classification, and a "
                      "readiness summary. Never modifies the audited files."
    )

    parser.add_argument(
        "path",
        type = str,
        help = "The archive root (or single file) to audit."
    )

    group = parser.add_mutually_exclusive_group(required = False)

    group.add_argument(
        "-r", "--recursive",
        dest   = "recursive",
        action = "store_true",
        help   = "Recurse into subdirectories (the default)."
    )

    group.add_argument(
        "--no-recursive",
        dest   = "recursive",
        action = "store_false",
        help   = "Audit only the top level of the given directory."
    )

    # Read-only, so recursion is safe to default on: auditing an archive is
    # inherently a whole-tree scan.
    parser.set_defaults(recursive = True)

    parser.add_argument(
        "--checksum",
        choices = ["sha256"],
        default = None,
        help    = "Also record a per-file hash (opt-in; off by default "
                  "because hashing a terabyte archive is not free)."
    )

    parser.add_argument(
        "--mode",
        choices = ["metadata"],
        default = "metadata",
        help    = "Audit depth (only 'metadata' in this release)."
    )

    parser.add_argument(
        "-V", "--version",
        action  = "version",
        version = f"{PACKAGE_NAME} {__version__}"
    )

    parser.add_argument(
        "--output",
        type    = str,
        default = None,
        help    = "Write the per-file manifest to this path (format inferred "
                  "from the extension: '.csv' for the flat CSV view, else the "
                  "JSONL contract). JSONL is byte-for-byte what "
                  "'ncarnate convert --manifest' consumes."
    )

    return parser


def _write_manifest(report : AuditReport, path : str) -> None:

    writer = write_csv if path.lower().endswith(".csv") else write_jsonl

    with open(path, "w", newline="") as stream:

        writer(report, stream)


def main(argv : list[str]) -> int:

    '''

    The ``ncarnate audit`` CLI entry, invoked by the ``cli.py`` shim with the
    arguments following ``audit``. Audit is informational: an empty result
    is a successful (exit 0) run, not an error.

    '''

    parser = _build_audit_parser()
    args   = parser.parse_args(argv)
    logger = _configure_logging()

    options = AuditOptions(
        recursive = args.recursive,
        mode      = args.mode,
        checksum  = args.checksum,
    )

    try:

        report = audit_path(args.path, options)

        if args.output:

            _write_manifest(report, args.output)

    except (NcarnateError, OSError) as error:

        # NcarnateError: a bad target (no such path, etc.). OSError: a run-level
        # I/O failure the per-file guard cannot reach — discovery at the root
        # (e.g. a permission-denied directory in --no-recursive mode) or writing
        # the manifest to an unwritable --output path. Degrade to a clean error
        # exit, never a traceback.
        logger.error("%s", error)

        return 2

    print(render_summary(report))

    return 0
