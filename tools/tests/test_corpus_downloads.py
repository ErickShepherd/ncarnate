"""Exercise interrupted downloads, bounded retries and cache integrity offline."""
import hashlib
import http.client
import io
import json
import socket
import urllib.error

import pytest

from test_ci_helpers import load


URL = "https://example.org/source.hdf"
DATA = b"exact original bytes"


class Response(io.BytesIO):
    def geturl(self):
        return URL


@pytest.fixture
def download(monkeypatch):
    corpus = load("corpus")
    sleeps = []
    monkeypatch.setattr(corpus.time, "sleep", sleeps.append)
    entry = {"name": "source.hdf", "sha256": hashlib.sha256(DATA).hexdigest(),
             "urls": [URL]}
    return corpus, entry, sleeps


def test_partial_download_is_discarded_before_retry(download, monkeypatch, tmp_path, capsys):
    corpus, entry, sleeps = download
    calls = []

    class Interrupted(Response):
        def read1(self, size):
            if self.tell():
                raise TimeoutError("read timed out")
            return super().read1(3)

    def open_url(url, timeout):
        calls.append(url)
        assert timeout == 30
        assert len(list(tmp_path.iterdir())) == 1  # Only the fresh attempt's temporary file.
        return Interrupted(DATA) if len(calls) == 1 else Response(DATA)

    monkeypatch.setattr(corpus.urllib.request, "urlopen", open_url)
    assert corpus.acquire(entry, tmp_path, fetch=True)["status"] == "verified"
    assert (tmp_path / entry["name"]).read_bytes() == DATA
    assert [p.name for p in tmp_path.iterdir()] == [entry["name"]]
    assert sleeps == [2]
    assert "attempt 1/3" in capsys.readouterr().err


@pytest.mark.parametrize("code", [408, 429, 500, 502, 503, 504])
def test_transient_http_errors_stop_after_three_attempts(download, monkeypatch, tmp_path, code):
    corpus, entry, sleeps = download
    calls = []

    def fail(url, timeout):
        calls.append(url)
        raise urllib.error.HTTPError(url, code, "temporary", {}, None)

    monkeypatch.setattr(corpus.urllib.request, "urlopen", fail)
    row = corpus.acquire(entry, tmp_path, fetch=True)
    assert row["status"] == "missing"
    assert len(row["attempts"]) == len(calls) == 3
    assert sleeps == [2, 4]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("failure", [
    TimeoutError("timed out"),
    urllib.error.URLError(TimeoutError("timed out")),
    ConnectionResetError("reset by peer"),
    http.client.IncompleteRead(b"partial"),
    urllib.error.URLError(socket.gaierror(socket.EAI_AGAIN, "temporary DNS failure")),
])
def test_transport_failures_retry_then_succeed(download, monkeypatch, tmp_path, failure):
    corpus, entry, sleeps = download
    responses = iter([failure, Response(DATA)])

    def open_url(url, timeout):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(corpus.urllib.request, "urlopen", open_url)
    assert corpus.acquire(entry, tmp_path, fetch=True)["status"] == "verified"
    assert sleeps == [2]


@pytest.mark.parametrize("failure", ["not_found", "bad_digest", "local_io", "http_redirect"])
def test_permanent_failures_are_not_retried(download, monkeypatch, tmp_path, failure):
    corpus, entry, sleeps = download
    calls = []

    def open_url(url, timeout):
        calls.append(url)
        if failure == "not_found":
            raise urllib.error.HTTPError(url, 404, "missing", {}, None)
        if failure == "local_io":
            raise OSError("disk unavailable")
        response = Response(b"wrong bytes" if failure == "bad_digest" else DATA)
        if failure == "http_redirect":
            response.geturl = lambda: "http://example.org/source.hdf"
        return response

    monkeypatch.setattr(corpus.urllib.request, "urlopen", open_url)
    assert corpus.acquire(entry, tmp_path, fetch=True)["status"] == "missing"
    assert len(calls) == 1
    assert sleeps == []
    assert not list(tmp_path.iterdir())


def test_next_mirror_can_supply_correct_bytes(download, monkeypatch, tmp_path):
    corpus, entry, sleeps = download
    entry["urls"].append("https://mirror.example.org/source.hdf")
    monkeypatch.setattr(corpus.urllib.request, "urlopen",
                        lambda url, timeout: Response(b"wrong" if url == URL else DATA))
    row = corpus.acquire(entry, tmp_path, fetch=True)
    assert row["status"] == "verified"
    assert row["url"] == entry["urls"][1]
    assert sleeps == []


@pytest.mark.parametrize("data,status", [(DATA, "verified"), (b"corrupt cache", "digest_mismatch")])
def test_cached_bytes_are_rehashed_without_network(download, monkeypatch, tmp_path, data, status):
    corpus, entry, _ = download
    (tmp_path / entry["name"]).write_bytes(data)

    def unexpected_network(*args, **kwargs):
        pytest.fail("existing corpus files must be verified locally")

    monkeypatch.setattr(corpus.urllib.request, "urlopen", unexpected_network)
    assert corpus.acquire(entry, tmp_path, fetch=True)["status"] == status
    assert (tmp_path / entry["name"]).read_bytes() == data


def test_slow_stream_hits_time_budget(download, monkeypatch, tmp_path):
    corpus, entry, sleeps = download
    times = iter([0, 121, 200, 321, 400, 521])
    monkeypatch.setattr(corpus.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(corpus.urllib.request, "urlopen", lambda *a, **k: Response(DATA))
    row = corpus.acquire(entry, tmp_path, fetch=True)
    assert row["status"] == "missing"
    assert all("time budget" in error for error in row["attempts"])
    assert sleeps == [2, 4]
    assert not list(tmp_path.iterdir())


def test_size_limit_never_publishes_or_retries(download, monkeypatch, tmp_path):
    corpus, entry, sleeps = download
    monkeypatch.setattr(corpus, "MAX_BYTES", len(DATA) - 1)
    monkeypatch.setattr(corpus.urllib.request, "urlopen", lambda *a, **k: Response(DATA))
    assert corpus.acquire(entry, tmp_path, fetch=True)["status"] == "missing"
    assert sleeps == []
    assert not list(tmp_path.iterdir())


def test_cli_keeps_json_on_stdout_and_failure_visible_on_stderr(
        download, monkeypatch, tmp_path, capsys):
    corpus, entry, _ = download
    fixtures = tmp_path / "tests" / "fixtures"
    fixtures.mkdir(parents=True)
    (fixtures / "corpus.json").write_text(json.dumps({"granules": [entry]}))
    monkeypatch.setattr(corpus, "__file__", str(tmp_path / "tools" / "corpus.py"))
    monkeypatch.setattr(corpus.sys, "argv", ["corpus.py", str(tmp_path / "data")])
    assert corpus.main() == 1
    output = capsys.readouterr()
    assert json.loads(output.out)[0]["status"] == "missing"
    assert "source.hdf: missing" in output.err
