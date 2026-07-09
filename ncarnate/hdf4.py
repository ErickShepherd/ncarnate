#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

The HDF4/HDF-EOS2 read path: opens a file with the pyhdf SD API,
enumerates its SDS datasets into an intermediate tree (one netCDF4 group
per HDF-EOS2 structure), optionally decorates the tree with reconstructed
CF geolocation, and writes/verifies the netCDF4 output.

Fidelity discipline matches the netCDF path: every SDS value is copied
raw and bit-identical, attributes keep their exact HDF4 type codes, and
EOS metadata (``StructMetadata.0`` etc.) is preserved verbatim under an
``HDFEOS_INFORMATION`` group. Geolocation reconstruction is strictly
additive.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

# Standard library imports.
import dataclasses
import re

# Third party imports.
import netCDF4 as nc
import numpy as np
from pyhdf.SD import SD, SDC

# Local application imports.
from ncarnate.eos import grid as eos_grid
from ncarnate.eos import structmeta
from ncarnate.eos import swath as eos_swath
from ncarnate.errors import NcarnateError
from ncarnate.errors import UnsupportedGeolocationError
from ncarnate.errors import UnsupportedTypeError
from ncarnate.errors import VerificationError
from ncarnate.limits import check_array_size

# HDF4 DFNT type code -> numpy dtype for SDS payloads. CHAR8 *attributes*
# become Python strings (handled before this table); CHAR8 *datasets* map
# to netCDF's native NC_CHAR ('S1') and round-trip byte-for-byte.
_DFNT_DTYPES = {
    SDC.CHAR    : np.dtype("S1"),
    SDC.UCHAR8  : np.dtype("uint8"),
    SDC.INT8    : np.dtype("int8"),
    SDC.UINT8   : np.dtype("uint8"),
    SDC.INT16   : np.dtype("int16"),
    SDC.UINT16  : np.dtype("uint16"),
    SDC.INT32   : np.dtype("int32"),
    SDC.UINT32  : np.dtype("uint32"),
    SDC.FLOAT32 : np.dtype("float32"),
    SDC.FLOAT64 : np.dtype("float64"),
}

_CHAR_CODES = (SDC.CHAR, SDC.CHAR8, SDC.UCHAR8)

# File-level attributes that make up the HDF-EOS metadata layer; they are
# preserved verbatim under the HDFEOS_INFORMATION group in the output.
_EOS_METADATA_PREFIXES = (
    "structmetadata", "coremetadata", "archivemetadata", "productmetadata"
)

_EOS_INFORMATION_GROUP = "HDFEOS_INFORMATION"


@dataclasses.dataclass
class TreeVariable:

    '''One variable of the intermediate tree the writer serializes.'''

    name       : str
    dimensions : tuple[str, ...]
    values     : np.ndarray
    attributes : dict


@dataclasses.dataclass
class TreeGroup:

    '''One group of the intermediate tree (the root has name "").'''

    name       : str
    dimensions : dict
    variables  : list
    attributes : dict
    groups     : dict

    @classmethod
    def empty(cls, name : str) -> "TreeGroup":

        return cls(
            name       = name,
            dimensions = {},
            variables  = [],
            attributes = {},
            groups     = {},
        )

    def subgroup(self, name : str) -> "TreeGroup":

        # Group names follow the same sanitization policy as variables
        # (5DaySnow grids are named 'Northern Hemisphere'); the original
        # is recorded as a group attribute.
        sanitized = sanitize_name(name)

        if sanitized not in self.groups:

            group = TreeGroup.empty(sanitized)

            if sanitized != name:

                group.attributes["hdf4_eos_name"] = name

            self.groups[sanitized] = group

        return self.groups[sanitized]

    def variable(self, name : str) -> "TreeVariable | None":

        for variable in self.variables:

            if variable.name == name:

                return variable

        return None

    def add_dimension(self, name : str, size : int) -> None:

        if name in self.dimensions and self.dimensions[name] != size:

            raise NcarnateError(
                f"Dimension {name!r} has conflicting sizes "
                f"{self.dimensions[name]} and {size} in group "
                f"{self.name or '/'}."
            )

        self.dimensions[name] = size


