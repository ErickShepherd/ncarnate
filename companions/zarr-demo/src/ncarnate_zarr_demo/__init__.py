"""A deliberately narrow external consumer; never deletes the netCDF checkpoint."""
import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile

import netCDF4 as nc
import numpy as np
import xarray as xr
import zarr

from ncarnate import check_materializable
from ncarnate.hashing import sha256_of_file
from ncarnate.result import json_safe
from ncarnate.streaming import slices


def _attributes(obj):
    attrs = {}
    for name in obj.ncattrs():
        value = obj.getncattr(name)
        array = np.asarray(value)
        if array.dtype.kind in "fc" and not np.all(np.isfinite(array)):
            raise ValueError("demo profile refuses non-finite attributes")
        attrs[name] = json_safe(value)
    return attrs


def _groups(group):
    yield group
    for child in group.groups.values():
        yield from _groups(child)


def _no_links(path):
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(ancestor)):
            raise ValueError(f"link boundary refused: {ancestor}")


def _inventory(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path != root / "COMMIT.json":
            result[path.relative_to(root).as_posix()] = sha256_of_file(str(path))
    return result


def materialize(record, checkpoint, destination):
    """Validate, stage, reopen and compare numeric arrays, then commit locally.

Requires a caller-located checkpoint; the handoff's advisory path is never
opened. Numeric fixed-size variables and JSON-safe finite attributes only.
No cloud, concurrent writers, hostile-filesystem race protection or global
process-memory bound is claimed.
"""
    check_materializable(record)
    checkpoint, destination = Path(checkpoint).absolute(), Path(destination).absolute()
    _no_links(checkpoint)
    _no_links(destination)
    if destination.exists() or not destination.parent.is_dir():
        raise ValueError("destination must be new and its parent must exist")
    expected = record["destination"]
    def verify_checkpoint():
        if checkpoint.stat().st_size != expected["size_bytes"] or sha256_of_file(str(checkpoint)) != expected["sha256"]:
            raise ValueError("netCDF checkpoint identity mismatch")
    verify_checkpoint()
    temporary = Path(tempfile.mkdtemp(prefix=".ncarnate-zarr-", dir=destination.parent))
    try:
        root = zarr.open_group(str(temporary), mode="w", zarr_format=3)
        dimensions = {}
        with nc.Dataset(checkpoint) as source:
            for group in _groups(source):
                path = group.path.strip("/")
                target = root.require_group(path) if path else root
                target.attrs.update(_attributes(group))
                dimensions[group.path] = {name: {"size": len(dim), "unlimited": dim.isunlimited()}
                                          for name, dim in group.dimensions.items()}
                for name, variable in group.variables.items():
                    if variable.dtype.kind not in "iuf" or variable.dtype.itemsize > 8:
                        raise ValueError(f"demo supports fixed-size real numeric arrays only: {group.path}/{name}")
                    variable.set_auto_maskandscale(False)
                    attrs = _attributes(variable)
                    if "_FillValue" in attrs and variable.dtype.kind == "f":
                        # The pinned xarray Zarr v3 codec stores floating CF
                        # fill attributes as base64 little-endian float64.
                        attrs["_FillValue"] = base64.standard_b64encode(
                            struct.pack("<d", float(attrs["_FillValue"]))
                        ).decode("ascii")
                    # Small rectangular chunks, independent of ncarnate storage.
                    capacity = 65536 // variable.dtype.itemsize
                    chunks = [1] * variable.ndim
                    for axis in reversed(range(variable.ndim)):
                        chunks[axis] = max(1, min(variable.shape[axis], capacity))
                        capacity = max(1, capacity // chunks[axis])
                    array = target.create_array(name, shape=variable.shape, dtype=variable.dtype.newbyteorder("="),
                                                chunks=tuple(chunks), dimension_names=variable.dimensions,
                                                attributes=attrs)
                    for selection in slices(variable.shape, variable.dtype.itemsize):
                        array[selection] = variable[selection]
            # Independent consumer reopening, raw values (no CF mask/scale).
            for group in _groups(source):
                path = group.path.strip("/") or None
                with xr.open_zarr(str(temporary), group=path, consolidated=False, chunks=None,
                                  decode_cf=False, mask_and_scale=False, create_default_indexes=False,
                                  use_zarr_fill_value_as_mask=False) as reopened:
                    if set(reopened.variables) != set(group.variables):
                        raise ValueError("xarray variable inventory mismatch")
                    if json_safe(dict(reopened.attrs)) != _attributes(group):
                        raise ValueError("xarray group metadata mismatch")
                    for name, variable in group.variables.items():
                        output = reopened[name]
                        if tuple(output.dims) != variable.dimensions or output.shape != variable.shape:
                            raise ValueError("xarray dimensions mismatch")
                        if output.dtype != variable.dtype.newbyteorder("=") or json_safe(dict(output.attrs)) != _attributes(variable):
                            raise ValueError("xarray dtype/metadata mismatch")
                        for selection in slices(variable.shape, variable.dtype.itemsize):
                            if not np.array_equal(output[selection].values, variable[selection], equal_nan=variable.dtype.kind == "f"):
                                raise ValueError("xarray raw value mismatch")
        verify_checkpoint()
        manifest = {"schema_version": 1, "profile": "local-fixed-numeric-v1",
                    "checkpoint_sha256": expected["sha256"], "handoff": record,
                    "netcdf_dimensions": dimensions, "files": _inventory(temporary)}
        with (temporary / "COMMIT.json").open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        verify_store(temporary)
        # All writers are closed. Cooperating readers only use the named target
        # after this rename and validate COMMIT.json; hidden stages are not ready.
        os.rename(temporary, destination)
        return manifest
    finally:
        if temporary.exists():
            if temporary.resolve().parent != destination.parent.resolve() or not temporary.name.startswith(".ncarnate-zarr-"):
                raise ValueError("unexpected staging cleanup path")
            shutil.rmtree(temporary)  # only this function's unique staging dir


def verify_store(destination):
    """Refuse incomplete or changed stores; hashes detect damage, not forgery."""
    destination = Path(destination).absolute()
    _no_links(destination)
    for path in destination.rglob("*"):
        _no_links(path)
    manifest = json.loads((destination / "COMMIT.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("profile") != "local-fixed-numeric-v1":
        raise ValueError("unknown committed store format")
    check_materializable(manifest["handoff"])
    if manifest["files"] != _inventory(destination):
        raise ValueError("committed store content mismatch")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("handoff", type=Path)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    materialize(json.loads(args.handoff.read_text(encoding="utf-8")), args.checkpoint, args.destination)
    print(f"Verified local store: {args.destination}")
