#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

Parses HDF-EOS2 ``StructMetadata.0`` (ODL/PVL text) into typed grid and
swath models. The parser handles real nested ``GROUP=``/``OBJECT=``
structure — dimension maps, multi-grid files, and index maps need more
than regex spot-lifts.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

# Standard library imports.
import dataclasses

# Local application imports.
from ncarnate.errors import EosParseError


@dataclasses.dataclass
class EosField:

    '''A grid data field, swath data field, or swath geolocation field.'''

    name       : str
    data_type  : str
    dimensions : tuple[str, ...]


@dataclasses.dataclass
class EosDimensionMap:

    '''A swath geolocation→data dimension mapping: geolocation pixel
    ``g`` sits at data index ``offset + increment * g``.'''

    geo_dimension  : str
    data_dimension : str
    offset         : int
    increment      : int


@dataclasses.dataclass
class EosGrid:

    '''One HDF-EOS2 GRID structure.'''

    name               : str
    x_dim              : int
    y_dim              : int
    upper_left         : tuple[float, float]
    lower_right        : tuple[float, float]
    projection         : str
    proj_params        : tuple[float, ...]
    sphere_code        : int
    grid_origin        : str
    zone_code          : int | None
    pixel_registration : str
    dimensions         : dict[str, int]
    data_fields        : list[EosField]


@dataclasses.dataclass
class EosSwath:

    '''One HDF-EOS2 SWATH structure.'''

    name           : str
    dimensions     : dict[str, int]
    dimension_maps : list[EosDimensionMap]
    geo_fields     : list[EosField]
    data_fields    : list[EosField]
    has_index_maps : bool
    has_merged_fields : bool


@dataclasses.dataclass
class EosStructMetadata:

    '''The parsed content of a file's concatenated StructMetadata parts.'''

    grids  : list[EosGrid]
    swaths : list[EosSwath]


class _Node:

    '''One ODL GROUP/OBJECT block: attributes plus ordered children.'''

    def __init__(self, name : str) -> None:

        self.name       = name
        self.attributes = {}
        self.children   = []

    def child(self, name : str) -> "_Node | None":

        for node in self.children:

            if node.name == name:

                return node

        return None


def _parse_scalar(text : str):

    text = text.strip()

    if text.startswith('"') and text.endswith('"'):

        return text[1:-1]

    try:

        return int(text)

    except ValueError:

        pass

    try:

        return float(text)

    except ValueError:

        pass

    return text


def _parse_value(text : str):

    text = text.strip()

    if text.startswith("(") and text.endswith(")"):

        inner = text[1:-1].strip()

        if not inner:

            return ()

        return tuple(_parse_scalar(part) for part in inner.split(","))

    return _parse_scalar(text)


def _logical_lines(text : str) -> list[str]:

    # Joins physical lines until parentheses balance, so multi-line
    # tuples (long DimLists) parse as one assignment.
    lines   = []
    pending = ""

    for raw in text.splitlines():

        line = raw.strip()

        if not line:

            continue

        pending = f"{pending} {line}".strip() if pending else line

        if pending.count("(") > pending.count(")"):

            continue

        lines.append(pending)
        pending = ""

    if pending:

        raise EosParseError(
            f"StructMetadata ends with unbalanced parentheses: {pending!r}"
        )

    return lines


def _parse_odl(text : str) -> _Node:

    root  = _Node("")
    stack = [root]

    for line in _logical_lines(text):

        if line == "END":

            break

        if "=" not in line:

            raise EosParseError(f"Malformed StructMetadata line: {line!r}")

        key, _, value = line.partition("=")
        key   = key.strip()
        value = value.strip()

        if key in ("GROUP", "OBJECT"):

            node = _Node(value)

            stack[-1].children.append(node)
            stack.append(node)

        elif key in ("END_GROUP", "END_OBJECT"):

            if len(stack) < 2 or stack[-1].name != value:

                raise EosParseError(
                    f"Mismatched {key}={value} (open block: "
                    f"{stack[-1].name!r})"
                )

            stack.pop()

        else:

            stack[-1].attributes[key] = _parse_value(value)

    if len(stack) != 1:

        raise EosParseError(
            f"Unclosed StructMetadata block: {stack[-1].name!r}"
        )

    return root