def sanitize_name(name : str) -> str:

    '''

    Makes an HDF4 object name legal and friendly for netCDF: ``/`` (the
    netCDF group separator) and whitespace become ``_``. Everything else
    (including ``*`` in EOS dimension names) is preserved.

    '''

    return re.sub(r"[/\s]+", "_", name)


def _attribute_value(dfnt_code : int, value):

    '''

    Types an attribute value with its exact source HDF4 type code —
    pyhdf's ``attributes()`` erases types to Python scalars, and
    re-inferring them corrupts e.g. an INT16 ``_FillValue`` to INT32.

    '''

    if dfnt_code in _CHAR_CODES:

        text = value if isinstance(value, str) else str(value)

        # HDF4 character attributes are NUL-padded fixed blocks
        # (StructMetadata parts are 32000 bytes); netCDF stores C strings,
        # so the padding cannot survive and carries no information.
        return text.rstrip("\x00")

    dtype = _DFNT_DTYPES.get(dfnt_code)

    if dtype is None:

        raise UnsupportedTypeError(
            f"Attribute uses unsupported HDF4 type code {dfnt_code}."
        )

    array = np.asarray(value, dtype = dtype)

    return array[()] if array.ndim == 0 else array


def _read_attributes(hdf_object, count : int) -> dict:

    attributes = {}

    def put(key : str, value) -> None:

        # Guard EVERY write — including the companion keys — so a hostile
        # file cannot ship a real attribute whose name equals a generated
        # companion (e.g. `foo/x` -> `foo_x__hdf4_name`, plus a real
        # `foo_x__hdf4_name`) and silently overwrite it. Because
        # verify_conversion re-reads the source through this same function,
        # an unguarded overwrite would be invisible to verification.
        if key in attributes:

            raise NcarnateError(
                f"Attribute name {key!r} collides with another attribute "
                f"or a generated companion after sanitization."
            )

        attributes[key] = value

    for index in range(count):

        attribute          = hdf_object.attr(index)
        name, dfnt_code, _ = attribute.info()
        value              = _attribute_value(dfnt_code, attribute.get())

        # Attribute names get the same sanitization as variables (MOD03
        # ships names like 'Ephemeris/Attitude Source'); the original
        # name is recorded in a companion attribute.
        sanitized = sanitize_name(name)

        if isinstance(value, str) and "\x00" in value:

            # Embedded NULs (MODIS PGE record-separator quirk) cannot
            # survive netCDF's C-string attributes; preserve the exact
            # bytes instead, with a self-describing companion.
            put(sanitized, np.frombuffer(
                value.encode("latin-1"), dtype = np.uint8
            ))
            put(f"{sanitized}__hdf4_encoding",
                "uint8 bytes of an HDF4 char8 attribute containing "
                "embedded NUL bytes")

        else:

            put(sanitized, value)

        if sanitized != name:

            put(f"{sanitized}__hdf4_name", name)

    return attributes


def _metadata_part_order(name : str) -> int:

    # EOS metadata attributes are named "<Name>.N"; order by the integer
    # suffix so that .10 follows .2 (a lexicographic sort would scramble a
    # granule whose metadata spans >= 11 parts).
    suffix = name.rsplit(".", 1)[-1]

    return int(suffix) if suffix.isdigit() else 0


def _structmetadata_text(file_attributes : dict) -> "str | None":

    parts = sorted(
        (name for name in file_attributes
         if name.lower().startswith("structmetadata")),
        key = _metadata_part_order,
    )

    if not parts:

        return None

    return "".join(str(file_attributes[name]) for name in parts)


def _field_index(metadata : structmeta.EosStructMetadata) -> dict:

    '''

    Maps each HDF4 field name to its owning EOS structure and declared
    dimension names.

    '''

    index = {}

    for eos_grid_ in metadata.grids:

        for field in eos_grid_.data_fields:

            index[field.name] = (eos_grid_.name, field.dimensions)

    for eos_swath_ in metadata.swaths:

        for field in eos_swath_.geo_fields + eos_swath_.data_fields:

            index[field.name] = (eos_swath_.name, field.dimensions)

    return index


