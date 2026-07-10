"""Opt-in per-file checksums (design §One record schema).

``--checksum sha256`` records a per-file hash so a manifest can be *executed*
(the consumer re-verifies sha256 before touching data); it is off by default
because hashing a terabyte archive is not free. So ``sha256`` is present iff
``--checksum sha256``, and ``null`` otherwise.

RED until the paired [impl] lands hashing in ``_audit_file`` (the scaffold
records ``sha256=None`` unconditionally).
"""

import hashlib

from ncarnate.audit import AuditOptions, audit_path

from conftest import NETCDF_FIXTURES, stage


def _audit_one(workdir, checksum):
    src = stage(NETCDF_FIXTURES[0], workdir)
    report = audit_path(
        str(workdir),
        AuditOptions(recursive=False, mode="metadata", checksum=checksum),
    )
    return src, report.files[0]


def test_sha256_is_null_without_checksum(workdir):
    _, result = _audit_one(workdir, checksum=None)
    assert result.sha256 is None
    assert result.to_record()["sha256"] is None


def test_sha256_present_and_correct_with_checksum(workdir):
    src, result = _audit_one(workdir, checksum="sha256")
    expected = hashlib.sha256(src.read_bytes()).hexdigest()
    assert result.sha256 == expected
    assert result.to_record()["sha256"] == expected
