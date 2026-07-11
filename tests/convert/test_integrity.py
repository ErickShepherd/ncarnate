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
import os
import shutil

import pytest

from conftest import NETCDF_FIXTURES, stage

from ncarnate.audit.codes import RULESET_VERSION
from ncarnate.audit.models import SCHEMA_VERSION
from ncarnate.errors import NcarnateError
from ncarnate.hashing import sha256_of_file

from ncarnate.convert import ConvertOptions, convert_manifest
from ncarnate.convert.reader import read_manifest
from ncarnate.convert.integrity import (
    ContainmentError,
    IntegrityError,
    resolve_within,
    verify_sha256,
)


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


def test_non_string_sha256_is_a_clean_integrity_error(workdir):
    """A hostile manifest recording a non-string sha256 (e.g. an int) is
    refused as a clean IntegrityError, not a bare TypeError on the `[:12]`
    slice that only the run-survival belt would catch."""
    staged = stage(NETCDF_FIXTURES[0], workdir)
    record = {
        "schema_version": SCHEMA_VERSION, "ncarnate_version": "0.0.0",
        "ruleset_version": RULESET_VERSION, "mode": "metadata",
        "audited_at": "2026-01-01T00:00:00Z", "root": str(workdir),
        "path": staged.name, "size_bytes": staged.stat().st_size,
        "sha256": 12345,                       # non-string (malformed/hostile)
        "format": "HDF5", "status": "ready", "structures": [], "issues": [],
        "plan": {"operation": "recompress"},
    }
    manifest = workdir / "m.jsonl"
    manifest.write_text(json.dumps(record) + "\n", encoding="utf-8")
    parsed = read_manifest(str(manifest))[0]

    with pytest.raises(IntegrityError):
        verify_sha256(parsed, str(staged))


def test_matching_hash_passes(workdir):
    """Positive control: an untampered file whose hash matches passes."""
    record, staged = _staged_record(workdir, record_true_hash=True)
    # No exception ⇒ the gate accepts the untampered, hash-matching file.
    assert verify_sha256(record, str(staged)) is None


def test_tampered_file_is_rejected_by_the_gate(workdir):
    """NEGATIVE: a file changed since the audit fails the sha256 gate. (That a
    failed gate produces no conversion is a convert_manifest-level property —
    asserted in test_convert_manifest; verify_sha256 never writes, so an
    out_dir assertion here would be vacuous.)"""
    record, staged = _staged_record(workdir, record_true_hash=True)

    # Tamper: the recorded hash no longer describes the bytes on disk.
    with open(staged, "ab") as stream:
        stream.write(b"\x00tampered")

    with pytest.raises(IntegrityError):
        verify_sha256(record, str(staged))


def test_null_hash_refused_without_override(workdir):
    """NEGATIVE: a record with no recorded hash is refused by default."""
    record, staged = _staged_record(workdir, record_true_hash=False)
    with pytest.raises(IntegrityError):
        verify_sha256(record, str(staged))


def test_null_hash_allowed_with_override(workdir):
    """--allow-unverified opts a null-hash record past the gate (KD2)."""
    record, staged = _staged_record(workdir, record_true_hash=False)
    assert verify_sha256(record, str(staged), allow_unverified=True) is None


# --- path containment (traversal + absolute) — §Risks ------------------
#
# The manifest is untrusted data that becomes a filesystem read/write path.
# A crafted record.path could redirect a read or land an output outside the
# tree; the sha256 gate does NOT help here (an attacker who authors the
# manifest also authors record.sha256). Containment is the sole defense and
# is required on BOTH the resolved source (under root) and output (under
# --out-dir). These use hand-authored traversal manifests as real inputs.


def _record_with_path(workdir, path):
    """Write a one-record manifest carrying an arbitrary (hostile) ``path``
    and parse it back through the real reader — the traversal string is a
    real constructed input, not a mock."""
    record = {
        "schema_version": SCHEMA_VERSION,
        "ncarnate_version": "0.0.0",
        "ruleset_version": RULESET_VERSION,
        "mode": "metadata",
        "audited_at": "2026-01-01T00:00:00Z",
        "root": str(workdir),
        "path": path,
        "size_bytes": 1,
        "sha256": "ab" * 32,
        "format": "HDF5",
        "status": "ready",
        "structures": [],
        "issues": [],
        "plan": {"operation": "recompress"},
    }
    manifest = workdir / "m.jsonl"
    manifest.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return read_manifest(str(manifest))[0]


