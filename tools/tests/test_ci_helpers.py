"""Negative cases for release checks: never accept absent or skipped evidence."""
import importlib.util
import io
from pathlib import Path
import re
import sys
import tarfile

import pytest


def load(name):
    path = Path(__file__).resolve().parents[1] / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("cases", ["", "<testcase><skipped/></testcase>",
                                  "<testcase><failure/></testcase>",
                                  "<testcase><error/></testcase>",
                                  "<testcase/><testcase><skipped/></testcase>"])
def test_full_corpus_rejects_incomplete_validation(tmp_path, cases):
    report = tmp_path / "result.xml"
    report.write_text("<testsuites><testsuite>" + cases + "</testsuite></testsuites>")
    with pytest.raises(SystemExit):
        load("test_full_corpus").require_executed_tests(report, {"conversion"})


def test_full_corpus_accepts_executed_validation(tmp_path):
    report = tmp_path / "result.xml"
    report.write_text('<testsuites><testsuite><testcase name="conversion"/></testsuite></testsuites>')
    load("test_full_corpus").require_executed_tests(report, {"conversion"})


def test_full_corpus_rejects_missing_tests_even_when_others_pass(tmp_path):
    report = tmp_path / "result.xml"
    report.write_text('<testsuites><testsuite><testcase name="conversion"/></testsuite></testsuites>')
    with pytest.raises(SystemExit, match="inventory"):
        load("test_full_corpus").require_executed_tests(report, {"conversion", "reference"})


@pytest.mark.parametrize('path', ['tests/fixture.bin', 'tests/../../escaped.bin'])
def test_sdist_tests_come_from_archive_with_path_containment(tmp_path, path):
    archive = tmp_path / 'source.tar.gz'
    with tarfile.open(archive, 'w:gz') as bundle:
        member = tarfile.TarInfo('ncarnate-2.3.0/' + path)
        member.size = 4
        bundle.addfile(member, io.BytesIO(b'data'))
    output = tmp_path / 'output'
    if '..' in path:
        with pytest.raises(ValueError, match='unsafe'):
            load('test_distribution').stage_tests(archive, tmp_path / 'absent-checkout', output, 'sdist')
        assert not (tmp_path / 'escaped.bin').exists()
    else:
        load('test_distribution').stage_tests(archive, tmp_path / 'absent-checkout', output, 'sdist')
        assert (output / 'tests/fixture.bin').read_bytes() == b'data'


@pytest.mark.parametrize("count", [0, 2])
def test_distribution_selection_requires_one_artifact(tmp_path, monkeypatch, count):
    for number in range(count):
        (tmp_path / f"ncarnate-{number}-py3-none-any.whl").write_bytes(b"not installed")
    monkeypatch.setattr(sys, "argv", ['test_distribution', str(tmp_path), '--kind', 'wheel'])
    with pytest.raises(SystemExit) as error:
        load("test_distribution").main()
    assert error.value.code == 2


def test_corpus_rejects_missing_and_wrong_bytes(tmp_path):
    corpus = load("corpus")
    entry = {'name': 'source.hdf', 'sha256': '0' * 64}
    assert corpus.acquire(entry, tmp_path)['status'] == 'missing'
    (tmp_path / 'source.hdf').write_bytes(b'wrong dataset')
    assert corpus.acquire(entry, tmp_path)['status'] == 'digest_mismatch'


def test_production_metadata_requires_the_matching_tag():
    validator = load('validate_release_metadata')
    repo = Path(__file__).resolve().parents[2]
    version = re.search(r'__version__\s*=\s*"([^"]+)"',
                        (repo / 'ncarnate/constants.py').read_text(encoding='utf-8')).group(1)
    assert any('requires a version tag' in e for e in validator.validate(repo, require_tag=True))
    assert any('tag differs' in e for e in validator.validate(repo, tag='v0.0.0', require_tag=True))
    assert validator.validate(repo, tag='v' + version, require_tag=True) == []