def read_hdf4(path : str, geolocation : bool = True) -> TreeGroup:

    '''

    Reads an HDF4/HDF-EOS2 file into the intermediate tree: raw SDS
    payloads grouped one-netCDF4-group-per-EOS-structure, EOS metadata
    attributes relocated verbatim to ``HDFEOS_INFORMATION``, and (when
    ``geolocation`` is true and the file is HDF-EOS2) reconstructed CF
    coordinates added on top.

    '''

    source = SD(path, SDC.READ)

    try:

        root = _read_payload(source)

    finally:

        source.end()

    if geolocation:

        text = _eos_metadata(root).attributes
        text = _structmetadata_text(text)

        if text is not None:

            metadata = structmeta.parse(text)

            _decorate_grids(root, metadata)
            _decorate_swaths(root, metadata)

    return root


def _eos_metadata(root : TreeGroup) -> TreeGroup:

    return root.groups.get(_EOS_INFORMATION_GROUP,
                           TreeGroup.empty(_EOS_INFORMATION_GROUP))


def _read_payload(source : SD) -> TreeGroup:

    root = TreeGroup.empty("")

    dataset_count, attribute_count = source.info()

    for name, value in _read_attributes(source, attribute_count).items():

        if name.lower().startswith(_EOS_METADATA_PREFIXES):

            root.subgroup(_EOS_INFORMATION_GROUP).attributes[name] = value

        else:

            root.attributes[name] = value

    metadata_text = _structmetadata_text(
        _eos_metadata(root).attributes
    )

    field_index = {}

    if metadata_text is not None:

        field_index = _field_index(structmeta.parse(metadata_text))

    for index in range(dataset_count):

        dataset = source.select(index)

        try:

            _read_dataset(dataset, field_index, root)

        finally:

            dataset.endaccess()

    return root


def _read_dataset(dataset, field_index : dict, root : TreeGroup) -> None:

    hdf4_name, rank, shape, dfnt_code, attribute_count = dataset.info()

    if rank == 1 and not isinstance(shape, (list, tuple)):

        shape = [shape]

    dtype = _DFNT_DTYPES.get(dfnt_code)

    if dtype is None:

        raise UnsupportedTypeError(
            f"SDS {hdf4_name!r} uses unsupported HDF4 type code "
            f"{dfnt_code} (character and non-numeric SDS are outside the "
            f"v2 guarantee)."
        )

    pyhdf_dims = [dataset.dim(axis).info()[0] for axis in range(rank)]

    # Resolve the owning EOS structure and the EOS dimension names:
    # StructMetadata's field list is authoritative (trimmed/subsetted
    # granules lose pyhdf dim names); the pyhdf "dim:Structure" suffix is
    # the fallback; plain HDF4 keeps its own dim names at the root.
    if hdf4_name in field_index:

        group_name, dim_names = field_index[hdf4_name]

        if len(dim_names) != rank:

            raise NcarnateError(
                f"SDS {hdf4_name!r}: StructMetadata declares "
                f"{len(dim_names)} dimensions but the dataset has {rank}."
            )

    elif any(":" in dim for dim in pyhdf_dims):

        suffixes = {dim.partition(":")[2] for dim in pyhdf_dims
                    if ":" in dim}

        group_name = sorted(suffixes)[0]
        dim_names  = tuple(dim.partition(":")[0] for dim in pyhdf_dims)

    else:

        group_name = None
        dim_names  = tuple(pyhdf_dims)

    # `shape` is attacker-controlled; a tiny file can declare a giant SDS
    # that only materializes on get(). Bound it before reading.
    check_array_size(shape, dtype.itemsize, f"SDS {hdf4_name!r}")

    values = np.asarray(dataset.get())

    if values.dtype != dtype:

        raise UnsupportedTypeError(
            f"SDS {hdf4_name!r}: pyhdf returned dtype {values.dtype}, "
            f"expected {dtype} from the declared HDF4 type."
        )

    attributes = _read_attributes(dataset, attribute_count)
    name       = sanitize_name(hdf4_name)

    if name != hdf4_name:

        if "hdf4_name" in attributes:

            raise NcarnateError(
                f"SDS {hdf4_name!r} carries an attribute named 'hdf4_name' "
                f"that collides with the generated original-name companion."
            )

        attributes["hdf4_name"] = hdf4_name

    group     = root if group_name is None else root.subgroup(group_name)
    dim_names = tuple(sanitize_name(dim) for dim in dim_names)

    for dim_name, size in zip(dim_names, values.shape):

        group.add_dimension(dim_name, size)

    group.variables.append(
        TreeVariable(
            name       = name,
            dimensions = dim_names,
            values     = values,
            attributes = attributes,
        )
    )


