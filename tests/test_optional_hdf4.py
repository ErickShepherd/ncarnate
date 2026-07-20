"""Failing-test matrix for optional-pyhdf capability degradation
(IMPLEMENTATION_PLAN step 2 / priority-queue step 2.1–2.3 / readiness
action 2; KD-L3/KD-L4; gate G2).

Without a usable pyhdf, the netCDF-only surface must keep working —
`import ncarnate`, `ncarnate --help`, `--version`, `detect_format`, and a
netCDF-only recompress all succeed — while an HDF4 conversion attempt
refuses with the stable ``HDF4_RUNTIME_UNAVAILABLE`` code **before any
output is created**, naming the detected cause, the unaffected
capabilities, and the exact conda-forge install command. Gate G2: a
missing HDF4 runtime never surfaces as an unexplained import traceback.

Simulation: a shadow ``pyhdf`` package whose ``__init__`` raises the
Windows-shaped ``ImportError`` is prepended to ``PYTHONPATH`` and every
assertion drives a **fresh subprocess** — the in-process alternatives the
plan sketches (patching ``builtins.__import__`` / ``sys.modules``) cannot
un-import the already-loaded real pyhdf in this test process, and the
subprocess boundary is also the strongest form of the claim (the real CLI,
a real interpreter, a genuine exit code — the seam gate G2 is about).

The lazy-import chain does not exist yet (`import ncarnate` eagerly pulls
`core` -> `hdf4` -> `pyhdf.SD`); these fail until the paired step-2 impl
items land (the pattern test_convert_collisions.py used for the preflight).
"""

import json
import os
import shutil
import subprocess
import sys
import textwrap

import pytest

from conftest import (
    HDFEOS2_FIXTURES,
    NETCDF_FIXTURES,
)

# The stable degraded-capability refusal code (KD-L4). The step-2 impl
# units must register this string in `ncarnate.audit.codes` (and
# ALL_CODES); the tests assert the string itself because the
# operator-facing contract is the string.
HDF4_UNAVAILABLE_CODE = "HDF4_RUNTIME_UNAVAILABLE"

# KD-L4: the refusal names the *exact* supported install command.
CONDA_FORGE_COMMAND = "conda install -c conda-forge pyhdf"

# A distinctive marker proving the refusal carried the *detected cause*
# (the shadow package's ImportError text) rather than a canned message.
SIMULATED_CAUSE_MARKER = "simulated DLL load failed"


@pytest.fixture
def no_pyhdf_env(tmp_path_factory):
    """os.environ with a shadow ``pyhdf`` package prepended to PYTHONPATH.

    The shadow raises the Windows-pip-shaped ImportError on package init,
    so any ``import pyhdf`` / ``from pyhdf.X import Y`` in a subprocess
    fails exactly as on a machine with no usable HDF4 runtime. PYTHONPATH
    precedes site-packages on sys.path, so the shadow wins over the real
    pyhdf installed in the venv. Built via ``tmp_path_factory`` in its own
    directory — never inside ``workdir``, whose zero-output-mutation
    assertions must see only what the code under test created.
    """
    shadow = tmp_path_factory.mktemp("no_pyhdf") / "pyhdf"
    shadow.mkdir(parents=True)
    (shadow / "__init__.py").write_text(
        "raise ImportError(\n"
        f"    '{SIMULATED_CAUSE_MARKER} while importing _hdfext: '\n"
        "    'The specified module could not be found.'\n"
        ")\n"
    )
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(shadow.parent) + (os.pathsep + existing if existing else "")
    )
    return env


def _run(args, env, **kwargs):
    return subprocess.run(
        [sys.executable, *args], env=env,
        capture_output=True, text=True, **kwargs,
    )


def _run_code(code, env):
    return _run(["-c", textwrap.dedent(code)], env)


# --- the netCDF-only surface survives a missing HDF4 runtime (KD-L3) ---

def test_import_ncarnate_without_pyhdf(no_pyhdf_env):
    completed = _run_code("import ncarnate", no_pyhdf_env)
    assert completed.returncode == 0, completed.stderr


