#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

Terminal summary rendering for an audit run. The JSONL/CSV record writers
(the migration-manifest contract) arrive in increment 3; this module holds
the human-facing summary the CLI prints after an audit.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

from __future__ import annotations

# Local application imports.
from ncarnate.audit.models import AuditReport


def _human_bytes(count : int) -> str:

    '''

    A compact human-readable byte size (1024-based), e.g. ``3.2 MiB``.

    '''

    size  = float(count)
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]

    for unit in units:

        if size < 1024.0 or unit == units[-1]:

            # Whole bytes read cleaner without a decimal.
            if unit == "B":

                return f"{int(size)} {unit}"

            return f"{size:.1f} {unit}"

        size /= 1024.0


def _percent(part : int, whole : int) -> float:

    return (100.0 * part / whole) if whole else 0.0


def render_summary(report : AuditReport) -> str:

    '''

    Renders the audit summary as text: the total files/bytes audited and a
    per-status breakdown reporting each status's share **by files and by
    bytes** (design §CLI integration).

    '''

    summary = report.summary
    lines   = [
        f"Audited {summary.total_files} file(s), "
        f"{_human_bytes(summary.total_bytes)} under {report.root}",
        f"Mode: {report.mode}",
    ]

    if summary.total_files:

        lines.append("Readiness by status (files | bytes):")

        # Rank statuses by affected bytes — the executive-artifact detail.
        for status in sorted(
            summary.files_by_status,
            key = lambda s: summary.bytes_by_status.get(s, 0),
            reverse = True,
        ):

            file_count = summary.files_by_status[status]
            byte_count = summary.bytes_by_status.get(status, 0)

            lines.append(
                f"  {status:<16} "
                f"{file_count:>6} "
                f"({_percent(file_count, summary.total_files):5.1f}%) | "
                f"{_human_bytes(byte_count):>10} "
                f"({_percent(byte_count, summary.total_bytes):5.1f}%)"
            )

    return "\n".join(lines)
