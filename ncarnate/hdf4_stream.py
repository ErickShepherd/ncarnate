#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

Bounded, lazy access to HDF4 SDS payloads for the streaming conversion
path (:func:`ncarnate.hdf4.read_hdf4` with ``stream=True``).

In streaming mode the intermediate tree never holds an SDS array. Each
SDS variable carries an :class:`SdsPayload` — the dataset's identity
(file, index, reference number, name, type, shape) and nothing else.
The writer and the verifier reopen the source through a
:class:`SourceSession`, select the SDS, and move its values in
rectangular blocks sized by :func:`ncarnate.streaming.slices`, so the
largest transfer array is bounded by the array budget regardless of the
SDS size. Every reopen re-checks the source file's identity (size,
mtime, device/inode) and every select re-checks the SDS identity, so a
source edited between read and write/verify is refused rather than
silently copied.

A payload is deliberately **not** array-like: ``np.asarray(payload)``
raises instead of reading unbounded data. The only materialization is
:func:`materialize`, which checks the array against an explicit byte
ceiling first (geolocation reconstruction needs the coarse Latitude/
Longitude fields in memory; see ``MAX_COORDINATE_SOURCE_BYTES``).

Not covered here (explicit limitation): the *generated* geolocation
arrays — a projected grid's 2-D lat/lon mesh and a swath's interpolated
coordinates — are still allocated eagerly by :mod:`ncarnate.eos`, bounded
only by :func:`ncarnate.limits.check_array_size`'s per-array ceiling and
the caller's process memory limit.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

from __future__ import annotations

# Standard library imports.
import contextlib
import dataclasses
import logging
import os

# Third party imports.
import numpy as np
from pyhdf.error import HDF4Error
from pyhdf.SD import SD, SDC

# Local application imports.
from ncarnate.errors import NcarnateError
from ncarnate.errors import UnsupportedTypeError
from ncarnate.limits import check_array_size
from ncarnate.streaming import slices

__all__ = [
    "MAX_COORDINATE_SOURCE_BYTES",
    "SdsPayload",
    "SourceIdentity",
    "SourceSession",
    "copy_payload",
    "materialize",
    "read_block",
    "sds_shape",
    "verify_payload",
]

# Ceiling on one coarse geolocation field (swath Latitude/Longitude) that
# geolocation reconstruction may materialize in full. 256 MiB covers every
# surveyed product by a wide margin (MODIS 1-km geolocation is ~11 MB per
# field; VIIRS 750-m is ~42 MB) while refusing a hostile file that
# declares a terabyte geolocation field. Read at call time so a caller may
# adjust it, like ``ncarnate.limits.DEFAULT_MAX_ARRAY_BYTES``.
MAX_COORDINATE_SOURCE_BYTES = 256 * 1024 ** 2

_logger = logging.getLogger(__name__)


def sds_shape(rank : int, shape) -> tuple[int, ...]:

    '''

    Normalizes the ``shape`` member of ``SDS.info()`` to a tuple of ints:
    pyhdf reports a rank-1 dataset's shape as a bare int.

    '''

    if rank == 1 and not isinstance(shape, (list, tuple)):

        shape = [shape]

    return tuple(int(size) for size in shape)


@dataclasses.dataclass(frozen = True)
class SourceIdentity:

    '''

    The stat identity of the HDF4 source captured when the tree was read.
    ``check`` re-stats the path and refuses to continue if the file was
    replaced or modified in between.

    '''

    path     : str
    size     : int
    mtime_ns : int
    device   : int
    inode    : int

    @classmethod
    def capture(cls, path : str) -> "SourceIdentity":

        status = os.stat(path)

        return cls(
            path     = os.fspath(path),
            size     = int(status.st_size),
            mtime_ns = int(status.st_mtime_ns),
            device   = int(status.st_dev),
            inode    = int(status.st_ino),
        )

    def differences(self) -> list[str]:

        '''Describes every way the file on disk differs from this identity.'''

        try:

            status = os.stat(self.path)

        except OSError as error:

            return [f"cannot stat the source: {error}"]

        differences = []

        if int(status.st_size) != self.size:

            differences.append(
                f"size {self.size} -> {int(status.st_size)}"
            )

        if int(status.st_mtime_ns) != self.mtime_ns:

            differences.append(
                f"mtime_ns {self.mtime_ns} -> {int(status.st_mtime_ns)}"
            )

        # Device/inode are zero on filesystems that do not report them
        # (some network shares); only compare when both sides have them.
        if self.inode and status.st_ino \
           and (int(status.st_ino) != self.inode
                or int(status.st_dev) != self.device):

            differences.append("device/inode (the file was replaced)")

        return differences

    def check(self, context : str, error_type = NcarnateError) -> None:

        differences = self.differences()

        if differences:

            raise error_type(
                f"{context}: the HDF4 source {self.path!r} changed since it "
                f"was read ({'; '.join(differences)}); refusing to continue "
                f"because the output could not be trusted to match it."
            )


