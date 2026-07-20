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
from ncarnate.audit.codes import DESTINATION_COLLISION
from ncarnate.errors import NcarnateError
from ncarnate.formats import FileFormat, detect_format
from ncarnate.convert.integrity import resolve_within, verify_sha256
from ncarnate.convert.models import ConvertRecord

__all__ = [
    "DestinationCollisionError",
    "preflight_destinations",
]


class DestinationCollisionError(NcarnateError):

    '''

    Raised when the destination preflight finds any collision among a
    manifest run's computed outputs. The whole selected run is refused
    before any directory or output is created (KD-L1); the message lists
    every involved source and the contested destination, and ``code`` is
    the stable ``DESTINATION_COLLISION`` registry string (KD-L2).

    '''


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

    # Duplicate actionable source records (readiness action 1 step 6): two
    # selected records resolving to one file — realpath'd, so symlinked
    # duplicates collide too — would double-convert it.
    by_source = {}

    for record, source, _, _ in plans:

        by_source.setdefault(source, []).append(record.path)

    for source, paths in sorted(by_source.items()):

        if len(paths) > 1:

            problems.append(
                f"duplicate records for source {source}: {', '.join(paths)}"
            )

    # Destination-based collision checks run over every plan with a real
    # output path — mirrored out-dir destinations and, under --in-place,
    # HDF4 derived .nc siblings alike (F1). A netCDF --in-place replacement
    # has destination None (a genuine in-place rewrite, no separate output)
    # and takes no part in these checks.
    dest_plans = [
        (record, source, destination, detected)
        for record, source, destination, detected in plans
        if destination is not None
    ]

    if plans and not options.in_place:

        out_real = os.path.realpath(options.out_dir)
        selected = ", ".join(record.path for record, _, _, _ in plans)

        # Source-tree/output-tree overlap (step 5): an output root inside
        # the source tree (or vice versa, or symlink-aliased to it) makes
        # outputs indistinguishable from sources. Out-dir mode only — an
        # --in-place run has no separate output tree to overlap; its derived
        # siblings sit inside the source tree by design (KD3), and their
        # data-loss shapes are caught by the destination checks below.
        for base in sorted({
            os.path.realpath(options.root or record.root)
            for record, _, _, _ in plans
        }):

            if _overlapping(base, out_real):

                problems.append(
                    f"output tree {out_real} overlaps source tree {base}; "
                    f"selected sources: {selected}"
                )

    if dest_plans:

        # Duplicate or case-fold-equivalent destinations (step 4): exact
        # duplicates lose data everywhere; case-fold equivalents lose it on
        # case-insensitive filesystems (NTFS/APFS), so both are refused on
        # every platform.
        by_destination = {}

        for record, _, destination, _ in dest_plans:

            by_destination.setdefault(
                destination.casefold(), []
            ).append((record.path, destination))

        for _, group in sorted(by_destination.items()):

            if len(group) > 1:

                destinations = " / ".join(sorted({d for _, d in group}))
                sources      = ", ".join(path for path, _ in group)
                problems.append(
                    f"destination {destinations} claimed by: {sources}"
                )

        # A destination aliasing a selected source (step 5): both sides are
        # realpath'd, so a symlinked out_dir pointing back into the source
        # tree — or an HDF4 --in-place derived .nc that lands on a selected
        # .nc source — collides here rather than silently overwriting it.
        sources_real = {source for _, source, _, _ in plans}

        for record, _, destination, _ in dest_plans:

            if destination in sources_real:

                problems.append(
                    f"destination {destination} aliases selected source "
                    f"{record.path}"
                )

        # A pre-existing destination (step 7) is refused unless the operator
        # selected the resume policy (--skip-existing, which skips it in the
        # convert loop instead). Presence-only for now; the verified resume
        # journal is readiness action 12, out of this loop's scope.
        if not options.skip_existing:

            for record, _, destination, _ in dest_plans:

                if os.path.lexists(destination):

                    problems.append(
                        f"destination {destination} already exists (source "
                        f"{record.path}); pass --skip-existing to resume"
                    )

    if problems:

        raise DestinationCollisionError(
            "destination preflight refused the entire run (no outputs were "
            "written):\n  " + "\n  ".join(problems),
            code=DESTINATION_COLLISION,
        )

    return plans, failed
