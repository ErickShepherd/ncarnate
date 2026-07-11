"""The migration-manifest contract (design §Testing.2).

Three guarantees that make the manifest a *contract* archive managers can
script against forever:

1. every emitted record validates against the checked-in JSON Schema
   (``record.schema.json``);
2. codes are **append-only** — the test fails if any known v1 code
   disappears from the registry;
3. ``CSV rows == JSONL lines``.

Validation uses a tiny stdlib JSON-Schema-subset validator — no new
dependency (spec constraint). This is a contract guard over already-shipped
behavior (models / codes / report), not an ATDD-red test, so it is green on
authoring and guards the contract from here on.
"""

import csv
import io
import json
from pathlib import Path

from ncarnate.audit import AuditOptions, audit_path
from ncarnate.audit import codes
from ncarnate.audit.models import (
    AuditIssue, AuditResult, ConversionPlan, StructureAudit,
)
from ncarnate.audit.report import write_csv, write_jsonl

from conftest import NETCDF_FIXTURES, stage

SCHEMA = json.loads((Path(__file__).parent / "record.schema.json").read_text())

# The frozen v1 code snapshot. Append-only: a code may be added but never
# removed, so this set must always remain a subset of the live registry.
V1_CODES = {
    "EOS_UNSUPPORTED_PROJECTION",
    "EOS_STRUCTMETADATA_MALFORMED",
    "SWATH_DIMMAP_UNRESOLVED",
    "SWATH_GEOLOCATION_UNSUPPORTED",
    "NETCDF_NAME_COLLISION",
    "UNSUPPORTED_TYPE",
    "DECLARED_ALLOCATION_TOO_LARGE",
    "FORMAT_UNRECOGNIZED",
}


# --- a minimal stdlib JSON Schema validator (the subset the schema uses) --

_JSON_TYPES = {
    "object": dict, "array": list, "string": str, "null": type(None),
}


def _matches_type(instance, json_type):
    if json_type == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if json_type == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if json_type == "boolean":
        return isinstance(instance, bool)
    return isinstance(instance, _JSON_TYPES[json_type])


def _schema_errors(instance, schema, path="$"):
    errors = []

    if "type" in schema:
        types = schema["type"]
        types = [types] if isinstance(types, str) else types
        if not any(_matches_type(instance, t) for t in types):
            return [f"{path}: expected {types}, got {type(instance).__name__}"]

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}")

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} not in enum {schema['enum']}")

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required {key!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in properties:
                    errors.append(f"{path}: unexpected property {key!r}")
        for key, subschema in properties.items():
            if key in instance:
                errors += _schema_errors(instance[key], subschema, f"{path}.{key}")

    if isinstance(instance, list) and "items" in schema:
        for index, item in enumerate(instance):
            errors += _schema_errors(item, schema["items"], f"{path}[{index}]")

    return errors


def _validate(record):
    errors = _schema_errors(record, SCHEMA)
    assert not errors, "schema violations: " + "; ".join(errors)


def _rich_record():
    """A record exercising the nested structures/issues/plan sub-schemas."""
    result = AuditResult(
        root="/archive", path="g/snow.hdf", size_bytes=100, sha256="a" * 64,
        format="HDF4", status="ready_no_geolocation", mode="metadata",
        audited_at="2026-07-10T18:30:00Z",
        structures=[StructureAudit(
            type="GRID", name="G", projection={"gctp_code": 16},
            geolocation_plan={"method": "grid_affine"},
        )],
        issues=[AuditIssue(
            code=codes.EOS_UNSUPPORTED_PROJECTION, severity="blocker",
            message="m", context={"projection": 22},
        )],
        plan=ConversionPlan(
            operation="convert", geolocation_method=None, output_format="NETCDF4",
        ),
    )
    return result.to_record()


# --- 1. records validate against the schema ---------------------------

def test_emitted_records_validate_against_schema(workdir):
    for fixture in NETCDF_FIXTURES[:2]:
        stage(fixture, workdir)
    report = audit_path(
        str(workdir), AuditOptions(recursive=False, mode="metadata", checksum="sha256")
    )

    stream = io.StringIO()
    write_jsonl(report, stream)
    for line in stream.getvalue().splitlines():
        _validate(json.loads(line))


def test_rich_record_with_nested_objects_validates():
    _validate(_rich_record())


def test_schema_rejects_a_malformed_record():
    # A record missing `status` and carrying a stray key must not validate —
    # proving the schema is tight, not vacuous.
    bad = _rich_record()
    del bad["status"]
    bad["extra"] = 1
    assert _schema_errors(bad, SCHEMA)


# --- 2. codes are append-only -----------------------------------------

def test_codes_are_append_only():
    missing = V1_CODES - set(codes.ALL_CODES)
    assert not missing, f"append-only violated: codes removed: {missing}"


# --- 3. CSV rows == JSONL lines ---------------------------------------

def test_csv_rows_equal_jsonl_lines(workdir):
    for fixture in NETCDF_FIXTURES[:2]:
        stage(fixture, workdir)
    report = audit_path(
        str(workdir), AuditOptions(recursive=False, mode="metadata", checksum=None)
    )

    jsonl = io.StringIO()
    write_jsonl(report, jsonl)
    csv_stream = io.StringIO()
    write_csv(report, csv_stream)

    jsonl_lines = [line for line in jsonl.getvalue().splitlines() if line.strip()]
    csv_rows = list(csv.DictReader(io.StringIO(csv_stream.getvalue())))
    assert len(csv_rows) == len(jsonl_lines)
    # Not just an equal count: the same files, so a CSV writer that emitted the
    # right number of rows with wrong paths would still fail the contract.
    assert sorted(row["path"] for row in csv_rows) \
        == sorted(json.loads(line)["path"] for line in jsonl_lines)
