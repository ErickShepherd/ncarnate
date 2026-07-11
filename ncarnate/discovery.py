#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

Shared entry-point plumbing: input-file discovery and logging setup, used
by all three ncarnate entry points (``ncarnate``, ``ncarnate audit``,
``ncarnate convert``).

These helpers were extracted from :mod:`ncarnate.cli` to break the
``cli`` ↔ ``audit`` / ``cli`` ↔ ``convert`` import cycle: ``cli`` dispatches
to (imports) the ``audit`` and ``convert`` subpackages, while those
subpackages need the file-enumeration and logging helpers ``cli`` used to
own — a cycle previously survived only by lazy in-function imports. This
module depends on neither, so every entry point imports downward into it.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

# Standard library imports.
import logging
import os
import stat

# Local application imports.
from ncarnate.constants import PACKAGE_NAME
from ncarnate.constants import SUPPORTED_EXTENSIONS
from ncarnate.errors import NcarnateError


def _has_supported_extension(path : str) -> bool:

    extension = os.path.splitext(path)[1].lower().lstrip(".")

    return extension in SUPPORTED_EXTENSIONS


def _files_to_paths(root : str, files : list[str]) -> list[str]:

    paths = [os.path.join(root, file) for file in files]

    return paths


def _is_hang_safe(path : str) -> bool:

    '''

    True unless ``path`` is a file kind that would block a reader on open — a
    FIFO, socket, or character/block device. A regular file is kept, and so is
    a broken symlink or a vanished entry: those fail *fast* when a reader opens
    them (surfacing as ``malformed``), never hanging, so excluding them would
    only hide them from the audit. This is the seatbelt against a hostile
    ``data.hdf`` that is really a named pipe, whose ``open`` never returns.

    '''

    try:

        mode = os.stat(path, follow_symlinks=True).st_mode

    except OSError:

        # Broken symlink / race-vanished entry: not a hang risk — keep it so a
        # downstream reader surfaces it rather than silently dropping it.
        return True

    return not (stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)
                or stat.S_ISCHR(mode) or stat.S_ISBLK(mode))


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
                    # Drop only hang-prone special files (FIFO/device/socket):
                    # one named `*.hdf` would otherwise be handed downstream
                    # and block a reader forever on open. Broken symlinks and
                    # unreadable regulars are kept — they fail fast and surface
                    # as `malformed`, which is the audit's whole point.
                    subfiles    = filter(_is_hang_safe, subfiles)
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
