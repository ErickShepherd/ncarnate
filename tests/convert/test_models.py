"""Unit tests for the convert data models (ncarnate.convert.models).

``ConvertOptions`` carries how a manifest-driven convert run behaves; the
result dataclasses carry the per-file outcome (converted / skipped / failed,
each with a path and a reason). These freeze the option defaults the spec
pins — ``statuses == {"ready"}`` (KD8) and ``in_place`` False (KD3) — and the
shape of the result record. ``ncarnate.convert.models`` does not exist yet;
these fail until the paired [impl] unit lands it.
"""

from dataclasses import fields

from ncarnate.convert.models import (
    ConvertOptions,
    ConvertRecord,
    ConvertResult,
)


# --- ConvertOptions ----------------------------------------------------

def test_convert_options_constructs_with_out_dir():
    """The out-dir path form (the non-destructive default, KD3) constructs."""
    options = ConvertOptions(out_dir="./modern")
    assert options.out_dir == "./modern"


def test_convert_options_status_default_is_ready_only():
    """KD8: the conservative default converts only the ``ready`` status."""
    assert ConvertOptions(out_dir="./modern").statuses == {"ready"}


def test_convert_options_in_place_defaults_false():
    """KD3: the archive is never mutated unless the operator opts in."""
    assert ConvertOptions(out_dir="./modern").in_place is False


def test_convert_options_safety_flags_default_off():
    """The unverified-hash override and skip-existing default to off."""
    options = ConvertOptions(out_dir="./modern")
    assert options.allow_unverified is False
    assert options.skip_existing is False


def test_convert_options_root_defaults_none():
    """``root`` (the containment base for source paths) defaults to None."""
    assert ConvertOptions(out_dir="./modern").root is None


def test_convert_options_encoding_flags_mirror_recompress_defaults():
    """The encoding flags compose with recompress and share its defaults."""
    options = ConvertOptions(out_dir="./modern")
    assert options.zlib is True
    assert options.shuffle is True
    assert options.complevel == 7
    assert options.geolocation is True


def test_convert_options_fields_are_overridable():
    """Every documented field is a real constructor argument."""
    options = ConvertOptions(
        out_dir="./out",
        statuses={"ready", "already_modern"},
        allow_unverified=True,
        in_place=True,
        skip_existing=True,
        root="/data/archive",
        zlib=False,
        shuffle=False,
        complevel=1,
        geolocation=False,
    )
    assert options.statuses == {"ready", "already_modern"}
    assert options.allow_unverified is True
    assert options.in_place is True
    assert options.skip_existing is True
    assert options.root == "/data/archive"
    assert options.complevel == 1


# --- ConvertRecord / ConvertResult -------------------------------------

def test_convert_record_carries_path_and_reason():
    """A per-file outcome names the file and the reason (skip/fail cause)."""
    record = ConvertRecord(path="a/b.hdf", reason="sha256 mismatch")
    assert record.path == "a/b.hdf"
    assert record.reason == "sha256 mismatch"


def test_convert_record_reason_defaults_none():
    """A successful conversion needs no reason."""
    assert ConvertRecord(path="a/b.nc").reason is None


def test_convert_result_defaults_to_empty_lists():
    """An empty run has three empty outcome lists."""
    result = ConvertResult()
    assert result.converted == []
    assert result.skipped == []
    assert result.failed == []


def test_convert_result_has_the_three_outcome_lists():
    """The result freezes exactly the converted / skipped / failed triad."""
    names = {f.name for f in fields(ConvertResult)}
    assert {"converted", "skipped", "failed"} <= names


def test_convert_result_lists_hold_convert_records():
    """Each outcome list holds ConvertRecord entries with path + reason."""
    result = ConvertResult(
        converted=[ConvertRecord(path="ok.nc")],
        skipped=[ConvertRecord(path="blk.hdf", reason="blocker")],
        failed=[ConvertRecord(path="bad.hdf", reason="recompress error")],
    )
    assert result.converted[0].path == "ok.nc"
    assert result.skipped[0].reason == "blocker"
    assert result.failed[0].reason == "recompress error"