@dataclasses.dataclass(frozen = True)
class SdsPayload:

    '''

    A lazy handle to one SDS's values. Holds only identity and shape; the
    values are read in bounded blocks through a :class:`SourceSession`.

    '''

    source    : SourceIdentity
    index     : int
    reference : int
    hdf4_name : str
    dfnt_code : int
    dtype     : np.dtype
    shape     : tuple[int, ...]

    @property
    def ndim(self) -> int:

        return len(self.shape)

    @property
    def nbytes(self) -> int:

        # Unbounded Python ints: the shape is untrusted file metadata.
        elements = 1

        for size in self.shape:

            elements *= int(size)

        return elements * int(self.dtype.itemsize)

    def __array__(self, dtype = None, copy = None):

        # Refuse implicit materialization loudly: numpy would otherwise
        # either read the whole SDS (unbounded) or, without this method,
        # wrap the handle in a 0-d object array (silently wrong).
        raise NcarnateError(
            f"SDS {self.hdf4_name!r} is a lazy streaming payload of shape "
            f"{self.shape}; it cannot be converted to an array implicitly. "
            f"Use ncarnate.hdf4_stream.materialize with an explicit byte "
            f"ceiling, or copy_payload/verify_payload for bounded access."
        )


class SourceSession:

    '''

    Lazily reopens the one HDF4 source a tree's payloads refer to and
    closes it on exit — on exceptions too. ``select`` hands out an SDS
    handle for the duration of a ``with`` block and ends access
    deterministically afterwards.

    '''

    def __init__(self) -> None:

        self._identity : "SourceIdentity | None" = None
        self._sd                                  = None

    def __enter__(self) -> "SourceSession":

        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:

        identity = self._identity

        try:

            # A source edited *during* the copy is refused here; a
            # propagating exception takes precedence over this check.
            if exc_type is None and identity is not None and self._sd \
               is not None:

                identity.check("After reading the HDF4 source")

        finally:

            self.close()

        return False

    @property
    def is_open(self) -> bool:

        return self._sd is not None

    def close(self) -> None:

        sd, self._sd = self._sd, None

        if sd is not None:

            sd.end()

    def _open(self, identity : SourceIdentity):

        if self._sd is None:

            identity.check("Reopening the HDF4 source")

            try:

                self._sd = SD(identity.path, SDC.READ)

            except HDF4Error as error:

                raise NcarnateError(
                    f"Cannot reopen the HDF4 source {identity.path!r}: "
                    f"{error}"
                ) from error

            self._identity = identity

            _logger.debug("Reopened HDF4 source %s", identity.path)

        elif identity != self._identity:

            raise NcarnateError(
                f"A streaming session serves one HDF4 source; got payloads "
                f"from both {self._identity.path!r} and {identity.path!r}."
            )

        return self._sd

    @contextlib.contextmanager
    def select(self, payload : SdsPayload):

        sd = self._open(payload.source)

        try:

            dataset = sd.select(payload.index)

        except HDF4Error as error:

            raise NcarnateError(
                f"SDS {payload.hdf4_name!r} (index {payload.index}) can no "
                f"longer be selected in {payload.source.path!r}: {error}"
            ) from error

        try:

            _check_dataset_identity(dataset, payload)

            yield dataset

        finally:

            dataset.endaccess()