def test_containment_error_is_ncarnate_error():
    """A containment rejection is an NcarnateError so the CLI catches it."""
    assert issubclass(ContainmentError, NcarnateError)


def test_relative_path_resolves_within_base(workdir):
    """Positive control: an ordinary relative path resolves under the base."""
    base = workdir / "root"
    base.mkdir()
    resolved = resolve_within(str(base), "sub/granule.hdf")
    real_base = os.path.realpath(str(base))
    assert os.path.commonpath([os.path.realpath(resolved), real_base]) == real_base


def test_dotdot_escaping_path_is_rejected_under_root(workdir):
    """NEGATIVE: a `..`-escaping source path is rejected (read redirection)."""
    root = workdir / "root"
    root.mkdir()
    record = _record_with_path(workdir, "../../etc/passwd")
    with pytest.raises(ContainmentError):
        resolve_within(str(root), record.path)


def test_dotdot_escaping_path_is_rejected_under_out_dir(workdir):
    """NEGATIVE: the same containment applies to the output base (write)."""
    out_dir = workdir / "out"
    out_dir.mkdir()
    record = _record_with_path(workdir, "../../etc/passwd")
    with pytest.raises(ContainmentError):
        resolve_within(str(out_dir), record.path)


def test_absolute_path_is_rejected_under_root(workdir):
    """NEGATIVE: an absolute source path is rejected outright."""
    root = workdir / "root"
    root.mkdir()
    record = _record_with_path(workdir, "/etc/passwd")
    with pytest.raises(ContainmentError):
        resolve_within(str(root), record.path)


def test_absolute_path_is_rejected_under_out_dir(workdir):
    """NEGATIVE: an absolute path is rejected against the output base too."""
    out_dir = workdir / "out"
    out_dir.mkdir()
    record = _record_with_path(workdir, "/etc/passwd")
    with pytest.raises(ContainmentError):
        resolve_within(str(out_dir), record.path)


# --- read-base trust: --root vs the untrusted manifest root ------------

def test_manifest_mode_refuses_untrusted_root_by_default(workdir):
    """SECURITY: with neither --root nor --allow-manifest-root, convert refuses
    to trust the manifest's own recorded root as the containment base — the run
    stops (ContainmentError) before any file is read or converted."""
    _staged_record(workdir, record_true_hash=True)      # writes workdir/m.jsonl
    out_dir = workdir / "out"
    with pytest.raises(ContainmentError):
        convert_manifest(str(workdir / "m.jsonl"),
                         ConvertOptions(out_dir=str(out_dir)))
    assert not out_dir.exists() or not list(out_dir.iterdir())


def test_allow_manifest_root_opts_into_trusting_recorded_root(workdir):
    """The explicit opt-in restores using the manifest's recorded root as the
    base (today's behavior, now behind a flag)."""
    _staged_record(workdir, record_true_hash=True)
    out_dir = workdir / "out"
    result = convert_manifest(
        str(workdir / "m.jsonl"),
        ConvertOptions(out_dir=str(out_dir), allow_manifest_root=True),
    )
    assert result.converted and not result.failed


def test_root_flag_anchors_reads_and_neutralises_manifest_root(workdir):
    """SECURITY: --root supplies the base, so a hostile record.root pointing
    elsewhere is ignored. The granule exists only under --root (not under the
    recorded root), so a successful conversion proves the base was --root — had
    record.root been trusted, the source would not resolve."""
    archive = workdir / "archive"
    archive.mkdir()
    staged = archive / "g.nc"
    shutil.copyfile(NETCDF_FIXTURES[0], staged)

    record = {
        "schema_version": SCHEMA_VERSION, "ncarnate_version": "0.0.0",
        "ruleset_version": RULESET_VERSION, "mode": "metadata",
        "audited_at": "2026-01-01T00:00:00Z",
        "root": "/nonexistent/hostile/root",            # ignored when --root given
        "path": "g.nc", "size_bytes": staged.stat().st_size,
        "sha256": sha256_of_file(str(staged)),
        "format": "HDF5", "status": "ready", "structures": [], "issues": [],
        "plan": {"operation": "recompress"},
    }
    manifest = workdir / "m.jsonl"
    manifest.write_text(json.dumps(record) + "\n", encoding="utf-8")

    out_dir = workdir / "out"
    result = convert_manifest(
        str(manifest),
        ConvertOptions(out_dir=str(out_dir), root=str(archive)),
    )
    assert result.converted and not result.failed
    assert (out_dir / "g.nc").is_file()
