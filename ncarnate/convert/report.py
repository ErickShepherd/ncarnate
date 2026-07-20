#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

Terminal summary rendering for a manifest-driven convert run, symmetric with
:mod:`ncarnate.audit.report` (KD7): the CLI prints this after the per-record
loop finishes. It reports the converted / skipped / failed tallies and, for
every skip and failure, the record path and the reason — so a partial-failure
run on a terabyte-scale archive is legible at a glance (§The per-record loop).

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

from __future__ import annotations

# Local application imports.
from ncarnate.convert.models import ConvertResult


def render_summary(result : ConvertResult) -> str:

    '''

    Renders the convert summary as text: a one-line tally of converted /
    skipped / failed, followed by a per-record breakdown of the skipped and
    failed records with their reasons (the converted records need no reason).
    The exit code the CLI returns is :attr:`ConvertResult.exit_code`.

    '''

    lines = [
        f"Converted {len(result.converted)}, "
        f"skipped {len(result.skipped)}, "
        f"failed {len(result.failed)}."
    ]

    # record.path is attacker-controlled (a manifest field); render it with
    # !r so an embedded newline or ANSI escape can't forge summary lines or
    # manipulate the operator's terminal.
    if result.skipped:

        lines.append("Skipped:")

        for record in result.skipped:

            lines.append(f"  {record.path!r} — {record.reason}")

    if result.failed:

        lines.append("Failed:")

        for record in result.failed:

            # Render the stable refusal code as a [CODE] prefix when the
            # failure carried one (F2), so a manifest run's failures are as
            # scriptable as the one-file path's — grep the summary for
            # HDF4_RUNTIME_UNAVAILABLE / DESTINATION_COLLISION / etc. A
            # failure with no registered code renders as its reason alone,
            # unchanged.
            prefix = f"[{record.code}] " if record.code else ""
            lines.append(f"  {record.path!r} — {prefix}{record.reason}")

    return "\n".join(lines)
