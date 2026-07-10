"""Metadata-only inspection (design §The metadata-only inspection path).

``inspect_file(path)`` returns raw facts about one file **without** reading
science arrays: HDF4/HDF-EOS2 via ``pyhdf`` ``.info()`` / attribute reads
plus a single ``StructMetadata`` parse (never ``SDS.get()``, the array read
at ``hdf4.py:434``); netCDF3/HDF5 via a ``netCDF4.Dataset`` structure walk
with no values. ``structures`` is populated for modern files too (KD10).

RED until the paired [impl] inspect unit lands ``ncarnate.audit.inspect``.

Facts contract this [test] item fixes (duck-typed here so the impl may name
its internal classes freely):
  facts.format          -> str  ("HDF4" | "HDF5" | "NETCDF3" | "UNKNOWN")
  facts.already_modern  -> bool
  facts.variables       -> seq of objects with .name/.rank/.shape/.dtype
  facts.structures      -> seq (KD10: non-empty for modern files too)
  facts.dimensions      -> mapping
  facts.attributes      -> mapping
  facts.eos_metadata    -> EosStructMetadata | None
"""

import pyhdf.SD

from ncarnate.audit.inspect import inspect_file
from ncarnate.eos.structmeta import EosStructMetadata

from conftest import HDFEOS2_FIXTURES, NETCDF_FIXTURES


# --- HDF4 / HDF-EOS2: names/ranks/shapes/dtypes + parsed StructMetadata

def test_hdf_eos2_inspection_returns_variable_facts_and_metadata():
    for fixture in HDFEOS2_FIXTURES:
        facts = inspect_file(str(fixture))

        assert facts.format == "HDF4"
        assert facts.already_modern is False

        assert facts.variables, f"{fixture.name}: no variable facts"
        for var in facts.variables:
            assert isinstance(var.name, str) and var.name
            assert isinstance(var.rank, int) and var.rank >= 0
            assert len(var.shape) == var.rank
            assert var.dtype is not None

        # Every committed HDF-EOS2 fixture carries StructMetadata, parsed
        # once (design §metadata-only path, reusing the single-parse flow).
        assert isinstance(facts.eos_metadata, EosStructMetadata)
        assert facts.eos_metadata.grids or facts.eos_metadata.swaths


def test_hdf_eos2_inspection_never_reads_arrays(monkeypatch):
    # SDS.get() is the science-array read (hdf4.py:434). Metadata mode must
    # never call it; make any call fail loudly. (Attribute reads go through
    # SDAttr.get, a different class, so they are unaffected.)
    def _forbidden_get(self, *args, **kwargs):
        raise AssertionError(
            "inspect_file read an SDS science array via SDS.get(); "
            "metadata mode must never touch arrays."
        )

    monkeypatch.setattr(pyhdf.SD.SDS, "get", _forbidden_get)

    for fixture in HDFEOS2_FIXTURES:
        facts = inspect_file(str(fixture))
        # Inspection still produced metadata facts, without the array read.
        assert facts.variables


# --- netCDF3 / HDF5: dims/vars/types/attrs + already_modern + structures

def test_netcdf_inspection_walks_structure_and_flags_modern():
    total_variables = 0
    for fixture in NETCDF_FIXTURES:
        facts = inspect_file(str(fixture))

        assert facts.format in ("HDF5", "NETCDF3")
        assert facts.already_modern is True
        assert facts.eos_metadata is None

        # KD10: structures[] populated for modern files too.
        assert facts.structures, f"{fixture.name}: no structures"

        assert isinstance(facts.dimensions, dict)
        assert isinstance(facts.attributes, dict)

        total_variables += len(facts.variables)

    # The structure walk surfaces variables (the "vars/types" of the walk).
    assert total_variables > 0


def test_netcdf_variable_facts_have_types_and_shapes():
    seen = 0
    for fixture in NETCDF_FIXTURES:
        for var in inspect_file(str(fixture)).variables:
            assert isinstance(var.name, str) and var.name
            assert var.dtype is not None
            assert len(var.shape) == var.rank
            seen += 1
    assert seen > 0
