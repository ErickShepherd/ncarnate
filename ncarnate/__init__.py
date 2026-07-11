#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

ncarnate: reincarnate legacy scientific data files as recompressed netCDF4.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

# Local application imports.
from ncarnate.constants import __author__
from ncarnate.constants import __version__
from ncarnate.core import recompress
from ncarnate.errors import NcarnateError
from ncarnate.errors import UnsupportedFormatError
from ncarnate.errors import UnsupportedTypeError
from ncarnate.errors import VerificationError
from ncarnate.formats import FileFormat
from ncarnate.formats import detect_format
from ncarnate.audit import AuditOptions
from ncarnate.audit import audit_path
from ncarnate.convert import ConvertOptions
from ncarnate.convert import convert_manifest

__all__ = [
    "recompress",
    "audit_path",
    "AuditOptions",
    "convert_manifest",
    "ConvertOptions",
    "detect_format",
    "FileFormat",
    "NcarnateError",
    "UnsupportedFormatError",
    "UnsupportedTypeError",
    "VerificationError",
    "__author__",
    "__version__",
]
