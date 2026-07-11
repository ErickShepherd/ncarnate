"""Manifest reader + compatibility-check tests (ncarnate.convert.reader).

The reader parses the audit's JSONL migration manifest back into per-record
objects and runs the design's step-1 compatibility check (§The per-record
loop): a ``schema_version`` mismatch **hard-refuses the whole run** (the
record shape may differ), while a ``ruleset_version`` mismatch **warns once
and proceeds** (KD5 — steps 3-4 still guarantee safety). Manifests are built
as real JSONL files on disk, not mocks. The consumer's expected versions are
imported from the audit constants (the single source of truth), so these
tests do not hardcode a version and survive a future bump.

``ncarnate.convert.reader`` does not exist yet; these fail until the paired
[impl] unit lands it.
"""

import json
import logging

import pytest

from ncarnate.audit.codes import RULESET_VERSION
from ncarnate.audit.models import SCHEMA_VERSION
from ncarnate.errors import NcarnateError

from ncarnate.convert.reader import (
    ManifestCompatError,
    ManifestRecord,
    read_manifest,
)


def _record(**overrides):
    """A faithful frozen-v1 record dict (the shape AuditResult.to_record emits)."""
    record = {
        "schema_version": SCHEMA_VERSION,
        "ncarnate_version": "0.0.0",
        "ruleset_version": RULESET_VERSION,
        "mode": "metadata",
        "audited_at": "2026-01-01T00:00:00Z",
        "root": "/data/archive",
        "path": "sub/granule.hdf",
        "size_bytes": 123,
        "sha256": "ab" * 32,
        "format": "HDF4",
        "status": "ready",
        "structures": [],
        "issues": [],
        "plan": {"operation": "convert"},
    }
    record.update(overrides)
    return record


def _write_manifest(path, records):
    """Write a list of record dicts as JSONL (one object per line)."""
    with open(path, "w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record) + "\n")
    return str(path)


# --- happy path: parse to records --------------------------------------

def test_reads_jsonl_to_records(tmp_path):
    """A well-formed manifest parses to one ManifestRecord per line."""
    manifest = _write_manifest(
        tmp_path / "m.jsonl",
        [_record(path="a.hdf"), _record(path="b.nc", format="HDF5",
                                        status="already_modern")],
    )
    records = read_manifest(manifest)
    assert len(records) == 2
    assert all(isinstance(r, ManifestRecord) for r in records)


def test_record_exposes_converter_fields(tmp_path):
    """Each record exposes the fields the convert loop reads, by attribute."""
    manifest = _write_manifest(tmp_path / "m.jsonl", [_record()])
    record = read_manifest(manifest)[0]
    assert record.schema_version == SCHEMA_VERSION
    assert record.ruleset_version == RULESET_VERSION
    assert record.root == "/data/archive"
    assert record.path == "sub/granule.hdf"
    assert record.sha256 == "ab" * 32
    assert record.format == "HDF4"
    assert record.status == "ready"
    assert record.plan is not None


def test_null_sha256_is_carried_through(tmp_path):
    """A null recorded hash survives the read (the gate refuses it later)."""
    manifest = _write_manifest(tmp_path / "m.jsonl", [_record(sha256=None)])
    assert read_manifest(manifest)[0].sha256 is None


# --- schema mismatch: hard-refuse the whole run ------------------------

def test_schema_mismatch_hard_refuses(tmp_path):
    """A record under a different schema_version refuses the whole run."""
    manifest = _write_manifest(
        tmp_path / "m.jsonl",
        [_record(), _record(schema_version=SCHEMA_VERSION + 1)],
    )
    with pytest.raises(ManifestCompatError):
        read_manifest(manifest)


def test_schema_compat_error_is_ncarnate_error():
    """The refusal is an NcarnateError so the CLI catches it uniformly."""
    assert issubclass(ManifestCompatError, NcarnateError)


# --- ruleset mismatch: warn once and proceed (KD5) ---------------------

def test_ruleset_mismatch_warns_and_proceeds(tmp_path, caplog):
    """A stale ruleset warns but still yields records (safety via steps 3-4)."""
    manifest = _write_manifest(
        tmp_path / "m.jsonl",
        [_record(ruleset_version=RULESET_VERSION + 1)],
    )
    with caplog.at_level(logging.WARNING):
        records = read_manifest(manifest)
    assert len(records) == 1
    assert any("ruleset" in r.message.lower() for r in caplog.records)


def test_ruleset_mismatch_warns_only_once(tmp_path, caplog):
    """Many stale-ruleset records emit exactly one warning, not one each."""
    manifest = _write_manifest(
        tmp_path / "m.jsonl",
        [_record(ruleset_version=RULESET_VERSION + 1) for _ in range(3)],
    )
    with caplog.at_level(logging.WARNING):
        read_manifest(manifest)
    ruleset_warnings = [r for r in caplog.records if "ruleset" in r.message.lower()]
    assert len(ruleset_warnings) == 1
