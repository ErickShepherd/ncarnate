Verified local Zarr demonstration
===================================

This is a separate package, excluded from ncarnate's wheel and source
distribution. It consumes a handoff and a caller-located netCDF checkpoint.
It never opens the handoff's advisory source path.

Use Python 3.11 or newer. The example requires ncarnate >=2.3,<3. To test a
local release candidate, install that checkout first, then this directory::

    python -m pip install -e .
    python -m pip install -e companions/zarr-demo
    ncarnate-zarr-demo handoff.json verified.nc output.zarr

Create handoff.json using json.dump(execute_prepared(prepare(source, output)),
stream). The output store must be new and its parent must already exist.
The tested dependency pair is Zarr 3.1.6 with xarray 2026.9.0; the package pins
them because floating CF fill attributes have a specific xarray v3 encoding.

The supported profile contains real fixed-size integer and floating variables,
groups, scalars, inherited dimensions and finite JSON-safe attributes. Raw
packing declarations are preserved. Character arrays, variable-length arrays,
complex values and non-finite attributes are refused. The handoff retains
netCDF attribute storage types; Zarr attributes themselves are JSON values.
Unused and unlimited netCDF dimensions are recorded in COMMIT.json because
Zarr does not have netCDF's independent dimension declarations.

The consumer checks the checkpoint digest before and after reading. It writes
to a private staging directory, reopens every group through xarray with CF
decoding disabled, and compares raw values, shapes, dimensions, attributes and
dtypes. It then records every store file's digest and renames the completed
directory into place. Readers must call verify_store and require COMMIT.json.
Any failed validation removes only this run's staging directory and preserves
the checkpoint. The hashes detect corruption; they are not signatures.

This is local-filesystem, cooperating-writer behavior. It does not claim cloud
transactions, hostile-filesystem race protection, power-loss durability, a
global memory bound or production readiness. A killed process can leave a
hidden staging directory; that directory is not a published store.

Validation on October 8, 2026 covered exact full RainGrid, Snow and MYD05
originals, plus failure injection and synthetic packed, scalar and grouped
data. These are conversion and materialization checks, not an independent
scientific validation of reconstructed coordinates.

Install pytest and run the companion's tests explicitly; the main pytest
configuration does not collect them automatically::

    python -m pip install pytest
    python -m pytest companions/zarr-demo/tests -q

The separate CI job builds both package formats, installs the companion wheel
with the candidate ncarnate wheel, checks dependencies, and runs these tests
outside the source checkout. The example stays separate from the main PyPI
publication and does not acquire a production-readiness claim from passing CI.
