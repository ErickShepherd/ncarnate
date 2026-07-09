#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

Reconstructs CF coordinates for HDF-EOS2 GRID structures: 1-D projection
``x``/``y`` cell-center coordinates from the declared corner points, a
CF grid-mapping variable, and 2-D ``lat``/``lon`` auxiliary coordinates
via inverse projection.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

# Standard library imports.
import dataclasses

# Third party imports.
import numpy as np
import pyproj

# Local application imports.
from ncarnate.eos.gctp import ProjectionInfo
from ncarnate.eos.gctp import decode_packed_dms
from ncarnate.eos.gctp import projection_info
from ncarnate.eos.structmeta import EosGrid
from ncarnate.errors import UnsupportedGeolocationError


@dataclasses.dataclass
class GridGeolocation:

    '''

    Reconstructed coordinates for one grid. For projected grids ``x``
    and ``y`` are metres and ``latitude``/``longitude`` are the 2-D
    inverse-projected auxiliary coordinates; for geographic (GCTP_GEO)
    grids ``x``/``y`` *are* longitude/latitude degrees and the 2-D
    arrays are ``None``.

    '''

    projection : ProjectionInfo
    x          : np.ndarray
    y          : np.ndarray
    latitude   : "np.ndarray | None"
    longitude  : "np.ndarray | None"


def _cell_centers(grid : EosGrid) -> tuple[np.ndarray, np.ndarray]:

    '''

    Cell-center coordinates from the corner points: HDF-EOS corner
    points span the full grid extent, so with ``GridOrigin=HDFE_GD_UL``
    row 0 / column 0 sit half a cell inside the upper-left corner.

    '''

    upper_left_x, upper_left_y   = grid.upper_left
    lower_right_x, lower_right_y = grid.lower_right

    x_step = (lower_right_x - upper_left_x) / grid.x_dim
    y_step = (lower_right_y - upper_left_y) / grid.y_dim

    x = upper_left_x + (np.arange(grid.x_dim, dtype = np.float64) + 0.5) \
        * x_step
    y = upper_left_y + (np.arange(grid.y_dim, dtype = np.float64) + 0.5) \
        * y_step

    return x, y


def reconstruct(grid : EosGrid) -> GridGeolocation:

    '''

    Builds the grid's coordinate reconstruction. Raises
    `UnsupportedGeolocationError` for layouts no fixture has proven
    (non-UL grid origins, non-center pixel registration) and
    `UnsupportedProjectionError` for unverified projections.

    '''

    if grid.grid_origin != "HDFE_GD_UL":

        raise UnsupportedGeolocationError(
            f"Grid {grid.name!r} has GridOrigin={grid.grid_origin}; only "
            f"HDFE_GD_UL is supported (no fixture proves the variants)."
        )

    if grid.pixel_registration != "HDFE_CENTER":

        raise UnsupportedGeolocationError(
            f"Grid {grid.name!r} has "
            f"PixelRegistration={grid.pixel_registration}; only "
            f"HDFE_CENTER is supported (no fixture proves the variants)."
        )

    projection = projection_info(grid)

    if projection.crs is None:

        # Geographic grid: corner points are packed-DMS degrees and the
        # cell centers are the coordinates themselves.
        geo_grid = dataclasses.replace(
            grid,
            upper_left  = (decode_packed_dms(grid.upper_left[0]),
                           decode_packed_dms(grid.upper_left[1])),
            lower_right = (decode_packed_dms(grid.lower_right[0]),
                           decode_packed_dms(grid.lower_right[1])),
        )

        x, y = _cell_centers(geo_grid)

        return GridGeolocation(
            projection = projection,
            x          = x,
            y          = y,
            latitude   = None,
            longitude  = None,
        )

    x, y = _cell_centers(grid)

    transformer = pyproj.Transformer.from_crs(
        projection.crs, "EPSG:4326", always_xy = True
    )

    x_mesh, y_mesh       = np.meshgrid(x, y)
    longitude, latitude  = transformer.transform(x_mesh, y_mesh)

    return GridGeolocation(
        projection = projection,
        x          = x,
        y          = y,
        latitude   = np.asarray(latitude, dtype = np.float64),
        longitude  = np.asarray(longitude, dtype = np.float64),
    )
