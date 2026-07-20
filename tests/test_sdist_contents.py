"""Distribution-content guard for the source distribution (independent-review
finding F5; IMPLEMENTATION_PLAN remediation-hold item 6).

The build config's ``[tool.hatch.build.targets.sdist]`` exclude list declares
that internal planning/design/checklist/audit material is *not* release
content. Those exclusions are pathed at ``docs/…`` and did not match the
root-level loop artifacts ``IMPLEMENTATION_PLAN.md`` and ``LOOP_LEARNINGS.md``,
so an offline-built ``ncarnate-*.tar.gz`` shipped both. This test builds a
real sdist and asserts the policy holds: the loop artifacts and the docs/
internals stay out, while the package, the tests + fixtures (so
``pip install <sdist> && pytest`` works), and the fidelity contract stay in.

The build is hermetic (``--no-isolation``): ``build`` and the ``hatchling``
backend are test dependencies, so no network fetch is needed.
"""

import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Internal development material that must never ship in the sdist (F5).
EXCLUDED_MEMBERS = (
    "IMPLEMENTATION_PLAN.md",
    "LOOP_LEARNINGS.md",
)
EXCLUDED_PREFIXES = (
    "docs/plans/",
    "docs/design/",
    "docs/audits/",
)

# A few load-bearing members that must stay in — the sdist is useless without
# the package, and `pip install <sdist> && pytest` needs the tests + fixtures.
REQUIRED_MEMBERS = (
    "ncarnate/__init__.py",
    "ncarnate/convert/preflight.py",
    "README.md",
    "docs/fidelity-notes.md",
)


def _member_relpaths(tar_path: Path) -> set[str]:
    """The sdist members with the leading ``ncarnate-<version>/`` stripped."""
    with tarfile.open(tar_path, "r:gz") as tar:
        names = tar.getnames()
    relpaths = set()
    for name in names:
        parts = name.split("/", 1)
        if len(parts) == 2 and parts[1]:
            relpaths.add(parts[1])
    return relpaths


@pytest.fixture(scope="module")
def sdist_members(tmp_path_factory) -> set[str]:
    pytest.importorskip("build", reason="build is a test dependency")
    pytest.importorskip("hatchling", reason="hatchling is a test dependency")

    out_dir = tmp_path_factory.mktemp("sdist")
    completed = subprocess.run(
        [sys.executable, "-m", "build", "--sdist", "--no-isolation",
         "--outdir", str(out_dir), str(PROJECT_ROOT)],
        capture_output=True, text=True,
    )
    assert completed.returncode == 0, (
        f"sdist build failed:\n{completed.stdout}\n{completed.stderr}"
    )

    tarballs = list(out_dir.glob("*.tar.gz"))
    assert len(tarballs) == 1, f"expected one sdist, got {tarballs}"
    return _member_relpaths(tarballs[0])


def test_sdist_excludes_root_loop_artifacts(sdist_members):
    leaked = [member for member in EXCLUDED_MEMBERS if member in sdist_members]
    assert not leaked, f"loop artifacts leaked into the sdist: {leaked}"


def test_sdist_excludes_internal_docs(sdist_members):
    leaked = [
        member for member in sdist_members
        if member.startswith(EXCLUDED_PREFIXES)
    ]
    assert not leaked, f"internal docs leaked into the sdist: {leaked}"


def test_sdist_includes_package_tests_and_contract(sdist_members):
    missing = [
        member for member in REQUIRED_MEMBERS if member not in sdist_members
    ]
    assert not missing, f"required members missing from the sdist: {missing}"
    # The tests + at least one committed fixture must ride along so an
    # installed sdist can run its own suite.
    assert any(m.startswith("tests/") for m in sdist_members)
    assert any(
        m.startswith("tests/fixtures/") and m.endswith(".hdf")
        for m in sdist_members
    )


# --- the WHEEL must ship the frozen handoff schema --------------------
# The whole point of promoting the schema to package_data is that a
# `pip install ncarnate` (a wheel) carries the frozen contract a downstream
# consumer validates against. The sdist tests above don't cover the wheel; a
# future `[tool.hatch.build.targets.wheel]` include/exclude narrowing could
# silently drop the schema. This guards that invariant directly.

@pytest.fixture(scope="module")
def wheel_members(tmp_path_factory) -> set[str]:
    pytest.importorskip("build", reason="build is a test dependency")
    pytest.importorskip("hatchling", reason="hatchling is a test dependency")

    out_dir = tmp_path_factory.mktemp("wheel")
    completed = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation",
         "--outdir", str(out_dir), str(PROJECT_ROOT)],
        capture_output=True, text=True,
    )
    assert completed.returncode == 0, (
        f"wheel build failed:\n{completed.stdout}\n{completed.stderr}"
    )

    wheels = list(out_dir.glob("*.whl"))
    assert len(wheels) == 1, f"expected one wheel, got {wheels}"
    with zipfile.ZipFile(wheels[0]) as whl:
        return set(whl.namelist())


def test_wheel_ships_the_frozen_handoff_schema(wheel_members):
    assert "ncarnate/schemas/handoff.schema.json" in wheel_members, (
        "the frozen handoff schema must ship in the wheel (package_data); "
        f"wheel schemas: {sorted(m for m in wheel_members if 'schema' in m)}"
    )
