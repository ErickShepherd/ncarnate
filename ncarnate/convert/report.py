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
from ncarnate.errors import NcarnateError


def render_refusal(error : NcarnateError) -> str:

    '''

    Renders a whole-run refusal (e.g. the destination preflight's
    :class:`~ncarnate.convert.preflight.DestinationCollisionError`,
    KD-L1/KD-L2) as operator-facing text: the stable registry code in a
    ``[CODE]`` prefix, then the message listing every involved source and
    the contested destination. The code is rendered *textually* — not just
    carried on the exception — because the CLI surface is what operators
    script against (grep stderr, branch on exit code 2). An error with no
    ``code`` renders as its message alone, unchanged.

    '''

    if getattr(error, "code", None):

        return f"[{error.code}] {error}"

    return str(error)


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

            lines.append(f"  {record.path!r} — {record.reason}")

    return "\n".join(lines)