def _reserve_names(group : TreeGroup, names : tuple, context : str) -> None:

    for name in names:

        if group.variable(name) is not None:

            raise UnsupportedGeolocationError(
                f"{context}: cannot add reconstructed variable {name!r}; "
                f"the name already exists in the file. Convert with "
                f"--no-geolocation."
            )


def _decorate_grids(root     : TreeGroup,
                    metadata : structmeta.EosStructMetadata) -> None:

    for eos_grid_ in metadata.grids:

        group = root.subgroup(eos_grid_.name)

        # A subsetted/trimmed granule must still agree with the grid's
        # declared shape — corner-derived coordinates are wrong otherwise.
        for dim_name, declared in (("XDim", eos_grid_.x_dim),
                                   ("YDim", eos_grid_.y_dim)):

            actual = group.dimensions.get(dim_name)

            if actual is not None and actual != declared:

                raise UnsupportedGeolocationError(
                    f"Grid {eos_grid_.name!r}: StructMetadata declares "
                    f"{dim_name}={declared} but the file's SDS use "
                    f"{actual}; refusing to reconstruct coordinates for "
                    f"a subsetted grid."
                )

            group.add_dimension(dim_name, declared)

        reconstruction = eos_grid.reconstruct(eos_grid_)

        if reconstruction.projection.crs is None:

            _decorate_geographic_grid(group, reconstruction)

        else:

            _decorate_projected_grid(group, reconstruction)


def _decorate_geographic_grid(group          : TreeGroup,
                              reconstruction : eos_grid.GridGeolocation
                              ) -> None:

    _reserve_names(group, ("lat", "lon"), f"Grid {group.name!r}")

    group.variables.append(TreeVariable(
        name       = "lat",
        dimensions = ("YDim",),
        values     = reconstruction.y,
        attributes = {
            "standard_name" : "latitude",
            "units"         : "degrees_north",
            "comment"       : "reconstructed from the HDF-EOS2 grid "
                              "corner points",
        },
    ))

    group.variables.append(TreeVariable(
        name       = "lon",
        dimensions = ("XDim",),
        values     = reconstruction.x,
        attributes = {
            "standard_name" : "longitude",
            "units"         : "degrees_east",
            "comment"       : "reconstructed from the HDF-EOS2 grid "
                              "corner points",
        },
    ))

    _attach_grid_coordinates(group, grid_mapping = None)


def _decorate_projected_grid(group          : TreeGroup,
                             reconstruction : eos_grid.GridGeolocation
                             ) -> None:

    mapping_name = reconstruction.projection.mapping_name

    _reserve_names(
        group, ("x", "y", "lat", "lon", mapping_name),
        f"Grid {group.name!r}"
    )

    # Cells outside the projection's valid domain (EASE-Grid corner
    # cells lie off the Earth disk) inverse-project to non-finite
    # values; emit them as declared fill instead.
    latitude    = reconstruction.latitude
    longitude   = reconstruction.longitude
    off_earth   = ~np.isfinite(latitude) | ~np.isfinite(longitude)
    fill_attrs  = {}

    if off_earth.any():

        fill_value = np.float64(nc.default_fillvals["f8"])
        latitude   = np.where(off_earth, fill_value, latitude)
        longitude  = np.where(off_earth, fill_value, longitude)
        fill_attrs = {"_FillValue" : fill_value}

    group.variables.append(TreeVariable(
        name       = "x",
        dimensions = ("XDim",),
        values     = reconstruction.x,
        attributes = {
            "standard_name" : "projection_x_coordinate",
            "units"         : "m",
            "comment"       : "cell centers reconstructed from the "
                              "HDF-EOS2 grid corner points",
        },
    ))

    group.variables.append(TreeVariable(
        name       = "y",
        dimensions = ("YDim",),
        values     = reconstruction.y,
        attributes = {
            "standard_name" : "projection_y_coordinate",
            "units"         : "m",
            "comment"       : "cell centers reconstructed from the "
                              "HDF-EOS2 grid corner points",
        },
    ))

    comment = (
        "inverse-projected from the reconstructed grid coordinates"
        + ("; cells outside the projection's valid Earth domain are fill"
           if fill_attrs else "")
    )

    group.variables.append(TreeVariable(
        name       = "lat",
        dimensions = ("YDim", "XDim"),
        values     = latitude,
        attributes = {
            "standard_name" : "latitude",
            "units"         : "degrees_north",
            "comment"       : comment,
            **fill_attrs,
        },
    ))

    group.variables.append(TreeVariable(
        name       = "lon",
        dimensions = ("YDim", "XDim"),
        values     = longitude,
        attributes = {
            "standard_name" : "longitude",
            "units"         : "degrees_east",
            "comment"       : comment,
            **fill_attrs,
        },
    ))

    group.variables.append(TreeVariable(
        name       = mapping_name,
        dimensions = (),
        values     = np.int32(0),
        attributes = dict(reconstruction.projection.cf_attributes),
    ))

    _attach_grid_coordinates(group, grid_mapping = mapping_name)


