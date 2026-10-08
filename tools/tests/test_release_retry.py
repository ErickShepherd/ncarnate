"""The uploader retry must not accept untested or substituted distributions."""

import hashlib
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "release_retry", Path(__file__).parents[1] / "verify_release_retry.py",
)
retry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retry)


@pytest.fixture
def candidate(tmp_path):
    payload = b"tested artifact"
    (tmp_path / "package.whl").write_bytes(payload)
    receipt = {"run_id": 17, "repository": "example/package", "tag": "v2.3.0",
               "head_sha": "a" * 40, "required_jobs": ["Linux", "Windows", "macOS"],
               "files": {"package.whl": hashlib.sha256(payload).hexdigest()}}
    run = {"id": 17, "repository": {"full_name": "example/package"},
           "path": ".github/workflows/publish.yml", "event": "release",
           "head_branch": "v2.3.0", "head_sha": "a" * 40, "status": "completed"}
    jobs = {"total_count": 3, "jobs": [{"name": name, "status": "completed",
                                       "conclusion": "success"}
                                      for name in receipt["required_jobs"]]}
    tag = {"object": {"type": "commit", "sha": "a" * 40}}
    return receipt, run, jobs, tag, tmp_path


def test_accepts_original_tested_files(candidate):
    retry.validate(*candidate)


@pytest.mark.parametrize("result", ["failure", "skipped", "cancelled", None])
def test_refuses_nonpassing_platform(candidate, result):
    candidate[2]["jobs"][1]["conclusion"] = result
    with pytest.raises(ValueError, match="validation did not pass"):
        retry.validate(*candidate)


def test_refuses_incomplete_inventory(candidate):
    candidate[2]["jobs"].pop()
    with pytest.raises(ValueError, match="Incomplete"):
        retry.validate(*candidate)


def test_refuses_missing_required_job_even_with_complete_page(candidate):
    candidate[2]["jobs"].pop()
    candidate[2]["total_count"] -= 1
    with pytest.raises(ValueError, match="validation did not pass"):
        retry.validate(*candidate)


@pytest.mark.parametrize("field,value", [("id", 18), ("event", "pull_request"),
                                       ("head_branch", "main"), ("head_sha", "b" * 40),
                                       ("path", ".github/workflows/ci.yml")])
def test_refuses_another_source_run(candidate, field, value):
    candidate[1][field] = value
    with pytest.raises(ValueError, match="run identity"):
        retry.validate(*candidate)


def test_refuses_moved_tag(candidate):
    candidate[3]["object"]["sha"] = "b" * 40
    with pytest.raises(ValueError, match="tag differs"):
        retry.validate(*candidate)


def test_refuses_changed_artifact(candidate):
    (candidate[4] / "package.whl").write_bytes(b"different artifact")
    with pytest.raises(ValueError, match="tested artifact"):
        retry.validate(*candidate)


def test_refuses_extra_artifact(candidate):
    (candidate[4] / "unexpected.whl").write_bytes(b"extra artifact")
    with pytest.raises(ValueError, match="Unexpected"):
        retry.validate(*candidate)
