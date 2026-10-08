import sys
import errno

import netCDF4 as nc
import numpy as np
import pytest

from ncarnate import prepare, execute_bounded
from ncarnate.errors import NcarnateError
from conftest import NETCDF_FIXTURES, HDFEOS2_FIXTURES, assert_lossless_netcdf

supported = pytest.mark.skipif(sys.platform not in ("win32", "linux"), reason="hard worker limit backend not qualified on this platform")


@supported
@pytest.mark.parametrize("fixture", NETCDF_FIXTURES, ids=lambda f: f.stem)
def test_bounded_worker_preserves_netcdf(fixture, tmp_path):
    target = tmp_path / "result.nc"
    report = execute_bounded(prepare(fixture, target))
    assert_lossless_netcdf(fixture, target)
    assert report["result"]["destination"]["path"] == str(target)
    assert report["resources"]["limit_bytes"] == 512 * 1024 ** 2
    if sys.platform == "win32":
        assert 0 < report["resources"]["peak_job_committed_bytes"] <= report["resources"]["limit_bytes"]
    assert not list(tmp_path.glob(".ncarnate-bounded-*"))


@supported
@pytest.mark.parametrize("fixture", HDFEOS2_FIXTURES, ids=lambda f: f.stem)
def test_bounded_worker_preserves_hdf4(fixture, tmp_path):
    report = execute_bounded(prepare(fixture, tmp_path / "result.nc"))
    assert report["result"]["verification"]["status"] == "verified"


@supported
def test_timeout_never_publishes_and_cleans_stage(tmp_path):
    with pytest.raises(NcarnateError) as error:
        execute_bounded(prepare(NETCDF_FIXTURES[0], tmp_path / "out.nc"), timeout_seconds=0.00001)
    assert error.value.code == "WORKER_TIMEOUT"
    assert not (tmp_path / "out.nc").exists()
    assert not list(tmp_path.glob(".ncarnate-bounded-*"))


@supported
def test_large_native_chunk_cannot_escape_worker_cap(tmp_path):
    # At this intentionally tiny cap imports may fail first. The separate
    # native allocator probe proves enforcement after a worker starts.
    source = tmp_path / "native-chunk.nc"
    with nc.Dataset(source, "w") as ds:
        ds.createDimension("x", 24 * 1024 * 1024)
        variable = ds.createVariable("v", "f4", ("x",), chunksizes=(24 * 1024 * 1024,), zlib=True)
        # One 96 MiB native chunk. Write incrementally from small Python arrays.
        for start in range(0, len(variable), 1024 * 1024):
            variable[start:start + 1024 * 1024] = np.zeros(1024 * 1024, dtype="f4")
    item = prepare(source, tmp_path / "out.nc")
    with pytest.raises(NcarnateError) as error:
        execute_bounded(item, memory_bytes=64 * 1024 * 1024, array_bytes=1024 * 1024)
    assert error.value.code == "BOUNDED_WORKER_FAILED"
    assert not (tmp_path / "out.nc").exists()
    assert source.is_file()
    assert not list(tmp_path.glob(".ncarnate-bounded-*"))


@supported
def test_worker_cannot_overwrite_existing_destination(tmp_path):
    target = tmp_path / "out.nc"
    target.write_bytes(b"keep")
    with pytest.raises(NcarnateError):
        execute_bounded(prepare(NETCDF_FIXTURES[0], target))
    assert target.read_bytes() == b"keep"


def test_unsupported_platform_refuses_without_writes(tmp_path, monkeypatch):
    import ncarnate.memory as memory
    monkeypatch.setattr(memory.sys, "platform", "unsupported")
    with pytest.raises(NcarnateError) as error:
        execute_bounded(prepare(NETCDF_FIXTURES[0], tmp_path / "out.nc"))
    assert error.value.code == "MEMORY_LIMIT_UNAVAILABLE"
    assert not list(tmp_path.iterdir())


@supported
def test_native_allocator_is_refused_after_worker_start(tmp_path):
    from ncarnate.memory import _run_worker
    probe = tmp_path / 'native_probe.py'
    probe.write_text('''import sys, json
request = json.loads(sys.stdin.buffer.readline())
limit = request['limit']
if sys.platform == 'linux':
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
import ctypes
library = ctypes.CDLL('ucrtbase.dll') if sys.platform == 'win32' else ctypes.CDLL(None)
library.malloc.argtypes = [ctypes.c_size_t]
library.malloc.restype = ctypes.c_void_p
library.free.argtypes = [ctypes.c_void_p]
small = library.malloc(1024 * 1024)
assert small, 'native allocator must work within the limit'
library.free(small)
pointer = library.malloc(limit * 2)
if pointer:
    library.free(pointer)
    raise RuntimeError('native allocation escaped cap')
print('native allocation refused after successful startup')
''')
    limit = 256 * 1024**2
    code, peak = _run_worker([getattr(sys, '_base_executable', sys.executable), '-I', '-S', str(probe)],
                             {'limit': limit}, limit=limit, timeout=30, directory=tmp_path)
    assert code == 0
    assert 'after successful startup' in (tmp_path / 'worker.log').read_text()
    if peak is not None:
        # This Windows counter included the rejected allocation in our probe.
        # The allocator's null result, not this counter, proves the refusal.
        assert peak > 0


@supported
def test_cleanup_failure_returns_published_success(tmp_path, monkeypatch):
    import ncarnate.memory as memory
    def fail(path):
        raise OSError(errno.EIO, 'injected cleanup failure')
    monkeypatch.setattr(memory.shutil, 'rmtree', fail)
    target = tmp_path / 'out.nc'
    report = execute_bounded(prepare(NETCDF_FIXTURES[0], target))
    assert_lossless_netcdf(NETCDF_FIXTURES[0], target)
    assert report['warnings'][0]['code'] == 'WORKER_CLEANUP_INCOMPLETE'
    assert report['result']['verification']['status'] == 'verified'


@supported
@pytest.mark.parametrize('collision', [True, False])
def test_exclusive_publication_failure_is_coded(tmp_path, monkeypatch, collision):
    import ncarnate.memory as memory
    target = tmp_path / 'out.nc'
    def fail(source, destination):
        if collision:
            target.write_bytes(b'other writer')
            raise FileExistsError('injected destination race')
        raise OSError(errno.EOPNOTSUPP, 'hard links unavailable')
    monkeypatch.setattr(memory.os, 'link', fail)
    with pytest.raises(NcarnateError) as error:
        execute_bounded(prepare(NETCDF_FIXTURES[0], target))
    assert error.value.code == ('DESTINATION_COLLISION' if collision else 'OUTPUT_PUBLISH_FAILED')
    if collision:
        assert target.read_bytes() == b'other writer'
    else:
        assert not target.exists()
    assert not list(tmp_path.glob('.ncarnate-bounded-*'))


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux inherited limit')
def test_inherited_hard_limit_is_respected_before_work(tmp_path, monkeypatch):
    import resource
    monkeypatch.setattr(resource, 'getrlimit', lambda kind: (128 * 1024**2, 128 * 1024**2))
    with pytest.raises(NcarnateError) as error:
        execute_bounded(prepare(NETCDF_FIXTURES[0], tmp_path / 'out.nc'))
    assert error.value.code == 'MEMORY_LIMIT_UNAVAILABLE'
    assert not list(tmp_path.iterdir())
