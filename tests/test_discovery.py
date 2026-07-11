"""Input-discovery tests (ncarnate.discovery).

These cover the shared file-enumeration helper extracted from cli.py. The
regression of interest: a non-regular file (FIFO/device) named with a
supported extension must never be handed downstream, where a reader opening
it for read would block the whole scan forever.
"""

import os

import pytest

from ncarnate.discovery import _get_files


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires os.mkfifo")
def test_recursive_scan_excludes_hang_prone_special_files(tmp_path):
    # A real granule and a FIFO masquerading as one, both with a supported
    # extension, in a recursively-scanned tree.
    (tmp_path / "real.hdf").write_bytes(b"\x0e\x03\x13\x01payload")
    os.mkfifo(tmp_path / "trap.hdf")

    files = _get_files([str(tmp_path)], recursive=True)

    assert str(tmp_path / "real.hdf") in files
    assert str(tmp_path / "trap.hdf") not in files   # never yielded to a reader


def test_recursive_scan_keeps_broken_symlink_for_surfacing(tmp_path):
    # A dangling symlink is NOT a hang risk (open fails fast); it must be kept
    # so the audit can surface it as malformed rather than silently dropping
    # it — the audit's visible-loss principle.
    (tmp_path / "dangling.nc").symlink_to(tmp_path / "does_not_exist.nc")

    files = _get_files([str(tmp_path)], recursive=True)

    assert str(tmp_path / "dangling.nc") in files
