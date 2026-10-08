"""Opt-in exact-granule conversions; no silent substitution of trimmed fixtures."""
import json
import importlib.util
from pathlib import Path
import numpy as np
import pytest
from ncarnate import prepare, execute_prepared, check_materializable
from ncarnate.hashing import sha256_of_file
from conftest import GRANULE_DIR

CATALOG = json.loads((Path(__file__).parent / "fixtures/corpus.json").read_text())


@pytest.mark.raw_granules
@pytest.mark.parametrize("entry", CATALOG["granules"], ids=lambda e: e["name"])
def test_exact_full_granule(entry, tmp_path):
    source = GRANULE_DIR / entry["name"]
    if not source.is_file():
        pytest.skip("exact original not acquired; run tools/corpus.py")
    assert sha256_of_file(str(source)) == entry["sha256"]
    record = execute_prepared(prepare(source, tmp_path / "converted.nc"))
    check_materializable(record)
    assert record["source"]["sha256"] == entry["sha256"]
    assert record["verification"]["status"] == "verified"


@pytest.mark.raw_granules
def test_myd05_fixture_regenerates_from_exact_source(tmp_path):
    """Pin the trimmed fixture's complete decoded contract, including omissions."""
    root = Path(__file__).parent / "fixtures"
    provenance = json.loads((root / "data/hdfeos2/myd05_trim.provenance.json").read_text())
    source = GRANULE_DIR / provenance["source_granule"]
    if not source.is_file():
        pytest.skip("exact original not acquired; run tools/corpus.py")
    spec = importlib.util.spec_from_file_location("trim_hdfeos2", root / "trim_hdfeos2.py")
    trimmer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trimmer)
    assert sha256_of_file(str(source)) == provenance["source_sha256"]
    regenerated = trimmer.trim_myd05(GRANULE_DIR, tmp_path)
    from pyhdf.SD import SD, SDC

    def compare_attributes(left, right, count):
        for index in range(count):
            a, b = left.attr(index), right.attr(index)
            assert a.info() == b.info()
            np.testing.assert_equal(a.get(), b.get())

    expected = SD(str(root / "data/hdfeos2/myd05_trim.hdf"), SDC.READ)
    actual = SD(str(regenerated), SDC.READ)
    try:
        assert expected.info() == actual.info()
        count, attributes = expected.info()
        compare_attributes(expected, actual, attributes)
        for index in range(count):
            a, b = expected.select(index), actual.select(index)
            try:
                assert a.info() == b.info()
                compare_attributes(a, b, a.info()[4])
                np.testing.assert_array_equal(a.get(), b.get())
                for dim in range(a.info()[1]):
                    assert a.dim(dim).info() == b.dim(dim).info()
                    compare_attributes(a.dim(dim), b.dim(dim), a.dim(dim).info()[3])
            finally:
                a.endaccess()
                b.endaccess()
    finally:
        expected.end()
        actual.end()
    generated_provenance = json.loads(regenerated.with_suffix(".provenance.json").read_text())
    assert generated_provenance["trim"] == provenance["trim"]
