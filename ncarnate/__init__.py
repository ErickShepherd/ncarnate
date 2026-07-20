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
from ncarnate.errors import HandoffError
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
from ncarnate.handoff import check_materializable
from ncarnate.handoff import load_handoff_schema
from ncarnate.handoff import materializability_error
from ncarnate.handoff import schema_errors
from ncarnate.handoff import validate_handoff
from ncarnate.result import OPERATION_RESULT_SCHEMA_VERSION
from ncarnate.result import OperationResult
from ncarnate.result import canonical_json
from ncarnate.stage import Plan
from ncarnate.stage import execute
from ncarnate.stage import execute_batch
from ncarnate.stage import inspect
from ncarnate.stage import plan

__all__ = [
    "recompress",
    "audit_path",
    "AuditOptions",
    "convert_manifest",
    "ConvertOptions",
    "inspect",
    "plan",
    "execute",
    "execute_batch",
    "Plan",
    "OperationResult",
    "OPERATION_RESULT_SCHEMA_VERSION",
    "canonical_json",
    "validate_handoff",
    "check_materializable",
    "materializability_error",
    "schema_errors",
    "load_handoff_schema",
    "detect_format",
    "FileFormat",
    "HandoffError",
    "NcarnateError",
    "UnsupportedFormatError",
    "UnsupportedTypeError",
    "VerificationError",
    "__author__",
    "__version__",
]
