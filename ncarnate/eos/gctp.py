#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

Translates HDF-EOS2 GCTP projection declarations into ``pyproj.CRS``
objects (for computation) and CF grid-mapping attribute dicts (for the
emitted netCDF4). The dispatch is generic, but v2 claims only
fixture-proven projections; anything else raises
`UnsupportedProjectionError` — a wrong coordinate is worse than a
refused conversion.

GCTP conventions handled here (GCTP/HDF-EOS documentation):

- ``ProjParams[0]`` is the semi-major axis in metres; ``ProjParams[1]``
  encodes the ellipsoid's second parameter — positive means semi-minor
  axis in metres, negative means minus eccentricity-squared, zero means
  a sphere of radius ``ProjParams[0]``.
- Angular parameters are packed DMS: ``±DDDMMMSSS.SS``.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

# Standard library imports.
import dataclasses
import math

# Third party imports.
import pyproj

# Local application imports.
from ncarnate.eos.structmeta import EosGrid
from ncarnate.errors import UnsupportedProjectionError


@dataclasses.dataclass
class ProjectionInfo:

    '''

    A decoded grid projection: the CF ``grid_mapping_name``, the CF
    grid-mapping attributes, and a `pyproj.CRS` for coordinate math.
    ``crs`` is ``None`` for geographic (GCTP_GEO) grids, whose
    coordinates are already latitude/longitude degrees.

    '''

    mapping_name  : str
    cf_attributes : dict
    crs           : "pyproj.CRS | None"


def decode_packed_dms(value : float) -> float:

    '''

    Decodes a GCTP packed-DMS angle (``±DDDMMMSSS.SS``) to decimal
    degrees: ``-45000000.0`` → ``-45.0``.

    '''

    sign  = -1.0 if value < 0 else 1.0
    value = abs(value)

    degrees = math.floor(value / 1e6)
    minutes = math.floor((value - degrees * 1e6) / 1e3)
    seconds = value - degrees * 1e6 - minutes * 1e3

    if minutes >= 60 or seconds >= 60:

        raise UnsupportedProjectionError(
            f"Angle {sign * value} is not valid packed DMS "
            f"(minutes={minutes}, seconds={seconds})."
        )

    return sign * (degrees + minutes / 60.0 + seconds / 3600.0)


def _ellipsoid(params : tuple[float, ...]) -> tuple[float, float]:

    '''

    Decodes ``ProjParams[0:2]`` into (semi-major, semi-minor) metres.

    '''

    semi_major = params[0] if len(params) > 0 else 0.0
    second     = params[1] if len(params) > 1 else 0.0

    if semi_major <= 0:

        # A zero semi-major axis defers to the GCTP sphere-code table,
        # which no fixture exercises yet.
        raise UnsupportedProjectionError(
            "Grid relies on the GCTP sphere-code table "
            "(ProjParams[0] == 0), which is not supported yet."
        )

    if second > 0:

        semi_minor = second

    elif second < 0:

        eccentricity_squared = -second

        # A physical ellipsoid has 0 <= e^2 < 1; a hostile ProjParams[1]
        # below -1 would otherwise reach sqrt() of a negative number and
        # raise a bare math-domain ValueError instead of a named error.
        if eccentricity_squared >= 1.0:

            raise UnsupportedProjectionError(
                f"ProjParams[1]={second} implies eccentricity-squared "
                f">= 1, which is not a valid ellipsoid."
            )

        semi_minor = semi_major * math.sqrt(1.0 - eccentricity_squared)

    else:

        semi_minor = semi_major

    return semi_major, semi_minor