def _attach_grid_coordinates(group        : TreeGroup,
                             grid_mapping : "str | None") -> None:

    reconstructed = {"x", "y", "lat", "lon", grid_mapping}

    for variable in group.variables:

        if variable.name in reconstructed:

            continue

        if "XDim" in variable.dimensions and "YDim" in variable.dimensions:

            _set_reversible(variable, "coordinates", "lon lat")

            if grid_mapping is not None:

                _set_reversible(variable, "grid_mapping", grid_mapping)


def _decorate_swaths(root     : TreeGroup,
                     metadata : structmeta.EosStructMetadata) -> None:

    for eos_swath_ in metadata.swaths:

        context = f"Swath {eos_swath_.name!r}"

        if eos_swath_.has_index_maps:

            raise UnsupportedGeolocationError(
                f"{context} uses index dimension maps, which are not "
                f"supported; convert with --no-geolocation."
            )

        if eos_swath_.has_merged_fields:

            raise UnsupportedGeolocationError(
                f"{context} uses merged fields, which are not supported; "
                f"convert with --no-geolocation."
            )

        group     = root.groups.get(sanitize_name(eos_swath_.name))
        geo_names = {field.name for field in eos_swath_.geo_fields}

        if not {"Latitude", "Longitude"} <= geo_names:

            raise UnsupportedGeolocationError(
                f"{context} lacks Latitude/Longitude geolocation fields "
                f"(found: {sorted(geo_names)}); convert with "
                f"--no-geolocation."
            )

        if group is None \
           or group.variable("Latitude") is None \
           or group.variable("Longitude") is None:

            raise UnsupportedGeolocationError(
                f"{context}: the Latitude/Longitude SDS are missing from "
                f"the file; convert with --no-geolocation."
            )

        latitude  = group.variable("Latitude")
        longitude = group.variable("Longitude")

        for variable, units in ((latitude, "degrees_north"),
                                (longitude, "degrees_east")):

            _normalize_coordinate(variable, units)

        _attach_swath_coordinates(group, eos_swath_, latitude, longitude)


def _set_reversible(variable : TreeVariable, name : str, value) -> None:

    # CF normalization is confined and reversible: any differing original
    # value is kept under original_<name>.
    original = variable.attributes.get(name)

    if original is not None and str(original) != str(value):

        variable.attributes[f"original_{name}"] = original

    variable.attributes[name] = value


def _normalize_coordinate(variable : TreeVariable, units : str) -> None:

    standard_name = ("latitude" if units == "degrees_north"
                     else "longitude")

    _set_reversible(variable, "units", units)
    _set_reversible(variable, "standard_name", standard_name)

    # Packed geolocation would make every derived coordinate silently
    # wrong; no surveyed product packs it, so fail loud if one does.
    scale  = variable.attributes.get("scale_factor")
    offset = variable.attributes.get("add_offset")

    if (scale is not None and float(scale) != 1.0) \
       or (offset is not None and float(offset) != 0.0):

        raise UnsupportedGeolocationError(
            f"Geolocation field {variable.name!r} is packed "
            f"(scale_factor={scale}, add_offset={offset}), which is not "
            f"supported; convert with --no-geolocation."
        )


def _axis_specification(eos_swath_ : structmeta.EosSwath,
                        geo_dim    : str,
                        var_dim    : str) -> "tuple[int, int] | None | str":

    '''

    Classifies one axis of a candidate data variable against one axis of
    the geolocation: same dimension → ``None`` (native resolution), a
    dimension-map target → ``(offset, increment)``, anything else → the
    sentinel ``"unrelated"``.

    '''

    if var_dim == geo_dim:

        return None

    for mapping in eos_swath_.dimension_maps:

        if sanitize_name(mapping.geo_dimension) == geo_dim \
           and sanitize_name(mapping.data_dimension) == var_dim:

            return (mapping.offset, mapping.increment)

    return "unrelated"


