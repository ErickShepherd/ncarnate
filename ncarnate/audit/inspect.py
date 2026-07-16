#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

Metadata-only inspection: one file -> raw facts, **without** reading any
science array (institutions scan terabytes). ``inspect_file`` composes an
array-free pass from parts that already exist (design §The metadata-only
inspection path):

* HDF4/HDF-EOS2 via ``pyhdf`` ``.info()`` / attribute reads and a single
  ``StructMetadata`` parse — reusing ``hdf4._read_attributes`` /
  ``_structmetadata_text`` / ``_field_index`` and never calling ``SDS.get()``
  (the science-array read at ``hdf4.py:434``).
* netCDF3/HDF5 via a ``netCDF4.Dataset`` structure walk (dims/vars/types/
  attrs, no values), flagged ``already_modern``, with ``structures``
  populated for modern files too (KD10).

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

from __future__ import annotations

# Standard library imports.
from dataclasses import dataclass, field
from typing import Any

# Third party imports.
import netCDF4 as nc

# Local application imports.
from ncarnate.audit.models import StructureAudit
from ncarnate.eos import structmeta
from ncarnate.formats import FileFormat, detect_format


@dataclass
class VariableFacts:

    '''

    One variable/SDS at metadata depth: its name, rank, shape, dtype, and
    attributes — never its values.

    '''

    name       : str
    rank       : int
    shape      : tuple
    dtype      : Any
    attributes : dict[str, Any] = field(default_factory=dict)


@dataclass
class FileFacts:

    '''

    The raw facts about one file, produced without any array read.
    ``classify.py`` turns these into a status + issues.

    '''

    format         : str
    already_modern : bool
    variables      : list[VariableFacts] = field(default_factory=list)
    structures     : list[StructureAudit] = field(default_factory=list)
    dimensions     : dict[str, int] = field(default_factory=dict)
    attributes     : dict[str, Any] = field(default_factory=dict)
    eos_metadata   : "structmeta.EosStructMetadata | None" = None


def _inspect_hdf4(path : str, file_format : FileFormat) -> FileFacts:

    # Acquired here, not at module level, so importing the audit stack
    # never touches the HDF4 runtime (KD-L3): pyhdf has no Windows pip
    # wheel, and only this HDF4-dispatch path actually needs it. The gate
    # turns a missing runtime into the stable HDF4_RUNTIME_UNAVAILABLE
    # blocker (KD-L4) — `issue_for_exception` maps it into the record and
    # the whole-archive scan continues. Once the gate passes, pyhdf itself
    # is importable (ncarnate.hdf4 already imported pyhdf.SD).
    from ncarnate.hdf4_runtime import require_hdf4_runtime

    hdf4 = require_hdf4_runtime()

    from pyhdf.SD import SD, SDC

    _DFNT_DTYPES         = hdf4._DFNT_DTYPES
    _read_attributes     = hdf4._read_attributes
    _structmetadata_text = hdf4._structmetadata_text

    source = SD(path, SDC.READ)

    try:

        dataset_count, attribute_count = source.info()

        file_attributes = _read_attributes(source, attribute_count)

        # Reuse the converter's single ODL parse (hdf4.py:357): the
        # (potentially many-part) StructMetadata text is joined and parsed
        # exactly once, never re-parsed.
        metadata_text = _structmetadata_text(file_attributes)
        eos_metadata  = (
            structmeta.parse(metadata_text)
            if metadata_text is not None else None
        )

        variables : list[VariableFacts] = []

        for index in range(dataset_count):

            dataset = source.select(index)

            try:

                name, rank, shape, dfnt_code, attr_count = dataset.info()

                if rank == 1 and not isinstance(shape, (list, tuple)):

                    shape = [shape]

                variables.append(VariableFacts(
                    name       = name,
                    rank       = rank,
                    shape      = tuple(shape),
                    # None where the HDF4 type code is outside the v2 set;
                    # classify.py maps that to UNSUPPORTED_TYPE (no raise here).
                    dtype      = _DFNT_DTYPES.get(dfnt_code),
                    attributes = _read_attributes(dataset, attr_count),
                ))

            finally:

                dataset.endaccess()

    finally:

        source.end()

    structures = _hdf_eos_structures(eos_metadata)

    return FileFacts(
        format         = file_format.name,
        already_modern = False,
        variables      = variables,
        structures     = structures,
        eos_metadata   = eos_metadata,
    )


def _hdf_eos_structures(
    eos_metadata : "structmeta.EosStructMetadata | None",
) -> list[StructureAudit]:

    '''

    The GRID/SWATH structures at metadata depth (KD10). Projection and
    geolocation plan are filled in later by classification/decoration; here
    only the structure's type and name are known.

    '''

    if eos_metadata is None:

        return []

    structures = [
        StructureAudit(type="GRID", name=eos_grid_.name)
        for eos_grid_ in eos_metadata.grids
    ]
    structures += [
        StructureAudit(type="SWATH", name=eos_swath_.name)
        for eos_swath_ in eos_metadata.swaths
    ]

    return structures


def _walk_netcdf_group(
    group,
    prefix : str,
    variables : list[VariableFacts],
    structures : list[StructureAudit],
) -> None:

    # KD10: every group is a structure, so even a flat file yields >= 1.
    structures.append(StructureAudit(type="GROUP", name=prefix or "/"))

    for name, variable in group.variables.items():

        variables.append(VariableFacts(
            name       = name,
            # `.datatype` (not `.dtype`) so a user-defined type surfaces as a
            # CompoundType/VLType/EnumType object, exactly what
            # core._copy_variables rejects; `.dtype` would flatten a compound
            # to a structured np.dtype and hide it from classify.
            rank       = len(variable.dimensions),
            shape      = tuple(variable.shape),
            dtype      = variable.datatype,
            attributes = {a: variable.getncattr(a) for a in variable.ncattrs()},
        ))

    for name, subgroup in group.groups.items():

        _walk_netcdf_group(subgroup, f"{prefix}/{name}", variables, structures)


def _inspect_netcdf(path : str, file_format : FileFormat) -> FileFacts:

    variables  : list[VariableFacts] = []
    structures : list[StructureAudit] = []

    with nc.Dataset(path, "r") as dataset:

        _walk_netcdf_group(dataset, "", variables, structures)

        dimensions = {
            name: dimension.size
            for name, dimension in dataset.dimensions.items()
        }
        attributes = {
            name: dataset.getncattr(name) for name in dataset.ncattrs()
        }

    return FileFacts(
        format         = file_format.name,
        already_modern = True,
        variables      = variables,
        structures     = structures,
        dimensions     = dimensions,
        attributes     = attributes,
        eos_metadata   = None,
    )


def inspect_file(path : str) -> FileFacts:

    '''

    Inspects ``path`` at metadata depth and returns its :class:`FileFacts`.
    Never opens science arrays, never touches the network, never writes.

    '''

    file_format = detect_format(path)

    if file_format is FileFormat.HDF4:

        return _inspect_hdf4(path, file_format)

    if file_format in (FileFormat.HDF5, FileFormat.NETCDF3):

        return _inspect_netcdf(path, file_format)

    # UNKNOWN / non-science: nothing to walk; classify.py records `unknown`.
    return FileFacts(format=file_format.name, already_modern=False)
