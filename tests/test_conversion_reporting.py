import shutil
from ncarnate.convert import main, convert_manifest, ConvertOptions
from ncarnate.journal import ResultJournal
from ncarnate.hashing import sha256_of_file
from conftest import NETCDF_FIXTURES
from test_convert_collisions import _record, _write_manifest


def setup_manifest(tmp_path, *, unknown=False):
    root = tmp_path / "root"
    root.mkdir()
    src = root / "data.nc"
    if unknown:
        src.write_bytes(b"not a scientific container")
    else:
        shutil.copyfile(NETCDF_FIXTURES[0], src)
    manifest = _write_manifest(tmp_path, [_record(root, src.name, src, plan={"operation": "recompress"})])
    return root, src, manifest, tmp_path / "out"


def test_unknown_format_leaves_output_tree_absent(tmp_path):
    root, _, manifest, out = setup_manifest(tmp_path, unknown=True)
    result = convert_manifest(manifest, ConvertOptions(root=str(root), out_dir=str(out)))
    assert result.failed[0].code == "FORMAT_UNRECOGNIZED"
    assert not out.exists()


def test_unavailable_journal_refuses_before_conversion(tmp_path, capsys):
    root, _, manifest, out = setup_manifest(tmp_path)
    assert main(["--manifest", manifest, "--root", str(root), "--out-dir", str(out),
                 "--result-journal", str(tmp_path / "missing" / "journal")]) == 2
    assert not out.exists()
    assert "converted" in capsys.readouterr().out.lower()


def test_late_journal_failure_retains_summary_and_output(tmp_path, monkeypatch, capsys):
    root, src, manifest, out = setup_manifest(tmp_path)
    original = sha256_of_file(str(src))
    def fail(*args):
        raise OSError("injected failure")
    monkeypatch.setattr(ResultJournal, "publish", fail)
    assert main(["--manifest", manifest, "--root", str(root), "--out-dir", str(out),
                 "--result-journal", str(tmp_path / "journal")]) == 3
    assert (out / src.name).exists()
    assert sha256_of_file(str(src)) == original
    assert "Converted 1" in capsys.readouterr().out


def test_summary_survives_legacy_console_encoding(monkeypatch):
    import io
    import sys
    from ncarnate.convert import _print_summary
    from ncarnate.convert.models import ConvertResult, ConvertRecord
    binary = io.BytesIO()
    console = io.TextIOWrapper(binary, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", console)
    result = ConvertResult()
    result.failed.append(ConvertRecord("snow-\u2744.nc", reason="failed"))
    _print_summary(result)
    console.flush()
    assert b"failed" in binary.getvalue().lower()


def test_journal_flag_preserves_manifest_and_containment_refusals(tmp_path, capsys):
    root, _, manifest, out = setup_manifest(tmp_path)
    assert main(['--manifest', manifest, '--out-dir', str(out),
                 '--result-journal', str(tmp_path / 'journal')]) == 2
    assert 'JOURNAL_UNAVAILABLE' not in capsys.readouterr().err
    assert not (tmp_path / 'journal').exists()
    assert main(['--manifest', str(tmp_path / 'missing.jsonl'), '--root', str(root),
                 '--out-dir', str(out), '--result-journal', str(tmp_path / 'journal')]) == 2
    assert 'JOURNAL_UNAVAILABLE' not in capsys.readouterr().err
    assert not out.exists()


def test_journal_cannot_occupy_an_output_parent(tmp_path):
    root, src, _, out = setup_manifest(tmp_path)
    nested = root / 'sub'
    nested.mkdir()
    moved = nested / src.name
    src.rename(moved)
    manifest = _write_manifest(tmp_path, [_record(root, 'sub/' + src.name, moved,
                                                plan={'operation': 'recompress'})])
    out.mkdir()
    assert main(['--manifest', manifest, '--root', str(root), '--out-dir', str(out),
                 '--result-journal', str(out / 'sub')]) == 2
    assert not list(out.iterdir())
