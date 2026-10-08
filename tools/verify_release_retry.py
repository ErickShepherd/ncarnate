"""Verify the exact already-tested 2.3.0 artifacts before an uploader retry."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import urllib.request


def validate(receipt, run, jobs, tag, directory):
    """Refuse changed identities, missing/failed tests, or different package bytes."""
    if (run.get("id") != receipt["run_id"]
            or run.get("repository", {}).get("full_name") != receipt["repository"]
            or run.get("path") != ".github/workflows/publish.yml"
            or run.get("event") != "release"
            or run.get("head_branch") != receipt["tag"]
            or run.get("head_sha") != receipt["head_sha"]
            or run.get("status") != "completed"):
        raise ValueError("Original release run identity differs")
    if tag.get("object") != {"type": "commit", "sha": receipt["head_sha"]}:
        raise ValueError("Published release tag differs")
    if jobs.get("total_count") != len(jobs.get("jobs", [])):
        raise ValueError("Incomplete job inventory")
    for name in receipt["required_jobs"]:
        matches = [job for job in jobs["jobs"] if job.get("name") == name]
        if (len(matches) != 1 or matches[0].get("status") != "completed"
                or matches[0].get("conclusion") != "success"):
            raise ValueError("Required release validation did not pass")
    paths = list(directory.iterdir())
    if {p.name for p in paths} != set(receipt["files"]):
        raise ValueError("Unexpected or missing distribution")
    for path in paths:
        if (path.is_symlink() or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest()
                != receipt["files"][path.name]):
            raise ValueError("Distribution differs from the tested artifact")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    receipt = json.loads((Path(__file__).resolve().parents[1]
                          / ".github/release-recovery-2.3.0.json").read_text(encoding="utf-8"))

    def get(path):
        request = urllib.request.Request(
            "https://api.github.com/repos/" + receipt["repository"] + path,
            headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    run = get(f"/actions/runs/{receipt['run_id']}")
    jobs = get(f"/actions/runs/{receipt['run_id']}/jobs?per_page=100")
    tag = get("/git/ref/tags/" + receipt["tag"])
    # Discard the provider's URL field; retain the identity and object type.
    tag = {"object": {key: tag["object"][key] for key in ("type", "sha")}}
    validate(receipt, run, jobs, tag, args.directory)
    print("Original release tests passed; tag and exact package bytes verified.")


if __name__ == "__main__":
    main()
