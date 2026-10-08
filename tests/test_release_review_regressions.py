import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import netCDF4 as nc
import numpy as np
import pytest

from ncarnate import prepare, prepare_batch, execute_prepared, execute_prepared_batch
from ncarnate.errors import NcarnateError
from ncarnate.journal import ResultJournal
from ncarnate.streaming import array_budget, slices
from conftest import NETCDF_FIXTURES


def source(root, name):
    path = root / name
    shutil.copyfile(NETCDF_FIXTURES[0], path)
    return path


def test_total_checkpoint_size_can_exceed_one_record_limit(tmp_path, monkeypatch):
    import ncarnate.checkpoints as checkpoints
    monkeypatch.setattr(checkpoints, 'MAX_RECORD_BYTES', 64 * 1024)
    pairs = []
    for i in range(4):
        src = source(tmp_path, f'source{i}.nc')
        with nc.Dataset(src, 'a') as dataset:
            dataset.description = 'x' * 22000
        pairs.append((src, tmp_path / f'output{i}.nc'))
    jobs = prepare_batch(pairs)
    journal = tmp_path / 'resume.json'
    first = execute_prepared_batch(jobs, journal=journal)
    files = list(Path(str(journal) + '.records').glob('*.json'))
    assert len(files) == 4
    assert sum(p.stat().st_size for p in files) > checkpoints.MAX_RECORD_BYTES
    assert all(p.stat().st_size <= checkpoints.MAX_RECORD_BYTES for p in files)
    assert execute_prepared_batch(jobs, journal=journal, resume=True) == first


def test_legacy_checkpoint_migration_ignores_unusable_unrelated_record(tmp_path):
    job = prepare(source(tmp_path, 'source.nc'), tmp_path / 'output.nc')
    good = execute_prepared(job)
    bad = copy.deepcopy(good)
    bad['destination']['path'] = str(tmp_path / 'unrelated.nc')
    bad['destination']['sha256'] = ''
    journal = tmp_path / 'resume.json'
    journal.write_text(json.dumps({'schema_version': 'ncarnate-resume-1', 'records': [good, bad]}))
    before = Path(job.plan.destination).stat().st_mtime_ns
    assert execute_prepared(job, journal=journal, resume=True) == good
    assert Path(job.plan.destination).stat().st_mtime_ns == before
    assert json.loads(journal.read_text())['schema_version'] == 'ncarnate-resume-2'
    assert len(list(Path(str(journal) + '.records').glob('*.json'))) == 1


def test_missing_later_parent_refuses_entire_batch(tmp_path):
    a, b = source(tmp_path, 'a.nc'), source(tmp_path, 'b.nc')
    parent = tmp_path / 'later'
    parent.mkdir()
    jobs = prepare_batch([(a, tmp_path / 'a-out.nc'), (b, parent / 'b-out.nc')])
    parent.rmdir()
    with pytest.raises(NcarnateError) as error:
        execute_prepared_batch(jobs)
    assert error.value.code == 'DESTINATION_COLLISION'
    assert not (tmp_path / 'a-out.nc').exists()


@pytest.mark.parametrize('failure', [OSError, KeyboardInterrupt, MemoryError])
def test_failed_legacy_migration_preserves_journal_and_can_retry(tmp_path, monkeypatch, failure):
    import ncarnate.checkpoints as checkpoints
    job = prepare(source(tmp_path, 'source.nc'), tmp_path / 'output.nc')
    record = execute_prepared(job)
    journal = tmp_path / 'resume.json'
    journal.write_text(json.dumps({'schema_version': 'ncarnate-resume-1', 'records': [record]}))
    original = journal.read_bytes()
    publish = ResultJournal.publish
    def fail_descriptor(self, text):
        if self.target == str(journal):
            raise failure('injected descriptor publication failure')
        return publish(self, text)
    with monkeypatch.context() as patch:
        patch.setattr(ResultJournal, 'publish', fail_descriptor)
        with pytest.raises(NcarnateError if failure is OSError else failure):
            execute_prepared(job, journal=journal, resume=True)
    assert journal.read_bytes() == original
    assert not Path(checkpoints.records_path(journal)).exists()
    assert execute_prepared(job, journal=journal, resume=True) == record


