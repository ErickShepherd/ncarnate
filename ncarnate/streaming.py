"""Bounded netCDF and HDF4 slices; not a total-process memory limit.

The budget covers transfer/compare arrays. Native library caches, metadata,
Python and compression workspace add overhead. Generated HDF-EOS coordinates
can still require whole arrays; execute_bounded supplies a worker memory cap.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import math
import logging

_budget = ContextVar("ncarnate_array_budget", default=16 * 1024 * 1024)


@contextmanager
def array_budget(size_bytes):
    """Set the array working budget for this synchronous conversion context."""
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 1024:
        raise ValueError("array budget must be an integer of at least 1024 bytes")
    token = _budget.set(size_bytes)
    try:
        yield
    finally:
        _budget.reset(token)


def slices(shape, itemsize, chunks=None):
    """Yield rectangular slices, reserving room for both arrays and comparison.

Using an eightfold reserve also bounds NumPy's comparison temporaries. Scalars
use (), empty arrays need no reads. No full index list is retained.
"""
    if any(size == 0 for size in shape):
        return
    capacity = max(1, _budget.get() // (8 * max(1, itemsize)))
    if isinstance(chunks, (list, tuple)) and len(chunks) == len(shape) and all(c > 0 for c in chunks):
        elements = math.prod(chunks)
        if elements > capacity:
            # Finish each native chunk before evicting it. Row-major slabs
            # spanning multiple chunks repeatedly recompress partially written
            # chunks when the cache can hold only one.
            for chunk in _tiles(shape, chunks):
                local_shape = tuple(part.stop - part.start for part in chunk)
                for selection in slices(local_shape, itemsize):
                    yield tuple(slice(base.start + part.start, base.start + part.stop)
                                for base, part in zip(chunk, selection))
            return
        block = list(chunks)
        multiples = max(1, capacity // elements)
        for axis in reversed(range(len(shape))):
            count = min((shape[axis] + chunks[axis] - 1) // chunks[axis], multiples)
            block[axis] *= count
            multiples = max(1, multiples // count)
        yield from _tiles(shape, block)
        return
    block = [1] * len(shape)
    for axis in reversed(range(len(shape))):
        block[axis] = min(shape[axis], capacity)
        capacity = max(1, capacity // block[axis])
    yield from _tiles(shape, block)


def _tiles(shape, block):
    # itertools.product caches its inputs: use a counter instead of ranges for
    # arbitrarily long dimensions.
    start = [0] * len(shape)
    while True:
        yield tuple(slice(a, min(a + b, size)) for a, b, size in zip(start, block, shape))
        for axis in reversed(range(len(shape))):
            start[axis] += block[axis]
            if start[axis] < shape[axis]:
                break
            start[axis] = 0
        else:
            return


def limit_cache(variable):
    """Keep each variable's native chunk cache small (not a global RSS bound)."""
    if hasattr(variable, "set_var_chunk_cache"):
        # netCDF-C can reopen the wrong HDF5 object when a multidimensional
        # variable shares a dimension's name (e.g. MOD03 Scan_Type). Its data
        # become unreadable after changing the cache. Keep the native default
        # in this case; execute_bounded still caps the whole worker.
        group = variable.group()
        # netCDF3 variables expose the cache methods in netCDF4-python,
        # but netCDF-C rejects these HDF5-only operations on classic files.
        if group.data_model.startswith("NETCDF3"):
            return
        while group is not None:
            if variable.name in group.dimensions and variable.dimensions != (variable.name,):
                return
            group = group.parent
        chunks = variable.chunking()
        chunk_bytes = math.prod(chunks) * variable.dtype.itemsize if isinstance(chunks, list) else 0
        if chunk_bytes > _budget.get():
            logging.getLogger("ncarnate").warning("Variable %r has a %d-byte native chunk exceeding the %d-byte array budget; native cache is additional memory", variable.name, chunk_bytes, _budget.get())
        variable.set_var_chunk_cache(size=max(chunk_bytes, min(1024 * 1024, _budget.get() // 8)), nelems=1009, preemption=0.75)