def _check_dataset_identity(dataset, payload : SdsPayload) -> None:

    name, rank, shape, dfnt_code, _ = dataset.info()
    shape                           = sds_shape(rank, shape)
    reference                       = int(dataset.ref())

    found    = (name, shape, int(dfnt_code), reference)
    expected = (payload.hdf4_name, payload.shape, int(payload.dfnt_code),
                int(payload.reference))

    if found != expected:

        raise NcarnateError(
            f"SDS at index {payload.index} of {payload.source.path!r} is "
            f"not the dataset that was read: expected (name, shape, type, "
            f"ref) {expected}, found {found}."
        )


def read_block(dataset, payload : SdsPayload, selection : tuple) -> np.ndarray:

    '''

    Reads one rectangular block (a tuple of ``slice`` objects, one per
    axis, as yielded by :func:`ncarnate.streaming.slices`) from an open
    SDS and returns it as an ndarray of exactly ``payload.dtype`` and the
    block's shape; anything else is a fidelity failure and raises.

    '''

    if selection:

        start = [int(part.start) for part in selection]
        count = [int(part.stop) - int(part.start) for part in selection]
        raw   = dataset.get(start, count)

    else:

        # Rank-0 is not expressible in HDF4 SDS, but keep the scalar
        # contract symmetric with the writer/verifier's `()` selection.
        count = []
        raw   = dataset.get()

    block = np.asarray(raw)

    if block.dtype != payload.dtype:

        raise UnsupportedTypeError(
            f"SDS {payload.hdf4_name!r}: pyhdf returned dtype {block.dtype}, "
            f"expected {payload.dtype} from the declared HDF4 type.",
            code="UNSUPPORTED_TYPE",
        )

    if block.shape != tuple(count):

        raise NcarnateError(
            f"SDS {payload.hdf4_name!r}: pyhdf returned a block of shape "
            f"{block.shape} for a request of {tuple(count)}."
        )

    return block


def _each_block(payload : SdsPayload, session : SourceSession, consume, chunks=None) -> bool:

    '''

    Drives ``consume(selection, block)`` over every bounded block of the
    payload inside one select/endaccess scope. Stops early (returning
    ``False``) when ``consume`` returns ``False``. Empty payloads yield
    no blocks; scalar payloads yield the single ``()`` selection.

    '''

    with session.select(payload) as dataset:

        for selection in slices(payload.shape, payload.dtype.itemsize, chunks):

            if consume(selection, read_block(dataset, payload, selection)) \
               is False:

                return False

    return True


def copy_payload(payload : SdsPayload, session : SourceSession,
                 target) -> None:

    '''

    Streams the SDS values into ``target`` (an open netCDF4 variable of
    the same shape and dtype) block by block.

    '''

    def write(selection, block) -> None:

        target[selection] = block

    _each_block(payload, session, write, target.chunking() if hasattr(target, "chunking") else None)


def verify_payload(payload : SdsPayload, session : SourceSession,
                   actual) -> bool:

    '''

    Compares the SDS values against ``actual`` (an open netCDF4 variable)
    block by block; returns ``False`` on the first differing block.
    Floating-point blocks compare NaN-insensitively, like the eager
    verifier.

    '''

    equal_nan = payload.dtype.kind in "fc"

    def compare(selection, block) -> bool:

        return bool(np.array_equal(actual[selection], block,
                                   equal_nan = equal_nan))

    return _each_block(payload, session, compare, actual.chunking() if hasattr(actual, "chunking") else None)


def materialize(payload   : SdsPayload,
                session   : SourceSession,
                context   : str,
                max_bytes : "int | None" = None) -> np.ndarray:

    '''

    The only full read of a payload: checks ``payload`` against the
    explicit ``max_bytes`` ceiling (``MAX_COORDINATE_SOURCE_BYTES`` by
    default), allocates the result once, and fills it in bounded blocks.

    '''

    if max_bytes is None:

        max_bytes = MAX_COORDINATE_SOURCE_BYTES

    check_array_size(payload.shape, payload.dtype.itemsize, context,
                     max_bytes = max_bytes)

    result = np.empty(payload.shape, dtype = payload.dtype)

    def store(selection, block) -> None:

        result[selection] = block

    _each_block(payload, session, store)

    return result
