#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

The command line interface for the ncarnate package, which allows users to
alter the compression of a supported netCDF or HDF file.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

# Standard library imports.
import argparse
import logging
import os
import sys

# Third party imports.
from tqdm import tqdm

# Local application imports.
from ncarnate.constants import PACKAGE_NAME
from ncarnate.constants import SUPPORTED_EXTENSIONS
from ncarnate.constants import __version__
from ncarnate.core import recompress
from ncarnate.errors import NcarnateError


def _has_supported_extension(path : str) -> bool:

    extension = os.path.splitext(path)[1].lower().lstrip(".")

    return extension in SUPPORTED_EXTENSIONS


def _files_to_paths(root : str, files : list[str]) -> list[str]:

    paths = [os.path.join(root, file) for file in files]

    return paths


def _log_walk_error(error : OSError) -> None:

    '''

    ``os.walk`` swallows directory-enumeration errors by default, silently
    dropping an unreadable subtree from the scan. For an auditor that is
    invisible data loss, so surface it as a warning (the files are still
    omitted — this only makes the omission visible, never fatal).

    '''

    logging.getLogger(PACKAGE_NAME).warning(
        "Skipping unreadable directory during scan: %s", error
    )


def _get_files(paths : list[str], recursive : bool) -> list[str]:

    '''

    Expands the given paths into the list of files to process. Directories
    are scanned (recursively with ``recursive``) for supported extensions;
    explicitly named files must exist and carry a supported extension.

    '''

    paths = [os.path.abspath(path) for path in paths]
    files = []

    for path in paths:

        if os.path.isdir(path):

            if recursive:

                for root, subdirectories, subfiles in os.walk(
                    path, onerror = _log_walk_error
                ):

                    subfiles    = _files_to_paths(root, subfiles)
                    valid_files = filter(_has_supported_extension, subfiles)

                    files += sorted(valid_files)

            else:

                subfiles    = _files_to_paths(path, os.listdir(path))
                subfiles    = filter(os.path.isfile, subfiles)
                valid_files = filter(_has_supported_extension, subfiles)

                files += sorted(valid_files)

        elif os.path.isfile(path):

            if not _has_supported_extension(path):

                raise NcarnateError(
                    f"Unsupported file extension: {path} (supported: "
                    f"{', '.join(sorted(SUPPORTED_EXTENSIONS))})"
                )

            files += [path]

        else:

            raise NcarnateError(f"No such file or directory: {path}")

    # De-duplicate while preserving order: overlapping arguments (the same
    # file twice, a directory plus a file inside it, or overlapping trees
    # under -r) would otherwise recompress the same path more than once.
    # Paths are already absolute, so equal files compare equal.
    return list(dict.fromkeys(files))


