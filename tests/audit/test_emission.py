"""JSONL/CSV emission (design §One record schema).

The per-file JSONL output IS the migration manifest: every line is one file
record of the v1 shape, with **no header and no trailer**. CSV is a flat
projection of the same records (one row per file), so CSV data rows equal
JSONL lines. ``ncarnate audit --output <file.jsonl>`` writes the manifest.

RED until the paired [impl] report.py writers + --output land.

Emission API this [test] item fixes (reversible/internal — recorded):
  write_jsonl(report, stream)  -> one json.dumps(record)+"\n" per file
  write_csv(report, stream)    -> header row + one flat data row per file
  audit --output <path.jsonl>  -> writes the JSONL manifest (format by ext)
"""

import csv
import io
import json

from ncarnate.audit import AuditOptions, audit_path
from ncarnate.audit import main as audit_main
from ncarnate.audit.models import AuditIssue, AuditReport, AuditResult
from ncarnate.audit.report import write_csv, write_jsonl

from conftest import NETCDF_FIXTURES, stage

MANIFEST_KEYS = {
    "schema_version", "ncarnate_version", "ruleset_version", "mode",
    "audited_at", "root", "path", "size_bytes", "sha256", "format",
    "status", "structures", "issues", "plan",
}


def _staged_report(workdir):
    for fixture in NETCDF_FIXTURES[:2]:
        stage(fixture, workdir)
    return audit_path(
        str(workdir), AuditOptions(recursive=False, mode="metadata", checksum=None)
    )


def _nonempty_lines(text):
    return [line for line in text.splitlines() if line.strip()]


# --- JSONL: one v1 record per line, no header/trailer -----------------

def test_jsonl_is_one_record_per_file(workdir):
    report = _staged_report(workdir)

    stream = io.StringIO()
    write_jsonl(report, stream)
    lines = _nonempty_lines(stream.getvalue())

    # No header, no trailer: exactly one line per audited file.
    assert len(lines) == len(report.files)

    for line in lines:
        record = json.loads(line)                       # each line valid JSON
        assert set(record) == MANIFEST_KEYS             # the v1 shape

    emitted = {json.loads(line)["path"] for line in lines}
    assert emitted == {f.path for f in report.files}


# --- CSV: a flat projection; CSV rows == JSONL lines ------------------

def test_csv_is_a_flat_projection_with_row_parity(workdir):
    report = _staged_report(workdir)

    jsonl_stream = io.StringIO()
    write_jsonl(report, jsonl_stream)
    csv_stream = io.StringIO()
    write_csv(report, csv_stream)

    jsonl_lines = _nonempty_lines(jsonl_stream.getvalue())
    rows = list(csv.DictReader(io.StringIO(csv_stream.getvalue())))

    # CSV data rows == JSONL lines (one row per file).
    assert len(rows) == len(jsonl_lines)

    # A flat projection: essential triage columns are present, and the
    # nested `structures` object is not a CSV column.
    assert {"path", "status"} <= set(rows[0].keys())
    assert "structures" not in rows[0]
    assert {row["path"] for row in rows} == {f.path for f in report.files}


# --- --output writes the JSONL manifest -------------------------------

def test_output_flag_writes_jsonl_manifest(workdir):
    for fixture in NETCDF_FIXTURES[:2]:
        stage(fixture, workdir)

    out = workdir / "audit.jsonl"
    exit_code = audit_main([str(workdir), "--output", str(out)])

    assert exit_code == 0
    lines = _nonempty_lines(out.read_text())
    assert len(lines) == 2
    for line in lines:
        assert set(json.loads(line)) == MANIFEST_KEYS


# --- CSV formula-injection guard (CWE-1236) ---------------------------

def test_csv_neutralises_formula_injection_in_free_text_cells():
    # A crafted archive filename / blocker message beginning with a formula
    # trigger must render as inert text in a spreadsheet, not execute.
    result = AuditResult(
        root="=cmd|'/c calc'!A1",
        path="@SUM(1+1)*payload.hdf",
        size_bytes=1,
        format="HDF4",
        status="unsupported",
        mode="metadata",
        audited_at="2026-07-10T00:00:00Z",
        issues=[AuditIssue(
            code="UNSUPPORTED_TYPE", severity="blocker",
            message="=HYPERLINK(evil)", context={},
        )],
    )
    report = AuditReport(root=result.root, mode="metadata", files=[result])

    stream = io.StringIO()
    write_csv(report, stream)
    row = next(csv.DictReader(io.StringIO(stream.getvalue())))

    for cell in (row["root"], row["path"], row["top_blocker_message"]):
        assert cell[0] == "'", f"formula cell not guarded: {cell!r}"
    # The fixed-registry code cell is a known-safe value, left as-is.
    assert row["top_blocker_code"] == "UNSUPPORTED_TYPE"