def _attach_swath_coordinates(group      : TreeGroup,
                              eos_swath_ : structmeta.EosSwath,
                              latitude   : TreeVariable,
                              longitude  : TreeVariable) -> None:

    geo_dims = latitude.dimensions

    if longitude.dimensions != geo_dims or len(geo_dims) != 2:

        raise UnsupportedGeolocationError(
            f"Swath {eos_swath_.name!r}: Latitude/Longitude dimensions "
            f"disagree or are not 2-D "
            f"({latitude.dimensions} vs {longitude.dimensions})."
        )

    fill_value   = latitude.attributes.get("_FillValue")
    fill_value   = None if fill_value is None else float(fill_value)
    interpolated = {}

    for variable in list(group.variables):

        if variable is latitude or variable is longitude:

            continue

        if len(variable.dimensions) < 2:

            continue

        specifications = tuple(
            _axis_specification(eos_swath_, geo_dims[axis],
                                variable.dimensions[axis])
            for axis in range(2)
        )

        if "unrelated" in specifications:

            continue

        if all(spec is None for spec in specifications):

            _set_reversible(variable, "coordinates",
                            f"{longitude.name} {latitude.name}")

            continue

        target_dims = variable.dimensions[:2]

        if target_dims not in interpolated:

            interpolated[target_dims] = _build_interpolated(
                group, eos_swath_, latitude, longitude,
                specifications, target_dims, fill_value
            )

        lon_name, lat_name = interpolated[target_dims]

        _set_reversible(variable, "coordinates", f"{lon_name} {lat_name}")


def _build_interpolated(group          : TreeGroup,
                        eos_swath_     : structmeta.EosSwath,
                        latitude       : TreeVariable,
                        longitude      : TreeVariable,
                        specifications : tuple,
                        target_dims    : tuple,
                        fill_value     : "float | None") -> tuple:

    data_shape = tuple(group.dimensions[dim] for dim in target_dims)

    interpolated_latitude, interpolated_longitude = \
        eos_swath.interpolate_geolocation(
            latitude.values,
            longitude.values,
            list(specifications),
            data_shape,
            fill_value,
        )

    suffix   = "_interpolated"
    lat_name = f"{latitude.name}{suffix}"
    lon_name = f"{longitude.name}{suffix}"

    while group.variable(lat_name) is not None \
            or group.variable(lon_name) is not None:

        suffix   += "_"
        lat_name  = f"{latitude.name}{suffix}"
        lon_name  = f"{longitude.name}{suffix}"

    comment = (
        f"interpolated to {' x '.join(target_dims)} resolution from the "
        f"{' x '.join(latitude.dimensions)} geolocation via the HDF-EOS2 "
        f"dimension maps; edge pixels outside the geolocation envelope "
        f"are linearly extrapolated"
    )

    for name, values, units in (
        (lat_name, interpolated_latitude, "degrees_north"),
        (lon_name, interpolated_longitude, "degrees_east"),
    ):

        attributes = {
            "standard_name" : ("latitude" if units == "degrees_north"
                               else "longitude"),
            "units"         : units,
            "comment"       : comment,
        }

        if fill_value is not None:

            attributes["_FillValue"] = np.float32(fill_value)

        group.variables.append(TreeVariable(
            name       = name,
            dimensions = target_dims,
            values     = values,
            attributes = attributes,
        ))

    return lon_name, lat_name


def write_netcdf(root      : TreeGroup,
                 path      : str,
                 zlib      : bool,
                 shuffle   : bool,
                 complevel : int) -> None:

    '''

    Serializes the intermediate tree as netCDF4 with the requested
    compression, declaring each variable's ``_FillValue`` at creation
    time (the same discipline as the netCDF recompression path).

    '''

    with nc.Dataset(path, mode = "w", format = "NETCDF4") as dataset:

        _write_group(root, dataset, zlib, shuffle, complevel)


