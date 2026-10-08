#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

The whole-manifest destination preflight (readiness action 1, KD-L1/KD-L2):
before any directory or output file is created, every selected record's
source is containment-resolved, sha256-verified, and byte-detected, and its
normalized destination computed — under the output root in ``--out-dir``
mode, or as the derived ``<source-stem>.nc`` sibling for an HDF4
``--in-place`` conversion (F1; a netCDF ``--in-place`` replacement writes
over its own source and has no separate destination to collide). Any
collision — duplicate or case-fold-equivalent destinations, a destination
aliasing a selected source, source-tree/output-tree overlap, duplicate
actionable source records, or a pre-existing destination without the resume
policy — refuses the **entire selected run** with the stable
``DESTINATION_COLLISION`` code listing every involved source and the
contested destination. No last-writer-wins, no auto-rename, no partial
proceed.

The destination suffix follows the source's **detected bytes**, never its
declared ``record.format`` — the manifest is untrusted input, and a false
declaration must not steer an output path (readiness action 1 step 2).

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

from __future__ import annotations

# Standard library imports.
import os

# Local application imports.
from ncarnate.destinations import DestinationCollisionError, validate_destinations
from ncarnate.errors import NcarnateError
from ncarnate.formats import FileFormat, detect_format
from ncarnate.convert.integrity import resolve_within, verify_sha256
from ncarnate.convert.models import ConvertRecord

__all__ = [
    "DestinationCollisionError",
    "preflight_destinations",
]


def _output_relpath(record, detected : FileFormat) -> str:

    '''

    The mirrored output path for a record, from its **detected** format: an
    HDF4/HDF-EOS2 source's extension is swapped to ``.nc`` (a conversion),
    anything else keeps its name (a recompressed copy). The declared
    ``record.format`` never drives the suffix — a manifest lying about the
    format must not steer the destination (readiness action 1 step 2).

    '''

    if detected is FileFormat.HDF4:

        return os.path.splitext(record.path)[0] + ".nc"

    return record.path


def _overlapping(tree_a : str, tree_b : str) -> bool:

    '''

    True when one realpath'd tree contains (or equals) the other.

    '''

    try:

        common = os.path.commonpath([tree_a, tree_b])

    except ValueError:

        # Different drives/anchors cannot overlap.
        return False

    return common in (tree_a, tree_b)


def preflight_destinations(
    records, options
) -> "tuple[list[tuple[object, str, str | None, FileFormat]], list[ConvertRecord]]":

    '''

    Resolve, verify, and plan every actionable record before anything is
    written. Returns ``(plans, failed)``: ``plans`` is one
    ``(record, resolved_source, destination, detected_format)`` tuple per
    convertible record (``destination`` is ``None`` only for a netCDF/HDF5
    ``--in-place`` replacement; an HDF4 ``--in-place`` record carries its
    derived ``<source-stem>.nc`` sibling so it joins the collision checks,
    F1; ``detected_format`` is the byte-detected :class:`FileFormat`, so the
    convert loop can gate capability refusals before any directory is
    created); ``failed`` holds the per-record resolution/verification
    failures, preserving the loop's one-bad-file-never-aborts-the-run
    isolation (§The per-record loop). Raises
    :class:`DestinationCollisionError` — refusing the entire run — on any
    cross-record collision (KD-L1/KD-L2).

    '''

    plans  = []
    failed = []

    for record in records:

        try:

            source = resolve_within(options.root or record.root, record.path)
            # Hash first, then detect from the now-trusted bytes (readiness
            # action 1 step 2). NB: the hash is taken here and the convert
            # loop re-opens the path later — the design's accepted TOCTOU
            # residual (§Risks), unchanged by the preflight.
            verify_sha256(
                record, source, allow_unverified=options.allow_unverified
            )
            detected = detect_format(source)
            if detected is FileFormat.UNKNOWN:
                raise NcarnateError(
                    f"{source} is not a recognized scientific container",
                    code="FORMAT_UNRECOGNIZED",
                )

            if options.in_place:

                # --in-place is not uniformly "no destination" (F1). A
                # netCDF/HDF5 source is genuinely replaced at its own path
                # after a verified write (KD3), so it has no separate output
                # to collide. But an HDF4/HDF-EOS2 source is a *conversion*:
                # recompress derives a <source-stem>.nc sibling beside the
                # source and never touches the HDF4 original. That derived
                # sibling is a real output, so it must take part in the
                # whole-run collision checks exactly as a mirrored out-dir
                # destination does — otherwise two HDF4 sources deriving one
                # .nc partially execute instead of refusing (violating G1's
                # zero-mutation rule). `source` is already realpath'd
                # (resolve_within), matching recompress's own realpath-based
                # derivation, so the checks below dedup on the true path.
                if detected is FileFormat.HDF4:

                    destination = os.path.splitext(source)[0] + ".nc"

                else:

                    destination = None

            else:

                destination = resolve_within(
                    options.out_dir, _output_relpath(record, detected)
                )

        # A record that cannot be resolved, verified, or detected is a
        # per-record failure, never a run abort — the same isolation the
        # convert loop applies (§The per-record loop). Its destination is
        # unknowable (untrusted bytes), so it takes no part in the
        # collision checks.
        except (NcarnateError, OSError) as error:

            failed.append(ConvertRecord(
                record.path, reason=str(error),
                code=getattr(error, "code", None),
            ))
            continue

        plans.append((record, source, destination, detected))

    problems = []
    if plans and not options.in_place:
        out_real = os.path.realpath(options.out_dir)
        for base in {os.path.realpath(options.root or record.root)
                     for record, _, _, _ in plans}:
            if _overlapping(base, out_real):
                affected = ", ".join(record.path for record, _, _, _ in plans)
                problems.append(f"output tree {out_real} overlaps source tree {base}: {affected}")
    validate_destinations(
        [(source, destination, record.path) for record, source, destination, _ in plans],
        allow_existing=options.skip_existing, problems=problems,
    )
    return plans, failed