def _require_attr(node : _Node, key : str, context : str):

    if key not in node.attributes:

        raise EosParseError(f"{context}: missing required {key}")

    return node.attributes[key]


def _parse_dimensions(container : _Node | None) -> dict[str, int]:

    dimensions = {}

    if container is not None:

        for node in container.children:

            name = _require_attr(node, "DimensionName", node.name)
            size = _require_attr(node, "Size", node.name)

            dimensions[name] = int(size)

    return dimensions


def _parse_fields(container  : _Node | None,
                  name_key   : str) -> list[EosField]:

    fields = []

    if container is not None:

        for node in container.children:

            dim_list = node.attributes.get("DimList", ())

            if not isinstance(dim_list, tuple):

                dim_list = (dim_list,)

            fields.append(
                EosField(
                    name       = _require_attr(node, name_key, node.name),
                    data_type  = str(node.attributes.get("DataType", "")),
                    dimensions = tuple(str(dim) for dim in dim_list),
                )
            )

    return fields


def _parse_grid(node : _Node) -> EosGrid:

    context     = node.name
    name        = _require_attr(node, "GridName", context)
    upper_left  = _require_attr(node, "UpperLeftPointMtrs", context)
    lower_right = _require_attr(node, "LowerRightMtrs", context)
    projection  = _require_attr(node, "Projection", context)

    proj_params = node.attributes.get("ProjParams", ())
    zone_code   = node.attributes.get("ZoneCode")

    return EosGrid(
        name               = str(name),
        x_dim              = int(_require_attr(node, "XDim", context)),
        y_dim              = int(_require_attr(node, "YDim", context)),
        upper_left         = tuple(float(v) for v in upper_left),
        lower_right        = tuple(float(v) for v in lower_right),
        projection         = str(projection),
        proj_params        = tuple(float(v) for v in proj_params),
        sphere_code        = int(node.attributes.get("SphereCode", -1)),
        grid_origin        = str(node.attributes.get("GridOrigin",
                                                     "HDFE_GD_UL")),
        zone_code          = None if zone_code is None else int(zone_code),
        pixel_registration = str(node.attributes.get("PixelRegistration",
                                                     "HDFE_CENTER")),
        dimensions         = _parse_dimensions(node.child("Dimension")),
        data_fields        = _parse_fields(node.child("DataField"),
                                           "DataFieldName"),
    )


def _parse_dimension_maps(container : _Node | None) -> list[EosDimensionMap]:

    maps = []

    if container is not None:

        for node in container.children:

            maps.append(
                EosDimensionMap(
                    geo_dimension  = str(_require_attr(node, "GeoDimension",
                                                       node.name)),
                    data_dimension = str(_require_attr(node, "DataDimension",
                                                       node.name)),
                    offset         = int(_require_attr(node, "Offset",
                                                       node.name)),
                    increment      = int(_require_attr(node, "Increment",
                                                       node.name)),
                )
            )

    return maps


def _parse_swath(node : _Node) -> EosSwath:

    index_maps    = node.child("IndexDimensionMap")
    merged_fields = node.child("MergedFields")

    return EosSwath(
        name           = str(_require_attr(node, "SwathName", node.name)),
        dimensions     = _parse_dimensions(node.child("Dimension")),
        dimension_maps = _parse_dimension_maps(node.child("DimensionMap")),
        geo_fields     = _parse_fields(node.child("GeoField"),
                                       "GeoFieldName"),
        data_fields    = _parse_fields(node.child("DataField"),
                                       "DataFieldName"),
        has_index_maps = bool(index_maps is not None
                              and index_maps.children),
        has_merged_fields = bool(merged_fields is not None
                                 and merged_fields.children),
    )


def parse(text : str) -> EosStructMetadata:

    '''

    Parses concatenated ``StructMetadata.0..N`` text into typed grid and
    swath models. Callers must join the parts before calling (files over
    32 KB split the attribute).

    '''

    root   = _parse_odl(text)
    grids  = []
    swaths = []

    grid_structure = root.child("GridStructure")

    if grid_structure is not None:

        grids = [_parse_grid(node) for node in grid_structure.children]

    swath_structure = root.child("SwathStructure")

    if swath_structure is not None:

        swaths = [_parse_swath(node) for node in swath_structure.children]

    return EosStructMetadata(grids = grids, swaths = swaths)