def _write_group(tree      : TreeGroup,
                 target,
                 zlib      : bool,
                 shuffle   : bool,
                 complevel : int) -> None:

    for name, size in tree.dimensions.items():

        target.createDimension(name, size)

    target.setncatts(tree.attributes)

    for variable in tree.variables:

        attributes = dict(variable.attributes)
        fill_value = attributes.pop("_FillValue", None)

        netcdf_variable = target.createVariable(
            variable.name,
            variable.values.dtype,
            variable.dimensions,
            zlib       = zlib,
            shuffle    = shuffle,
            complevel  = complevel,
            fill_value = fill_value,
        )

        netcdf_variable.set_auto_maskandscale(False)
        netcdf_variable.setncatts(attributes)

        if 0 not in variable.values.shape:

            netcdf_variable[...] = variable.values

    for name, subgroup in tree.groups.items():

        _write_group(subgroup, target.createGroup(name),
                     zlib, shuffle, complevel)


def verify_conversion(src_path : str, dst_path : str) -> None:

    '''

    Independently re-reads the HDF4 source (raw payload only, no
    geolocation) and asserts every SDS value, dimension, and attribute
    survives in the emitted netCDF4. Reconstructed variables are
    additive and ignored; a normalized attribute passes only if the
    original value is preserved under ``original_<name>``.

    '''

    expected = read_hdf4(src_path, geolocation = False)

    with nc.Dataset(dst_path, mode = "r") as actual:

        _verify_group(expected, actual, "/")


def _verify_group(expected : TreeGroup, actual, path : str) -> None:

    for name, size in expected.dimensions.items():

        if name not in actual.dimensions:

            raise VerificationError(
                f"Verification failed: dimension {path}{name} missing "
                f"from the output."
            )

        if actual.dimensions[name].size != size:

            raise VerificationError(
                f"Verification failed: dimension {path}{name} size "
                f"{actual.dimensions[name].size} != {size}."
            )

    _verify_attributes(expected.attributes, actual, path)

    for variable in expected.variables:

        if variable.name not in actual.variables:

            raise VerificationError(
                f"Verification failed: variable {path}{variable.name} "
                f"missing from the output."
            )

        netcdf_variable = actual.variables[variable.name]

        netcdf_variable.set_auto_maskandscale(False)

        if netcdf_variable.dtype != variable.values.dtype:

            raise VerificationError(
                f"Verification failed: variable {path}{variable.name} "
                f"dtype {netcdf_variable.dtype} != "
                f"{variable.values.dtype}."
            )

        if tuple(netcdf_variable.dimensions) != variable.dimensions:

            raise VerificationError(
                f"Verification failed: variable {path}{variable.name} "
                f"dimensions differ."
            )

        equal_nan = variable.values.dtype.kind in "fc"

        if not np.array_equal(netcdf_variable[...], variable.values,
                              equal_nan = equal_nan):

            raise VerificationError(
                f"Verification failed: variable {path}{variable.name} "
                f"values differ."
            )

        _verify_attributes(
            variable.attributes, netcdf_variable,
            f"{path}{variable.name}"
        )

    for name, subgroup in expected.groups.items():

        if name not in actual.groups:

            raise VerificationError(
                f"Verification failed: group {path}{name} missing from "
                f"the output."
            )

        _verify_group(subgroup, actual.groups[name], f"{path}{name}/")


def _verify_attributes(expected : dict, actual, path : str) -> None:

    actual_names = set(actual.ncattrs())

    for name, value in expected.items():

        if name in actual_names:

            actual_value = actual.getncattr(name)

            if _attribute_equal(value, actual_value):

                continue

        # A normalized attribute is acceptable only when the original
        # value survives under original_<name> (reversibility contract).
        original = f"original_{name}"

        if original in actual_names \
           and _attribute_equal(value, actual.getncattr(original)):

            continue

        raise VerificationError(
            f"Verification failed: attribute {name!r} on {path} was not "
            f"preserved."
        )


def _attribute_equal(expected, actual) -> bool:

    expected_array = np.asarray(expected)
    actual_array   = np.asarray(actual)

    if expected_array.dtype.kind in "US" or actual_array.dtype.kind in "US":

        return str(expected) == str(actual)

    if expected_array.dtype != actual_array.dtype:

        return False

    equal_nan = expected_array.dtype.kind in "fc"

    return np.array_equal(expected_array, actual_array,
                          equal_nan = equal_nan)