def test_public_api_discoverable_without_pyhdf(no_pyhdf_env):
    # Readiness action 2: the top-level public API stays discoverable.
    completed = _run_code(
        """
        import ncarnate
        for name in ncarnate.__all__:
            assert getattr(ncarnate, name) is not None, name
        """,
        no_pyhdf_env,
    )
    assert completed.returncode == 0, completed.stderr


def test_cli_help_without_pyhdf(no_pyhdf_env):
    completed = _run(["-m", "ncarnate", "--help"], no_pyhdf_env)
    assert completed.returncode == 0, completed.stderr
    assert "usage" in completed.stdout.lower()


def test_cli_version_without_pyhdf(no_pyhdf_env):
    completed = _run(["-m", "ncarnate", "--version"], no_pyhdf_env)
    assert completed.returncode == 0, completed.stderr
    assert "ncarnate" in completed.stdout.lower()


def test_detect_format_without_pyhdf(no_pyhdf_env):
    # Detection is a magic-byte scan; it must never need the HDF4 runtime —
    # including detecting HDF4 bytes themselves (that is how the refusal
    # path knows to refuse).
    completed = _run_code(
        f"""
        from ncarnate import FileFormat, detect_format
        assert detect_format({str(NETCDF_FIXTURES[0])!r}) in (
            FileFormat.NETCDF3, FileFormat.HDF5,
        )
        assert detect_format({str(HDFEOS2_FIXTURES[0])!r}) is FileFormat.HDF4
        """,
        no_pyhdf_env,
    )
    assert completed.returncode == 0, completed.stderr


def test_netcdf_recompress_without_pyhdf(no_pyhdf_env, workdir):
    # A supported netCDF-only operation end-to-end through the real CLI
    # (gate G2: a missing DLL never prevents supported operations).
    staged = workdir / NETCDF_FIXTURES[0].name
    shutil.copyfile(NETCDF_FIXTURES[0], staged)

    completed = _run(["-m", "ncarnate", str(staged)], no_pyhdf_env)

    assert completed.returncode == 0, completed.stderr
    assert staged.is_file()                    # replaced in place, verified
    assert "Traceback" not in completed.stderr


# --- an HDF4 attempt refuses with the stable KD-L4 surface -------------

def test_hdf4_conversion_refuses_with_stable_code(no_pyhdf_env, workdir):
    staged = workdir / HDFEOS2_FIXTURES[0].name
    shutil.copyfile(HDFEOS2_FIXTURES[0], staged)

    completed = _run(["-m", "ncarnate", str(staged)], no_pyhdf_env)

    assert completed.returncode != 0
    stderr = completed.stderr
    assert HDF4_UNAVAILABLE_CODE in stderr      # the stable code, scriptable
    assert SIMULATED_CAUSE_MARKER in stderr     # the *detected* cause
    assert CONDA_FORGE_COMMAND in stderr        # the exact install command
    assert "netCDF" in stderr                   # the unaffected capabilities
    # Gate G2: an explained refusal, never a raw import traceback.
    assert "Traceback" not in stderr
    # KD-L4: refusal happens before output creation.
    assert not staged.with_suffix(".nc").exists()
    assert sorted(p.name for p in workdir.iterdir()) == [staged.name]


