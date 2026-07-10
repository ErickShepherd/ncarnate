"""Read-only guarantee (design §Testing.3): the product's central promise.

Auditing never modifies the files it audits — not their contents, not even
their mtimes, and it neither creates nor deletes files in the tree — in any
mode, including ``--checksum sha256`` (which reads raw bytes but must not
write). This has a `verify:` (a deterministic property check over shipped
behavior), so the loop runs it; green on authoring pins the promise.
"""

import hashlib

import pytest

from ncarnate.audit import AuditOptions, audit_path

from conftest import HDFEOS2_FIXTURES, NETCDF_FIXTURES, stage


def _snapshot(root):
    """A path -> (mtime_ns, sha256) map for every file under ``root``."""
    state = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            state[path] = (
                path.stat().st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
    return state


def _stage_tree(workdir):
    # A small mixed tree: HDF-EOS2 and netCDF fixtures in subdirectories.
    hdf_dir = workdir / "hdf"
    hdf_dir.mkdir()
    nc_dir = workdir / "nc"
    nc_dir.mkdir()
    for fixture in HDFEOS2_FIXTURES[:2]:
        stage(fixture, hdf_dir)
    for fixture in NETCDF_FIXTURES[:2]:
        stage(fixture, nc_dir)
    return workdir


@pytest.mark.parametrize("checksum", [None, "sha256"])
def test_audit_never_modifies_audited_files(workdir, checksum):
    tree = _stage_tree(workdir)
    before = _snapshot(tree)

    audit_path(
        str(tree),
        AuditOptions(recursive=True, mode="metadata", checksum=checksum),
    )

    after = _snapshot(tree)

    assert set(after) == set(before), "audit created or deleted a file"
    for path, state in before.items():
        assert after[path] == state, f"audit modified {path}"
