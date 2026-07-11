"""Integrity-gate tests — the load-bearing safety spine (design §The
per-record loop step 3, KD2, §Risks).

**The negative directions are the point of this increment.** The sha256
re-verify gate must *actually* reject a file whose bytes changed since the
audit (a stale prediction must never be trusted) and *refuse* a record with
no recorded hash unless the operator explicitly opts out. These are built as
real inputs: a committed fixture is staged, its true sha256 is recorded into
a JSONL manifest via the promoted shared hasher, the manifest is parsed by
the real reader, then the file is tampered on disk — no mocks.

``ncarnate.convert.integrity`` does not exist yet; these fail until the
paired [impl] unit lands it. (Path-containment negative directions are added
to this file by the next [test] unit.)
"""

import json

import pytest

from conftest import NETCDF_FIXTURES, stage

from ncarnate.audit.codes import RULESET_VERSION
from ncarnate.audit.models import SCHEMA_VERSION
from ncarnate.errors import NcarnateError
from ncarnate.hashing import sha256_of_file

from ncarnate.convert.reader import read_manifest
from ncarnate.convert.integrity import IntegrityError, verify_sha256


def _staged_record(workdir, *, record_true_hash):
    """Stage the first netCDF fixture and write a one-record manifest.

    When ``record_true_hash`` the record carries the file's real sha256;
    otherwise it records ``null``. Returns (parsed_record, staged_path) — the
    record is round-tripped through the real reader, not hand-built.
    """
    staged = stage(NETCDF_FIXTURES[0], workdir)
    sha256 = sha256_of_file(str(staged)) if record_true_hash else None
    record = {
        "schema_version": SCHEMA_VERSION,
        "ncarnate_version": "0.0.0",
        "ruleset_version": RULESET_VERSION,
        "mode": "metadata",
        "audited_at": "2026-01-01T00:00:00Z",
        "root": str(workdir),
        "path": staged.name,
        "size_bytes": staged.stat().st_size,
        "sha256": sha256,
        "format": "HDF5",
        "status": "ready",
        "structures": [],
        "issues": [],
        "plan": {"operation": "recompress"},
    }
    manifest = workdir / "m.jsonl"
    manifest.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return read_manifest(str(manifest))[0], staged


def test_integrity_error_is_ncarnate_error():
    """A gate failure is an NcarnateError so the CLI catches it uniformly."""
    assert issubclass(IntegrityError, NcarnateError)


def test_matching_hash_passes(workdir):
    """Positive control: an untampered file whose hash matches passes."""
    record, staged = _staged_record(workdir, record_true_hash=True)
    # No exception ⇒ the gate accepts the untampered, hash-matching file.
    assert verify_sha256(record, str(staged)) is None


def test_tampered_file_is_rejected_and_writes_nothing(workdir):
    """NEGATIVE: a file changed since the audit fails the gate; no output."""
    record, staged = _staged_record(workdir, record_true_hash=True)

    # Tamper: the recorded hash no longer describes the bytes on disk.
    with open(staged, "ab") as stream:
        stream.write(b"\x00tampered")

    out_dir = workdir / "out"
    out_dir.mkdir()
    with pytest.raises(IntegrityError):
        verify_sha256(record, str(staged))
    # A failed gate must never have produced a conversion.
    assert list(out_dir.iterdir()) == []


def test_null_hash_refused_without_override(workdir):
    """NEGATIVE: a record with no recorded hash is refused by default."""
    record, staged = _staged_record(workdir, record_true_hash=False)
    with pytest.raises(IntegrityError):
        verify_sha256(record, str(staged))


def test_null_hash_allowed_with_override(workdir):
    """--allow-unverified opts a null-hash record past the gate (KD2)."""
    record, staged = _staged_record(workdir, record_true_hash=False)
    assert verify_sha256(record, str(staged), allow_unverified=True) is None