def test_audit_without_pyhdf_records_capability_blocker(no_pyhdf_env, workdir):
    # The audit/plan path (KD-L4, priority-queue step 2.3): scanning an
    # archive containing HDF4 files on a pyhdf-less install must survive
    # (one capability gap never aborts the scan), recording the HDF4 file
    # as a blocker with the stable code — a capability *result*, produced
    # before any output creation, that a later convert run refuses on.
    archive = workdir / "archive"
    staged = archive / HDFEOS2_FIXTURES[0].name
    staged.parent.mkdir(parents=True)
    shutil.copyfile(HDFEOS2_FIXTURES[0], staged)
    manifest = workdir / "m.jsonl"

    completed = _run(
        ["-m", "ncarnate", "audit", str(archive), "--output", str(manifest)],
        no_pyhdf_env,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Traceback" not in completed.stderr
    (record,) = [
        json.loads(line)
        for line in manifest.read_text().splitlines() if line.strip()
    ]
    assert record["status"] == "unsupported"
    assert [issue["code"] for issue in record["issues"]] == [
        HDF4_UNAVAILABLE_CODE
    ]
    assert record["plan"] is None               # never planned for conversion


def test_hdf4_refusal_library_raise_carries_code(no_pyhdf_env, workdir):
    # The library seam: recompress() on HDF4 bytes raises an NcarnateError
    # whose .code is the stable registry string (the CLI renders it; the
    # exception carries it for embedders).
    staged = workdir / HDFEOS2_FIXTURES[0].name
    shutil.copyfile(HDFEOS2_FIXTURES[0], staged)

    completed = _run_code(
        f"""
        from ncarnate import NcarnateError, recompress
        try:
            recompress({str(staged)!r})
        except NcarnateError as error:
            assert error.code == {HDF4_UNAVAILABLE_CODE!r}, error.code
        else:
            raise AssertionError("recompress did not refuse")
        """,
        no_pyhdf_env,
    )
    assert completed.returncode == 0, completed.stderr
    assert not staged.with_suffix(".nc").exists()


# --- manifest path: HDF4 refusal precedes directory creation (KD-L4) ---

def test_manifest_hdf4_refusal_creates_no_directories(no_pyhdf_env, workdir):
    # Reviewer note (pre-merge review 2026-07-16): a manifest authored on a
    # full install can carry `ready` HDF4 records that pass the destination
    # preflight on a runtime-less machine; the per-record HDF4 refusal must
    # fire *before* that record's mirrored output directory is created, so
    # a capability failure leaves no empty directories behind. Neighbouring
    # supported records still convert (per-record isolation).
    from ncarnate.audit.codes import RULESET_VERSION
    from ncarnate.audit.models import SCHEMA_VERSION
    from ncarnate.formats import detect_format
    from ncarnate.hashing import sha256_of_file

    archive = workdir / "archive"
    out_dir = workdir / "modern"
    hdf_staged = archive / "legacy" / HDFEOS2_FIXTURES[0].name
    nc_staged = archive / "flat.nc"
    hdf_staged.parent.mkdir(parents=True)
    shutil.copyfile(HDFEOS2_FIXTURES[0], hdf_staged)
    shutil.copyfile(NETCDF_FIXTURES[0], nc_staged)

    def record(relpath, staged, plan):
        return {
            "schema_version": SCHEMA_VERSION,
            "ncarnate_version": "0.0.0",
            "ruleset_version": RULESET_VERSION,
            "mode": "metadata",
            "audited_at": "2026-01-01T00:00:00Z",
            "root": str(archive),
            "path": relpath,
            "size_bytes": staged.stat().st_size,
            "sha256": sha256_of_file(str(staged)),
            "format": detect_format(str(staged)).name,
            "status": "ready",
            "structures": [],
            "issues": [],
            "plan": plan,
        }

    manifest = workdir / "m.jsonl"
    manifest.write_text("".join(json.dumps(r) + "\n" for r in [
        record(f"legacy/{hdf_staged.name}", hdf_staged,
               {"operation": "convert"}),
        record("flat.nc", nc_staged, {"operation": "recompress"}),
    ]), encoding="utf-8")

    completed = _run_code(
        f"""
        import json
        from ncarnate.convert import ConvertOptions, convert_manifest
        result = convert_manifest({str(manifest)!r}, ConvertOptions(
            out_dir={str(out_dir)!r}, allow_manifest_root=True,
        ))
        print(json.dumps({{
            "converted": [r.path for r in result.converted],
            "failed": [[r.path, r.reason] for r in result.failed],
            "skipped": [r.path for r in result.skipped],
        }}))
        """,
        no_pyhdf_env,
    )

    assert completed.returncode == 0, completed.stderr
    outcome = json.loads(completed.stdout)

    # Per-record isolation: the supported netCDF record still converted.
    assert outcome["converted"] == ["flat.nc"]
    assert (out_dir / "flat.nc").is_file()

    # The HDF4 record failed with the KD-L4 refusal (cause + install cmd).
    ((failed_path, reason),) = outcome["failed"]
    assert failed_path == f"legacy/{hdf_staged.name}"
    assert SIMULATED_CAUSE_MARKER in reason
    assert CONDA_FORGE_COMMAND in reason

    # The regression's core claim: the refusal preceded makedirs, so the
    # failed record's mirrored directory was never created.
    assert not (out_dir / "legacy").exists()


# --- manifest failures expose the same stable code the one-file path does --
# --- (independent-review finding F2; gate G2)                              --

def test_manifest_hdf4_failure_carries_stable_code(no_pyhdf_env, workdir):
    # The library seam (F2): a ready HDF4 record that fails in the convert
    # loop on a runtime-less install must carry the stable
    # HDF4_RUNTIME_UNAVAILABLE code on its ConvertRecord — not just prose —
    # so an embedder branches on the same code the direct recompress() raise
    # and the audit path already expose.
    from ncarnate.audit.codes import RULESET_VERSION
    from ncarnate.audit.models import SCHEMA_VERSION
    from ncarnate.formats import detect_format
    from ncarnate.hashing import sha256_of_file

    archive = workdir / "archive"
    out_dir = workdir / "modern"
    staged = archive / HDFEOS2_FIXTURES[0].name
    staged.parent.mkdir(parents=True)
    shutil.copyfile(HDFEOS2_FIXTURES[0], staged)

    record = {
        "schema_version": SCHEMA_VERSION,
        "ncarnate_version": "0.0.0",
        "ruleset_version": RULESET_VERSION,
        "mode": "metadata",
        "audited_at": "2026-01-01T00:00:00Z",
        "root": str(archive),
        "path": HDFEOS2_FIXTURES[0].name,
        "size_bytes": staged.stat().st_size,
        "sha256": sha256_of_file(str(staged)),
        "format": detect_format(str(staged)).name,
        "status": "ready",
        "structures": [],
        "issues": [],
        "plan": {"operation": "convert"},
    }
    manifest = workdir / "m.jsonl"
    manifest.write_text(json.dumps(record) + "\n", encoding="utf-8")

    completed = _run_code(
        f"""
        import json
        from ncarnate.convert import ConvertOptions, convert_manifest
        result = convert_manifest({str(manifest)!r}, ConvertOptions(
            out_dir={str(out_dir)!r}, allow_manifest_root=True,
        ))
        print(json.dumps([[r.path, r.code] for r in result.failed]))
        """,
        no_pyhdf_env,
    )

    assert completed.returncode == 0, completed.stderr
    ((failed_path, code),) = json.loads(completed.stdout)
    assert failed_path == HDFEOS2_FIXTURES[0].name
    assert code == HDF4_UNAVAILABLE_CODE           # the stable code, not None


def test_manifest_summary_renders_stable_code(no_pyhdf_env, workdir):
    # The operator seam (F2): the same failure rendered through the real CLI
    # carries the stable code as a [CODE] prefix in the run summary, so a
    # manifest run's failures are as scriptable (grep stdout) as the
    # one-file path's, and the run exits nonzero.
    from ncarnate.audit.codes import RULESET_VERSION
    from ncarnate.audit.models import SCHEMA_VERSION
    from ncarnate.formats import detect_format
    from ncarnate.hashing import sha256_of_file

    archive = workdir / "archive"
    out_dir = workdir / "modern"
    staged = archive / HDFEOS2_FIXTURES[0].name
    staged.parent.mkdir(parents=True)
    shutil.copyfile(HDFEOS2_FIXTURES[0], staged)

    record = {
        "schema_version": SCHEMA_VERSION,
        "ncarnate_version": "0.0.0",
        "ruleset_version": RULESET_VERSION,
        "mode": "metadata",
        "audited_at": "2026-01-01T00:00:00Z",
        "root": str(archive),
        "path": HDFEOS2_FIXTURES[0].name,
        "size_bytes": staged.stat().st_size,
        "sha256": sha256_of_file(str(staged)),
        "format": detect_format(str(staged)).name,
        "status": "ready",
        "structures": [],
        "issues": [],
        "plan": {"operation": "convert"},
    }
    manifest = workdir / "m.jsonl"
    manifest.write_text(json.dumps(record) + "\n", encoding="utf-8")

    completed = _run(
        ["-m", "ncarnate", "convert", "--manifest", str(manifest),
         "--out-dir", str(out_dir), "--root", str(archive)],
        no_pyhdf_env,
    )

    assert completed.returncode != 0               # a selected record failed
    assert f"[{HDF4_UNAVAILABLE_CODE}]" in completed.stdout  # scriptable prefix
    assert "Traceback" not in completed.stderr
