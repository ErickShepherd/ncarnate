#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

The migration-manifest reader and step-1 compatibility check (design §The
per-record loop). :func:`read_manifest` parses the audit's JSONL manifest
back into :class:`ManifestRecord` objects and enforces the version contract
before any conversion is attempted:

* a ``schema_version`` that differs from the consumer's ``SCHEMA_VERSION``
  **hard-refuses the whole run** (:class:`ManifestCompatError`) — the record
  shape may differ, so nothing downstream can be trusted;
* a ``ruleset_version`` mismatch only means the *predictions* may be stale,
  which the sha256 gate and ``recompress``'s own fail-loud predicates
  (steps 3-4) already defend against — so it **warns once and proceeds**
  (KD5).

The version constants are imported from the audit models/codes — the single
source of truth — never re-declared here.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

from __future__ import annotations

# Standard library imports.
import json
import logging
from dataclasses import dataclass

# Local application imports.
from ncarnate.audit.codes import RULESET_VERSION
from ncarnate.audit.models import SCHEMA_VERSION
from ncarnate.constants import PACKAGE_NAME
from ncarnate.errors import NcarnateError


class ManifestCompatError(NcarnateError):

    '''

    Raised when a manifest record's ``schema_version`` does not match the
    consumer's ``SCHEMA_VERSION``. The record shape may differ, so the whole
    run is refused rather than risk misreading a field.

    '''


class MalformedManifestError(NcarnateError):

    '''

    Raised when a manifest line is not valid JSON, is not a JSON object, or
    omits a required field. The manifest is untrusted input, so a bad line is
    a clean, named refusal of the whole run (exit 2) — never an uncaught
    ``JSONDecodeError``/``KeyError`` traceback.

    '''


@dataclass
class ManifestRecord:

    '''

    One consumer-side manifest record — the fields the convert loop reads
    from a frozen v1 record, by attribute. This is deliberately *not* the
    audit's ``AuditResult`` (which carries no version fields on the instance
    and has no reverse-parse path); it mirrors only what conversion needs.

    '''

    schema_version  : int
    ruleset_version : int
    root            : str
    path            : str
    format          : str
    status          : str
    sha256          : str | None = None
    size_bytes      : int | None = None
    plan            : dict | None = None

    @classmethod
    def from_record(cls, record : dict) -> "ManifestRecord":

        '''Build a record from one parsed JSONL object.'''

        return cls(
            schema_version  = record["schema_version"],
            ruleset_version = record["ruleset_version"],
            root            = record["root"],
            path            = record["path"],
            format          = record["format"],
            status          = record["status"],
            sha256          = record.get("sha256"),
            size_bytes      = record.get("size_bytes"),
            plan            = record.get("plan"),
        )


def read_manifest(path : str) -> list[ManifestRecord]:

    '''

    Parse the JSONL manifest at ``path`` into records, enforcing the step-1
    compatibility check. Raises :class:`ManifestCompatError` on a
    ``schema_version`` mismatch (refusing the whole run); warns once on a
    ``ruleset_version`` mismatch and proceeds.

    '''

    logger        = logging.getLogger(PACKAGE_NAME)
    records        = []
    ruleset_warned = False

    with open(path, encoding="utf-8") as stream:

        for line_number, line in enumerate(stream, start=1):

            line = line.strip()

            if not line:

                continue

            # The manifest is untrusted input; a non-JSON line, a non-object
            # line, or a missing required field is a clean named refusal of
            # the whole run, never an uncaught JSONDecodeError/KeyError.
            try:

                raw = json.loads(line)

            except json.JSONDecodeError as error:

                raise MalformedManifestError(
                    f"manifest line {line_number} is not valid JSON: {error}"
                ) from error

            if not isinstance(raw, dict):

                raise MalformedManifestError(
                    f"manifest line {line_number} is a JSON "
                    f"{type(raw).__name__}, not an object"
                )

            if raw.get("schema_version") != SCHEMA_VERSION:

                raise ManifestCompatError(
                    f"manifest schema_version {raw.get('schema_version')!r} "
                    f"does not match this ncarnate's SCHEMA_VERSION "
                    f"{SCHEMA_VERSION!r}; the record shape may differ — "
                    f"re-audit with this version"
                )

            if raw.get("ruleset_version") != RULESET_VERSION and not ruleset_warned:

                logger.warning(
                    "manifest produced under ruleset v%s; classification "
                    "semantics have changed (consumer ruleset v%s) — re-audit "
                    "recommended", raw.get("ruleset_version"), RULESET_VERSION,
                )
                ruleset_warned = True

            try:

                records.append(ManifestRecord.from_record(raw))

            except KeyError as error:

                raise MalformedManifestError(
                    f"manifest line {line_number} is missing required field "
                    f"{error}"
                ) from error

    return records
