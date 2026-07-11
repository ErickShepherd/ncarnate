#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

Swath geolocation support: interpolates dimension-mapped geolocation to
data resolution. HDF-EOS2 dimension maps place geolocation pixel ``g``
at data index ``offset + increment * g``; interior data pixels
interpolate linearly between bracketing geolocation pixels and edge
pixels extrapolate. All interpolation happens through 3-D Cartesian
(ECEF unit-sphere) space — linear interpolation on raw degrees breaks at
the antimeridian and near the poles, exactly where polar-orbiter swaths
live.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

# Third party imports.
import numpy as np

# Local application imports.
from ncarnate.errors import UnsupportedGeolocationError
from ncarnate.limits import check_array_size


def _axis_weights(data_size  : int,
                  geo_size   : int,
                  offset     : int,
                  increment  : int) -> tuple[np.ndarray, np.ndarray]:

    '''

    For one mapped axis, returns ``(lower_index, weight)`` per data
    pixel: the interpolated value is
    ``value[lower] * (1 - weight) + value[lower + 1] * weight``.
    Weights outside [0, 1] are linear extrapolation off the first/last
    geolocation segment (edge pixels outside the geolocation envelope).

    '''

    if increment <= 0:

        raise UnsupportedGeolocationError(
            f"Dimension map with non-positive increment {increment} is "
            f"not supported.",
            code="SWATH_GEOLOCATION_UNSUPPORTED",
        )

    if geo_size < 2:

        raise UnsupportedGeolocationError(
            f"Dimension map needs at least 2 geolocation pixels to "
            f"interpolate; got {geo_size}.",
            code="SWATH_GEOLOCATION_UNSUPPORTED",
        )

    fractional  = (np.arange(data_size, dtype = np.float64) - offset) \
        / increment
    lower_index = np.clip(
        np.floor(fractional).astype(np.int64), 0, geo_size - 2
    )
    weight      = fractional - lower_index

    return lower_index, weight


def _to_unit_xyz(latitude : np.ndarray,
                 longitude : np.ndarray) -> np.ndarray:

    lat_radians = np.deg2rad(latitude.astype(np.float64))
    lon_radians = np.deg2rad(longitude.astype(np.float64))

    cos_lat = np.cos(lat_radians)

    return np.stack(
        (
            cos_lat * np.cos(lon_radians),
            cos_lat * np.sin(lon_radians),
            np.sin(lat_radians),
        )
    )


def _interpolate_axis(values : np.ndarray,
                      axis   : int,
                      lower  : np.ndarray,
                      weight : np.ndarray) -> np.ndarray:

    lower_values = np.take(values, lower, axis = axis)
    upper_values = np.take(values, lower + 1, axis = axis)

    shape       = [1] * values.ndim
    shape[axis] = weight.size
    weight      = weight.reshape(shape)

    return lower_values * (1.0 - weight) + upper_values * weight


def interpolate_geolocation(latitude   : np.ndarray,
                            longitude  : np.ndarray,
                            axis_maps  : "list[tuple[int, int] | None]",
                            data_shape : tuple[int, ...],
                            fill_value : "float | None") -> tuple:

    '''

    Interpolates a 2-D geolocation pair to data resolution through ECEF.

    ``axis_maps[k]`` is ``(offset, increment)`` for a mapped axis or
    ``None`` for an axis already at data resolution. Geolocation fill
    pixels propagate: any data pixel whose bracketing geolocation pixels
    include fill comes out as fill, never interpolated across.

    Returns ``(latitude, longitude)`` as float32 arrays of
    ``data_shape``.

    '''

    # Defensive local bound: the intermediate/output arrays are data_shape-
    # sized. This is transitively bounded (the owning variable already passed
    # the read-time ceiling), but assert it here so the invariant doesn't
    # depend on a cross-module argument. itemsize 8 covers the float64 ECEF
    # intermediate (the widest of the arrays allocated below).
    check_array_size(data_shape, 8, "swath geolocation interpolation")

    valid = np.isfinite(latitude) & np.isfinite(longitude)

    if fill_value is not None:

        valid &= (latitude != fill_value) & (longitude != fill_value)

    xyz = _to_unit_xyz(latitude, longitude)
    ok  = valid.astype(np.float64)

    for axis, mapping in enumerate(axis_maps):

        if mapping is None:

            if data_shape[axis] != latitude.shape[axis]:

                raise UnsupportedGeolocationError(
                    f"Axis {axis}: data size {data_shape[axis]} differs "
                    f"from geolocation size {latitude.shape[axis]} but no "
                    f"dimension map covers it.",
                    code="SWATH_DIMMAP_UNRESOLVED",
                )

            continue

        offset, increment = mapping
        lower, weight     = _axis_weights(
            data_shape[axis], latitude.shape[axis], offset, increment
        )

        # +1 skips the stacked-component axis of `xyz`.
        xyz = _interpolate_axis(xyz, axis + 1, lower, weight)

        # A data pixel is valid only if every contributing geolocation
        # pixel is valid (extrapolated edges use the outermost segment).
        ok = np.minimum(
            np.take(ok, lower, axis = axis),
            np.take(ok, lower + 1, axis = axis),
        )

    norm = np.linalg.norm(xyz, axis = 0)
    ok   = (ok >= 1.0) & (norm > 0)
    norm = np.where(norm == 0, 1.0, norm)
    xyz  = xyz / norm

    out_latitude  = np.rad2deg(np.arcsin(np.clip(xyz[2], -1.0, 1.0)))
    out_longitude = np.rad2deg(np.arctan2(xyz[1], xyz[0]))

    if fill_value is not None:

        out_latitude  = np.where(ok, out_latitude, fill_value)
        out_longitude = np.where(ok, out_longitude, fill_value)

    return (
        out_latitude.astype(np.float32),
        out_longitude.astype(np.float32),
    )
