"""Trim real HDF-EOS2 granules into small committed test fixtures.

Reads the raw sample granules (kept OUTSIDE the repo — see the provenance
notes in the local source-granule archive) and writes small HDF4
fixtures that preserve the HDF-EOS2 *structure* the geolocation subsystem
must handle (docs/design/2026-07-08-hdfeos2-geolocation.md):

- ``amsre_seaice12km_trim.hdf`` — GRID case. Both polar-stereographic grids
  (NH + SH ICECON_DAY), full-resolution, deflate-compressed;
  ``StructMetadata.0`` copied VERBATIM (grid dims are unchanged, so the
  metadata stays exactly true; DataField entries for SDS not carried over
  remain listed — readers must tolerate metadata-listed-but-absent fields,
  which mirrors subsetted granules in the wild).
- ``mod03_trim.hdf`` — SWATH case, full-res geolocation. First
  ``N_SCANS_1KM`` along-track lines of Latitude/Longitude/SensorZenith;
  along-track dimension sizes in ``StructMetadata.0`` are rewritten to the
  trimmed sizes (the ONLY edit; documented in the sidecar).
- ``myd05_trim.hdf`` — SWATH case with 5km→1km dimension maps. Trimmed
  along-track keeping the DimensionMap offset/increment intact.

Each fixture gets a ``<name>.provenance.json`` sidecar recording the source
granule, its SHA-256, the trim parameters, and the date.

Usage:
    python tests/fixtures/trim_hdfeos2.py [--granule-dir DIR] [--out DIR]

Requires: pyhdf, numpy.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np
from pyhdf.SD import SD, SDC

DEFAULT_GRANULE_DIR = Path.home() / "ncarnate-data" / "granules"
DEFAULT_OUT = Path(__file__).parent / "data" / "hdfeos2"

AMSRE = "AMSR_E_L3_SeaIce12km_B02_20020619.hdf"
MOD03 = "MOD03.A2002299.0710.006.2012261211245.hdf"
MYD05 = "MYD05_L2.A2020060.1635.061.2020061153519.hdf"

N_SCANS_1KM = 20   # MOD03: 2 scans x 10 lines (along-track)
N_FRAMES_1KM = 270  # MOD03: across-track frames kept (mframes*2 -> 540)
N_LINES_1KM = 50   # MYD05: 1km along-track lines kept
N_LINES_5KM = 10   # MYD05: 5km along-track lines kept (offset=2, inc=5 -> covers 47)

FIXTURE_BUDGET = 200_000  # bytes; the plan's Phase-1 "< 200 KB each" DoD


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def structmetadata(sd: SD) -> str:
    def suffix(key: str) -> int:
        m = re.search(r"\.(\d+)$", key)
        return int(m.group(1)) if m else 0

    keys = sorted((k for k in sd.attributes() if k.startswith("StructMetadata")),
                  key=suffix)
    return "".join(sd.attributes()[k] for k in keys)


def copy_file_attrs(src: SD, dst: SD, structmeta_override: str | None = None) -> None:
    """Copy global attributes; optionally replace the StructMetadata text."""
    wrote_sm = False
    for name, value in src.attributes().items():
        if name.startswith("StructMetadata"):
            if not wrote_sm:
                text = structmeta_override if structmeta_override is not None \
                    else structmetadata(src)
                dst.attr("StructMetadata.0").set(SDC.CHAR, text)
                wrote_sm = True
            continue  # never duplicate split parts
        if isinstance(value, str):
            dst.attr(name).set(SDC.CHAR, value)
        # Non-string globals (none in the surveyed granules) are skipped.


def copy_attrs_typed(src_obj, dst_obj, n_attrs: int) -> None:
    """Copy attributes preserving each one's exact source HDF4 type code.

    pyhdf's ``attributes()`` type-erases values to Python scalars; inferring
    the output type from those would silently widen (e.g. INT16 -> INT32).
    ``attr(index).info()`` reports the true stored type, so we write with it.
    """
    for idx in range(n_attrs):
        attr = src_obj.attr(idx)
        attr_name, attr_type, _n = attr.info()
        dst_obj.attr(attr_name).set(attr_type, attr.get())


def copy_sds(src: SD, dst: SD, name: str,
             rows: int | None = None, cols: int | None = None) -> None:
    """Copy one SDS (optionally trimmed along axes 0/1), deflated."""
    ds = src.select(name)
    _, rank, dims, dtype, n_attrs = ds.info()
    dims = [dims] if rank == 1 else list(dims)
    data = ds[:]
    if rows is not None:
        data = data[0:rows]
        dims[0] = rows
    if cols is not None:
        data = data[:, 0:cols]
        dims[1] = cols
    out = dst.create(name, dtype, tuple(dims))
    for axis in range(rank):
        dim_name = ds.dim(axis).info()[0]
        out.dim(axis).setname(dim_name)
    out.setcompress(SDC.COMP_DEFLATE, 9)
    copy_attrs_typed(ds, out, n_attrs)
    out[:] = data
    out.endaccess()
    ds.endaccess()


def rewrite_dim_sizes(sm: str, new_sizes: dict[str, int]) -> str:
    """Rewrite Size= for the named EOS dimensions in StructMetadata text."""
    for dim_name, size in new_sizes.items():
        pattern = (
            r'(OBJECT=Dimension_\d+\s*\n\s*DimensionName="'
            + re.escape(dim_name)
            + r'"\s*\n\s*Size=)\d+'
        )
        sm, count = re.subn(pattern, rf"\g<1>{size}", sm)
        if count != 1:
            raise RuntimeError(
                f"expected exactly one Size= for dimension {dim_name!r}, got {count}"
            )
    return sm


def provenance(out: Path, source: Path, kept: list[str], trim: dict) -> None:
    sidecar = out.with_suffix(".provenance.json")
    sidecar.write_text(json.dumps({
        "fixture": out.name,
        "source_granule": source.name,
        "source_sha256": sha256(source),
        "source_notes": "see the local source-granule archive",
        "sds_kept": kept,
        "trim": trim,
        "generated": datetime.date.today().isoformat(),
        "generator": "tests/fixtures/trim_hdfeos2.py",
    }, indent=2) + "\n")


def trim_amsre(granule_dir: Path, out_dir: Path) -> Path:
    source = granule_dir / AMSRE
    out = out_dir / "amsre_seaice12km_trim.hdf"
    kept = ["SI_12km_NH_ICECON_DAY", "SI_12km_SH_ICECON_DAY"]
    src = SD(str(source), SDC.READ)
    dst = SD(str(out), SDC.WRITE | SDC.CREATE | SDC.TRUNC)
    copy_file_attrs(src, dst)  # StructMetadata verbatim: grid dims unchanged
    for name in kept:
        copy_sds(src, dst, name)
    dst.end()
    src.end()
    provenance(out, source, kept, {"mode": "full-resolution, SDS subset only"})
    return out


def trim_mod03(granule_dir: Path, out_dir: Path) -> Path:
    source = granule_dir / MOD03
    out = out_dir / "mod03_trim.hdf"
    kept = ["Latitude", "Longitude", "SensorZenith"]
    src = SD(str(source), SDC.READ)
    # MOD03 declares exactly four EOS dimensions (no bare "nscans"):
    # nscans*10, mframes, nscans*20, mframes*2. Both directions are trimmed
    # to meet the < 200 KB fixture budget; the DimensionMap offsets and
    # increments are untouched (0/2 stays consistent with the new sizes:
    # geo index g maps to data index 2g <= 2*(N-1) < 2N).
    sm = rewrite_dim_sizes(structmetadata(src), {
        "nscans*10": N_SCANS_1KM,
        "nscans*20": N_SCANS_1KM * 2,
        "mframes": N_FRAMES_1KM,
        "mframes*2": N_FRAMES_1KM * 2,
    })
    dst = SD(str(out), SDC.WRITE | SDC.CREATE | SDC.TRUNC)
    copy_file_attrs(src, dst, structmeta_override=sm)
    for name in kept:
        copy_sds(src, dst, name, rows=N_SCANS_1KM, cols=N_FRAMES_1KM)
    dst.end()
    src.end()
    provenance(out, source, kept, {
        "mode": "along-track + across-track trim",
        "rows_kept_1km": N_SCANS_1KM,
        "cols_kept_1km": N_FRAMES_1KM,
        "structmetadata_edit":
            "Size= rewritten for nscans*10, nscans*20, mframes, mframes*2",
    })
    return out


def trim_myd05(granule_dir: Path, out_dir: Path) -> Path:
    source = granule_dir / MYD05
    out = out_dir / "myd05_trim.hdf"
    src = SD(str(source), SDC.READ)
    sm = rewrite_dim_sizes(structmetadata(src), {
        "Cell_Along_Swath_1km": N_LINES_1KM,
        "Cell_Along_Swath_5km": N_LINES_5KM,
    })
    dst = SD(str(out), SDC.WRITE | SDC.CREATE | SDC.TRUNC)
    copy_file_attrs(src, dst, structmeta_override=sm)
    copy_sds(src, dst, "Latitude", rows=N_LINES_5KM)
    copy_sds(src, dst, "Longitude", rows=N_LINES_5KM)
    copy_sds(src, dst, "Water_Vapor_Near_Infrared", rows=N_LINES_1KM)
    dst.end()
    src.end()
    provenance(out, source,
               ["Latitude", "Longitude", "Water_Vapor_Near_Infrared"], {
                   "mode": "along-track trim (dimension maps intact)",
                   "rows_kept_1km": N_LINES_1KM,
                   "rows_kept_5km": N_LINES_5KM,
                   "dimension_map": "offset=2, increment=5 (unchanged)",
                   "structmetadata_edit":
                       "Size= rewritten for Cell_Along_Swath_1km/5km",
               })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--granule-dir", type=Path, default=DEFAULT_GRANULE_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    status = 0
    for trimmer in (trim_amsre, trim_mod03, trim_myd05):
        path = trimmer(args.granule_dir, args.out)
        size = path.stat().st_size
        print(f"wrote {path} ({size:,} bytes)")
        if size > FIXTURE_BUDGET:
            print(f"ERROR: {path.name} exceeds the {FIXTURE_BUDGET:,}-byte budget",
                  file=sys.stderr)
            status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