def _build_argument_parser() -> argparse.ArgumentParser:

    parser = argparse.ArgumentParser(
        prog        = PACKAGE_NAME,
        description = "Converts HDF4/HDF-EOS2 granules (MODIS, AMSR-E) "
                      "to CF-annotated netCDF4, and losslessly rewrites "
                      "netCDF4/HDF5 files with different compression "
                      "settings."
    )

    parser.add_argument(
        "path",
        type  = str,
        nargs = "+",
        help  = "The path(s) to the file(s) to convert or recompress."
    )

    parser.add_argument(
        "--complevel",
        type    = int,
        default = 7,
        choices = list(range(10)),
        help    = "The desired gzip deflate compression level."
    )

    group = parser.add_mutually_exclusive_group(required = False)

    group.add_argument(
        "--zlib",
        dest   = "zlib",
        action = "store_true",
        help   = "Enables zlib gzip compression."
    )

    group.add_argument(
        "--no-zlib",
        dest   = "zlib",
        action = "store_false",
        help   = "Disables zlib gzip compression."
    )

    parser.set_defaults(zlib = True)

    group = parser.add_mutually_exclusive_group(required = False)

    group.add_argument(
        "--shuffle",
        dest   = "shuffle",
        action = "store_true",
        help   = "Enables the HDF5 shuffle filter."
    )

    group.add_argument(
        "--no-shuffle",
        dest   = "shuffle",
        action = "store_false",
        help   = "Disables the HDF5 shuffle filter."
    )

    parser.set_defaults(shuffle = True)

    group = parser.add_mutually_exclusive_group(required = False)

    group.add_argument(
        "--overwrite",
        dest   = "overwrite",
        action = "store_true",
        help   = "Replaces each source file with its recompressed copy "
                 "(only after the copy verifies lossless). Applies to "
                 "recompression only; HDF4 conversion always writes a "
                 "new .nc file beside the source."
    )

    group.add_argument(
        "--no-overwrite",
        dest   = "overwrite",
        action = "store_false",
        help   = "Keeps the source file; writes the recompressed copy "
                 "alongside it with a '_recompressed' suffix. Applies to "
                 "recompression only; HDF4 conversion always writes a "
                 "new .nc file beside the source."
    )

    parser.set_defaults(overwrite = True)

    parser.add_argument(
        "--no-geolocation",
        dest    = "geolocation",
        action  = "store_false",
        default = True,
        help    = "Converts HDF-EOS2 files SDS-only, skipping CF "
                  "geolocation reconstruction (the escape hatch for "
                  "unsupported projections/layouts)."
    )

    parser.add_argument(
        "-r",
        "--recursive",
        dest    = "recursive",
        action  = "store_true",
        default = False,
        help    = "Acts recursively on the given director(y/ies)."
    )

    parser.add_argument(
        "-V",
        "--version",
        action  = "version",
        version = f"{PACKAGE_NAME} {__version__}",
        help    = "Prints the current package version."
    )

    return parser


def _configure_logging() -> logging.Logger:

    logger = logging.getLogger(PACKAGE_NAME)

    # Idempotent: repeated main() calls in one process (e.g. under tests)
    # must not stack duplicate handlers.
    if not logger.handlers:

        handler   = logging.StreamHandler()
        formatter = logging.Formatter("%(levelname)s: %(message)s")

        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.setLevel(logging.WARNING)

    return logger


def main() -> int:

    # Pre-dispatch shim (design §CLI integration): the CLI is a flat
    # argparse command with no subparsers, so subcommands are dispatched
    # here, *before* argparse, to keep every existing `ncarnate <path>`
    # invocation working unchanged.
    argv = sys.argv[1:]

    if argv and argv[0] == "audit":

        # Imported lazily: ncarnate.audit imports cli._get_files, so a
        # top-level import here would be circular.
        from ncarnate.audit import main as audit_main

        return audit_main(argv[1:])

    if argv and argv[0] == "convert":

        # An explicit alias for today's flat behavior; strip the verb and
        # fall through to the legacy parser unchanged. Not deprecated.
        argv = argv[1:]

    parser = _build_argument_parser()
    args   = parser.parse_args(argv)
    logger = _configure_logging()

    try:

        files = _get_files(args.path, args.recursive)

    except NcarnateError as error:

        logger.error(str(error))

        return 2

    if not files:

        logger.error("No supported input files found.")

        return 2

    failures = 0

    for file in tqdm(files, desc = "Files processed"):

        try:

            recompress(
                file,
                zlib        = args.zlib,
                shuffle     = args.shuffle,
                complevel   = args.complevel,
                overwrite   = args.overwrite,
                geolocation = args.geolocation
            )

        except (NcarnateError, OSError) as error:

            logger.error("%s", error)

            failures += 1

        except Exception:

            logger.exception(
                "Unexpected error while recompressing the given file: %s",
                file
            )

            failures += 1

    if failures:

        logger.error(
            "%d of %d file(s) failed to recompress.", failures, len(files)
        )

        return 1

    return 0
