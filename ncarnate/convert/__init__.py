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

# Local application imports.
from ncarnate.convert.models import (
    ConvertOptions,
    ConvertRecord,
    ConvertResult,
)

__all__ = [
    "ConvertOptions",
    "ConvertRecord",
    "ConvertResult",
]