@pytest.mark.parametrize('unknown', [False, True])
def test_interrupted_migration_directory_is_verified_before_reuse(tmp_path, unknown):
    import ncarnate.checkpoints as checkpoints
    job = prepare(source(tmp_path, 'source.nc'), tmp_path / 'output.nc')
    record = execute_prepared(job)
    journal = tmp_path / 'resume.json'
    journal.write_text(json.dumps({'schema_version': 'ncarnate-resume-1', 'records': [record]}))
    directory = Path(checkpoints.records_path(journal))
    directory.mkdir()
    (directory / checkpoints.record_name(record)).write_text(json.dumps(record))
    retained = directory / ('unrelated.txt' if unknown else '.ncarnate-journal-interrupted')
    retained.write_bytes(b'preserve this file')
    if unknown:
        with pytest.raises(NcarnateError, match='unrecognized entry'):
            execute_prepared(job, journal=journal, resume=True)
        assert json.loads(journal.read_text())['schema_version'] == 'ncarnate-resume-1'
    else:
        assert execute_prepared(job, journal=journal, resume=True) == record
    assert retained.read_bytes() == b'preserve this file'


def test_exact_record_byte_limit_round_trips_on_windows(tmp_path, monkeypatch):
    import ncarnate.checkpoints as checkpoints
    job = prepare(source(tmp_path, 'source.nc'), tmp_path / 'output.nc')
    record = execute_prepared(job)
    size = len(json.dumps(record, ensure_ascii=True, allow_nan=False).encode('utf-8')) + 1
    monkeypatch.setattr(checkpoints, 'MAX_RECORD_BYTES', size)
    journal = tmp_path / 'resume.json'
    writer = checkpoints.CheckpointJournal(journal, {}, None)
    try:
        writer.reserve(job.plan.destination)
        writer.publish_record(record)
    finally:
        writer.close()
    checkpoint = Path(checkpoints.records_path(journal)) / checkpoints.record_name(record)
    assert checkpoint.stat().st_size == size
    assert execute_prepared(job, journal=journal, resume=True) == record


def test_parent_alias_is_resolved_for_prepared_jobs_and_journals(tmp_path):
    real, alias = tmp_path / 'real', tmp_path / 'alias'
    real.mkdir()
    try:
        alias.symlink_to(real, target_is_directory=True)
    except OSError:
        if sys.platform != 'win32':
            raise
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(real)], check=True, capture_output=True)
    source(real, 'source.nc')
    item = prepare(alias / 'source.nc', alias / 'out.nc')
    execute_prepared(item, journal=alias / 'resume.json')
    assert Path(item.plan.destination) == real / 'out.nc'
    writer = ResultJournal(alias / 'cli.json')
    try:
        writer.publish('{}')
    finally:
        writer.close()
    assert (real / 'cli.json').is_file()


def test_chunk_tiles_finish_large_chunks_and_coalesce_small_chunks():
    shape, chunks = (19, 27), (7, 9)
    covered = np.zeros(shape, dtype='u1')
    visits = []
    with array_budget(1024):
        for selection in slices(shape, 8, chunks):
            covered[selection] += 1
            assert covered[selection].size <= 16
            chunk = tuple(part.start // size for part, size in zip(selection, chunks))
            if not visits or visits[-1] != chunk:
                assert chunk not in visits
                visits.append(chunk)
        assert len(list(slices((1024,), 8, (1,)))) == 64
    assert np.all(covered == 1)


def test_output_staging_failure_is_coded(tmp_path, monkeypatch):
    import ncarnate.core as core
    job = prepare(source(tmp_path, 'source.nc'), tmp_path / 'out.nc')
    def fail(**kwargs):
        raise OSError('injected unavailable staging')
    monkeypatch.setattr(core.tempfile, 'mkstemp', fail)
    with pytest.raises(NcarnateError) as error:
        execute_prepared(job)
    assert error.value.code == 'OUTPUT_PUBLISH_FAILED'
    assert not os.path.exists(job.plan.destination)
