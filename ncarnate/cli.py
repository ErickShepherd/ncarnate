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
from ncarnate.constants import LOG_FILE
from ncarnate.constants import PACKAGE_NAME
from ncarnate.constants import SUPPORTED_EXTENSIONS
from ncarnate.constants import __version__
from ncarnate.core import recompress


def _print_version() -> None:

    print(f"{PACKAGE_NAME} {__version__}")


def _validate_extension(path : str) -> bool:

    filename, file_extension = os.path.splitext(path)

    for extension in SUPPORTED_EXTENSIONS:

        if extension.lower() in file_extension:

            return True

    else:

        return False


def _files_to_paths(root : str, files : list) -> list:

    paths = [os.path.join(root, file) for file in files]

    return paths


def _get_files(paths : str, recursive : bool) -> int:

    paths = [os.path.abspath(path) for path in paths]
    files = []

    for path in paths:

        if os.path.isdir(path):

            if recursive:

                for root, subdirectories, subfiles in os.walk(path):

                    subfiles    = _files_to_paths(root, subfiles)
                    valid_files = list(filter(_validate_extension, subfiles))

                    files += valid_files

            else:

                subfiles    = _files_to_paths(path, os.listdir(path))
                subfiles    = list(filter(os.path.isfile, subfiles))
                valid_files = list(filter(_validate_extension, subfiles))

                files += valid_files

        elif os.path.isfile(path):

            if _validate_extension(path):

                files += [path]

        else:

            raise ValueError

    return files


def _build_argument_parser():

    parser = argparse.ArgumentParser(
        description = "Alters the compression of HDF5/netCDF4 files."
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

    parser.set_defaults(**{"zlib" : True})

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

    parser.set_defaults(**{"shuffle" : True})

    group = parser.add_mutually_exclusive_group(required = False)

    group.add_argument(
        "--overwrite",
        dest   = "overwrite",
        action = "store_true",
        help   = "Only the re/de-compressed file(s) is/are kept."
    )

    group.add_argument(
        "--no-overwrite",
        dest   = "overwrite",
        action = "store_false",
        help   = "Both the re/de-compressed and original file(s) is/are kept."
    )

    parser.set_defaults(**{"overwrite" : True})

    parser.add_argument(
        "-r",
        "--recursive",
        dest   = "recursive",
        action = "store_true",
        help   = "Acts recursively on the given director(y/ies)."
    )

    parser.set_defaults(**{"recursive" : False})

    parser.add_argument(
        "-V",
        "--version",
        action  = "store_true",
        help    = "Prints the current package version."
    )

    return parser


def _build_logger():

    datefmt = "%Y-%m-%d, %H:%M:%S"

    message  = "-" * 79 + "\n"
    message += "Timestamp:              %(asctime)s\n"
    message += "Level:                  %(levelname)s\n"
    message += "System argument vector: " + " ".join(sys.argv) + "\n\n"
    message += "Logged message:\n\n\t%(message)s\n\n"

    logger    = logging.getLogger(PACKAGE_NAME)
    handler   = logging.FileHandler(LOG_FILE)
    formatter = logging.Formatter(message, datefmt = datefmt)

    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)

    return logger


def main() -> None:

    parser    = _build_argument_parser()
    args      = parser.parse_args()
    kwargs    = vars(args)
    paths     = kwargs.pop("path")
    recursive = kwargs.pop("recursive")
    version   = kwargs.pop("version")
    logger    = _build_logger()
    files     = _get_files(paths, recursive)

    # TODO: Raises an error if path argument is not provided.
    if version:

        _print_version()

    for file in tqdm(files, desc = "Files re/de-compressed"):

        try:

            raise ValueError
            recompress(file, **kwargs)

        except Exception:

            message = (f"An error occurred while attempting to recompress the "
                       f"given file: {file}")

            logger.exception(message)


if __name__ == "__main__":

    main()
