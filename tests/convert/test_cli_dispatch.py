"""`convert` sub-parser dispatch tests (design §Invocation shape, KD1).

Increment 2.3's integration seam at the argparse layer: the `convert` verb must
grow its own parser that routes `--manifest` runs to the convert engine while
keeping the legacy positional form (and the bare `ncarnate <path>` form)
byte-for-byte unchanged. These tests drive the real CLI entry (`cli.main` via
`sys.argv`, exactly as `tests/test_cli.py` does) and assert *observable*
dispatch — they deliberately do not pin the impl's internal entry-point
structure (a `ncarnate.convert.main` vs inline wiring is the impl author's
call).

Red until the paired [impl] wires the parser:
- routing (`convert --manifest …` reaching the engine) fails now — the legacy
  fall-through rejects `--manifest` as unrecognized;
- mutual exclusion (KD1) fails now — the current error is "unrecognized
  arguments", not argparse's mutually-exclusive rejection.
The legacy-form and bare-form tests are live positive controls (must stay
green), and a guard test pins that `tests/test_cli.py` is left untouched.
"""

import json
import os
import sys

import pytest

from conftest import NETCDF_FIXTURES, stage

from ncarnate.audit.codes import RULESET_VERSION
from ncarnate.audit.models import SCHEMA_VERSION
from ncarnate.cli import main


def run_cli(monkeypatch, *argv: str) -> int:
    """Drive the CLI exactly as tests/test_cli.py does — monkeypatch argv and
    call the real entry point, so dispatch is exercised end-to-end."""
    monkeypatch.setattr(sys, "argv", ["ncarnate", *argv])
    return main()


def _skipped_manifest(workdir) -> str:
    """A one-line manifest whose only record is a non-selected status — the
    status filter skips it before any source I/O, so the run needs no staged
    file and exits 0. Enough to prove the manifest was *routed to the engine*."""
    record = {
        "schema_version": SCHEMA_VERSION,
        "ncarnate_version": "0.0.0",
        "ruleset_version": RULESET_VERSION,
        "mode": "metadata",
        "audited_at": "2026-01-01T00:00:00Z",
        "root": str(workdir),
        "path": "some/granule.hdf",
        "size_bytes": 0,
        "sha256": None,
        "format": "HDF4",
        "status": "unsupported",     # not in the default {"ready"} -> skipped
        "structures": [],
        "issues": [],
        "plan": None,
    }
    manifest = workdir / "m.jsonl"
    manifest.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return str(manifest)


# --- routing: convert --manifest reaches the convert engine ------------

def test_convert_manifest_routes_to_the_convert_engine(monkeypatch, workdir):
    manifest = _skipped_manifest(workdir)
    # Routed to the engine, the sole record is skipped by the status filter,
    # so the run exits 0. The legacy fall-through instead rejects --manifest
    # as unrecognized (SystemExit 2) — which is why this is red until wired.
    assert run_cli(
        monkeypatch, "convert",
        "--manifest", manifest, "--out-dir", str(workdir / "out"),
    ) == 0


# --- KD1: --manifest and positional path... are mutually exclusive -----

def test_manifest_and_positional_paths_are_mutually_exclusive(
    monkeypatch, workdir, capsys
):
    manifest = _skipped_manifest(workdir)
    src = stage(NETCDF_FIXTURES[0], workdir)
    with pytest.raises(SystemExit) as excinfo:
        run_cli(
            monkeypatch, "convert",
            "--manifest", manifest, "--out-dir", str(workdir / "out"),
            str(src),                       # both manifest AND a positional
        )
    assert excinfo.value.code == 2
    # argparse's mutually-exclusive rejection — distinct from the legacy
    # fall-through's "unrecognized arguments" (which is the red-now state).
    assert "not allowed with" in capsys.readouterr().err.lower()


# --- legacy positional convert form is unchanged (positive control) ----

def test_legacy_convert_positional_form_still_works(monkeypatch, workdir):
    src = stage(NETCDF_FIXTURES[0], workdir)
    assert run_cli(monkeypatch, "convert", "--no-overwrite", str(src)) == 0
    assert (workdir / f"{NETCDF_FIXTURES[0].stem}_recompressed.nc").exists()


# --- bare `ncarnate <path>` form is unchanged (positive control) -------

def test_bare_path_form_unchanged(monkeypatch, workdir):
    src = stage(NETCDF_FIXTURES[0], workdir)
    assert run_cli(monkeypatch, "--no-overwrite", str(src)) == 0
    assert (workdir / f"{NETCDF_FIXTURES[0].stem}_recompressed.nc").exists()


# --- the 8 existing tests/test_cli.py cases must be left untouched ------

def test_existing_cli_test_suite_is_untouched():
    """The impl must not edit tests/test_cli.py — its 8 cases pin the legacy
    contract. Guard the count so an accidental deletion/rewrite is caught."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "test_cli.py"
    )
    with open(path, encoding="utf-8") as stream:
        text = stream.read()
    assert text.count("def test_") == 8
