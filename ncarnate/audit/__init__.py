#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

The read-only ``ncarnate audit`` subpackage: recursive discovery,
metadata-only inspection, per-file classification into a stable status
taxonomy, and the versioned migration-manifest record schema.

This module is the public API (``audit_path``, ``AuditOptions``) and the
``ncarnate audit`` CLI entry (``main``), which the ``ncarnate/cli.py``
pre-dispatch shim routes to. At this (increment-1 scaffold) depth the
classifier emits only ``already_modern`` / ``unknown`` / ``unsafe``; the
full taxonomy and metadata inspection arrive in increment 2.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

from __future__ import annotations

# Standard library imports.
import argparse
import datetime
import os

# Local application imports.
from ncarnate.cli import _configure_logging, _get_files
from ncarnate.constants import PACKAGE_NAME
from ncarnate.constants import __version__
from ncarnate.errors import NcarnateError
from ncarnate.formats import FileFormat, detect_format
from ncarnate.audit.models import AuditOptions, AuditReport, AuditResult
from ncarnate.audit.report import render_summary

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


def _scaffold_status(file_format : FileFormat) -> str:

    '''

    The increment-1 classifier: modern netCDF3/HDF5 is ``already_modern``;
    everything else (legacy HDF4, not yet inspectable at this depth, and
    unrecognized formats) reads as ``unknown``. Increment 2 refines HDF4
    into the full taxonomy (``ready`` / ``unsupported`` / ``malformed`` …).

    '''

    if file_format in (FileFormat.NETCDF3, FileFormat.HDF5):

        return "already_modern"

    return "unknown"


def _audit_file(
    file_path : str, root : str, options : AuditOptions, audited_at : str
) -> AuditResult:

    file_format = detect_format(file_path)

    return AuditResult(
        root       = root,
        path       = os.path.relpath(file_path, root),
        size_bytes = os.path.getsize(file_path),
        format     = file_format.name,
        status     = _scaffold_status(file_format),
        mode       = options.mode,
        audited_at = audited_at,
        sha256     = None,          # opt-in --checksum arrives in increment 3
        structures = [],            # metadata inspection arrives in increment 2
        issues     = [],
        plan       = None,
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

    return parser


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

    except NcarnateError as error:

        logger.error(str(error))

        return 2

    print(render_summary(report))

    return 0
