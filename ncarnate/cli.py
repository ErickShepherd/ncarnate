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

                for root, subdirectories, subfiles in os.walk(path):

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

    return files


def _build_argument_parser() -> argparse.ArgumentParser:

    parser = argparse.ArgumentParser(
        prog        = PACKAGE_NAME,
        description = "Losslessly rewrites netCDF4/HDF5 files with "
                      "different compression settings."
    )

    parser.add_argument(
        "path",
        type  = str,
        nargs = "+",
        help  = "The path(s) to the file(s) to alter."
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
                 "(only after the copy verifies lossless)."
    )

    group.add_argument(
        "--no-overwrite",
        dest   = "overwrite",
        action = "store_false",
        help   = "Keeps the source file; writes the recompressed copy "
                 "alongside it with a '_recompressed' suffix."
    )

    parser.set_defaults(overwrite = True)

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

    logger    = logging.getLogger(PACKAGE_NAME)
    handler   = logging.StreamHandler()
    formatter = logging.Formatter("%(levelname)s: %(message)s")

    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)

    return logger


def main() -> int:

    parser = _build_argument_parser()
    args   = parser.parse_args()
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

    for file in tqdm(files, desc = "Files recompressed"):

        try:

            recompress(
                file,
                zlib      = args.zlib,
                shuffle   = args.shuffle,
                complevel = args.complevel,
                overwrite = args.overwrite
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
