#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

Shared content-hashing utilities for ncarnate.

The chunked SHA-256 hasher lives here so both the read-only ``audit``
subpackage (which records a granule's ``sha256`` into the migration
manifest) and the ``convert`` subpackage (which re-verifies that hash
before touching a file) call **one** implementation rather than each
carrying a copy.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

from __future__ import annotations

# Standard library imports.
import hashlib


def sha256_of_file(file_path : str) -> str:

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