def _polar_stereographic(grid : EosGrid) -> ProjectionInfo:

    params = grid.proj_params

    # Polar stereographic reads the central longitude and true-scale
    # latitude from ProjParams[4]/[5]; a short array would otherwise raise
    # a bare IndexError instead of the contract's named error (mirrors the
    # length guard in _lambert_azimuthal).
    if len(params) < 6:

        raise UnsupportedProjectionError(
            f"Grid {grid.name!r} declares GCTP_PS but ProjParams has only "
            f"{len(params)} entries (need at least 6): {params}."
        )

    semi_major, semi_minor = _ellipsoid(params)

    longitude_from_pole = decode_packed_dms(params[4])
    standard_parallel   = decode_packed_dms(params[5])
    false_easting       = params[6] if len(params) > 6 else 0.0
    false_northing      = params[7] if len(params) > 7 else 0.0

    # The GCTP PS convention: the sign of the true-scale latitude
    # selects the hemisphere (the projection is centered on that pole).
    origin_latitude = 90.0 if standard_parallel >= 0 else -90.0

    crs = pyproj.CRS.from_proj4(
        f"+proj=stere +lat_0={origin_latitude} "
        f"+lat_ts={standard_parallel} +lon_0={longitude_from_pole} "
        f"+x_0={false_easting} +y_0={false_northing} "
        f"+a={semi_major} +b={semi_minor} +units=m +no_defs"
    )

    cf_attributes = {
        "grid_mapping_name"                     : "polar_stereographic",
        "straight_vertical_longitude_from_pole" : longitude_from_pole,
        "latitude_of_projection_origin"         : origin_latitude,
        "standard_parallel"                     : standard_parallel,
        "false_easting"                         : false_easting,
        "false_northing"                        : false_northing,
        "semi_major_axis"                       : semi_major,
        "semi_minor_axis"                       : semi_minor,
    }

    return ProjectionInfo(
        mapping_name  = "polar_stereographic",
        cf_attributes = cf_attributes,
        crs           = crs,
    )


def _geographic(grid : EosGrid) -> ProjectionInfo:

    # A GEO grid's coordinates are latitude/longitude degrees directly;
    # no projection math and no CF grid-mapping variable are needed.
    return ProjectionInfo(
        mapping_name  = "latitude_longitude",
        cf_attributes = {},
        crs           = None,
    )


def _lambert_azimuthal(grid : EosGrid) -> ProjectionInfo:

    params = grid.proj_params

    if len(params) < 6 or params[0] <= 0:

        raise UnsupportedProjectionError(
            f"GCTP_LAMAZ grid {grid.name!r} lacks a usable sphere radius "
            f"in ProjParams[0]."
        )

    # GCTP LAMAZ is spherical: ProjParams[0] is the sphere radius
    # (EASE-Grid uses 6371228 m).
    radius           = params[0]
    center_longitude = decode_packed_dms(params[4])
    center_latitude  = decode_packed_dms(params[5])
    false_easting    = params[6] if len(params) > 6 else 0.0
    false_northing   = params[7] if len(params) > 7 else 0.0

    crs = pyproj.CRS.from_proj4(
        f"+proj=laea +lat_0={center_latitude} +lon_0={center_longitude} "
        f"+x_0={false_easting} +y_0={false_northing} "
        f"+R={radius} +units=m +no_defs"
    )

    cf_attributes = {
        "grid_mapping_name"             : "lambert_azimuthal_equal_area",
        "longitude_of_projection_origin": center_longitude,
        "latitude_of_projection_origin" : center_latitude,
        "false_easting"                 : false_easting,
        "false_northing"                : false_northing,
        "earth_radius"                  : radius,
    }

    return ProjectionInfo(
        mapping_name  = "lambert_azimuthal_equal_area",
        cf_attributes = cf_attributes,
        crs           = crs,
    )


# GCTP projection name -> decoder. The table is generic; entries are
# added only once a fixture proves them (claim-what-you-test).
_DECODERS = {
    "GCTP_PS"    : _polar_stereographic,
    "GCTP_GEO"   : _geographic,
    "GCTP_LAMAZ" : _lambert_azimuthal,
}


def projection_info(grid : EosGrid) -> ProjectionInfo:

    '''

    Decodes the grid's GCTP declaration. Raises
    `UnsupportedProjectionError` (naming the projection and its
    parameters) for anything outside the verified set.

    '''

    decoder = _DECODERS.get(grid.projection)

    if decoder is None:

        raise UnsupportedProjectionError(
            f"Grid {grid.name!r} uses unsupported projection "
            f"{grid.projection} (ProjParams={grid.proj_params}); "
            f"convert with --no-geolocation to skip reconstruction."
        )

    return decoder(grid)
