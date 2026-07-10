"""The CLI pre-dispatch shim (design §CLI integration).

`ncarnate`'s CLI is a flat argparse command with no subparsers. The shim
dispatches in ``main()`` *before* argparse: ``audit`` routes to the audit
entry point, ``convert`` is an explicit alias for today's flat behavior,
and the bare ``ncarnate <path>`` form is untouched.

These tests fix that contract before the paired [impl] shim lands, so they
are RED now. The audit entry (``ncarnate.audit.main``) is stubbed by a spy
so dispatch is tested in isolation from the audit machinery, which lands in
later increments. ``tests/test_cli.py`` is never edited.
"""

import sys

import pytest

from ncarnate.cli import main
from ncarnate.constants import __version__

from conftest import NETCDF_FIXTURES, stage


class _Spy:
    """Records the argv each call receives; returns a fixed exit code."""

    def __init__(self, result: int = 0):
        self.calls: list[list[str]] = []
        self.result = result

    def __call__(self, argv):
        self.calls.append(list(argv))
        return self.result


# --- `audit` routes to the audit entry point --------------------------

def test_audit_subcommand_routes_to_audit_entry(monkeypatch):
    spy = _Spy(result=0)
    # The shim dispatches to ncarnate.audit.main; stub it so this test
    # exercises only the routing, not the (not-yet-built) audit path.
    monkeypatch.setattr("ncarnate.audit.main", spy, raising=False)
    monkeypatch.setattr(sys, "argv", ["ncarnate", "audit", "some/path", "-r"])
    rc = main()
    assert spy.calls == [["some/path", "-r"]]   # argv after "audit"
    assert rc == 0                              # shim returns the entry's code


def test_audit_entry_exit_code_propagates(monkeypatch):
    monkeypatch.setattr("ncarnate.audit.main", _Spy(result=3), raising=False)
    monkeypatch.setattr(sys, "argv", ["ncarnate", "audit", "some/path"])
    assert main() == 3


# --- bare `ncarnate <path>` is unchanged (legacy behavior) ------------

def test_bare_path_is_unchanged_legacy_behavior(monkeypatch, workdir):
    spy = _Spy()
    monkeypatch.setattr("ncarnate.audit.main", spy, raising=False)
    monkeypatch.setattr(sys, "argv", ["ncarnate", str(workdir / "missing.nc")])
    assert main() == 2          # legacy: a missing file exits 2
    assert spy.calls == []      # the audit branch was not taken


def test_bare_version_flag_unchanged(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ncarnate", "-V"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    assert f"ncarnate {__version__}" in capsys.readouterr().out


# --- `convert` is an alias for the legacy flat behavior ---------------

def test_convert_alias_matches_legacy_success(monkeypatch, workdir):
    spy = _Spy()
    monkeypatch.setattr("ncarnate.audit.main", spy, raising=False)
    src = stage(NETCDF_FIXTURES[0], workdir)
    monkeypatch.setattr(
        sys, "argv", ["ncarnate", "convert", "--no-overwrite", str(src)]
    )
    # Identical outcome to the bare `ncarnate --no-overwrite <src>` form
    # (cf. tests/test_cli.py::test_successful_run_exits_0).
    assert main() == 0
    assert (workdir / f"{NETCDF_FIXTURES[0].stem}_recompressed.nc").exists()
    assert spy.calls == []      # convert is legacy, never the audit entry


def test_convert_alias_missing_file_exits_2(monkeypatch, workdir):
    monkeypatch.setattr(
        sys, "argv", ["ncarnate", "convert", str(workdir / "missing.nc")]
    )
    assert main() == 2          # same as the bare missing-file case


# --- the shim requires no edits to the existing CLI suite -------------

def test_existing_cli_suite_cases_intact():
    # The shim must not force rewriting existing CLI tests to pass; guard
    # that the 8 documented cases still exist in tests/test_cli.py.
    import test_cli

    expected = {
        "test_version_works_without_path",
        "test_missing_path_exits_2",
        "test_unsupported_extension_exits_2",
        "test_successful_run_exits_0",
        "test_failing_file_exits_1",
        "test_directory_scan_processes_supported_files",
        "test_overlapping_arguments_deduplicated",
        "test_no_geolocation_flag_reaches_conversion",
    }
    present = {name for name in dir(test_cli) if name.startswith("test_")}
    assert expected <= present
